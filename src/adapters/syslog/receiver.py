"""Syslog receiver for legacy system logs."""

import asyncio
import re
from datetime import datetime
from uuid import uuid4

import structlog

from src.core.config import settings
from src.core.event_processor import get_event_processor
from src.core.models import Event, EventSeverity, EventSource

logger = structlog.get_logger()

# Syslog severity mapping (RFC 5424)
SYSLOG_SEVERITY = {
    0: EventSeverity.CRITICAL,  # Emergency
    1: EventSeverity.CRITICAL,  # Alert
    2: EventSeverity.CRITICAL,  # Critical
    3: EventSeverity.WARNING,   # Error
    4: EventSeverity.WARNING,   # Warning
    5: EventSeverity.INFO,      # Notice
    6: EventSeverity.INFO,      # Informational
    7: EventSeverity.INFO,      # Debug
}

# Syslog facility names
SYSLOG_FACILITY = {
    0: "kern", 1: "user", 2: "mail", 3: "daemon",
    4: "auth", 5: "syslog", 6: "lpr", 7: "news",
    8: "uucp", 9: "cron", 10: "authpriv", 11: "ftp",
    16: "local0", 17: "local1", 18: "local2", 19: "local3",
    20: "local4", 21: "local5", 22: "local6", 23: "local7",
}


class SyslogProtocol(asyncio.DatagramProtocol):
    """UDP syslog protocol handler."""

    def __init__(self, receiver: "SyslogReceiver") -> None:
        self.receiver = receiver

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Handle received syslog message."""
        try:
            message = data.decode("utf-8", errors="replace")
            asyncio.create_task(self.receiver.process_message(message, addr[0]))
        except Exception as e:
            logger.error("Error processing syslog message", error=str(e))


class SyslogReceiver:
    """Receives and processes syslog messages."""

    def __init__(self, port: int = settings.syslog_port) -> None:
        self.port = port
        self._transport: asyncio.DatagramTransport | None = None
        self._running = False

    async def start(self) -> None:
        """Start the syslog receiver."""
        loop = asyncio.get_event_loop()

        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: SyslogProtocol(self),
            local_addr=("0.0.0.0", self.port),
        )

        self._running = True
        logger.info("Syslog receiver started", port=self.port)

    async def stop(self) -> None:
        """Stop the syslog receiver."""
        if self._transport:
            self._transport.close()
        self._running = False
        logger.info("Syslog receiver stopped")

    async def process_message(self, message: str, source_ip: str) -> None:
        """Process a syslog message and submit as event."""
        parsed = self._parse_syslog(message)

        event = Event(
            id=str(uuid4()),
            source=EventSource.SYSLOG,
            severity=parsed["severity"],
            title=f"Syslog: {parsed['facility']} from {parsed['hostname'] or source_ip}",
            description=parsed["message"],
            labels={
                "source_ip": source_ip,
                "facility": parsed["facility"],
                "hostname": parsed["hostname"] or source_ip,
                "program": parsed["program"] or "unknown",
            },
            raw_data={"raw": message, "parsed": parsed},
        )

        processor = get_event_processor()
        await processor.submit_event(event)

        logger.debug(
            "Syslog event submitted",
            event_id=event.id,
            source_ip=source_ip,
            facility=parsed["facility"],
        )

    def _parse_syslog(self, message: str) -> dict:
        """Parse a syslog message (RFC 3164/5424)."""
        result = {
            "severity": EventSeverity.INFO,
            "facility": "unknown",
            "hostname": None,
            "program": None,
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
        }

        # Try to parse RFC 3164 format: <PRI>TIMESTAMP HOSTNAME TAG: MSG
        match = re.match(
            r"<(\d+)>(\w{3}\s+\d+\s+\d+:\d+:\d+)\s+(\S+)\s+(\S+?):\s*(.*)",
            message,
        )

        if match:
            pri = int(match.group(1))
            facility_num = pri >> 3
            severity_num = pri & 0x07

            result["severity"] = SYSLOG_SEVERITY.get(severity_num, EventSeverity.INFO)
            result["facility"] = SYSLOG_FACILITY.get(facility_num, f"facility{facility_num}")
            result["hostname"] = match.group(3)
            result["program"] = match.group(4)
            result["message"] = match.group(5)
            return result

        # Try simpler format: <PRI>MSG
        match = re.match(r"<(\d+)>(.*)", message)
        if match:
            pri = int(match.group(1))
            severity_num = pri & 0x07
            result["severity"] = SYSLOG_SEVERITY.get(severity_num, EventSeverity.INFO)
            result["message"] = match.group(2)

        return result

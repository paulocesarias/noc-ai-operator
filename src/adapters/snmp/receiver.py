"""SNMP trap receiver for network devices and legacy infrastructure."""

import asyncio
from datetime import datetime
from typing import Any
from uuid import uuid4

import structlog
from pysnmp.carrier.asyncio.dgram import udp
from pysnmp.entity import config, engine
from pysnmp.entity.rfc3413 import ntfrcv

from src.core.config import settings
from src.core.event_processor import get_event_processor
from src.core.models import Event, EventSeverity, EventSource

logger = structlog.get_logger()

# SNMP trap severity mapping based on common conventions
TRAP_SEVERITY_MAP = {
    # Generic trap types (SNMPv1)
    0: EventSeverity.CRITICAL,  # coldStart
    1: EventSeverity.WARNING,   # warmStart
    2: EventSeverity.CRITICAL,  # linkDown
    3: EventSeverity.INFO,      # linkUp
    4: EventSeverity.WARNING,   # authenticationFailure
    5: EventSeverity.WARNING,   # egpNeighborLoss
    6: EventSeverity.INFO,      # enterpriseSpecific
}

# Common OIDs for trap identification
WELL_KNOWN_OIDS = {
    "1.3.6.1.6.3.1.1.5.1": ("coldStart", EventSeverity.CRITICAL),
    "1.3.6.1.6.3.1.1.5.2": ("warmStart", EventSeverity.WARNING),
    "1.3.6.1.6.3.1.1.5.3": ("linkDown", EventSeverity.CRITICAL),
    "1.3.6.1.6.3.1.1.5.4": ("linkUp", EventSeverity.INFO),
    "1.3.6.1.6.3.1.1.5.5": ("authenticationFailure", EventSeverity.WARNING),
    "1.3.6.1.4.1.9.9.43.2.0.1": ("ciscoConfigManEvent", EventSeverity.INFO),
    "1.3.6.1.4.1.9.9.41.2.0.1": ("ciscoSyslogMessage", EventSeverity.WARNING),
}


class SNMPTrapReceiver:
    """Receives and processes SNMP traps from network devices."""

    def __init__(self, port: int = settings.snmp_port) -> None:
        self.port = port
        self._running = False
        self._engine: engine.SnmpEngine | None = None
        self._transport = None

    async def start(self) -> None:
        """Start the SNMP trap receiver."""
        self._engine = engine.SnmpEngine()

        # Configure SNMP engine for receiving traps
        config.addTransport(
            self._engine,
            udp.domainName,
            udp.UdpTransport().openServerMode(("0.0.0.0", self.port)),
        )

        # Configure community string (SNMPv2c)
        config.addV1System(self._engine, "public-area", "public")

        # Register callback for incoming notifications
        ntfrcv.NotificationReceiver(self._engine, self._trap_callback)

        self._running = True
        logger.info("SNMP trap receiver started", port=self.port)

        # Run the dispatcher
        self._engine.transportDispatcher.jobStarted(1)
        try:
            self._engine.transportDispatcher.runDispatcher()
        except Exception as e:
            logger.error("SNMP dispatcher error", error=str(e))

    async def stop(self) -> None:
        """Stop the SNMP trap receiver."""
        self._running = False
        if self._engine:
            self._engine.transportDispatcher.closeDispatcher()
        logger.info("SNMP trap receiver stopped")

    def _trap_callback(
        self,
        snmp_engine: engine.SnmpEngine,
        state_reference: Any,
        context_engine_id: Any,
        context_name: Any,
        var_binds: list,
        cb_ctx: Any,
    ) -> None:
        """Callback for incoming SNMP traps."""
        try:
            # Get transport info
            transport_domain, transport_address = snmp_engine.msgAndPduDsp.getTransportInfo(
                state_reference
            )
            source_ip = transport_address[0] if transport_address else "unknown"

            # Parse trap data
            trap_data = self._parse_trap(var_binds)

            # Create event
            event = Event(
                id=str(uuid4()),
                source=EventSource.SNMP,
                severity=trap_data["severity"],
                title=f"SNMP Trap: {trap_data['trap_type']} from {source_ip}",
                description=trap_data["description"],
                labels={
                    "source_ip": source_ip,
                    "trap_oid": trap_data["trap_oid"],
                    "trap_type": trap_data["trap_type"],
                },
                raw_data=trap_data,
            )

            # Submit to event processor (in async context)
            asyncio.create_task(self._submit_event(event))

            logger.info(
                "SNMP trap received",
                source_ip=source_ip,
                trap_type=trap_data["trap_type"],
            )

        except Exception as e:
            logger.error("Error processing SNMP trap", error=str(e))

    async def _submit_event(self, event: Event) -> None:
        """Submit event to processor."""
        processor = get_event_processor()
        await processor.submit_event(event)

    def _parse_trap(self, var_binds: list) -> dict[str, Any]:
        """Parse SNMP trap variable bindings."""
        result = {
            "trap_oid": "",
            "trap_type": "unknown",
            "severity": EventSeverity.INFO,
            "description": "",
            "variables": {},
            "timestamp": datetime.utcnow().isoformat(),
        }

        for oid, val in var_binds:
            oid_str = str(oid)
            val_str = str(val)

            # Check for trap OID
            if "1.3.6.1.6.3.1.1.4.1" in oid_str:  # snmpTrapOID
                result["trap_oid"] = val_str
                if val_str in WELL_KNOWN_OIDS:
                    result["trap_type"], result["severity"] = WELL_KNOWN_OIDS[val_str]
                else:
                    result["trap_type"] = val_str.split(".")[-1]

            # Store all variables
            result["variables"][oid_str] = val_str

        # Build description from variables
        var_desc = "; ".join(
            f"{k.split('.')[-1]}={v}" for k, v in list(result["variables"].items())[:5]
        )
        result["description"] = f"Trap {result['trap_type']}: {var_desc}"

        return result


class SNMPTrapReceiverAsync:
    """Async-friendly SNMP trap receiver using asyncio transport."""

    def __init__(self, port: int = settings.snmp_port) -> None:
        self.port = port
        self._running = False
        self._transport: asyncio.DatagramTransport | None = None

    async def start(self) -> None:
        """Start the async SNMP trap receiver."""
        loop = asyncio.get_event_loop()

        # Create UDP endpoint
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: SNMPTrapProtocol(self),
            local_addr=("0.0.0.0", self.port),
        )

        self._running = True
        logger.info("Async SNMP trap receiver started", port=self.port)

    async def stop(self) -> None:
        """Stop the async SNMP trap receiver."""
        self._running = False
        if self._transport:
            self._transport.close()
        logger.info("Async SNMP trap receiver stopped")

    async def process_trap(self, data: bytes, addr: tuple[str, int]) -> None:
        """Process incoming SNMP trap data."""
        try:
            # Basic SNMP packet parsing
            trap_info = self._parse_snmp_packet(data)

            event = Event(
                id=str(uuid4()),
                source=EventSource.SNMP,
                severity=trap_info.get("severity", EventSeverity.INFO),
                title=f"SNMP Trap from {addr[0]}",
                description=trap_info.get("description", "SNMP trap received"),
                labels={
                    "source_ip": addr[0],
                    "source_port": str(addr[1]),
                },
                raw_data={"raw_hex": data.hex()[:200], **trap_info},
            )

            processor = get_event_processor()
            await processor.submit_event(event)

            logger.debug("SNMP trap processed", source_ip=addr[0])

        except Exception as e:
            logger.error("Error processing SNMP trap", error=str(e), source_ip=addr[0])

    def _parse_snmp_packet(self, data: bytes) -> dict[str, Any]:
        """Basic SNMP packet parsing."""
        # This is a simplified parser - production would use pysnmp's full decoder
        result = {
            "severity": EventSeverity.INFO,
            "description": "SNMP trap received",
        }

        # Check for common trap indicators in raw data
        if b"\x06\x08" in data:  # OID marker
            result["description"] = "SNMP v2c/v3 trap"
        elif b"\xa4" in data:  # SNMPv1 trap PDU
            result["description"] = "SNMP v1 trap"
            result["severity"] = EventSeverity.WARNING

        return result


class SNMPTrapProtocol(asyncio.DatagramProtocol):
    """UDP protocol handler for SNMP traps."""

    def __init__(self, receiver: SNMPTrapReceiverAsync) -> None:
        self.receiver = receiver

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Handle received SNMP trap datagram."""
        asyncio.create_task(self.receiver.process_trap(data, addr))

    def error_received(self, exc: Exception) -> None:
        """Handle protocol errors."""
        logger.error("SNMP protocol error", error=str(exc))

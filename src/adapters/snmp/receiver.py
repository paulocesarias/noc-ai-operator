"""SNMP trap receiver."""

import structlog

from src.core.config import settings

logger = structlog.get_logger()


class SNMPTrapReceiver:
    """Receives SNMP traps from network devices."""

    def __init__(self, port: int = settings.snmp_port) -> None:
        self.port = port
        self._running = False

    async def start(self) -> None:
        """Start the SNMP trap receiver."""
        self._running = True
        logger.info("SNMP trap receiver started", port=self.port)
        # TODO: Implement pysnmp trap receiver

    async def stop(self) -> None:
        """Stop the SNMP trap receiver."""
        self._running = False
        logger.info("SNMP trap receiver stopped")

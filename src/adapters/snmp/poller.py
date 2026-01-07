"""SNMP poller for device metrics and monitoring."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

import structlog
from pysnmp.hlapi import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    getCmd,
    nextCmd,
)

from src.core.config import settings
from src.core.event_processor import get_event_processor
from src.core.models import Event, EventSeverity, EventSource

logger = structlog.get_logger()

# Thread pool for blocking SNMP operations
_executor = ThreadPoolExecutor(max_workers=10)

# Common SNMP OIDs for monitoring
COMMON_OIDS = {
    # System
    "sysDescr": "1.3.6.1.2.1.1.1.0",
    "sysUpTime": "1.3.6.1.2.1.1.3.0",
    "sysName": "1.3.6.1.2.1.1.5.0",
    "sysLocation": "1.3.6.1.2.1.1.6.0",
    # Interfaces
    "ifNumber": "1.3.6.1.2.1.2.1.0",
    "ifTable": "1.3.6.1.2.1.2.2",
    # CPU/Memory (HOST-RESOURCES-MIB)
    "hrProcessorLoad": "1.3.6.1.2.1.25.3.3.1.2",
    "hrStorageUsed": "1.3.6.1.2.1.25.2.3.1.6",
    "hrStorageSize": "1.3.6.1.2.1.25.2.3.1.5",
    # Network statistics
    "ifInOctets": "1.3.6.1.2.1.2.2.1.10",
    "ifOutOctets": "1.3.6.1.2.1.2.2.1.16",
    "ifInErrors": "1.3.6.1.2.1.2.2.1.14",
    "ifOutErrors": "1.3.6.1.2.1.2.2.1.20",
}

# Thresholds for generating alerts
DEFAULT_THRESHOLDS = {
    "cpu_percent": 90,
    "memory_percent": 90,
    "disk_percent": 95,
    "interface_errors": 100,
}


@dataclass
class SNMPDevice:
    """SNMP device configuration."""

    host: str
    port: int = 161
    community: str = "public"
    version: int = 2
    name: str | None = None
    poll_interval: int = 60
    oids: list[str] | None = None


@dataclass
class SNMPResult:
    """Result from SNMP operation."""

    host: str
    oid: str
    value: Any
    timestamp: datetime
    error: str | None = None


class SNMPPoller:
    """Polls SNMP devices for metrics with async support."""

    def __init__(
        self,
        community: str = "public",
        version: int = 2,
        timeout: int = 5,
        retries: int = 2,
    ) -> None:
        self.community = community
        self.version = version
        self.timeout = timeout
        self.retries = retries
        self._engine = SnmpEngine()

    def _get_snmp_version(self) -> int:
        """Map version number to pysnmp version constant."""
        return 1 if self.version == 2 else 0

    def _sync_get(self, host: str, port: int, oids: list[str]) -> dict[str, Any]:
        """Synchronous SNMP GET operation."""
        results = {}

        for oid in oids:
            error_indication, error_status, error_index, var_binds = next(
                getCmd(
                    self._engine,
                    CommunityData(self.community, mpModel=self._get_snmp_version()),
                    UdpTransportTarget(
                        (host, port), timeout=self.timeout, retries=self.retries
                    ),
                    ContextData(),
                    ObjectType(ObjectIdentity(oid)),
                )
            )

            if error_indication:
                logger.warning(
                    "SNMP error",
                    host=host,
                    oid=oid,
                    error=str(error_indication),
                )
                results[oid] = {"error": str(error_indication)}
            elif error_status:
                logger.warning(
                    "SNMP error status",
                    host=host,
                    oid=oid,
                    error=f"{error_status.prettyPrint()} at {error_index}",
                )
                results[oid] = {
                    "error": f"{error_status.prettyPrint()} at {error_index}"
                }
            else:
                for var_bind in var_binds:
                    oid_str = str(var_bind[0])
                    value = var_bind[1].prettyPrint()
                    results[oid_str] = value

        return results

    def _sync_walk(self, host: str, port: int, oid: str) -> list[tuple[str, Any]]:
        """Synchronous SNMP WALK operation."""
        results = []

        for error_indication, error_status, error_index, var_binds in nextCmd(
            self._engine,
            CommunityData(self.community, mpModel=self._get_snmp_version()),
            UdpTransportTarget(
                (host, port), timeout=self.timeout, retries=self.retries
            ),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
            lexicographicMode=False,
        ):
            if error_indication:
                logger.warning(
                    "SNMP walk error",
                    host=host,
                    oid=oid,
                    error=str(error_indication),
                )
                break
            elif error_status:
                logger.warning(
                    "SNMP walk error status",
                    host=host,
                    oid=oid,
                    error=f"{error_status.prettyPrint()} at {error_index}",
                )
                break
            else:
                for var_bind in var_binds:
                    oid_str = str(var_bind[0])
                    value = var_bind[1].prettyPrint()
                    results.append((oid_str, value))

        return results

    async def poll(
        self, host: str, oids: list[str], port: int = 161
    ) -> dict[str, Any]:
        """Poll SNMP OIDs from a host asynchronously."""
        logger.info("Polling SNMP", host=host, oids=oids)

        loop = asyncio.get_event_loop()
        try:
            results = await loop.run_in_executor(
                _executor, self._sync_get, host, port, oids
            )
            logger.debug("SNMP poll complete", host=host, results_count=len(results))
            return results
        except Exception as e:
            logger.error("SNMP poll failed", host=host, error=str(e))
            return {"error": str(e)}

    async def walk(
        self, host: str, oid: str, port: int = 161
    ) -> list[tuple[str, Any]]:
        """Walk an SNMP OID tree asynchronously."""
        logger.info("Walking SNMP", host=host, oid=oid)

        loop = asyncio.get_event_loop()
        try:
            results = await loop.run_in_executor(
                _executor, self._sync_walk, host, port, oid
            )
            logger.debug("SNMP walk complete", host=host, results_count=len(results))
            return results
        except Exception as e:
            logger.error("SNMP walk failed", host=host, error=str(e))
            return []

    async def get_system_info(self, host: str, port: int = 161) -> dict[str, str]:
        """Get basic system information from device."""
        system_oids = [
            COMMON_OIDS["sysDescr"],
            COMMON_OIDS["sysUpTime"],
            COMMON_OIDS["sysName"],
            COMMON_OIDS["sysLocation"],
        ]
        results = await self.poll(host, system_oids, port)

        return {
            "description": results.get(COMMON_OIDS["sysDescr"], "Unknown"),
            "uptime": results.get(COMMON_OIDS["sysUpTime"], "Unknown"),
            "name": results.get(COMMON_OIDS["sysName"], "Unknown"),
            "location": results.get(COMMON_OIDS["sysLocation"], "Unknown"),
        }

    async def get_interface_stats(
        self, host: str, port: int = 161
    ) -> list[dict[str, Any]]:
        """Get interface statistics from device."""
        interfaces = []

        # Walk interface table
        if_results = await self.walk(host, COMMON_OIDS["ifTable"], port)

        # Group by interface index
        if_data: dict[str, dict[str, Any]] = {}
        for oid, value in if_results:
            parts = oid.split(".")
            if len(parts) >= 2:
                # OID format: ifTable.column.ifIndex
                column = parts[-2] if len(parts) > 1 else "unknown"
                if_index = parts[-1]

                if if_index not in if_data:
                    if_data[if_index] = {"index": if_index}

                # Map column numbers to names
                column_map = {
                    "1": "index",
                    "2": "description",
                    "3": "type",
                    "5": "speed",
                    "7": "admin_status",
                    "8": "oper_status",
                    "10": "in_octets",
                    "16": "out_octets",
                    "14": "in_errors",
                    "20": "out_errors",
                }
                col_name = column_map.get(column, f"col_{column}")
                if_data[if_index][col_name] = value

        interfaces = list(if_data.values())
        return interfaces


class SNMPMonitor:
    """Monitors SNMP devices and generates events based on thresholds."""

    def __init__(
        self,
        devices: list[SNMPDevice] | None = None,
        thresholds: dict[str, int] | None = None,
    ) -> None:
        self.devices = devices or []
        self.thresholds = thresholds or DEFAULT_THRESHOLDS
        self.poller = SNMPPoller()
        self._running = False
        self._tasks: list[asyncio.Task] = []

    def add_device(self, device: SNMPDevice) -> None:
        """Add a device to monitor."""
        self.devices.append(device)
        logger.info("Added SNMP device", host=device.host, name=device.name)

    def remove_device(self, host: str) -> None:
        """Remove a device from monitoring."""
        self.devices = [d for d in self.devices if d.host != host]
        logger.info("Removed SNMP device", host=host)

    async def start(self) -> None:
        """Start monitoring all devices."""
        self._running = True
        logger.info("Starting SNMP monitor", device_count=len(self.devices))

        for device in self.devices:
            task = asyncio.create_task(self._monitor_device(device))
            self._tasks.append(task)

    async def stop(self) -> None:
        """Stop monitoring all devices."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        logger.info("SNMP monitor stopped")

    async def _monitor_device(self, device: SNMPDevice) -> None:
        """Monitor a single device."""
        logger.info("Starting device monitor", host=device.host)

        while self._running:
            try:
                await self._poll_and_check(device)
            except Exception as e:
                logger.error(
                    "Device monitoring error", host=device.host, error=str(e)
                )
                # Generate event for monitoring failure
                await self._submit_event(
                    device=device,
                    title=f"SNMP monitoring failed for {device.name or device.host}",
                    description=f"Failed to poll device: {e}",
                    severity=EventSeverity.WARNING,
                    labels={"error_type": "poll_failure"},
                )

            await asyncio.sleep(device.poll_interval)

    async def _poll_and_check(self, device: SNMPDevice) -> None:
        """Poll device and check thresholds."""
        # Get system info
        system_info = await self.poller.get_system_info(device.host, device.port)

        # Get interface stats
        interfaces = await self.poller.get_interface_stats(device.host, device.port)

        # Check for interface errors
        for iface in interfaces:
            in_errors = int(iface.get("in_errors", 0) or 0)
            out_errors = int(iface.get("out_errors", 0) or 0)
            total_errors = in_errors + out_errors

            if total_errors > self.thresholds["interface_errors"]:
                await self._submit_event(
                    device=device,
                    title=f"High interface errors on {device.name or device.host}",
                    description=f"Interface {iface.get('description', iface.get('index'))} has {total_errors} errors",
                    severity=EventSeverity.WARNING,
                    labels={
                        "interface": iface.get("description", str(iface.get("index"))),
                        "in_errors": str(in_errors),
                        "out_errors": str(out_errors),
                    },
                )

            # Check for interface down
            oper_status = iface.get("oper_status", "1")
            admin_status = iface.get("admin_status", "1")
            if admin_status == "1" and oper_status == "2":
                await self._submit_event(
                    device=device,
                    title=f"Interface down on {device.name or device.host}",
                    description=f"Interface {iface.get('description', iface.get('index'))} is administratively up but operationally down",
                    severity=EventSeverity.CRITICAL,
                    labels={
                        "interface": iface.get("description", str(iface.get("index"))),
                        "admin_status": "up",
                        "oper_status": "down",
                    },
                )

        # Poll custom OIDs if configured
        if device.oids:
            custom_results = await self.poller.poll(
                device.host, device.oids, device.port
            )
            logger.debug(
                "Custom OID poll complete",
                host=device.host,
                results=custom_results,
            )

    async def _submit_event(
        self,
        device: SNMPDevice,
        title: str,
        description: str,
        severity: EventSeverity,
        labels: dict[str, str],
    ) -> None:
        """Submit an event to the processor."""
        event = Event(
            id=str(uuid4()),
            source=EventSource.SNMP,
            severity=severity,
            title=title,
            description=description,
            labels={
                "device_host": device.host,
                "device_name": device.name or device.host,
                **labels,
            },
            raw_data={
                "device": {
                    "host": device.host,
                    "port": device.port,
                    "community": device.community,
                    "name": device.name,
                }
            },
        )

        processor = get_event_processor()
        await processor.submit_event(event)


# Convenience function for quick polling
async def quick_poll(
    host: str,
    oids: list[str] | None = None,
    community: str = "public",
) -> dict[str, Any]:
    """Quick poll helper function."""
    poller = SNMPPoller(community=community)
    target_oids = oids or [
        COMMON_OIDS["sysDescr"],
        COMMON_OIDS["sysUpTime"],
        COMMON_OIDS["sysName"],
    ]
    return await poller.poll(host, target_oids)

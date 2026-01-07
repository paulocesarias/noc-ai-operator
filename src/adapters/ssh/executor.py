"""SSH command executor for legacy systems."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from typing import Any

import paramiko
import structlog

from src.core.models import RemediationAction

logger = structlog.get_logger()


@dataclass
class SSHResult:
    """Result of an SSH command execution."""

    stdout: str
    stderr: str
    exit_code: int
    success: bool
    host: str
    command: str
    duration_ms: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class SSHHost:
    """SSH host configuration."""

    host: str
    username: str
    password: str | None = None
    key_filename: str | None = None
    port: int = 22


class SSHExecutor:
    """Executes commands on remote systems via SSH."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str | None = None,
        key_filename: str | None = None,
        port: int = 22,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.key_filename = key_filename
        self.port = port
        self._client: paramiko.SSHClient | None = None

    async def connect(self) -> None:
        """Establish SSH connection."""
        loop = asyncio.get_event_loop()

        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: dict[str, Any] = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
            "timeout": 30,
        }

        if self.password:
            connect_kwargs["password"] = self.password
        if self.key_filename:
            connect_kwargs["key_filename"] = self.key_filename

        await loop.run_in_executor(
            None,
            partial(self._client.connect, **connect_kwargs),
        )
        logger.info("SSH connected", host=self.host, port=self.port)

    async def disconnect(self) -> None:
        """Close SSH connection."""
        if self._client:
            self._client.close()
            self._client = None
            logger.info("SSH disconnected", host=self.host)

    async def execute(self, command: str, timeout: int = 30) -> SSHResult:
        """Execute a command via SSH."""
        if not self._client:
            await self.connect()

        logger.info("Executing SSH command", host=self.host, command=command[:100])

        loop = asyncio.get_event_loop()
        start_time = datetime.utcnow()

        # Run in executor since paramiko is blocking
        stdin, stdout, stderr = await loop.run_in_executor(
            None,
            partial(self._client.exec_command, command, timeout=timeout),
        )

        # Read output in executor
        exit_code = await loop.run_in_executor(
            None,
            stdout.channel.recv_exit_status,
        )
        stdout_text = await loop.run_in_executor(
            None,
            lambda: stdout.read().decode("utf-8", errors="replace"),
        )
        stderr_text = await loop.run_in_executor(
            None,
            lambda: stderr.read().decode("utf-8", errors="replace"),
        )

        duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000

        result = SSHResult(
            stdout=stdout_text,
            stderr=stderr_text,
            exit_code=exit_code,
            success=exit_code == 0,
            host=self.host,
            command=command,
            duration_ms=duration_ms,
        )

        if result.success:
            logger.info(
                "SSH command succeeded",
                host=self.host,
                exit_code=exit_code,
                duration_ms=duration_ms,
            )
        else:
            logger.warning(
                "SSH command failed",
                host=self.host,
                exit_code=exit_code,
                stderr=stderr_text[:200],
            )

        return result

    async def execute_script(self, script: str, timeout: int = 300) -> SSHResult:
        """Execute a multi-line script via SSH."""
        # Escape single quotes and wrap in bash
        escaped = script.replace("'", "'\\''")
        command = f"bash -c '{escaped}'"
        return await self.execute(command, timeout=timeout)

    async def upload_file(
        self,
        local_path: str,
        remote_path: str,
    ) -> dict[str, Any]:
        """Upload a file via SFTP."""
        if not self._client:
            await self.connect()

        loop = asyncio.get_event_loop()

        sftp = await loop.run_in_executor(
            None,
            self._client.open_sftp,
        )

        try:
            await loop.run_in_executor(
                None,
                partial(sftp.put, local_path, remote_path),
            )
            logger.info("File uploaded", host=self.host, remote_path=remote_path)
            return {
                "action": "upload_file",
                "host": self.host,
                "remote_path": remote_path,
                "success": True,
            }
        finally:
            sftp.close()

    async def download_file(
        self,
        remote_path: str,
        local_path: str,
    ) -> dict[str, Any]:
        """Download a file via SFTP."""
        if not self._client:
            await self.connect()

        loop = asyncio.get_event_loop()

        sftp = await loop.run_in_executor(
            None,
            self._client.open_sftp,
        )

        try:
            await loop.run_in_executor(
                None,
                partial(sftp.get, remote_path, local_path),
            )
            logger.info("File downloaded", host=self.host, remote_path=remote_path)
            return {
                "action": "download_file",
                "host": self.host,
                "remote_path": remote_path,
                "local_path": local_path,
                "success": True,
            }
        finally:
            sftp.close()

    async def __aenter__(self) -> "SSHExecutor":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()


class SSHActionHandler:
    """Handles SSH-based remediation actions."""

    def __init__(self) -> None:
        self._host_configs: dict[str, SSHHost] = {}

    def register_host(self, name: str, config: SSHHost) -> None:
        """Register a host configuration."""
        self._host_configs[name] = config
        logger.info("Registered SSH host", name=name, host=config.host)

    async def handle_action(self, action: RemediationAction) -> dict[str, Any]:
        """Handle an SSH remediation action."""
        params = action.parameters

        host_name = params.get("host_name")
        host_config = self._host_configs.get(host_name) if host_name else None

        if not host_config:
            # Use direct connection params
            host_config = SSHHost(
                host=params.get("host", ""),
                username=params.get("username", ""),
                password=params.get("password"),
                key_filename=params.get("key_filename"),
                port=params.get("port", 22),
            )

        if not host_config.host or not host_config.username:
            raise ValueError("SSH host and username are required")

        command = params.get("command", "")
        if not command:
            raise ValueError("SSH command is required")

        async with SSHExecutor(
            host=host_config.host,
            username=host_config.username,
            password=host_config.password,
            key_filename=host_config.key_filename,
            port=host_config.port,
        ) as executor:
            result = await executor.execute(command, timeout=params.get("timeout", 30))

        return {
            "action": "ssh_command",
            "host": host_config.host,
            "command": command,
            "exit_code": result.exit_code,
            "success": result.success,
            "stdout": result.stdout[:1000] if result.stdout else "",
            "stderr": result.stderr[:500] if result.stderr else "",
            "duration_ms": result.duration_ms,
        }


# Pre-defined safe commands for common remediation tasks
SAFE_COMMANDS = {
    "restart_service": "sudo systemctl restart {service}",
    "check_disk": "df -h",
    "check_memory": "free -m",
    "check_processes": "ps aux --sort=-%mem | head -20",
    "clear_logs": "sudo find /var/log -name '*.log' -mtime +7 -delete",
    "rotate_logs": "sudo logrotate -f /etc/logrotate.conf",
    "check_service": "sudo systemctl status {service}",
    "tail_logs": "sudo tail -n 100 {log_file}",
}

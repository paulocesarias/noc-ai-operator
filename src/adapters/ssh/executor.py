"""SSH command executor for legacy systems."""

from dataclasses import dataclass

import paramiko
import structlog

logger = structlog.get_logger()


@dataclass
class SSHResult:
    """Result of an SSH command execution."""

    stdout: str
    stderr: str
    exit_code: int
    success: bool


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
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
        }

        if self.password:
            connect_kwargs["password"] = self.password
        if self.key_filename:
            connect_kwargs["key_filename"] = self.key_filename

        self._client.connect(**connect_kwargs)
        logger.info("SSH connected", host=self.host)

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

        logger.info("Executing SSH command", host=self.host, command=command)

        stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)

        exit_code = stdout.channel.recv_exit_status()
        stdout_text = stdout.read().decode("utf-8")
        stderr_text = stderr.read().decode("utf-8")

        return SSHResult(
            stdout=stdout_text,
            stderr=stderr_text,
            exit_code=exit_code,
            success=exit_code == 0,
        )

    async def __aenter__(self) -> "SSHExecutor":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()

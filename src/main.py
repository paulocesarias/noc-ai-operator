"""Main entry point for NOC AI Operator."""

import asyncio
import sys

import structlog
import uvicorn

from src.actions.kubernetes.executor import K8sExecutor
from src.adapters.ssh.executor import SSHActionHandler
from src.adapters.syslog.receiver import SyslogReceiver
from src.api.app import create_app
from src.core.config import settings
from src.core.event_processor import get_event_processor
from src.core.models import ActionType

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        (
            structlog.processors.JSONRenderer()
            if settings.log_format == "json"
            else structlog.dev.ConsoleRenderer()
        ),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


async def register_action_handlers() -> None:
    """Register all action handlers with the event processor."""
    processor = get_event_processor()

    # Kubernetes handlers
    k8s_executor = K8sExecutor()
    processor.register_handler(ActionType.K8S_RESTART_POD, k8s_executor.handle_action)
    processor.register_handler(ActionType.K8S_SCALE_DEPLOYMENT, k8s_executor.handle_action)
    processor.register_handler(ActionType.K8S_ROLLBACK, k8s_executor.handle_action)

    # SSH handler
    ssh_handler = SSHActionHandler()
    processor.register_handler(ActionType.SSH_COMMAND, ssh_handler.handle_action)

    logger.info("Action handlers registered")


async def start_services() -> None:
    """Start all background services."""
    # Start event processor
    processor = get_event_processor()
    await processor.start()

    # Register action handlers
    await register_action_handlers()

    # Start syslog receiver if enabled
    # Note: Requires root/elevated privileges for port 514
    # syslog_receiver = SyslogReceiver()
    # await syslog_receiver.start()

    logger.info("All services started")


async def shutdown_services() -> None:
    """Shutdown all services gracefully."""
    processor = get_event_processor()
    await processor.stop()
    logger.info("All services stopped")


def main() -> None:
    """Main entry point."""
    logger.info(
        "Starting NOC AI Operator",
        version="0.1.0",
        api_port=settings.api_port,
        log_level=settings.log_level,
    )

    app = create_app()

    config = uvicorn.Config(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )
    server = uvicorn.Server(config)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(start_services())
        loop.run_until_complete(server.serve())
    except KeyboardInterrupt:
        logger.info("Shutdown requested...")
    except Exception as e:
        logger.error("Fatal error", error=str(e))
        sys.exit(1)
    finally:
        loop.run_until_complete(shutdown_services())
        loop.close()
        logger.info("NOC AI Operator stopped")


if __name__ == "__main__":
    main()

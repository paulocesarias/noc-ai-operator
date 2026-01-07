"""Main entry point for NOC AI Operator."""

import asyncio

import structlog
import uvicorn

from src.api.app import create_app
from src.core.config import settings
from src.core.event_processor import EventProcessor

logger = structlog.get_logger()


async def start_services() -> None:
    """Start all background services."""
    processor = EventProcessor()
    await processor.start()


def main() -> None:
    """Main entry point."""
    logger.info("Starting NOC AI Operator", version="0.1.0")

    app = create_app()

    config = uvicorn.Config(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.create_task(start_services())
        loop.run_until_complete(server.serve())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        loop.close()


if __name__ == "__main__":
    main()

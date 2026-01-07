"""FastAPI application."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import events, health
from src.api.routes.approvals import router as approvals_router
from src.api.routes.runbooks import router as runbooks_router
from src.dashboard.router import router as dashboard_router
from src.workflows.approval import get_approval_service
from src.workflows.slack import get_slack_notifier


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    from src.core.event_processor import get_event_processor

    # Startup
    approval_service = get_approval_service()
    slack_notifier = get_slack_notifier()
    event_processor = get_event_processor()

    # Connect Slack notifier to approval service
    if slack_notifier.is_available:
        approval_service.set_notifier(slack_notifier)

    # Connect event processor to approval service
    event_processor.set_approval_service(approval_service)

    # Start services
    await approval_service.start()
    await event_processor.start()

    yield

    # Shutdown
    await event_processor.stop()
    await approval_service.stop()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="NOC AI Operator",
        description="AI-driven infrastructure monitoring and remediation",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(health.router, tags=["Health"])
    app.include_router(events.router, prefix="/api/v1", tags=["Events"])
    app.include_router(runbooks_router, prefix="/api/v1/knowledge", tags=["Knowledge Base"])
    app.include_router(approvals_router, prefix="/api/v1/approvals", tags=["Approvals"])
    app.include_router(dashboard_router, prefix="/dashboard", tags=["Dashboard"])

    return app

"""FastAPI application."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api.routes import events, health
from src.dashboard.router import router as dashboard_router


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="NOC AI Operator",
        description="AI-driven infrastructure monitoring and remediation",
        version="0.1.0",
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
    app.include_router(dashboard_router, prefix="/dashboard", tags=["Dashboard"])

    return app

"""FastAPI application for Simulatte Web UI."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from simulatte.web.simulation_bridge import SimulationProtocol

# Module-level state for the simulation class
_simulation_class: type[SimulationProtocol] | None = None
_db_path: Path | None = None


def set_simulation_class(cls: type[SimulationProtocol]) -> None:
    """Set the simulation class to use for this application instance."""
    global _simulation_class
    _simulation_class = cls


def get_simulation_class() -> type[SimulationProtocol]:
    """Get the current simulation class."""
    if _simulation_class is None:
        raise RuntimeError("Simulation class not set. Call set_simulation_class() first.")
    return _simulation_class


def get_db_path() -> Path:
    """Get the current database path."""
    if _db_path is None:
        raise RuntimeError("Database path not set.")
    return _db_path


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler for startup/shutdown."""
    # Startup
    from simulatte.web.db.models import init_db

    if _db_path is not None:
        init_db(_db_path)

    yield

    # Shutdown
    # Clean up resources if needed


def create_app(db_path: Path) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Configured FastAPI application instance.
    """
    global _db_path
    _db_path = db_path

    app = FastAPI(
        title="Simulatte Web UI",
        description="Visualization and analytics for discrete-event simulations",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS middleware for development
    app.add_middleware(
        CORSMiddleware,  # type: ignore[arg-type]
        allow_origins=["*"],  # In production, restrict this
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include API routes
    from simulatte.web.api.routes import router as api_router

    app.include_router(api_router, prefix="/api")

    # Serve static frontend files
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/", response_class=HTMLResponse)
        async def serve_index() -> HTMLResponse:
            """Serve the main frontend page."""
            index_path = static_dir / "index.html"
            if index_path.exists():
                return HTMLResponse(content=index_path.read_text())
            return HTMLResponse(content=_get_placeholder_html())

        @app.get("/{path:path}", response_class=HTMLResponse)
        async def serve_spa(path: str) -> HTMLResponse:
            """Serve SPA for all other routes."""
            index_path = static_dir / "index.html"
            if index_path.exists():
                return HTMLResponse(content=index_path.read_text())
            return HTMLResponse(content=_get_placeholder_html())
    else:
        # No static files yet, serve placeholder
        @app.get("/", response_class=HTMLResponse)
        async def serve_placeholder() -> HTMLResponse:
            """Serve placeholder page while frontend is not built."""
            return HTMLResponse(content=_get_placeholder_html())

    return app


def _get_placeholder_html() -> str:
    """Return placeholder HTML when frontend is not built."""
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Simulatte Web UI</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #e8e8e8;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 2rem;
        }
        .container {
            max-width: 600px;
            text-align: center;
        }
        h1 {
            font-size: 2.5rem;
            margin-bottom: 0.5rem;
            background: linear-gradient(90deg, #00d9ff, #00ff88);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        .subtitle {
            color: #888;
            margin-bottom: 2rem;
        }
        .card {
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 12px;
            padding: 2rem;
            margin-bottom: 1.5rem;
        }
        .status {
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            background: rgba(0,217,255,0.1);
            padding: 0.5rem 1rem;
            border-radius: 20px;
            font-size: 0.9rem;
            color: #00d9ff;
        }
        .dot {
            width: 8px;
            height: 8px;
            background: #00d9ff;
            border-radius: 50%;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .api-links {
            text-align: left;
            margin-top: 1.5rem;
        }
        .api-links h3 {
            font-size: 0.9rem;
            color: #888;
            margin-bottom: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .api-links a {
            display: block;
            color: #00ff88;
            text-decoration: none;
            padding: 0.5rem 0;
            border-bottom: 1px solid rgba(255,255,255,0.05);
            font-family: monospace;
            font-size: 0.95rem;
        }
        .api-links a:hover {
            color: #00d9ff;
        }
        .footer {
            margin-top: 2rem;
            color: #555;
            font-size: 0.85rem;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Simulatte</h1>
        <p class="subtitle">Discrete-Event Simulation Visualization</p>

        <div class="card">
            <div class="status">
                <span class="dot"></span>
                Backend Running
            </div>

            <div class="api-links">
                <h3>API Endpoints</h3>
                <a href="/api/simulation/info">/api/simulation/info</a>
                <a href="/api/simulation/status">/api/simulation/status</a>
                <a href="/api/snapshots">/api/snapshots</a>
                <a href="/docs">/docs (OpenAPI)</a>
            </div>
        </div>

        <p class="footer">
            Frontend not built yet. Run the build to see the visualization.
        </p>
    </div>
</body>
</html>
"""

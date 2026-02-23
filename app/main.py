"""
FastAPI application factory with lifespan management.
"""

import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.db.session import init_db, engine
from app.api.routes import router as api_router
from app.schemas import HealthResponse
from app.logging_config import logger

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("Starting Places Ingestion Service")
    await init_db()
    logger.info("Database tables ensured")
    yield
    await engine.dispose()
    logger.info("Service shut down")


app = FastAPI(
    title="Google Places Data Ingestion & Enrichment Service",
    description=(
        "Production-ready service for geo ingestion, website enrichment, "
        "business classification, competitor heatmaps, and location scoring."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routes
app.include_router(api_router, prefix="/api/v1", tags=["Places"])

# Serve static frontend
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Health check endpoint."""
    try:
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        db_status = "disconnected"

    return HealthResponse(
        status="healthy",
        version="1.0.0",
        database=db_status,
    )


@app.get("/", tags=["System"])
async def root():
    """Serve the frontend dashboard."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {
        "service": "Google Places Data Ingestion & Enrichment",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }

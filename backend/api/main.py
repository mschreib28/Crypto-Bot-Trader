"""FastAPI application initialization."""

import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import CORS_ORIGINS, LOG_LEVEL
from backend.api.routes import health, panic, strategies

# Configure structured logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="Omni-Bot API",
    version="0.1.0",
    description="API for the Omni-Bot Trading Platform",
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(health.router, prefix="/api/v1", tags=["System"])
app.include_router(panic.router, prefix="/api/v1", tags=["System"])
app.include_router(strategies.router, prefix="/api/v1", tags=["Strategies"])

logger.info("FastAPI application initialized")


@app.on_event("startup")
async def startup_event():
    """Log startup event."""
    logger.info("API server starting up")


@app.on_event("shutdown")
async def shutdown_event():
    """Log shutdown event."""
    logger.info("API server shutting down")

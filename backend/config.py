"""Configuration management for the backend application."""

import os
from typing import Optional

# API Configuration
API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
API_PORT: int = int(os.getenv("API_PORT", "8000"))

# CORS Configuration
CORS_ORIGINS: list[str] = os.getenv(
    "CORS_ORIGINS", "http://localhost:3000,http://localhost:8080"
).split(",")

# Logging Configuration
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

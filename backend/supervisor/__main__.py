"""Entry point: python -m backend.supervisor"""

import logging
import os

from backend.config import LOG_LEVEL
from backend.supervisor.service import SupervisorService

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s  %(name)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

if __name__ == "__main__":
    SupervisorService().run_forever()

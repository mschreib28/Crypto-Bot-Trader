"""Background audit writer — persists log_activity() events to PostgreSQL."""

import logging
import queue
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# How many items to batch before flushing, or flush every FLUSH_INTERVAL_SECONDS
FLUSH_BATCH_SIZE = 50
FLUSH_INTERVAL_SECONDS = 2.0


def _extract_symbol(details: Optional[Dict[str, Any]]) -> Optional[str]:
    if not details:
        return None
    return details.get("symbol") or details.get("pair") or details.get("asset")


def _extract_strategy(details: Optional[Dict[str, Any]]) -> Optional[str]:
    if not details:
        return None
    return (
        details.get("strategy_name")
        or details.get("strategy")
        or details.get("strategy_id")
    )


class AuditWriter:
    """
    Non-blocking background writer that persists activity events to PostgreSQL.

    Callers call enqueue() which returns immediately. A daemon thread drains
    the queue in batches every FLUSH_INTERVAL_SECONDS or FLUSH_BATCH_SIZE items.
    DB errors are logged as warnings and dropped (fire-and-forget semantics).
    """

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker,
            name="audit-writer",
            daemon=True,
        )
        self._thread.start()
        logger.info("AuditWriter background thread started")

    def stop(self) -> None:
        """Signal shutdown and flush remaining items."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        logger.info("AuditWriter stopped")

    def enqueue(
        self,
        activity_type: str,
        message: str,
        details: Optional[Dict[str, Any]],
        timestamp: Optional[str] = None,
    ) -> None:
        """Non-blocking enqueue. Returns immediately."""
        try:
            ts = (
                datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if timestamp
                else datetime.now(timezone.utc)
            )
            self._queue.put_nowait(
                {
                    "id": uuid.uuid4(),
                    "timestamp": ts,
                    "type": activity_type,
                    "message": message,
                    "details": details,
                    "symbol": _extract_symbol(details),
                    "strategy": _extract_strategy(details),
                }
            )
        except queue.Full:
            logger.warning("AuditWriter queue full, dropping event: %s", activity_type)
        except Exception as exc:
            logger.warning("AuditWriter enqueue error: %s", exc)

    # ------------------------------------------------------------------
    # Internal worker
    # ------------------------------------------------------------------

    def _worker(self) -> None:
        while not self._stop_event.is_set():
            self._flush(block=True, timeout=FLUSH_INTERVAL_SECONDS)
        # Drain remaining items on shutdown
        self._flush(block=False)

    def _flush(self, block: bool, timeout: float = FLUSH_INTERVAL_SECONDS) -> None:
        batch = []
        try:
            # Collect up to FLUSH_BATCH_SIZE items
            while len(batch) < FLUSH_BATCH_SIZE:
                item = self._queue.get(block=block, timeout=timeout)
                batch.append(item)
                block = False  # subsequent gets are non-blocking
        except queue.Empty:
            pass

        if not batch:
            return

        try:
            from backend.db import get_session
            from backend.db.models import ActivityLog

            session = get_session()
            try:
                session.bulk_insert_mappings(ActivityLog, batch)
                session.commit()
            except Exception as exc:
                session.rollback()
                logger.warning("AuditWriter DB flush error (batch dropped): %s", exc)
            finally:
                session.close()
        except Exception as exc:
            logger.warning("AuditWriter failed to open DB session: %s", exc)


# Module-level singleton
_writer: Optional[AuditWriter] = None


def get_audit_writer() -> AuditWriter:
    global _writer
    if _writer is None:
        _writer = AuditWriter()
    return _writer


def init_audit_writer() -> None:
    get_audit_writer().start()


def shutdown_audit_writer() -> None:
    if _writer is not None:
        _writer.stop()

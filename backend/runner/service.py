"""Strategy Runner service implementation.

Continuously consumes market data from Redis streams and feeds it to strategies.
Supports both single-strategy (StrategyRunner, legacy) and multi-strategy
(MultiStrategyRunner) modes.  The multi-strategy runner loads all active strategy
rows from the database and runs one StrategyWorker coroutine per row concurrently
via asyncio.gather().
"""

import asyncio
import json
import logging
import signal
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.api.routes.trading import get_bot_mode
from backend.supervisor.store import canonical_name as supervisor_canonical_name
from backend.supervisor.store import get_effective_mode
from backend.config import LOG_LEVEL
from backend.execution.executor import execute_trade
from backend.execution.persistence import persist_fill_with_intent_id
from backend.positions.tracker import get_position_tracker
from backend.redis import get_redis_client
from backend.redis.keys import (
    APLUS_SCORES_KEY,
    FORCED_EXIT_COOLDOWN_KEY,
    SCREENER_RESULTS_KEY,
)
from backend.redis.streams import consume_stream
from backend.risk.evaluator import evaluate_intent, TradeIntent as BackendTradeIntent
from backend.runner.config import (
    RUNNER_BLOCK_MS,
    RUNNER_CONSUMER_GROUP,
    RUNNER_CONSUMER_NAME,
    RUNNER_HEALTH_FILE,
    RUNNER_INTERVAL,
    RUNNER_STRATEGY_ID,
    RUNNER_SYMBOL,
    get_stream_key,
)
from research.strategies.meanrev.config import MeanReversionConfig
from research.strategies.meanrev.strategy import MeanReversionStrategy
from research.strategies.types import MarketDataEvent

# Error recovery settings
MAX_CONSECUTIVE_ERRORS = 10
ERROR_COOLDOWN_SECONDS = 5 * 60  # 5 minutes


@contextmanager
def get_db_session():
    """Context manager that yields a DB session and closes it on exit.

    Wraps ``backend.db.get_session()`` so callers never forget ``close()``.
    Tests can patch ``backend.runner.service.get_db_session`` to inject mocks.
    """
    from backend.db import get_session
    session = get_session()
    try:
        yield session
    finally:
        session.close()

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

# Screener grade gate: avoid hammering Redis on every bar (many workers / symbols).
_SCREENER_GATE_TTL_SEC = 10.0
_screener_results_cache: Optional[List[Dict[str, Any]]] = None
_screener_results_cache_expiry: float = 0.0
_aplus_grade_cache: Dict[str, Tuple[Optional[str], float]] = {}


def _prune_aplus_grade_cache(now: float) -> None:
    """Drop expired entries to cap memory when many symbols are seen over time."""
    global _aplus_grade_cache
    if len(_aplus_grade_cache) < 800:
        return
    _aplus_grade_cache = {
        k: v for k, v in _aplus_grade_cache.items() if v[1] > now
    }


def _get_screener_result_entry(symbol: str) -> Optional[Dict[str, Any]]:
    """Return the dict for ``symbol`` from cached ``screener:results``, or None."""
    global _screener_results_cache, _screener_results_cache_expiry
    now = time.monotonic()
    if _screener_results_cache is None or now > _screener_results_cache_expiry:
        client = get_redis_client()
        raw = client.get(SCREENER_RESULTS_KEY)
        parsed: List[Dict[str, Any]] = []
        if raw is not None:
            try:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                loaded = json.loads(raw)
                if isinstance(loaded, list):
                    parsed = [x for x in loaded if isinstance(x, dict)]
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                parsed = []
        _screener_results_cache = parsed
        _screener_results_cache_expiry = now + _SCREENER_GATE_TTL_SEC

    for row in _screener_results_cache:
        if row.get("symbol") == symbol:
            return row
    return None


_PASSING_GRADES = frozenset({"A+", "A", "B", "C"})


def _grade_from_aplus_hash(symbol: str) -> Optional[str]:
    """Authoritative pipeline grade from ``screener:aplus_scores``."""
    now = time.monotonic()
    cached = _aplus_grade_cache.get(symbol)
    if cached is not None and now < cached[1]:
        return cached[0] if cached[0] else None

    client = get_redis_client()
    raw = client.hget(APLUS_SCORES_KEY, symbol)
    grade_val: Optional[str] = None
    if raw:
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                g = data.get("grade")
                if g is not None and str(g).strip():
                    grade_val = str(g).strip()
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            grade_val = None

    _aplus_grade_cache[symbol] = (grade_val, now + _SCREENER_GATE_TTL_SEC)
    _prune_aplus_grade_cache(now)
    return grade_val


def _resolve_screener_grade(symbol: str, entry: Optional[Dict[str, Any]]) -> Optional[str]:
    """Pipeline letter grade: ``screener:aplus_scores`` first, then result indicators."""
    grade_val = _grade_from_aplus_hash(symbol)
    if grade_val:
        return grade_val

    if entry is None:
        return None

    indicators = entry.get("indicators") or {}
    if isinstance(indicators, dict):
        g = indicators.get("grade")
        if g is not None and str(g).strip():
            return str(g).strip()
    return None


def _grade_gate_allows(grade: Optional[str]) -> Tuple[bool, str]:
    """Return (allowed, fail_reason) for a resolved grade string."""
    norm = (grade or "").strip().upper()
    if not norm:
        return False, "missing_grade"
    if norm in ("D", "F"):
        return False, f"grade_{norm}"
    if norm not in _PASSING_GRADES:
        return False, "unknown_grade"
    return True, ""


def _screener_allows_strategy_evaluation(symbol: str) -> bool:
    """True if ``generate_signals`` may run for this symbol (flat: grade gate).

    Open positions always return True so exits still evaluate. Otherwise the
    symbol must have grade A+, A, B, or C from ``screener:aplus_scores`` or
  indicators. D, F, missing, unreadable, or unknown grades block evaluation.
    """
    try:
        if get_position_tracker().has_position(symbol):
            return True

        grade = _resolve_screener_grade(symbol, _get_screener_result_entry(symbol))
        allowed, reason = _grade_gate_allows(grade)
        if not allowed:
            logger.info(
                "grade_gate_fail_closed symbol=%s reason=%s grade=%r",
                symbol,
                reason,
                grade,
            )
            return False

        return True
    except Exception as exc:
        logger.warning(
            "grade_gate_fail_closed symbol=%s reason=exception error=%s",
            symbol,
            exc,
        )
        return False


class StrategyRunner:
    """
    Runner that consumes market data and feeds it to strategies.
    
    Implements the workflow from MSSD § 7:
    1. Consume OHLCV bar from Redis stream
    2. Feed bar to strategy.generate_signals()
    3. If TradeIntent generated, send to Risk Manager
    4. Log all bars processed and signals generated
    """
    
    def __init__(
        self,
        strategy_id: str = RUNNER_STRATEGY_ID,
        symbol: str = RUNNER_SYMBOL,
        interval: str = RUNNER_INTERVAL,
        consumer_group: str = RUNNER_CONSUMER_GROUP,
        consumer_name: str = RUNNER_CONSUMER_NAME,
        block_ms: int = RUNNER_BLOCK_MS,
    ):
        """
        Initialize the StrategyRunner.
        
        Args:
            strategy_id: Strategy identifier (e.g., "mean_reversion")
            symbol: Trading pair (e.g., "ETH/USD")
            interval: Time interval (e.g., "4h")
            consumer_group: Redis consumer group name
            consumer_name: Redis consumer name
            block_ms: Milliseconds to block waiting for new messages
        """
        self.strategy_id = strategy_id
        self.symbol = symbol
        self.interval = interval
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name
        self.block_ms = block_ms
        
        self.stream_key = get_stream_key(symbol, interval)
        self._running = False
        self._strategy: Optional[MeanReversionStrategy] = None
        self._last_price: Optional[float] = None  # Track latest price for execution
        
        logger.info(
            f"StrategyRunner initialized: strategy_id={strategy_id}, "
            f"symbol={symbol}, interval={interval}, stream_key={self.stream_key}"
        )
    
    def _init_strategy(self) -> MeanReversionStrategy:
        """
        Initialize the MeanReversionStrategy with configuration.
        
        Returns:
            Configured MeanReversionStrategy instance
        """
        config = MeanReversionConfig(
            strategy_id=self.strategy_id,
            symbol=self.symbol,
        )
        
        strategy = MeanReversionStrategy(config)
        logger.info(
            f"Initialized MeanReversionStrategy: "
            f"lookback={config.lookback_period}, rsi_period={config.rsi_period}, "
            f"risk_pct={config.notional_risk_pct}"
        )
        
        return strategy
    
    def _parse_bar(self, msg_data: dict) -> Optional[MarketDataEvent]:
        """
        Parse a Redis stream message into a MarketDataEvent.
        
        Args:
            msg_data: Dictionary from Redis stream message
            
        Returns:
            MarketDataEvent or None if parsing fails
        """
        try:
            return MarketDataEvent(
                symbol=msg_data.get("symbol", self.symbol),
                interval=msg_data.get("interval", self.interval),
                open=float(msg_data["open"]),
                high=float(msg_data["high"]),
                low=float(msg_data["low"]),
                close=float(msg_data["close"]),
                volume=float(msg_data["volume"]),
                timestamp=msg_data["timestamp"],
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Failed to parse bar from message: {e}. Data: {msg_data}")
            return None
    
    def _convert_intent(self, intent) -> BackendTradeIntent:
        """
        Convert research TradeIntent to backend TradeIntent.
        
        Args:
            intent: TradeIntent from research.strategies.types
            
        Returns:
            TradeIntent compatible with backend.risk.evaluator
        """
        return BackendTradeIntent(
            strategy_id=intent.strategy_id,
            symbol=intent.symbol,
            side=intent.side,
            intent_type=intent.intent_type,
            notional_risk_pct=intent.notional_risk_pct,
            metadata=intent.metadata,
        )
    
    async def _consume_next_bar(self) -> Optional[MarketDataEvent]:
        """
        Consume the next bar from the Redis stream.
        
        Returns:
            MarketDataEvent or None if no message available
        """
        try:
            # Run blocking Redis call in thread pool to avoid blocking event loop
            # This allows the screener scan loop to run concurrently
            messages = await asyncio.to_thread(
                consume_stream,
                stream_key=self.stream_key,
                consumer_group=self.consumer_group,
                consumer_name=self.consumer_name,
                count=1,
                block=self.block_ms,
            )
            
            if not messages:
                return None
            
            msg = messages[0]
            return self._parse_bar(msg["data"])
            
        except Exception as e:
            logger.error(f"Error consuming from stream {self.stream_key}: {e}")
            return None
    
    async def _process_bar(self, bar: MarketDataEvent) -> None:
        """
        Process a single market data bar.
        
        Args:
            bar: MarketDataEvent to process
        """
        logger.info(f"Processing bar: {bar.timestamp}")
        
        # Track latest price for execution
        self._last_price = bar.close

        if not _screener_allows_strategy_evaluation(bar.symbol):
            return
        
        # Generate signals from strategy
        intent = self._strategy.generate_signals(bar)
        
        if intent is not None:
            logger.info(f"Signal generated: {intent.side}")
            
            # Convert to backend TradeIntent and evaluate with Risk Manager
            backend_intent = self._convert_intent(intent)
            decision = evaluate_intent(backend_intent)
            
            if decision.approved:
                logger.info(
                    f"Signal approved by Risk Manager: intent_id={decision.intent_id}, "
                    f"portfolio_risk={decision.evaluated_portfolio_risk}%"
                )

                # Guard: skip BUY if position already exists (prevents duplicate fills)
                if intent.side == "buy":
                    tracker = get_position_tracker()
                    if tracker.has_position(intent.symbol):
                        logger.info(
                            f"Skipping BUY for {intent.symbol}: position already open"
                        )
                        return

                    # Guard: skip BUY if post-exit cooldown is active (prevents churn)
                    _redis = get_redis_client()
                    _ck = FORCED_EXIT_COOLDOWN_KEY.format(
                        symbol=intent.symbol, strategy_id=intent.strategy_id
                    )
                    if _redis.exists(_ck):
                        _ttl = _redis.ttl(_ck)
                        logger.info(
                            f"Skipping BUY for {intent.symbol}: post-exit cooldown active "
                            f"({_ttl}s remaining)"
                        )
                        return

                    # BUG5: Skip BUY if symbol is explicitly blocked or loss circuit breaker active
                    import os as _os
                    from backend.redis.keys import SYMBOL_BLOCKED_KEY as _SBK
                    _blocked_env = _os.getenv("BLOCKED_SYMBOLS", "")
                    if intent.symbol in [s.strip() for s in _blocked_env.split(",") if s.strip()]:
                        logger.info(f"Skipping BUY for {intent.symbol}: in BLOCKED_SYMBOLS env var")
                        return
                    _sym_blocked_key = _SBK.format(symbol=intent.symbol)
                    if _redis.exists(_sym_blocked_key):
                        logger.info(
                            f"Skipping BUY for {intent.symbol}: symbol blocked by loss circuit breaker "
                            f"({_redis.ttl(_sym_blocked_key)}s remaining)"
                        )
                        return

                # Effective execution: bot SHADOW → SIM paper; LIVE + strategy SIM → paper; else Kraken if LIVE+LIVE
                _canon = supervisor_canonical_name(self.strategy_id)
                _eff_mode, _eff_factor = get_effective_mode(_canon)
                backend_intent.metadata = dict(backend_intent.metadata or {})
                if get_bot_mode() == "LIVE" and _eff_mode == "LIVE":
                    backend_intent.metadata["supervisor_size_factor"] = float(_eff_factor)
                else:
                    backend_intent.metadata["supervisor_size_factor"] = 1.0
                if get_bot_mode() == "LIVE" and _eff_mode == "SIM":
                    backend_intent.metadata["strategy_canonical"] = _canon
                _live_exec = get_bot_mode() == "LIVE" and _eff_mode == "LIVE"
                try:
                    fill = await execute_trade(
                        backend_intent,
                        self._last_price,
                        live=_live_exec,
                    )
                    if fill:
                        logger.info(
                            f"Fill executed: {fill.symbol} {fill.side} "
                            f"qty={fill.quantity} @ {fill.executed_price}"
                        )
                    else:
                        logger.warning(
                            f"execute_trade returned None for intent {decision.intent_id}"
                        )
                except Exception as e:
                    logger.error(
                        f"Execution failed for intent {decision.intent_id}: {e}"
                    )
            else:
                logger.warning(
                    f"Signal rejected by Risk Manager: reason={decision.rejection_reason}"
                )
    
    async def run(self) -> None:
        """
        Run the strategy runner main loop.
        
        Continuously consumes bars from Redis stream and processes them.
        """
        logger.info(f"Starting StrategyRunner for {self.strategy_id}")
        
        # Initialize strategy
        self._strategy = self._init_strategy()
        self._running = True
        
        # Create health check file
        health_file = Path(RUNNER_HEALTH_FILE)
        try:
            health_file.parent.mkdir(parents=True, exist_ok=True)
            health_file.touch()
            logger.info(f"Health check file created: {health_file}")
        except Exception as e:
            logger.warning(f"Could not create health check file: {e}")
        
        bars_processed = 0
        signals_generated = 0
        consecutive_errors = 0
        
        try:
            while self._running:
                try:
                    # Update health check file periodically
                    if bars_processed % 10 == 0:
                        try:
                            health_file.touch()
                        except Exception:
                            pass
                    
                    # Consume next bar
                    bar = await self._consume_next_bar()
                    
                    if bar is None:
                        # No message available, continue waiting
                        continue
                    
                    # Process the bar
                    await self._process_bar(bar)
                    bars_processed += 1
                    
                    # Reset consecutive errors on success
                    consecutive_errors = 0
                    
                except Exception as e:
                    consecutive_errors += 1
                    logger.error(f"Error in runner loop (consecutive: {consecutive_errors}): {e}")
                    
                    # Cooldown after too many consecutive errors
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        logger.warning(
                            f"{consecutive_errors} consecutive errors, "
                            f"pausing for {ERROR_COOLDOWN_SECONDS // 60} minutes..."
                        )
                        await asyncio.sleep(ERROR_COOLDOWN_SECONDS)
                        consecutive_errors = 0
                    else:
                        await asyncio.sleep(1)  # Brief pause before retry
                
        except Exception as e:
            logger.error(f"Error in runner main loop: {e}", exc_info=True)
            raise
        finally:
            # Cleanup health file
            try:
                if health_file.exists():
                    health_file.unlink()
                    logger.info("Health check file removed")
            except Exception as e:
                logger.warning(f"Could not remove health check file: {e}")
            
            logger.info(
                f"StrategyRunner stopped. Bars processed: {bars_processed}"
            )
    
    async def stop(self) -> None:
        """Stop the strategy runner gracefully."""
        logger.info("Stopping StrategyRunner...")
        self._running = False


class StrategyWorker:
    """Consumes bars for a single (strategy, symbol, interval) triple.

    Created by MultiStrategyRunner for each active strategy row.  Mirrors the
    structure of StrategyRunner but is parameterised at construction time and
    uses a per-strategy consumer group so Redis stream offsets are isolated.
    """

    def __init__(
        self,
        strategy_name: str,
        strategy_id: str,
        config: Any,
        strategy: Any,
        block_ms: int = RUNNER_BLOCK_MS,
    ) -> None:
        self.strategy_name = strategy_name
        self.strategy_id = strategy_id
        self.config = config
        self._strategy = strategy

        self.symbol: str = config.symbol
        self.interval: str = config.interval
        # Consumer group is unique per strategy to keep stream offsets isolated.
        self.consumer_group: str = f"runner:{strategy_name}"
        self.consumer_name: str = f"worker_{strategy_name}_1"
        self.stream_key: str = get_stream_key(self.symbol, self.interval)
        self.block_ms: int = block_ms
        self._running: bool = False
        self._last_price: Optional[float] = None

        logger.info(
            f"[StrategyWorker] Ready: name={strategy_name}, "
            f"symbol={self.symbol}, interval={self.interval}, "
            f"stream={self.stream_key}, group={self.consumer_group}"
        )

    # ------------------------------------------------------------------
    # Internal helpers (mirrors StrategyRunner)
    # ------------------------------------------------------------------

    def _parse_bar(self, msg_data: dict) -> Optional[MarketDataEvent]:
        try:
            return MarketDataEvent(
                symbol=msg_data.get("symbol", self.symbol),
                interval=msg_data.get("interval", self.interval),
                open=float(msg_data["open"]),
                high=float(msg_data["high"]),
                low=float(msg_data["low"]),
                close=float(msg_data["close"]),
                volume=float(msg_data["volume"]),
                timestamp=msg_data["timestamp"],
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.error(
                f"[StrategyWorker:{self.strategy_name}] Failed to parse bar: {exc}"
            )
            return None

    def _convert_intent(self, intent) -> BackendTradeIntent:
        return BackendTradeIntent(
            strategy_id=intent.strategy_id,
            symbol=intent.symbol,
            side=intent.side,
            intent_type=intent.intent_type,
            notional_risk_pct=intent.notional_risk_pct,
            metadata=intent.metadata,
        )

    async def _consume_next_bar(self) -> Optional[MarketDataEvent]:
        try:
            messages = await asyncio.to_thread(
                consume_stream,
                stream_key=self.stream_key,
                consumer_group=self.consumer_group,
                consumer_name=self.consumer_name,
                count=1,
                block=self.block_ms,
            )
            if not messages:
                return None
            return self._parse_bar(messages[0]["data"])
        except Exception as exc:
            logger.error(
                f"[StrategyWorker:{self.strategy_name}] "
                f"Error consuming from {self.stream_key}: {exc}"
            )
            return None

    async def _process_bar(self, bar: MarketDataEvent) -> None:
        self._last_price = bar.close

        if not _screener_allows_strategy_evaluation(bar.symbol):
            return

        from backend.supervisor.store import is_drawdown_suspended

        if is_drawdown_suspended(supervisor_canonical_name(self.strategy_name)):
            return

        intent = self._strategy.generate_signals(bar)

        if intent is None:
            return

        logger.info(
            f"[StrategyWorker:{self.strategy_name}] Signal: {intent.side} {self.symbol}"
        )
        backend_intent = self._convert_intent(intent)
        decision = evaluate_intent(backend_intent)

        if not decision.approved:
            logger.warning(
                f"[StrategyWorker:{self.strategy_name}] "
                f"Rejected: {decision.rejection_reason}"
            )
            return

        logger.info(
            f"[StrategyWorker:{self.strategy_name}] Approved: "
            f"intent_id={decision.intent_id}, risk={decision.evaluated_portfolio_risk}%"
        )

        _canon = supervisor_canonical_name(self.strategy_name)
        _eff_mode, _eff_factor = get_effective_mode(_canon)
        backend_intent.metadata = dict(backend_intent.metadata or {})
        if get_bot_mode() == "LIVE" and _eff_mode == "LIVE":
            backend_intent.metadata["supervisor_size_factor"] = float(_eff_factor)
            logger.info(
                f"[StrategyWorker:{self.strategy_name}] Effective LIVE — "
                f"supervisor_size_factor={_eff_factor}"
            )
        else:
            backend_intent.metadata["supervisor_size_factor"] = 1.0
        if get_bot_mode() == "LIVE" and _eff_mode == "SIM":
            backend_intent.metadata["strategy_canonical"] = _canon
        _live_exec = get_bot_mode() == "LIVE" and _eff_mode == "LIVE"

        if intent.side == "buy":
            tracker = get_position_tracker()
            if tracker.has_position(intent.symbol):
                logger.info(
                    f"[StrategyWorker:{self.strategy_name}] "
                    f"Skipping BUY — position already open for {intent.symbol}"
                )
                return

            # Guard: skip BUY if post-exit cooldown is active (prevents churn)
            _redis = get_redis_client()
            _ck = FORCED_EXIT_COOLDOWN_KEY.format(
                symbol=intent.symbol, strategy_id=intent.strategy_id
            )
            if _redis.exists(_ck):
                _ttl = _redis.ttl(_ck)
                logger.info(
                    f"[StrategyWorker:{self.strategy_name}] "
                    f"Skipping BUY for {intent.symbol}: post-exit cooldown active ({_ttl}s remaining)"
                )
                return

            # BUG5: Skip BUY if symbol is explicitly blocked or loss circuit breaker active
            import os as _os
            from backend.redis.keys import SYMBOL_BLOCKED_KEY as _SBK
            _blocked_env = _os.getenv("BLOCKED_SYMBOLS", "")
            if intent.symbol in [s.strip() for s in _blocked_env.split(",") if s.strip()]:
                logger.info(
                    f"[StrategyWorker:{self.strategy_name}] "
                    f"Skipping BUY for {intent.symbol}: in BLOCKED_SYMBOLS env var"
                )
                return
            _sym_blocked_key = _SBK.format(symbol=intent.symbol)
            if _redis.exists(_sym_blocked_key):
                logger.info(
                    f"[StrategyWorker:{self.strategy_name}] "
                    f"Skipping BUY for {intent.symbol}: symbol blocked by loss circuit breaker "
                    f"({_redis.ttl(_sym_blocked_key)}s remaining)"
                )
                return

        try:
            fill = await execute_trade(
                backend_intent,
                self._last_price,
                live=_live_exec,
            )
            if fill:
                logger.info(
                    f"[StrategyWorker:{self.strategy_name}] Fill: "
                    f"{fill.symbol} {fill.side} qty={fill.quantity} @ {fill.executed_price}"
                )
            else:
                logger.warning(
                    f"[StrategyWorker:{self.strategy_name}] "
                    f"execute_trade returned None for intent {decision.intent_id}"
                )
        except Exception as exc:
            logger.error(
                f"[StrategyWorker:{self.strategy_name}] "
                f"Execution failed for intent {decision.intent_id}: {exc}"
            )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main loop: consume bars indefinitely until self._running is False."""
        logger.info(
            f"[StrategyWorker:{self.strategy_name}] Starting loop "
            f"({self.symbol}/{self.interval})"
        )
        self._running = True
        bars_processed = 0
        consecutive_errors = 0

        while self._running:
            try:
                bar = await self._consume_next_bar()
                if bar is None:
                    continue
                await self._process_bar(bar)
                bars_processed += 1
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                logger.error(
                    f"[StrategyWorker:{self.strategy_name}] "
                    f"Error (consecutive={consecutive_errors}): {exc}"
                )
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.warning(
                        f"[StrategyWorker:{self.strategy_name}] "
                        f"Too many errors, cooling down for "
                        f"{ERROR_COOLDOWN_SECONDS // 60} minutes"
                    )
                    await asyncio.sleep(ERROR_COOLDOWN_SECONDS)
                    consecutive_errors = 0
                else:
                    await asyncio.sleep(1)

        logger.info(
            f"[StrategyWorker:{self.strategy_name}] Stopped. "
            f"Bars processed: {bars_processed}"
        )

    async def stop(self) -> None:
        self._running = False


class MultiStrategyRunner:
    """Runs all active strategies concurrently via asyncio.gather().

    Workflow:
    1. Load active strategy rows from the DB on startup.
    2. Build one StrategyWorker per row via the strategy registry.
    3. Gather all worker coroutines so they run concurrently.
    4. Failed inits are logged and skipped — they do not crash other workers.
    """

    def __init__(self, block_ms: int = RUNNER_BLOCK_MS) -> None:
        self.block_ms = block_ms
        self._workers: List[StrategyWorker] = []
        self._running: bool = False

    # ------------------------------------------------------------------
    # DB helpers (patchable in tests)
    # ------------------------------------------------------------------

    def _load_active_strategies(self) -> list:
        """Return all Strategy rows with status='active'.

        Returns an empty list on any DB error so callers are never blocked.
        """
        try:
            from backend.db.models import Strategy
            with get_db_session() as session:
                return (
                    session.query(Strategy)
                    .filter(Strategy.status == "active")
                    .all()
                )
        except Exception as exc:
            logger.error(
                f"[MultiStrategyRunner] Failed to load active strategies: {exc}"
            )
            return []

    # ------------------------------------------------------------------
    # Worker construction (patchable in tests)
    # ------------------------------------------------------------------

    def _build_strategy_worker(self, db_row) -> Optional[StrategyWorker]:
        """Attempt to build a StrategyWorker from a DB Strategy row.

        Returns None and logs if the strategy name is unknown or construction
        fails, so callers can skip and continue with other rows.
        """
        from backend.strategies.registry import create_strategy

        strategy_name = db_row.name
        db_uuid = str(db_row.id)
        config_data = db_row.config or {}

        try:
            result = create_strategy(strategy_name, db_uuid, config_data)
        except Exception as exc:
            logger.error(
                f"[MultiStrategyRunner] create_strategy failed for {strategy_name}: {exc}"
            )
            return None

        if result is None:
            logger.warning(
                f"[MultiStrategyRunner] Skipping unknown/failed strategy: {strategy_name}"
            )
            return None

        config, strategy = result
        try:
            return StrategyWorker(
                strategy_name=strategy_name,
                strategy_id=db_uuid,
                config=config,
                strategy=strategy,
                block_ms=self.block_ms,
            )
        except Exception as exc:
            logger.error(
                f"[MultiStrategyRunner] Failed to build worker for {strategy_name}: {exc}"
            )
            return None

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Load active strategies and run all workers concurrently."""
        logger.info("[MultiStrategyRunner] Starting — loading active strategies from DB")

        try:
            from backend.startup.validation import run_startup_validation

            run_startup_validation()
        except Exception as exc:
            logger.error(
                "[MultiStrategyRunner] Startup validation failed: %s", exc, exc_info=True
            )

        db_rows = self._load_active_strategies()
        logger.info(f"[MultiStrategyRunner] Found {len(db_rows)} active strategy rows")

        workers: List[StrategyWorker] = []
        for row in db_rows:
            worker = self._build_strategy_worker(row)
            if worker is not None:
                workers.append(worker)

        self._workers = workers

        # Write health file so Docker healthcheck passes
        health_file = Path(RUNNER_HEALTH_FILE)
        try:
            health_file.parent.mkdir(parents=True, exist_ok=True)
            health_file.touch()
            logger.info(f"[MultiStrategyRunner] Health file created: {health_file}")
        except Exception as e:
            logger.warning(f"[MultiStrategyRunner] Could not create health file: {e}")

        if not workers:
            logger.warning(
                "[MultiStrategyRunner] No workers to run — waiting indefinitely. "
                "Check that at least one strategy has status='active' in the DB."
            )
            while True:
                await asyncio.sleep(60)

        logger.info(
            f"[MultiStrategyRunner] Launching {len(workers)} worker(s): "
            + ", ".join(w.strategy_name for w in workers)
        )
        self._running = True

        try:
            await asyncio.gather(*(w.run() for w in workers))
        finally:
            try:
                if health_file.exists():
                    health_file.unlink()
            except Exception:
                pass

    async def stop(self) -> None:
        """Stop all workers gracefully."""
        logger.info("[MultiStrategyRunner] Stopping all workers...")
        self._running = False
        for worker in self._workers:
            await worker.stop()


async def run_strategy_runner() -> None:
    """
    Run the strategy runner with signal handling for graceful shutdown.

    Note: ScreenerService runs in the API service (api/main.py).
    The runner focuses solely on consuming market data bars and executing strategies.
    """
    runner = StrategyRunner()
    shutdown_requested = False

    def signal_handler(sig, frame):
        nonlocal shutdown_requested
        if shutdown_requested:
            logger.warning("Shutdown already in progress, forcing exit...")
            sys.exit(1)
        shutdown_requested = True
        logger.info("Received shutdown signal, cleaning up...")
        loop = asyncio.get_event_loop()
        loop.create_task(runner.stop())

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        await runner.run()
    except Exception as e:
        logger.error(f"Fatal error in StrategyRunner: {e}", exc_info=True)
        raise
    finally:
        logger.info("Shutdown complete")


async def run_multi_strategy_runner() -> None:
    """Run the multi-strategy runner with graceful shutdown signal handling.

    This is the production entry point.  It loads all active strategies from
    the DB and runs one StrategyWorker coroutine per strategy concurrently.
    """
    runner = MultiStrategyRunner()
    shutdown_requested = False

    def signal_handler(sig, frame):
        nonlocal shutdown_requested
        if shutdown_requested:
            logger.warning("Shutdown already in progress, forcing exit...")
            sys.exit(1)
        shutdown_requested = True
        logger.info("Received shutdown signal, stopping all workers...")
        loop = asyncio.get_event_loop()
        loop.create_task(runner.stop())

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        await runner.run()
    except Exception as exc:
        logger.error(f"Fatal error in MultiStrategyRunner: {exc}", exc_info=True)
        raise
    finally:
        logger.info("MultiStrategyRunner shutdown complete")


def main():
    """Main entry point for the Strategy Runner service.

    Runs the multi-strategy runner which loads all active strategies from DB.
    Set RUNNER_LEGACY=1 in the environment to use the old single-strategy runner.
    """
    import os
    logger.info("Starting Strategy Runner service")

    use_legacy = os.getenv("RUNNER_LEGACY", "0").strip() == "1"
    entry = run_strategy_runner if use_legacy else run_multi_strategy_runner

    try:
        asyncio.run(entry())
    except KeyboardInterrupt:
        logger.info("Strategy Runner stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

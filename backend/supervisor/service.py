"""Supervisor service — evaluates all strategies every N hours and writes verdicts to Redis."""

import logging
import signal
import time
from datetime import datetime, timezone

from backend.supervisor.classifier import StrategyVerdict, classify
from backend.supervisor.config import (
    LIVE_EVAL_INTERVAL_SEC,
    SUPERVISOR_BACKTEST_DAYS,
    SUPERVISOR_BACKTEST_INTERVAL,
    SUPERVISOR_INTERVAL_SEC,
    SUPERVISOR_STRATEGIES,
    SUPERVISOR_STRATEGY_OVERRIDES,
    SUPERVISOR_TIMEOUT_SEC,
)
from backend.supervisor.logger import open_cycle_logger
from backend.supervisor.parser import parse_backtest_stdout
from backend.supervisor.runner import run_backtest
from backend.supervisor.store import (
    acquire_lock,
    read_all_verdicts,
    release_lock,
    write_last_run,
    write_verdict,
)

logger = logging.getLogger(__name__)

DRAWDOWN_BREACH_THRESHOLD = -5.0

_INITIAL_REDUCED_VERDICT = {
    "status": "REDUCED",
    "win_rate": None,
    "rr_ratio": None,
    "trades": None,
    "wins": None,
    "losses": None,
    "size_factor": 0.5,
    "reason": "initial_default",
    "last_evaluated": None,
    "backtest_exit_code": None,
    "lookback_days": None,
    "interval": None,
}


def _apply_drawdown_breach_override(
    strategy: str,
    verdict_obj: StrategyVerdict,
    reason: str,
    cycle_log: logging.Logger,
) -> tuple[StrategyVerdict, str]:
    """Override classifier verdict when live cumulative R loss breaches threshold."""
    from backend.supervisor.store import (
        is_drawdown_suspended,
        read_cumulative_r_loss,
        set_drawdown_suspended,
    )

    cumulative_r_loss = read_cumulative_r_loss(strategy)
    if cumulative_r_loss is None or cumulative_r_loss >= DRAWDOWN_BREACH_THRESHOLD:
        return verdict_obj, reason

    if not is_drawdown_suspended(strategy):
        set_drawdown_suspended(
            strategy, reason=f"cumulative_r_loss={cumulative_r_loss:.2f}"
        )
        cycle_log.warning(
            "Strategy %s auto-SUSPENDED: R loss %.2f < -5.0",
            strategy,
            cumulative_r_loss,
        )

    new_reason = f"drawdown_breach:r_loss={cumulative_r_loss:.2f}"
    return (
        StrategyVerdict(
            status="SUSPENDED",
            size_factor=0.0,
            reason=new_reason,
        ),
        new_reason,
    )


class SupervisorService:
    """Runs a full evaluation cycle every SUPERVISOR_INTERVAL_SEC seconds."""

    def __init__(self) -> None:
        self._running = True
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame) -> None:
        logger.info(f"Supervisor received signal {signum} — shutting down after current cycle")
        self._running = False

    def _seed_initial_defaults(self, cycle_log: logging.Logger) -> None:
        """On first run, write REDUCED defaults for all strategies before backtests begin."""
        cycle_log.info("No existing verdicts found — seeding REDUCED defaults for all strategies")
        for strategy in SUPERVISOR_STRATEGIES:
            verdict = dict(_INITIAL_REDUCED_VERDICT)
            verdict["strategy"] = strategy
            write_verdict(strategy, verdict)
            cycle_log.info(f"  Seeded {strategy} → REDUCED (initial_default)")

    def _run_strategy(self, strategy: str, cycle_log: logging.Logger) -> dict:
        """Run one strategy's backtest and return a verdict dict."""
        overrides = SUPERVISOR_STRATEGY_OVERRIDES.get(strategy, {})
        days = overrides.get("days", SUPERVISOR_BACKTEST_DAYS)
        interval = overrides.get("interval", SUPERVISOR_BACKTEST_INTERVAL)

        cycle_log.info(
            f"[{strategy}] Running backtest — days={days}, interval={interval}, "
            f"timeout={SUPERVISOR_TIMEOUT_SEC}s"
        )

        run = run_backtest(
            strategy=strategy,
            days=days,
            interval=interval,
            timeout_sec=SUPERVISOR_TIMEOUT_SEC,
        )

        cycle_log.info(
            f"[{strategy}] Backtest finished in {run.duration_sec}s "
            f"(exit_code={run.exit_code})"
        )

        if run.exit_code != 0:
            reason = f"backtest_failed:exit={run.exit_code}"
            if run.stderr:
                cycle_log.warning(f"[{strategy}] stderr: {run.stderr[:500]}")
            verdict_obj = classify(0.0, 0.0, 0)
            verdict_obj, reason = _apply_drawdown_breach_override(
                strategy, verdict_obj, reason, cycle_log
            )
            verdict = _build_verdict(
                strategy=strategy,
                metrics=None,
                verdict_obj=verdict_obj,
                days=days,
                interval=interval,
                exit_code=run.exit_code,
                reason=reason,
            )
            cycle_log.warning(f"[{strategy}] → {verdict['status']} ({reason})")
            return verdict

        try:
            metrics = parse_backtest_stdout(run.stdout)
        except ValueError as exc:
            reason = f"parse_error:{exc}"
            cycle_log.warning(f"[{strategy}] Parse failed: {exc}")
            # Log stdout snippet for debugging
            snippet = run.stdout[-800:] if run.stdout else "(empty)"
            cycle_log.debug(f"[{strategy}] stdout tail:\n{snippet}")
            verdict_obj = classify(0.0, 0.0, 0)
            verdict_obj, reason = _apply_drawdown_breach_override(
                strategy, verdict_obj, reason, cycle_log
            )
            verdict = _build_verdict(
                strategy=strategy,
                metrics=None,
                verdict_obj=verdict_obj,
                days=days,
                interval=interval,
                exit_code=run.exit_code,
                reason=reason,
            )
            cycle_log.warning(f"[{strategy}] → {verdict['status']} ({reason})")
            return verdict

        verdict_obj = classify(metrics.win_rate, metrics.rr_ratio, metrics.trades)
        verdict_obj, reason = _apply_drawdown_breach_override(
            strategy, verdict_obj, verdict_obj.reason, cycle_log
        )
        verdict = _build_verdict(
            strategy=strategy,
            metrics=metrics,
            verdict_obj=verdict_obj,
            days=days,
            interval=interval,
            exit_code=run.exit_code,
            reason=reason,
        )
        cycle_log.info(
            f"[{strategy}] → {verdict['status']}  "
            f"WR={metrics.win_rate:.1f}%  R:R={metrics.rr_ratio:.2f}  "
            f"trades={metrics.trades}  ({verdict_obj.reason})"
        )
        return verdict

    def run_cycle(self) -> None:
        """Run one full evaluation cycle across all strategies."""
        now = datetime.now(timezone.utc)
        cycle_log = open_cycle_logger(now)
        cycle_log.info("=== Supervisor cycle starting ===")

        if not acquire_lock():
            cycle_log.warning("Could not acquire supervisor lock — another cycle may be running. Skipping.")
            return

        try:
            # Seed defaults on first run
            existing = read_all_verdicts()
            if not existing:
                self._seed_initial_defaults(cycle_log)

            # Evaluate each strategy sequentially (avoid Kraken API rate limits)
            for strategy in SUPERVISOR_STRATEGIES:
                try:
                    verdict = self._run_strategy(strategy, cycle_log)
                    write_verdict(strategy, verdict)
                    from backend.supervisor.store import (
                        clear_drawdown_suspended,
                        read_cumulative_r_loss,
                    )

                    cumulative_r_loss = read_cumulative_r_loss(strategy)
                    breached = (
                        cumulative_r_loss is not None
                        and cumulative_r_loss < DRAWDOWN_BREACH_THRESHOLD
                    )
                    if str(verdict.get("status", "")).upper() == "ACTIVE" and not breached:
                        clear_drawdown_suspended(strategy)
                except Exception as exc:
                    cycle_log.error(f"[{strategy}] Unexpected error: {exc}", exc_info=True)
                    # Fallback: write REDUCED so runner degrades gracefully
                    fallback = dict(_INITIAL_REDUCED_VERDICT)
                    fallback["strategy"] = strategy
                    fallback["reason"] = f"cycle_error:{exc}"
                    fallback["last_evaluated"] = now.isoformat()
                    write_verdict(strategy, fallback)

            write_last_run(now.isoformat())
            cycle_log.info("=== Supervisor cycle complete ===")

        finally:
            release_lock()

    def run_live_eval_cycle(self) -> None:
        """Rolling live stats from Redis R-multiples + DB strategy names (no backtest lock)."""
        from backend.db import get_session
        from backend.supervisor.live_evaluator import run_live_eval_for_all_strategies
        from backend.supervisor.store import write_live_last_run

        now = datetime.now(timezone.utc).isoformat()
        try:
            session = get_session()
        except Exception as exc:
            logger.warning(f"[supervisor] Live eval skipped (no DB session): {exc}")
            return
        try:
            run_live_eval_for_all_strategies(session, SUPERVISOR_STRATEGIES)
        finally:
            session.close()
        write_live_last_run(now)
        logger.info("[supervisor] Live evaluation cycle complete")

    def run_forever(self) -> None:
        """Run an initial cycle, then repeat every SUPERVISOR_INTERVAL_SEC seconds."""
        logger.info(
            f"Supervisor service starting — interval={SUPERVISOR_INTERVAL_SEC}s "
            f"({SUPERVISOR_INTERVAL_SEC // 3600}h)"
        )

        while self._running:
            self.run_cycle()

            if not self._running:
                break

            try:
                self.run_live_eval_cycle()
            except Exception as exc:
                logger.error(f"[supervisor] Live eval after backtest failed: {exc}", exc_info=True)

            logger.info(
                f"Supervisor sleeping {SUPERVISOR_INTERVAL_SEC}s until next cycle"
            )
            # Sleep in 30s increments so SIGTERM is handled promptly; run live eval every LIVE_EVAL_INTERVAL_SEC
            elapsed = 0
            live_elapsed = 0
            while elapsed < SUPERVISOR_INTERVAL_SEC and self._running:
                time.sleep(30)
                elapsed += 30
                live_elapsed += 30
                if live_elapsed >= LIVE_EVAL_INTERVAL_SEC:
                    try:
                        self.run_live_eval_cycle()
                    except Exception as exc:
                        logger.error(f"[supervisor] Live eval failed: {exc}", exc_info=True)
                    live_elapsed = 0

        logger.info("Supervisor service stopped")


def _build_verdict(
    strategy: str,
    metrics,  # BacktestMetrics | None
    verdict_obj,
    days: int,
    interval: str,
    exit_code: int,
    reason: str,
) -> dict:
    now_iso = datetime.now(timezone.utc).isoformat()
    rr_ratio = None
    if metrics is not None:
        rr_ratio = metrics.rr_ratio if metrics.rr_ratio != float("inf") else 99.0

    return {
        "strategy": strategy,
        "status": verdict_obj.status,
        "win_rate": metrics.win_rate if metrics else None,
        "rr_ratio": rr_ratio,
        "trades": metrics.trades if metrics else None,
        "wins": metrics.wins if metrics else None,
        "losses": metrics.losses if metrics else None,
        "size_factor": verdict_obj.size_factor,
        "reason": reason,
        "last_evaluated": now_iso,
        "lookback_days": days,
        "interval": interval,
        "backtest_exit_code": exit_code,
    }

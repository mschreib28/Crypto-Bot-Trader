"""Pure classification logic: (win_rate, rr_ratio, trades) → StrategyVerdict."""

from dataclasses import dataclass

from backend.supervisor.config import (
    ACTIVE_RR_THRESHOLD,
    ACTIVE_WR_THRESHOLD,
    MIN_TRADES_FOR_ACTIVE,
    REDUCED_RR_THRESHOLD,
    REDUCED_WR_THRESHOLD,
)


@dataclass(frozen=True)
class StrategyVerdict:
    status: str        # ACTIVE | REDUCED | SUSPENDED
    size_factor: float
    reason: str


def classify(win_rate: float, rr_ratio: float, trades: int) -> StrategyVerdict:
    """Classify a strategy's performance into ACTIVE / REDUCED / SUSPENDED.

    rr_ratio of float('inf') (no losses in sample) is treated as 99.0.
    """
    effective_rr = min(rr_ratio, 99.0) if rr_ratio != float("inf") else 99.0

    if trades == 0:
        return StrategyVerdict(
            status="SUSPENDED",
            size_factor=0.0,
            reason="no_trades_in_period",
        )

    if win_rate >= ACTIVE_WR_THRESHOLD and effective_rr >= ACTIVE_RR_THRESHOLD:
        if trades < MIN_TRADES_FOR_ACTIVE:
            # Sample too small — demote to REDUCED even if numbers look good
            return StrategyVerdict(
                status="REDUCED",
                size_factor=0.5,
                reason=f"wr_rr_pass_but_sample_small:{trades}<{MIN_TRADES_FOR_ACTIVE}",
            )
        return StrategyVerdict(
            status="ACTIVE",
            size_factor=1.0,
            reason=f"wr>={ACTIVE_WR_THRESHOLD}_and_rr>={ACTIVE_RR_THRESHOLD}",
        )

    if win_rate >= REDUCED_WR_THRESHOLD and effective_rr >= REDUCED_RR_THRESHOLD:
        return StrategyVerdict(
            status="REDUCED",
            size_factor=0.5,
            reason=f"wr>={REDUCED_WR_THRESHOLD}_and_rr>={REDUCED_RR_THRESHOLD}",
        )

    return StrategyVerdict(
        status="SUSPENDED",
        size_factor=0.0,
        reason=f"wr={win_rate:.1f}_rr={effective_rr:.2f}_below_threshold",
    )

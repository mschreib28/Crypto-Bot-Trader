"""Supervisor status API route."""

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.supervisor.config import SUPERVISOR_STRATEGIES
from backend.supervisor.store import (
    canonical_name,
    get_effective_mode,
    get_strategy_manual_mode,
    read_all_live_verdicts,
    read_all_verdicts,
    read_last_run,
    read_live_last_run,
    read_strategy_sim_stats,
    read_verdict,
    set_strategy_manual_mode,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class StrategyStatus(BaseModel):
    strategy: str
    status: str
    win_rate: Optional[float]
    rr_ratio: Optional[float]
    trades: Optional[int]
    wins: Optional[int]
    losses: Optional[int]
    size_factor: float
    reason: Optional[str]
    last_evaluated: Optional[str]
    lookback_days: Optional[int]
    interval: Optional[str]


class SupervisorStatusResponse(BaseModel):
    success: bool
    data: dict[str, Any]


def _placeholder(strategy: str) -> dict:
    """Return a REDUCED placeholder verdict for a strategy with no data yet."""
    return {
        "strategy": strategy,
        "status": "REDUCED",
        "win_rate": None,
        "rr_ratio": None,
        "trades": None,
        "wins": None,
        "losses": None,
        "size_factor": 0.5,
        "reason": "no_data_yet",
        "last_evaluated": None,
        "lookback_days": None,
        "interval": None,
    }


@router.get("/supervisor/status", response_model=SupervisorStatusResponse, summary="Supervisor strategy status")
async def get_supervisor_status() -> SupervisorStatusResponse:
    """Return the current supervisor verdict for each strategy.

    Strategies with no recorded verdict are returned as REDUCED placeholders.
    """
    try:
        all_verdicts = read_all_verdicts()
        by_name = {v["strategy"]: v for v in all_verdicts}
        last_run = read_last_run()

        # Return in canonical order; fill placeholders for missing strategies
        strategies = []
        for name in SUPERVISOR_STRATEGIES:
            verdict = by_name.get(name, _placeholder(name))
            strategies.append(verdict)

        return SupervisorStatusResponse(
            success=True,
            data={
                "last_run": last_run,
                "strategies": strategies,
            },
        )
    except Exception as exc:
        logger.error(f"[supervisor route] Failed to read status: {exc}", exc_info=True)
        return SupervisorStatusResponse(
            success=False,
            data={"error": str(exc), "strategies": []},
        )


@router.get("/supervisor/live-status", response_model=SupervisorStatusResponse, summary="Rolling live supervisor stats")
async def get_supervisor_live_status() -> SupervisorStatusResponse:
    """Return last live eval time and per-strategy rolling window metrics from Redis."""
    try:
        last_run = read_live_last_run()
        strategies = read_all_live_verdicts(list(SUPERVISOR_STRATEGIES))
        return SupervisorStatusResponse(
            success=True,
            data={
                "last_run": last_run,
                "strategies": strategies,
            },
        )
    except Exception as exc:
        logger.error(f"[supervisor route] Failed to read live status: {exc}", exc_info=True)
        return SupervisorStatusResponse(
            success=False,
            data={"error": str(exc), "strategies": []},
        )


def _canonical_supervised(slug: str) -> str:
    c = canonical_name(slug.strip())
    if c not in SUPERVISOR_STRATEGIES:
        raise HTTPException(status_code=404, detail=f"Unknown strategy: {slug!r}")
    return c


class StrategyModeResponse(BaseModel):
    strategy: str
    manual_mode: str
    supervisor_status: str
    effective_mode: str
    updated_at: Optional[str] = None


class StrategyModeRequest(BaseModel):
    mode: str = Field(..., description="LIVE or SIM")


@router.get("/strategies/{strategy}/mode", response_model=StrategyModeResponse)
async def get_strategy_mode(strategy: str) -> StrategyModeResponse:
    canon = _canonical_supervised(strategy)
    manual = get_strategy_manual_mode(canon)
    verdict = read_verdict(canon)
    sup = str(verdict.get("status", "REDUCED")).upper() if verdict else "REDUCED"
    eff, _ = get_effective_mode(canon)
    from backend.redis import get_redis_client
    from backend.redis.keys import STRATEGY_MANUAL_MODE_UPDATED_KEY

    raw = get_redis_client().get(STRATEGY_MANUAL_MODE_UPDATED_KEY.format(strategy=canon))
    updated = raw.decode("utf-8") if isinstance(raw, bytes) else raw if raw else None
    return StrategyModeResponse(
        strategy=canon,
        manual_mode=manual,
        supervisor_status=sup,
        effective_mode=eff,
        updated_at=updated,
    )


@router.post("/strategies/{strategy}/mode", response_model=StrategyModeResponse)
async def post_strategy_mode(strategy: str, body: StrategyModeRequest) -> StrategyModeResponse:
    canon = _canonical_supervised(strategy)
    mode = str(body.mode).strip().upper()
    if mode not in ("LIVE", "SIM"):
        raise HTTPException(status_code=400, detail="mode must be LIVE or SIM")
    ts = set_strategy_manual_mode(canon, mode)
    from backend.supervisor.store import clear_drawdown_suspended

    clear_drawdown_suspended(canon)
    verdict = read_verdict(canon)
    sup = str(verdict.get("status", "REDUCED")).upper() if verdict else "REDUCED"
    eff, _ = get_effective_mode(canon)
    return StrategyModeResponse(
        strategy=canon,
        manual_mode=mode,
        supervisor_status=sup,
        effective_mode=eff,
        updated_at=ts,
    )


class StrategySimStatsResponse(BaseModel):
    strategy: str
    trades: int
    wins: int
    losses: int
    win_rate: Optional[float] = None
    rr_ratio: Optional[float] = None
    pnl: float
    balance: dict[str, Any]


@router.get("/strategies/{strategy}/sim-stats", response_model=StrategySimStatsResponse)
async def get_strategy_sim_stats(strategy: str) -> StrategySimStatsResponse:
    canon = _canonical_supervised(strategy)
    stats = read_strategy_sim_stats(canon)
    return StrategySimStatsResponse(
        strategy=canon,
        trades=int(stats["trades"]),
        wins=int(stats["wins"]),
        losses=int(stats["losses"]),
        win_rate=stats.get("win_rate"),
        rr_ratio=stats.get("rr_ratio"),
        pnl=float(stats.get("pnl", 0.0)),
        balance=stats.get("balance", {}),
    )

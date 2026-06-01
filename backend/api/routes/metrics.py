"""Metrics endpoint for strategy performance tracking."""

import logging

from fastapi import APIRouter, HTTPException

from backend.api.models import MetricsResponse, StrategyMetricsItem
from backend.risk.metrics import get_strategy_metrics
from backend.performance.monitor import get_performance_monitor

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/metrics", summary="Get strategy performance metrics", response_model=MetricsResponse)
async def get_metrics():
    """
    Get aggregated performance metrics for all strategies.

    Returns accuracy, P&L, and win/loss counts per strategy,
    as well as overall totals across all strategies.
    
    Uses the performance monitor for real-time P&L data from positions.
    """
    try:
        # Get all strategies from database
        from backend.db import get_session
        from backend.db.models import Strategy
        session = get_session()
        try:
            db_strategies = session.query(Strategy).all()
            uuid_to_name = {str(s.id): s.name for s in db_strategies}
        finally:
            session.close()
        
        # Use performance monitor for accurate P&L data
        perf_monitor = get_performance_monitor()
        
        # Recalculate to ensure data is up-to-date in Redis
        perf_results = perf_monitor.recalculate_all_metrics()
        logger.info(f"Recalculated performance: {len(perf_results)} strategies found: {list(perf_results.keys())}")
        
        # Also get legacy metrics for open_count
        metrics_tracker = get_strategy_metrics()
        legacy_data = metrics_tracker.get_all_metrics()
        
        strategies = {}
        total_pnl = 0.0
        total_wins = 0
        total_losses = 0
        
        # Get performance data for each strategy from database
        # Use recalculated results (most accurate)
        logger.info(f"Processing {len(db_strategies)} strategies from database")
        logger.info(f"Performance results keys: {list(perf_results.keys())}")
        
        for strategy in db_strategies:
            strategy_uuid = str(strategy.id)

            # Open-position unrealized P&L: recalculate_all_metrics keys may be name or UUID
            perf = perf_results.get(strategy_uuid) or perf_results.get(strategy.name)
            unrealized_pnl = perf.total_pnl if perf else 0.0

            # Legacy metrics track closed trades: accuracy, win/loss counts, realized P&L
            legacy_stats = legacy_data["strategies"].get(strategy_uuid, {})
            if legacy_stats:
                accuracy_pct = legacy_stats["accuracy_pct"]
                win_count = legacy_stats["win_count"]
                loss_count = legacy_stats["loss_count"]
                realized_pnl = legacy_stats["total_pnl"]
                open_count = legacy_stats["open_count"]
            elif perf:
                # No closed trades yet — use open-position data as best approximation
                accuracy_pct = perf.win_rate
                win_count = perf.winning_trades
                loss_count = perf.losing_trades
                realized_pnl = 0.0
                open_count = perf.total_trades
                unrealized_pnl = perf.total_pnl
            else:
                logger.debug(f"No metrics data for strategy {strategy_uuid} ({strategy.name})")
                continue

            combined_pnl = round(realized_pnl + unrealized_pnl, 4)
            logger.info(
                f"Strategy {strategy.name}: realized=${realized_pnl:.2f} "
                f"unrealized=${unrealized_pnl:.2f} combined=${combined_pnl:.2f} "
                f"accuracy={accuracy_pct:.1f}%"
            )

            item = StrategyMetricsItem(
                accuracy_pct=accuracy_pct,
                total_pnl=combined_pnl,
                win_count=win_count,
                loss_count=loss_count,
                open_count=open_count,
            )
            strategies[strategy_uuid] = item
            strategies[strategy.name] = item
            total_pnl += combined_pnl
            total_wins += win_count
            total_losses += loss_count
        
        # Add any strategies from legacy metrics that aren't in performance monitor
        # Only add if not already present (performance monitor takes precedence)
        for strategy_id, legacy_stats in legacy_data["strategies"].items():
            # Skip if we already have performance data for this UUID
            if strategy_id in strategies:
                logger.debug(f"Skipping legacy metrics for {strategy_id} - already have performance data")
                continue
            
            # Convert UUID to name if needed
            display_id = uuid_to_name.get(strategy_id, strategy_id)
            
            if display_id not in strategies:
                # Use legacy data if no performance data available
                logger.info(f"Adding legacy metrics for {strategy_id} (display: {display_id})")
                strategies[display_id] = StrategyMetricsItem(**legacy_stats)
                if strategy_id != display_id:
                    strategies[strategy_id] = StrategyMetricsItem(**legacy_stats)
                total_pnl += legacy_stats.get("total_pnl", 0.0)
                total_wins += legacy_stats.get("win_count", 0)
                total_losses += legacy_stats.get("loss_count", 0)
        
        # Calculate overall accuracy
        total_closed = total_wins + total_losses
        overall_accuracy = (total_wins / total_closed * 100.0) if total_closed > 0 else 0.0

        return MetricsResponse(
            strategies=strategies,
            total_pnl=round(total_pnl, 2),
            overall_accuracy_pct=round(overall_accuracy, 2),
        )

    except Exception as e:
        logger.error(f"Error fetching metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching metrics")

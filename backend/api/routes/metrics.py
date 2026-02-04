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
            
            # Get performance data from recalculated results
            perf = perf_results.get(strategy_uuid)
            
            # Debug: Check if UUID matches
            if strategy_uuid not in perf_results:
                logger.warning(f"Strategy UUID {strategy_uuid} not found in perf_results. Available keys: {list(perf_results.keys())}")
            
            if perf:
                logger.info(f"Using performance data for {strategy_uuid}: P&L=${perf.total_pnl:.2f}")
                # Use performance monitor data (most accurate)
                logger.info(f"Strategy {strategy_uuid} ({strategy.name}): P&L=${perf.total_pnl:.2f}, trades={perf.total_trades}")
                strategies[strategy_uuid] = StrategyMetricsItem(
                    accuracy_pct=perf.win_rate,
                    total_pnl=perf.total_pnl,
                    win_count=perf.winning_trades,
                    loss_count=perf.losing_trades,
                    open_count=legacy_data["strategies"].get(strategy_uuid, {}).get("open_count", 0),
                )
                # Also add name mapping
                strategies[strategy.name] = StrategyMetricsItem(
                    accuracy_pct=perf.win_rate,
                    total_pnl=perf.total_pnl,
                    win_count=perf.winning_trades,
                    loss_count=perf.losing_trades,
                    open_count=legacy_data["strategies"].get(strategy_uuid, {}).get("open_count", 0),
                )
                total_pnl += perf.total_pnl
                total_wins += perf.winning_trades
                total_losses += perf.losing_trades
            else:
                logger.warning(f"No performance data found for strategy {strategy_uuid} ({strategy.name})")
                # Fall back to legacy metrics if no performance data
                legacy_stats = legacy_data["strategies"].get(strategy_uuid, {})
                if legacy_stats:
                    strategies[strategy_uuid] = StrategyMetricsItem(**legacy_stats)
                    strategies[strategy.name] = StrategyMetricsItem(**legacy_stats)
                    total_pnl += legacy_stats.get("total_pnl", 0.0)
                    total_wins += legacy_stats.get("win_count", 0)
                    total_losses += legacy_stats.get("loss_count", 0)
        
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

"""Screener API endpoints for signal rankings and results."""

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from backend.redis import get_redis_client
from backend.redis.keys import SCREENER_SIGNALS_HISTORY_KEY
from backend.screener.models import ScreenerResult
from backend.screener.service import ScreenerService, get_trading_enabled

router = APIRouter(tags=["Screener"])
logger = logging.getLogger(__name__)

# Global screener service instance
_screener_service: Optional[ScreenerService] = None


def get_screener_service() -> ScreenerService:
    """Get or create the screener service instance."""
    global _screener_service
    if _screener_service is None:
        _screener_service = ScreenerService()
    return _screener_service


@router.get("/screener")
async def get_screener_results() -> dict:
    """
    Get all current screener results.
    
    Returns all symbols with their signal types, strengths, and indicators.
    """
    service = get_screener_service()
    results = service.get_results()
    last_scan = service.get_last_scan_time()
    
    return {
        "results": [r.to_dict() for r in results],
        "count": len(results),
        "last_scan": last_scan,
    }


@router.get("/screener/top")
async def get_top_signals(
    n: int = Query(default=10, ge=1, le=100, description="Number of top signals to return"),
    min_strength: float = Query(default=0, ge=0, le=100, description="Minimum signal strength"),
    signal_type: Optional[str] = Query(default=None, description="Filter by signal type: BUY, SELL"),
) -> dict:
    """
    Get top N signals sorted by strength.
    
    Filters out symbols with no data (waiting_for_data, insufficient_data).
    
    Args:
        n: Number of results to return (default: 10)
        min_strength: Minimum signal strength to include (default: 0)
        signal_type: Optional filter for BUY or SELL signals only
    
    Returns:
        Top signals sorted by strength descending.
    """
    service = get_screener_service()
    results = service.get_results()
    
    # Filter out symbols with no data first
    results_with_data = []
    for r in results:
        indicators = r.indicators
        # Skip symbols with waiting_for_data or insufficient_data notes
        if indicators.get("note") in ("waiting_for_data", "insufficient_data"):
            continue
        if indicators.get("error") == "insufficient_data":
            continue
        # Skip symbols with 0 bars available
        if indicators.get("bars_available", 0) == 0 and indicators.get("bars_required", 0) > 0:
            continue
        # Skip symbols missing critical data (no price and no RSI means no evaluation possible)
        price = indicators.get("price") or indicators.get("current_price")
        rsi = indicators.get("rsi")
        if not price and not rsi and r.signal_type == "NONE" and r.signal_strength == 0:
            continue
        results_with_data.append(r)
    
    # Filter by minimum strength
    filtered = [r for r in results_with_data if r.signal_strength >= min_strength]
    
    # Filter by signal type if specified
    if signal_type:
        signal_type_upper = signal_type.upper()
        filtered = [r for r in filtered if r.signal_type == signal_type_upper]
    
    # Sort by strength descending
    sorted_results = sorted(filtered, key=lambda r: r.signal_strength, reverse=True)
    
    # Take top N
    top_results = sorted_results[:n]
    
    # Convert to dicts and normalize price field
    result_dicts = []
    for r in top_results:
        result_dict = r.to_dict()
        # Normalize price field in indicators
        indicators = result_dict.get("indicators", {})
        if 'price' not in indicators and 'current_price' in indicators:
            indicators['price'] = indicators['current_price']
        result_dicts.append(result_dict)
    
    return {
        "results": result_dicts,
        "count": len(result_dicts),
        "total_scanned": len(results_with_data),  # Count only symbols with data
        "last_scan": service.get_last_scan_time(),
    }


@router.get("/screener/signals")
async def get_signal_history(
    limit: int = Query(default=50, ge=1, le=100, description="Maximum number of signals to return"),
) -> dict:
    """
    Get recent screener signal history.
    
    Returns BUY/SELL signals detected by the screener, stored in Redis.
    These are LOG ONLY signals - no automatic execution occurs.
    
    Args:
        limit: Number of signals to return (default: 50, max: 100)
    
    Returns:
        List of recent signals with timestamps and indicator data.
    """
    client = get_redis_client()
    
    try:
        # LRANGE returns elements from start to stop (inclusive)
        raw_signals = client.lrange(SCREENER_SIGNALS_HISTORY_KEY, 0, limit - 1)
        
        signals: List[Dict[str, Any]] = []
        for raw in raw_signals:
            try:
                signal_data = json.loads(raw)
                signals.append(signal_data)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse signal data: {raw}")
                continue
        
        return {
            "signals": signals,
            "count": len(signals),
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch signal history: {e}", exc_info=True)
        return {
            "signals": [],
            "count": 0,
            "error": "Failed to fetch signal history",
        }


@router.get("/screener/strategy/{strategy_id}")
async def get_strategy_screener_results(strategy_id: str) -> dict:
    """
    Get screener results for a specific strategy.
    
    Returns the top 5 signal results for the given strategy,
    sorted by confidence descending.
    
    Filters out symbols with no data (waiting_for_data, insufficient_data).
    
    Args:
        strategy_id: Strategy identifier (UUID or name like "mean_reversion")
    
    Returns:
        {
            "strategy_id": "...",
            "results": [top 5 SignalResults as dicts],
            "last_scan": "ISO timestamp",
            "trading_enabled": bool
        }
    """
    from backend.db import get_session
    from backend.db.models import Strategy
    
    service = get_screener_service()
    
    # Try to get results using the strategy_id directly (UUID)
    result = service.get_strategy_results(strategy_id)
    
    if result is not None:
        # Filter out symbols with no data
        filtered_results = []
        for r in result.get("results", []):
            indicators = r.get("indicators", {})
            # Skip symbols with waiting_for_data or insufficient_data notes
            if indicators.get("note") in ("waiting_for_data", "insufficient_data"):
                continue
            if indicators.get("error") == "insufficient_data":
                continue
            # Skip symbols with 0 bars available
            if indicators.get("bars_available", 0) == 0 and indicators.get("bars_required", 0) > 0:
                continue
            # Skip symbols missing critical data (no price and no RSI means no evaluation possible)
            price = indicators.get("price") or indicators.get("current_price")
            rsi = indicators.get("rsi")
            signal_type = r.get("signal_type", "NONE")
            confidence = r.get("confidence", 0.0)
            if not price and not rsi and signal_type == "NONE" and confidence == 0:
                continue
            filtered_results.append(r)
        
        # Normalize price field in filtered results
        for r in filtered_results:
            indicators = r.get("indicators", {})
            if 'price' not in indicators and 'current_price' in indicators:
                indicators['price'] = indicators['current_price']
        
        # Update results with filtered list
        result["results"] = filtered_results
        result["count"] = len(filtered_results)
        return result
    
    # If not found by ID, try to resolve name to UUID
    session = get_session()
    try:
        db_strategy = session.query(Strategy).filter(Strategy.name == strategy_id).first()
        if db_strategy:
            uuid_id = str(db_strategy.id)
            result = service.get_strategy_results(uuid_id)
            if result is not None:
                # Filter out symbols with no data and normalize price field
                filtered_results = []
                for r in result.get("results", []):
                    indicators = r.get("indicators", {})
                    # Normalize price field (ensure 'price' exists, use 'current_price' as fallback)
                    if 'price' not in indicators and 'current_price' in indicators:
                        indicators['price'] = indicators['current_price']
                    
                    # Skip symbols with waiting_for_data or insufficient_data notes
                    if indicators.get("note") in ("waiting_for_data", "insufficient_data"):
                        continue
                    if indicators.get("error") == "insufficient_data":
                        continue
                    # Skip symbols with 0 bars available
                    if indicators.get("bars_available", 0) == 0 and indicators.get("bars_required", 0) > 0:
                        continue
                    # Skip symbols missing critical data (no price and no RSI means no evaluation possible)
                    price = indicators.get("price") or indicators.get("current_price")
                    rsi = indicators.get("rsi")
                    signal_type = r.get("signal_type", "NONE")
                    confidence = r.get("confidence", 0.0)
                    if not price and not rsi and signal_type == "NONE" and confidence == 0:
                        continue
                    # Skip NONE signals with 0 confidence that have no meaningful indicators (empty rows)
                    if signal_type == "NONE" and confidence == 0:
                        # Check if this symbol has any meaningful data to display
                        has_meaningful_data = (
                            price is not None or
                            rsi is not None or
                            indicators.get("volume_24h") is not None or
                            indicators.get("rvol_pct") is not None or
                            indicators.get("change_24h_pct") is not None
                        )
                        if not has_meaningful_data:
                            continue
                    filtered_results.append(r)
                
                # Normalize price field in filtered results
                for r in filtered_results:
                    indicators = r.get("indicators", {})
                    if 'price' not in indicators and 'current_price' in indicators:
                        indicators['price'] = indicators['current_price']
                
                # Update results with filtered list
                result["results"] = filtered_results
                result["count"] = len(filtered_results)
                return result
    except Exception as e:
        logger.warning(f"Error resolving strategy name {strategy_id}: {e}")
    finally:
        session.close()
    
    # If still not found, return empty results with current status
    return {
        "strategy_id": strategy_id,
        "results": [],
        "last_scan": service.get_last_scan_time(),
        "trading_enabled": get_trading_enabled(),
        "message": f"No results found for strategy {strategy_id}",
    }


@router.get("/screener/{symbol}")
async def get_symbol_result(symbol: str) -> dict:
    """
    Get screener result for a specific symbol.
    
    Args:
        symbol: Trading pair symbol (e.g., "ETH/USD" or "ETH-USD")
    
    Returns:
        Screener result for the specified symbol.
    """
    service = get_screener_service()
    results = service.get_results()
    
    # Normalize symbol format (support both / and - separators)
    normalized = symbol.upper().replace("-", "/")
    
    for result in results:
        if result.symbol.upper() == normalized:
            return {
                "result": result.to_dict(),
                "found": True,
                "last_scan": service.get_last_scan_time(),
            }
    
    return {
        "result": None,
        "found": False,
        "message": f"Symbol {symbol} not found in screener results",
        "last_scan": service.get_last_scan_time(),
    }

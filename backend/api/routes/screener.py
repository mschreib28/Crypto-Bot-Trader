"""Screener API endpoints for signal rankings and results."""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from backend.redis import get_redis_client
from backend.redis.keys import SCREENER_SIGNALS_HISTORY_KEY, TOP_10_OBVIOUS_KEY, APLUS_SCORES_KEY
from backend.screener.models import ScreenerResult
from backend.screener.service import ScreenerService, get_trading_enabled, _get_enabled_strategy_display_names
from backend.screener.strategy_columns import calculate_vwap_distance, calculate_hod_distance, calculate_htf_trend
from backend.screener.pipeline import PIPELINE_CRITERIA
from backend.positions.tracker import get_position_tracker
from datetime import datetime, timezone

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


def enrich_with_aplus_scores(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Enrich screener results with A+ score data from Redis.
    
    For each result, fetches A+ score data and adds it to the indicators dict.
    Handles missing scores gracefully (leaves fields as None/empty).
    
    Args:
        results: List of screener result dictionaries
        
    Returns:
        List of enriched result dictionaries with A+ score data in indicators
    """
    service = get_screener_service()
    
    enriched_results = []
    for result in results:
        # Create a copy to avoid mutating original
        enriched_result = result.copy()
        
        # Ensure indicators dict exists
        if "indicators" not in enriched_result:
            enriched_result["indicators"] = {}
        
        # Get symbol from result
        symbol = enriched_result.get("symbol")
        if not symbol:
            enriched_results.append(enriched_result)
            continue
        
        # Fetch A+ score data
        aplus_data = service._get_aplus_score(symbol)
        
        if aplus_data:
            # Add A+ score fields to indicators
            enriched_result["indicators"]["score"] = aplus_data.get("score")
            enriched_result["indicators"]["grade"] = aplus_data.get("grade")
            enriched_result["indicators"]["rvol"] = aplus_data.get("rvol")
            # Map rvol to rvol_pct if not already present (for consistency)
            if "rvol_pct" not in enriched_result["indicators"] and aplus_data.get("rvol") is not None:
                enriched_result["indicators"]["rvol_pct"] = aplus_data.get("rvol") * 100  # Convert to percentage
            enriched_result["indicators"]["market_cap"] = aplus_data.get("market_cap")
            enriched_result["indicators"]["supply_ratio"] = aplus_data.get("supply_ratio")
            enriched_result["indicators"]["spread_bps"] = aplus_data.get("spread_bps")
            # Only add change_24h_pct if not already present (may come from other sources)
            if "change_24h_pct" not in enriched_result["indicators"]:
                enriched_result["indicators"]["change_24h_pct"] = aplus_data.get("change_24h_pct")
            enriched_result["indicators"]["pillars"] = aplus_data.get("pillars")
        
        enriched_results.append(enriched_result)
    
    return enriched_results


@router.get("/screener")
async def get_screener_results() -> dict:
    """
    Get all current screener results.
    
    Returns all symbols with their signal types, strengths, and indicators.
    Enriched with A+ score data.
    """
    service = get_screener_service()
    results = service.get_results()
    last_scan = service.get_last_scan_time()
    
    # Convert to dicts and enrich with A+ scores
    result_dicts = [r.to_dict() for r in results]
    enriched_results = enrich_with_aplus_scores(result_dicts)
    
    return {
        "results": enriched_results,
        "count": len(enriched_results),
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
    
    # Enrich with A+ scores
    enriched_results = enrich_with_aplus_scores(result_dicts)
    
    return {
        "results": enriched_results,
        "count": len(enriched_results),
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
        
        # Enrich with A+ scores
        enriched_results = enrich_with_aplus_scores(filtered_results)
        
        # Update results with filtered and enriched list
        result["results"] = enriched_results
        result["count"] = len(enriched_results)
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
                
                # Enrich with A+ scores
                enriched_results = enrich_with_aplus_scores(filtered_results)
                
                # Update results with filtered and enriched list
                result["results"] = enriched_results
                result["count"] = len(enriched_results)
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


@router.get("/screener/top-obvious")
async def get_top_obvious() -> dict:
    """
    Get top 10 obvious pairs from A+ scoring system.
    
    Returns pairs with score > 0.85, sorted by score descending.
    Data is fetched from Redis cache updated every 60 seconds by the screener service.
    
    Returns:
        {
            "results": [
                {
                    "symbol": "BTC/USD",
                    "signal_type": "NONE",
                    "signal_strength": 0.0,
                    "indicators": {
                        "score": 0.92,
                        "rvol": 5.2,
                        "market_cap": 850000000,
                        "supply_ratio": 0.95,
                        "spread_bps": 8.5,
                        "change_24h_pct": 2.3,
                        "price": 45000.0
                    },
                    "timestamp": "ISO timestamp"
                },
                ...
            ],
            "count": 10,
            "last_scan": "ISO timestamp or null"
        }
    """
    client = get_redis_client()
    
    try:
        # Read from Redis cache
        cached_data = client.get(TOP_10_OBVIOUS_KEY)
        
        if not cached_data:
            # Empty cache - return empty results
            return {
                "results": [],
                "count": 0,
                "last_scan": None,
            }
        
        # Parse JSON data
        if isinstance(cached_data, bytes):
            cached_data = cached_data.decode()
        
        top_pairs = json.loads(cached_data)
        
        if not isinstance(top_pairs, list):
            logger.warning(f"Invalid top_10_obvious data format: {type(top_pairs)}")
            return {
                "results": [],
                "count": 0,
                "last_scan": None,
            }
        
        # Convert to ScreenerSignal-like format for frontend compatibility
        results = []
        for pair_data in top_pairs:
            if not isinstance(pair_data, dict):
                continue
            
            symbol = pair_data.get("symbol")
            if not symbol:
                continue
            
            # Extract A+ scoring data
            score = pair_data.get("score")
            rvol = pair_data.get("rvol")
            market_cap = pair_data.get("market_cap")
            supply_ratio = pair_data.get("supply_ratio")
            spread_bps = pair_data.get("spread_bps")
            change_24h_pct = pair_data.get("change_24h_pct")
            
            # Build indicators dict with A+ scoring fields
            indicators = {
                "score": score,
                "rvol": rvol,
                "market_cap": market_cap,
                "supply_ratio": supply_ratio,
                "spread_bps": spread_bps,
                "change_24h_pct": change_24h_pct,
                "rvol_pct": rvol,  # Map rvol to rvol_pct for consistency
            }
            
            # Create result dict matching ScreenerSignal structure
            result = {
                "symbol": symbol,
                "signal_type": "NONE",  # A+ scoring doesn't generate signals, just rankings
                "signal_strength": (score * 100) if score else 0.0,  # Convert score (0-1) to strength (0-100)
                "indicators": indicators,
                "timestamp": pair_data.get("timestamp") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            
            results.append(result)
        
        # Results are already sorted by score descending (from backend)
        # Get last scan time from screener service
        service = get_screener_service()
        last_scan = service.get_last_scan_time()
        
        return {
            "results": results,
            "count": len(results),
            "last_scan": last_scan,
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse top_10_obvious data: {e}")
        return {
            "results": [],
            "count": 0,
            "last_scan": None,
        }
    except Exception as e:
        logger.error(f"Failed to fetch top_10_obvious: {e}", exc_info=True)
        return {
            "results": [],
            "count": 0,
            "last_scan": None,
        }


@router.get("/screener/unified")
async def get_unified_screener() -> dict:
    """
    Get unified screener results with all column groups (Pillars, Strategies, Active Trade).
    
    Two-stage processing:
    - Stage 1: Calculate Pillars for ALL symbols (lightweight)
    - Stage 2: Calculate Strategy columns ONLY for Score >= 0.70 (A+ and A grades)
    
    Returns:
        {
            "results": [
                {
                    "symbol": "BTC/USD",
                    "signal_type": "NONE",
                    "signal_strength": 0.0,
                    "indicators": {
                        # Pillars (all symbols)
                        "score": 0.85,
                        "grade": "A+",
                        "rvol": 5.2,
                        "market_cap": 850000000,
                        "supply_ratio": 0.95,
                        "spread_bps": 8.5,
                        "change_24h_pct": 2.3,
                        # Strategies (only if score >= 0.70, A+ and A grades)
                        "vwap_dist_pct": -2.5,
                        "hod_dist_pct": 1.2,
                        "htf_trend": "UP",
                        "signal_lead": "VWAP 92%",
                        # Active Trade (if position exists)
                        "status": "LIVE",
                        "entry_strategy": "vwap_meanreversion",
                        "current_pnl_pct": 1.2,
                        "time_minutes": 12,
                    },
                    "timestamp": "ISO timestamp"
                }
            ],
            "count": 100,
            "last_scan": "ISO timestamp"
        }
    """
    service = get_screener_service()
    tracker = get_position_tracker()
    client = get_redis_client()
    
    try:
        # Get all symbols with A+ scores
        aplus_scores = client.hgetall(APLUS_SCORES_KEY)

        results = []

        # Ensure symbols with active positions are always included in the screener,
        # even if they were dropped from aplus_scores (e.g. RVOL data gap during position hold).
        import asyncio  # noqa: E402
        active_positions = await asyncio.to_thread(tracker.get_all_positions)
        aplus_symbol_set = {
            (k.decode() if isinstance(k, bytes) else k) for k in aplus_scores.keys()
        }
        for pos in active_positions:
            if pos.symbol not in aplus_symbol_set and pos.quantity > 0:
                # Inject a minimal stub so the symbol appears with status=LIVE
                aplus_scores[pos.symbol] = json.dumps({
                    "score": 0.0,
                    "grade": "—",
                    "rvol": None,
                    "market_cap": None,
                    "supply_ratio": None,
                    "spread_bps": None,
                    "change_24h_pct": None,
                })

        # Process pairs in batches with yields to prevent timeout
        aplus_items = list(aplus_scores.items())
        BATCH_SIZE = 20  # Process 20 pairs at a time, then yield (reduced to prevent timeout)
        
        for i in range(0, len(aplus_items), BATCH_SIZE):
            batch = aplus_items[i:i + BATCH_SIZE]
            batch_results = []
            
            for symbol_bytes, score_data_json in batch:
                symbol = symbol_bytes.decode() if isinstance(symbol_bytes, bytes) else str(symbol_bytes)
                
                try:
                    # Parse A+ score data
                    if isinstance(score_data_json, bytes):
                        score_data_json = score_data_json.decode()
                    aplus_data = json.loads(score_data_json)
                    
                    # Handle score: convert None to 0.0 and ensure it's a float
                    score = aplus_data.get("score")
                    if score is None:
                        score = 0.0
                    else:
                        score = float(score)
                    grade = aplus_data.get("grade", "F")
                    
                    # Build base result with Pillars
                    rvol = aplus_data.get("rvol")
                    market_cap = aplus_data.get("market_cap")
                    supply_ratio = aplus_data.get("supply_ratio")
                    spread_bps = aplus_data.get("spread_bps")
                    change_24h_pct = aplus_data.get("change_24h_pct")
                    
                    # Get price: prefer cached value, fall back to live bid/ask mid-price
                    cached_price = aplus_data.get("price")
                    if cached_price is None:
                        try:
                            from backend.ingestor.symbols import get_symbol_price
                            cached_price = get_symbol_price(symbol)
                        except Exception:
                            pass

                    result = {
                        "symbol": symbol,
                        "signal_type": "NONE",
                        "signal_strength": 0.0,
                        "indicators": {
                            "score": score,
                            "grade": grade,
                            "rvol": rvol,  # Store as decimal (0.0-1.0)
                            "rvol_pct": rvol * 100 if rvol is not None else None,  # Also store as percentage for frontend
                            "market_cap": market_cap,
                            "supply_ratio": supply_ratio,
                            "spread_bps": spread_bps,
                            "change_24h_pct": change_24h_pct,
                            "pillars": aplus_data.get("pillars"),
                            "price": cached_price,
                        },
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    
                    # Stage 2: Calculate Strategy columns for Score >= 0.55 (B, A, A+ grades)
                    if score >= 0.55:
                        try:
                            # Fetch bars for strategy calculations using screener service's method
                            # This handles aggregation properly (tries direct bars, then aggregates from 5m/1m)
                            bars_15m = await service._get_recent_bars(symbol, 200, target_interval="15m")
                            bars_1h = await service._get_recent_bars(symbol, 250, target_interval="1h")
                            
                            # Calculate VWAP Distance
                            vwap_dist = calculate_vwap_distance(symbol, bars_15m)
                            if vwap_dist is not None:
                                result["indicators"]["vwap_dist_pct"] = vwap_dist
                            
                            # Calculate HOD Distance
                            hod_dist = calculate_hod_distance(symbol, bars_15m)
                            if hod_dist is not None:
                                result["indicators"]["hod_dist_pct"] = hod_dist
                            
                            # Calculate HTF Trend
                            htf_trend = calculate_htf_trend(symbol, bars_1h)
                            if htf_trend:
                                result["indicators"]["htf_trend"] = htf_trend
                            
                            # Get Signal Lead (highest confidence strategy) - offload blocking Redis call
                            signal_lead = await asyncio.to_thread(service._get_signal_lead, symbol)
                            if signal_lead:
                                result["indicators"]["signal_lead"] = signal_lead
                            else:
                                # Fallback: when no Redis strategy results, derive signal_lead from most extreme metric
                                # Only include candidates for ENABLED strategies (HOD Pullback excluded - no strategy)
                                ind = result.get("indicators", {})
                                vwap = ind.get("vwap_dist_pct")
                                htf = ind.get("htf_trend")
                                enabled_names = _get_enabled_strategy_display_names()
                                candidates = []
                                if vwap is not None and "VWAP Mean Reversion" in enabled_names:
                                    sig = "BUY" if vwap < 0 else "SELL"  # Below VWAP = buy, above = sell
                                    candidates.append(("VWAP Mean Reversion", min(100.0, abs(vwap) * 5), sig))
                                if htf in ("UP", "DOWN") and "HTF Trend Pullback" in enabled_names:
                                    sig = "BUY" if htf == "UP" else "SELL"
                                    candidates.append(("HTF Trend Pullback", 50.0, sig))
                                if candidates:
                                    best = max(candidates, key=lambda x: x[1])
                                    best_name, best_conf, best_sig = best[0], best[1], best[2]
                                    result["indicators"]["signal_lead"] = {
                                        "strategy_name": best_name,
                                        "confidence": best_conf,
                                        "signal_type": best_sig,
                                        "is_low_conviction": best_conf < 50.0,
                                        "meets_execution_threshold": False,
                                    }
                        except Exception as e:
                            logger.warning(f"Error calculating strategy columns for {symbol}: {e}")
                    
                    # Enrich with Position data — wrapped in its own try/except so
                    # a DB error here never causes the whole symbol to be dropped.
                    try:
                        position = await asyncio.to_thread(tracker.get_position, symbol)
                        if position and position.quantity > 0:
                            status = await asyncio.to_thread(tracker.get_position_status, symbol)
                            result["indicators"]["status"] = status

                            # Entry strategy — opened_by_strategy_id may be a name OR a UUID
                            if position.opened_by_strategy_id:
                                try:
                                    from backend.db import get_session
                                    from backend.db.models import Strategy, get_strategy_display_name
                                    sid = position.opened_by_strategy_id
                                    def _get_strategy_name(sid=sid):
                                        with get_session() as _s:
                                            # Try UUID lookup first
                                            try:
                                                import uuid as _uuid
                                                _uuid.UUID(sid)  # validate format
                                                row = _s.query(Strategy).filter(Strategy.id == sid).first()
                                            except (ValueError, Exception):
                                                row = None
                                            # Fall back to name lookup
                                            if row is None:
                                                row = _s.query(Strategy).filter(Strategy.name == sid).first()
                                            return get_strategy_display_name(row) if row else sid
                                    strategy_name = await asyncio.to_thread(_get_strategy_name)
                                    if strategy_name:
                                        result["indicators"]["entry_strategy"] = (
                                            f"{strategy_name} {int(position.opened_at_confidence or 0)}%"
                                        )
                                except Exception as _e:
                                    logger.debug(f"Could not resolve strategy name for {symbol}: {_e}")

                            if position.current_pnl_pct is not None:
                                result["indicators"]["current_pnl_pct"] = position.current_pnl_pct

                            if position.opened_at:
                                time_diff = datetime.now(timezone.utc) - position.opened_at
                                result["indicators"]["time_minutes"] = int(time_diff.total_seconds() / 60)
                        else:
                            status = await asyncio.to_thread(tracker.get_position_status, symbol)
                            result["indicators"]["status"] = status
                    except Exception as _pos_err:
                        logger.debug(f"Position enrichment failed for {symbol}: {_pos_err}")

                    batch_results.append(result)
                except Exception as e:
                    logger.debug(f"Error processing symbol {symbol} in unified screener: {e}")
                    continue
            
            # Add batch results to main results
            results.extend(batch_results)
            
            # Yield control to event loop after each batch to prevent timeout
            await asyncio.sleep(0)  # Allow other coroutines to run
            if (i // BATCH_SIZE + 1) % 2 == 0:  # Log progress every 2 batches
                logger.info(f"[UNIFIED_SCREENER] Processed {min(i + BATCH_SIZE, len(aplus_items))}/{len(aplus_items)} pairs")
                continue
        
        # Sort by score descending (default)
        results.sort(key=lambda x: x["indicators"].get("score", 0.0), reverse=True)

        # Safety net: ensure every active position symbol is present in results.
        # This guards against edge cases where APLUS_SCORES_KEY expired or the
        # A+ scoring scan ran without the symbol (e.g. race condition, symbol
        # temporarily absent from Kraken pairs list, TTL gap).
        result_symbols = {r["symbol"] for r in results}
        now_ts = datetime.now(timezone.utc).isoformat()
        for pos in active_positions:
            if pos.quantity <= 0 or pos.symbol in result_symbols:
                continue
            # Symbol has an active position but is not in results — inject it
            import asyncio as _asyncio
            status = await _asyncio.to_thread(tracker.get_position_status, pos.symbol)
            entry_price = getattr(pos, "entry_price", None)
            current_price = getattr(pos, "current_price", None)
            pnl_pct = None
            if entry_price and current_price and entry_price > 0:
                pnl_pct = ((current_price - entry_price) / entry_price) * 100.0
            time_minutes = None
            try:
                if pos.entry_time:
                    entry_dt = datetime.fromisoformat(
                        pos.entry_time.replace("Z", "+00:00")
                        if isinstance(pos.entry_time, str)
                        else pos.entry_time.isoformat()
                    )
                    time_minutes = int(
                        (datetime.now(timezone.utc) - entry_dt).total_seconds() / 60
                    )
            except Exception:
                pass
            injected = {
                "symbol": pos.symbol,
                "signal_type": "NONE",
                "signal_strength": 0.0,
                "indicators": {
                    "score": 0.0,
                    "grade": "—",
                    "rvol": None,
                    "rvol_pct": None,
                    "market_cap": None,
                    "supply_ratio": None,
                    "spread_bps": None,
                    "change_24h_pct": None,
                    "status": status,
                    "current_pnl_pct": pnl_pct,
                    "time_minutes": time_minutes,
                },
                "timestamp": now_ts,
            }
            # Pin to front of list so active positions are always visible
            results.insert(0, injected)
            result_symbols.add(pos.symbol)
            logger.info(
                f"[UNIFIED_SCREENER] Injected missing active position: {pos.symbol} (status={status})"
            )

        return {
            "results": results,
            "count": len(results),
            "last_scan": service.get_last_scan_time(),
        }
    except Exception as e:
        logger.error(f"Error in unified screener endpoint: {e}", exc_info=True)
        return {
            "results": [],
            "count": 0,
            "last_scan": service.get_last_scan_time(),
            "error": str(e),
        }


@router.get("/screener/criteria")
async def get_screener_criteria() -> dict:
    """
    Return the 3-stage 5-pillar screening criteria definitions.

    Used by the frontend (i) modal to explain how pairs are graded.
    Response is static — safe to cache client-side for the session.
    """
    return PIPELINE_CRITERIA


@router.get("/screener/status")
async def get_screener_status() -> dict:
    """
    Get screener scan progress and last completed scan time.

    Clients can poll this endpoint to check whether a scan is in progress
    without blocking on the full results payload.
    """
    service = get_screener_service()
    status = service.get_scan_status()
    last_scan = service.get_last_scan_time()

    return {
        "data": {
            "in_progress": status.get("in_progress", False),
            "scan_number": status.get("scan_number"),
            "stage": status.get("stage", "idle"),
            "progress": status.get("progress"),
            "started_at": status.get("started_at"),
            "completed_at": status.get("completed_at"),
            "elapsed_seconds": status.get("elapsed_seconds"),
            "last_scan": last_scan,
            "last_error": status.get("last_error"),
        }
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


@router.get("/screener/positions/realtime")
async def get_realtime_positions() -> dict:
    """
    Get real-time position updates for active positions only.
    
    Lightweight endpoint for 1-second polling. Returns only symbols with
    LIVE, PENDING, or EXITING status.
    
    Returns:
        {
            "positions": [
                {
                    "symbol": "BTC/USD",
                    "current_pnl_pct": 1.2,
                    "time_minutes": 12,
                    "status": "LIVE"
                }
            ],
            "timestamp": "ISO timestamp"
        }
    """
    tracker = get_position_tracker()
    
    try:
        all_positions = tracker.get_all_positions()
        active_positions = []
        
        for position in all_positions:
            if position.quantity <= 0:
                continue
            
            status = tracker.get_position_status(position.symbol)
            
            # Only return LIVE, PENDING, or EXITING positions
            if status not in ("LIVE", "PENDING", "EXITING"):
                continue
            
            pos_data = {
                "symbol": position.symbol,
                "status": status,
            }
            
            # Current PnL %
            if position.current_price and position.entry_price:
                if position.side == "long":
                    pnl_pct = ((position.current_price - position.entry_price) / position.entry_price) * 100.0
                else:  # short
                    pnl_pct = ((position.entry_price - position.current_price) / position.entry_price) * 100.0
                pos_data["current_pnl_pct"] = pnl_pct
            
            # Time Clock
            try:
                entry_time = datetime.fromisoformat(position.entry_time.replace('Z', '+00:00'))
                time_diff = datetime.now(timezone.utc) - entry_time
                time_minutes = int(time_diff.total_seconds() / 60)
                pos_data["time_minutes"] = time_minutes
            except (ValueError, TypeError):
                pass
            
            active_positions.append(pos_data)
        
        return {
            "positions": active_positions,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Error in realtime positions endpoint: {e}", exc_info=True)
        return {
            "positions": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error": str(e),
        }

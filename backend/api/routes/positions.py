"""Positions endpoint."""

import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from backend.api.models import PositionItem, PositionList
from backend.positions.tracker import get_position_tracker

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/positions", summary="List current positions")
async def list_positions():
    """
    List all current open positions.
    
    Returns a list of all positions currently held, including
    symbol, side, quantity, entry price, and unrealized P&L.
    Returns an empty list if no positions are open.
    """
    try:
        tracker = get_position_tracker()
        positions = tracker.get_all_positions()
        
        # Get strategy name mapping for display (use display name for UI consistency)
        from backend.db import get_session
        from backend.db.models import Strategy, get_strategy_display_name
        session = get_session()
        try:
            db_strategies = session.query(Strategy).all()
            uuid_to_name = {str(s.id): get_strategy_display_name(s) for s in db_strategies}
        finally:
            session.close()
        
        # Convert to response models
        # Filter out positions with zero or very small quantities (dust)
        # Use 0.01 as threshold to filter out essentially worthless positions
        MIN_POSITION_QUANTITY = 0.01  # Minimum quantity to consider a valid position
        
        position_items = []
        for pos in positions:
            # Skip positions with zero or very small quantities (dust)
            if pos.quantity < MIN_POSITION_QUANTITY:
                logger.debug(f"Skipping dust position: {pos.symbol} qty={pos.quantity}")
                continue
                
            strategy_id = pos.opened_by_strategy_id
            strategy_name = uuid_to_name.get(strategy_id) if strategy_id else None
            
            logger.debug(f"Position {pos.symbol}: strategy_id={strategy_id}, strategy_name={strategy_name}")
            
            item = PositionItem(
                symbol=pos.symbol,
                side=pos.side,
                quantity=pos.quantity,
                entry_price=pos.entry_price,
                entry_time=datetime.fromisoformat(pos.entry_time.replace('Z', '+00:00')),
                unrealized_pnl=pos.unrealized_pnl,
                current_price=getattr(pos, 'current_price', None),
                strategy_id=strategy_id,
                strategy_name=strategy_name,
            )
            
            # Verify the item has strategy fields
            item_dict = item.model_dump(exclude_none=False)
            logger.debug(f"PositionItem {pos.symbol} dict keys: {list(item_dict.keys())}, strategy_id={item_dict.get('strategy_id')}, strategy_name={item_dict.get('strategy_name')}")
            
            position_items.append(item)
        
        # Manually construct response dict to ensure strategy fields are included
        positions_data = []
        for item in position_items:
            pos_dict = {
                "symbol": item.symbol,
                "side": item.side,
                "quantity": item.quantity,
                "entry_price": item.entry_price,
                "entry_time": item.entry_time.isoformat() if isinstance(item.entry_time, datetime) else item.entry_time,
                "unrealized_pnl": item.unrealized_pnl,
                "current_price": getattr(item, 'current_price', None),
                "strategy_id": item.strategy_id,  # Include even if None
                "strategy_name": item.strategy_name,  # Include even if None
            }
            positions_data.append(pos_dict)
        
        response_dict = {"positions": positions_data}
        if positions_data:
            first_pos = positions_data[0]
            logger.info(f"First position keys: {list(first_pos.keys())}, strategy_id={first_pos.get('strategy_id')}, strategy_name={first_pos.get('strategy_name')}")
        
        # Use Response with explicit JSON to ensure None values are included
        from fastapi import Response
        import json as json_module
        try:
            json_str = json_module.dumps(response_dict, default=str)
            return Response(content=json_str, media_type="application/json", status_code=200)
        except Exception as json_err:
            logger.error(f"JSON serialization error: {json_err}", exc_info=True)
            # Fallback: return dict and let FastAPI handle it
            return response_dict
        
    except Exception as e:
        logger.error(f"Error fetching positions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error while fetching positions")


@router.post("/positions/sync", summary="Manually sync positions from Kraken")
async def sync_positions():
    """
    Manually trigger a sync of positions from Kraken.
    
    This will:
    - Fetch current balances from Kraken
    - Update local position tracking
    - Close positions that no longer exist on Kraken
    - Create positions for new holdings
    
    Useful after manual trades on Kraken to update the bot's position tracking.
    
    Returns:
        Dict with sync results: {created: int, updated: int, closed: int, errors: list}
    """
    try:
        tracker = get_position_tracker()
        result = await tracker.sync_from_kraken()
        logger.info(f"Manual position sync completed: {result}")
        return result
    except Exception as e:
        logger.error(f"Error syncing positions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to sync positions: {str(e)}")


@router.delete("/positions/{symbol:path}", summary="Manually close a position")
async def close_position(symbol: str):
    """
    Manually close a position by symbol.
    
    This will:
    - Remove the position from Redis
    - Record the closure in metrics (if strategy-owned)
    - Log the closure to activity feed
    
    Useful for:
    - Removing stuck shadow positions
    - Cleaning up positions that should have been closed
    - Manual position management
    
    Args:
        symbol: Trading pair symbol (e.g., "SCRT/USD")
    
    Returns:
        Dict with success status and message
    """
    try:
        tracker = get_position_tracker()
        position = tracker.get_position(symbol)
        
        if position is None:
            raise HTTPException(status_code=404, detail=f"Position {symbol} not found")
        
        # Get current price for metrics tracking
        current_price = position.current_price or position.entry_price
        
        # Close the position
        closed = tracker.close_position(symbol)
        
        if not closed:
            raise HTTPException(status_code=500, detail=f"Failed to close position {symbol}")
        
        # Record closure in metrics if strategy-owned
        if position.opened_by_strategy_id:
            try:
                from backend.risk.metrics import get_strategy_metrics
                metrics = get_strategy_metrics()
                
                # Calculate P&L
                if position.side == "long":
                    pnl = (current_price - position.entry_price) * position.quantity
                else:
                    pnl = (position.entry_price - current_price) * position.quantity
                
                # Record trade closure
                metrics.close_trade(
                    trade_id=symbol,
                    exit_price=current_price,
                    exit_reason="manual_close",
                    stop_loss_price=position.stop_loss_price,
                )
            except Exception as e:
                logger.warning(f"Failed to record position closure in metrics: {e}")
        
        # Log to activity feed
        from backend.api.routes.events import log_activity
        from backend.api.routes.trading import get_shadow_live_mode
        
        shadow_mode = get_shadow_live_mode()
        pnl_pct = ((current_price - position.entry_price) / position.entry_price) * 100.0 if position.side == "long" else ((position.entry_price - current_price) / position.entry_price) * 100.0
        
        log_activity(
            activity_type="EXIT_FORCED",
            message=f"Position manually closed: {symbol} - P&L: {pnl_pct:.1f}%",
            details={
                "symbol": symbol,
                "reason": "manual_close",
                "exit_price": current_price,
                "entry_price": position.entry_price,
                "pnl_pct": pnl_pct,
                "unrealized_pnl": position.unrealized_pnl,
                "strategy_id": position.opened_by_strategy_id,
                "mode": "shadow_live" if shadow_mode else "live",
            },
        )
        
        logger.info(f"Position {symbol} manually closed: P&L={pnl_pct:.1f}%")
        
        return {
            "success": True,
            "message": f"Position {symbol} closed successfully",
            "symbol": symbol,
            "exit_price": current_price,
            "pnl_pct": round(pnl_pct, 2),
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error closing position {symbol}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to close position: {str(e)}")

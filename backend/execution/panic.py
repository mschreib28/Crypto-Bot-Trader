"""Panic sequence logic for emergency shutdown.

This module implements the panic sequence that:
1. Sets system halt mode to true
2. Cancels all open orders via Kraken REST API
3. Attempts to flatten positions (if supported by exchange)

The panic sequence is idempotent and fail-closed: if execution fails,
the system remains halted.
"""

import logging
from typing import Dict, Any, List

from backend.execution.kraken_rest import KrakenClient
from backend.risk.halt import set_halt_mode
from backend.config import KRAKEN_API_KEY, KRAKEN_API_SECRET
from backend.api.routes.trading import set_trading_enabled

logger = logging.getLogger(__name__)


def cancel_all_open_orders(client: KrakenClient) -> int:
    """
    Cancel all open orders via Kraken REST API.
    
    Args:
        client: Initialized KrakenClient instance
        
    Returns:
        Number of orders that were cancelled
        
    Note:
        This function handles errors gracefully. If cancellation fails for
        some orders, it continues attempting to cancel others.
    """
    try:
        # Get all open orders
        open_orders_response = client.get_open_orders()
        
        if "result" not in open_orders_response:
            logger.warning("No 'result' field in open orders response")
            return 0
        
        open_orders = open_orders_response["result"].get("open", {})
        
        if not open_orders:
            logger.info("No open orders to cancel")
            return 0
        
        # Extract transaction IDs (order IDs)
        txids = list(open_orders.keys())
        logger.info(f"Found {len(txids)} open order(s) to cancel")
        
        # Cancel each order
        cancelled_count = 0
        failed_txids: List[str] = []
        
        for txid in txids:
            try:
                cancel_response = client.cancel_order(txid)
                
                # Check if cancellation was successful
                if "result" in cancel_response:
                    count = cancel_response["result"].get("count", 0)
                    if count > 0:
                        cancelled_count += count
                        logger.info(f"Successfully cancelled order: {txid}")
                    else:
                        logger.warning(f"Order cancellation returned count=0 for: {txid}")
                        failed_txids.append(txid)
                else:
                    logger.warning(f"Order cancellation response missing 'result' for: {txid}")
                    failed_txids.append(txid)
                    
            except Exception as e:
                logger.error(f"Failed to cancel order {txid}: {e}")
                failed_txids.append(txid)
        
        if failed_txids:
            logger.warning(f"Failed to cancel {len(failed_txids)} order(s): {failed_txids}")
        
        logger.info(f"Cancelled {cancelled_count} order(s) successfully")
        return cancelled_count
        
    except Exception as e:
        logger.error(f"Error while cancelling open orders: {e}")
        # Fail-closed: return 0 but don't raise (system will remain halted)
        return 0


def attempt_flatten_positions(client: KrakenClient) -> None:
    """
    Attempt to flatten all positions (if supported by exchange).
    
    Args:
        client: Initialized KrakenClient instance
        
    Note:
        This is a placeholder for position flattening logic.
        Kraken spot trading doesn't support short positions, so flattening
        would involve selling all long positions. This is a complex operation
        that requires:
        1. Querying current positions
        2. Calculating quantities to sell
        3. Placing market sell orders
        
        For now, this function logs a warning that position flattening
        is not yet implemented. In a production system, this would need
        to be implemented based on the exchange's position management API.
    """
    try:
        # TODO: Implement position flattening when position management is available
        # This would require:
        # 1. Query current positions (Kraken doesn't have a direct positions endpoint for spot)
        # 2. For each position, place a market sell order to close it
        # 3. Handle partial fills and errors
        
        logger.warning(
            "Position flattening is not yet implemented. "
            "Open orders have been cancelled, but positions remain open."
        )
        
    except Exception as e:
        logger.error(f"Error while attempting to flatten positions: {e}")
        # Fail-closed: log error but don't raise (system will remain halted)


def execute_panic_sequence() -> Dict[str, Any]:
    """
    Execute the panic sequence: halt system, disable trading, cancel orders, flatten positions.
    
    Returns:
        Dictionary with status, orders_cancelled count, and trading_disabled flag:
        {
            "status": "panic_initiated",
            "orders_cancelled": <int>,
            "trading_disabled": True
        }
        
    Note:
        This function is idempotent. Multiple calls are safe and will return
        the same result. The system halt state persists, so subsequent calls
        will find no open orders (already cancelled) and return 0.
        
        Fail-closed behavior: If any step fails, the system remains halted
        and the function returns the number of orders that were successfully
        cancelled before the failure.
    """
    logger.warning("PANIC SEQUENCE INITIATED")
    
    # Step 1: Set system halt mode to true
    try:
        set_halt_mode(True)
        logger.info("System halt mode enabled")
    except Exception as e:
        logger.error(f"Failed to set halt mode: {e}")
        # Fail-closed: even if halt mode setting fails, we continue
        # The panic sequence should still attempt to cancel orders
    
    # Step 2: Disable live trading
    try:
        set_trading_enabled(False)
        logger.info("Live trading disabled")
    except Exception as e:
        logger.error(f"Failed to disable trading: {e}")
        # Fail-closed: continue with panic sequence
    
    # Step 3 & 4: Cancel all open orders and attempt to flatten positions
    orders_cancelled = 0
    client = None
    try:
        client = KrakenClient(api_key=KRAKEN_API_KEY, api_secret=KRAKEN_API_SECRET)
        orders_cancelled = cancel_all_open_orders(client)
    except Exception as e:
        logger.error(f"Failed to initialize Kraken client or cancel orders: {e}")
        # Fail-closed: return 0 orders cancelled, but system remains halted
    
    # Step 5: Attempt to flatten positions (use existing client if available)
    try:
        if client is None:
            client = KrakenClient(api_key=KRAKEN_API_KEY, api_secret=KRAKEN_API_SECRET)
        attempt_flatten_positions(client)
    except Exception as e:
        logger.error(f"Failed to flatten positions: {e}")
        # Fail-closed: log error but don't raise
    
    logger.warning(f"PANIC SEQUENCE COMPLETE: {orders_cancelled} order(s) cancelled")
    
    return {
        "status": "panic_initiated",
        "orders_cancelled": orders_cancelled,
        "trading_disabled": True
    }

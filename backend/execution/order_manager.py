"""Order management and conversion logic.

Converts TradeIntents to Kraken order parameters and handles order execution.
"""

import logging
from typing import Dict, Any, Optional
from decimal import Decimal

from backend.execution.kraken_interface import KrakenClientInterface, KrakenOrderResponse

logger = logging.getLogger(__name__)


def convert_intent_to_order_params(
    symbol: str,
    side: str,
    intent_type: str,
    notional_risk_pct: float,
    current_price: Optional[float] = None,
    total_equity: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Convert a TradeIntent to Kraken order parameters.
    
    Args:
        symbol: Trading pair symbol (e.g., "BTC/USD")
        side: Order side ("buy" or "sell")
        intent_type: Intent type ("enter", "exit", "reduce")
        notional_risk_pct: Percentage of equity to risk
        current_price: Current market price (optional, for limit orders)
        total_equity: Total account equity (optional, for volume calculation)
        
    Returns:
        Dictionary with Kraken order parameters:
        - pair: Kraken trading pair format (e.g., "XBTUSD")
        - type: "buy" or "sell"
        - ordertype: "market" (default) or "limit"
        - volume: Order volume in base currency
        - price: Limit price (if ordertype is "limit")
        
    Notes:
        - For now, we use market orders by default
        - Volume calculation requires total_equity and current_price
        - If volume cannot be calculated, raises ValueError
    """
    # Convert symbol format: "BTC/USD" -> "XBTUSD" (Kraken format)
    kraken_pair = _convert_symbol_to_kraken_pair(symbol)
    
    # Determine order type (market orders for simplicity)
    ordertype = "market"
    
    # Calculate volume based on notional risk
    # Volume = (notional_risk_pct / 100) * total_equity / current_price
    volume = None
    if current_price is not None and total_equity is not None:
        risk_amount = (notional_risk_pct / 100.0) * total_equity
        volume = risk_amount / current_price
        logger.info(
            f"Calculated volume: {volume} for risk_pct={notional_risk_pct}%, "
            f"equity={total_equity}, price={current_price}"
        )
    else:
        # If we can't calculate volume, we'll need to get it from metadata
        # or use a default approach
        logger.warning(
            f"Cannot calculate volume: current_price={current_price}, "
            f"total_equity={total_equity}. Volume must be provided in metadata."
        )
    
    order_params = {
        "pair": kraken_pair,
        "type": side,
        "ordertype": ordertype,
    }
    
    if volume is not None:
        order_params["volume"] = str(volume)  # Kraken expects string format
    
    if ordertype == "limit" and current_price is not None:
        order_params["price"] = str(current_price)
    
    return order_params


def _convert_symbol_to_kraken_pair(symbol: str) -> str:
    """
    Convert standard symbol format to Kraken pair format.
    
    Args:
        symbol: Standard symbol (e.g., "BTC/USD", "ETH/USD")
        
    Returns:
        Kraken pair format (e.g., "XBTUSD", "ETHUSD")
        
    Notes:
        - BTC/USD -> XBTUSD (Kraken uses XBT for Bitcoin)
        - ETH/USD -> ETHUSD
        - Other pairs may need additional mapping
    """
    # Common mappings
    symbol_mappings = {
        "BTC/USD": "XBTUSD",
        "BTCUSD": "XBTUSD",
        "ETH/USD": "ETHUSD",
        "ETHUSD": "ETHUSD",
    }
    
    if symbol in symbol_mappings:
        return symbol_mappings[symbol]
    
    # Generic conversion: remove "/" and convert BTC -> XBT
    pair = symbol.replace("/", "").upper()
    if pair.startswith("BTC"):
        pair = pair.replace("BTC", "XBT", 1)
    
    logger.debug(f"Converted symbol {symbol} to Kraken pair {pair}")
    return pair


def classify_kraken_error(error_message: str) -> str:
    """
    Classify Kraken API errors into specific error types.
    
    TICKET-605: Enhanced error handling with specific error types.
    
    Args:
        error_message: Error message from Kraken API
        
    Returns:
        Error type: 'insufficient_funds', 'price_moved', 'below_costmin', 
                   'rate_limit', 'exchange_error', 'network_error', 'unknown_error'
    """
    error_lower = error_message.lower()
    
    # Check for insufficient funds
    if "insufficient funds" in error_lower or "eorder:insufficient funds" in error_lower:
        return "insufficient_funds"
    
    # Check for price moved
    if "price changed" in error_lower or "eorder:price changed" in error_lower or "price moved" in error_lower:
        return "price_moved"
    
    # Check for below costmin
    if "order minimum not met" in error_lower or "below_costmin" in error_lower or "eorder:order minimum" in error_lower:
        return "below_costmin"
    
    # Check for rate limit
    if "rate limit" in error_lower or "eapi:rate limit" in error_lower:
        return "rate_limit"
    
    # Check for exchange errors (5xx or invalid API)
    if "eapi:invalid" in error_lower or "eapi:" in error_lower:
        return "exchange_error"
    
    # Check for network errors
    if "connection" in error_lower or "timeout" in error_lower or "network" in error_lower:
        return "network_error"
    
    # Unknown error
    return "unknown_error"


def execute_order(
    client: KrakenClientInterface,
    order_params: Dict[str, Any],
) -> KrakenOrderResponse:
    """
    Execute an order on Kraken using the provided client.
    
    TICKET-605: Enhanced error handling with error classification.
    
    Args:
        client: Kraken REST API client (implements KrakenClientInterface)
        order_params: Order parameters from convert_intent_to_order_params
        
    Returns:
        KrakenOrderResponse with exchange_order_id and order details
        
    Raises:
        Exception: If order execution fails (with classified error type in message)
    """
    logger.info(f"Executing order with params: {order_params}")
    
    try:
        response = client.add_order(**order_params)
        
        if response.error:
            error_msg = ", ".join(response.error) if isinstance(response.error, list) else str(response.error)
            error_type = classify_kraken_error(error_msg)
            classified_error = f"{error_type}: {error_msg}"
            logger.error(f"Order execution failed: {classified_error}")
            raise Exception(classified_error)
        
        logger.info(f"Order executed successfully: txid={response.txid}")
        return response
        
    except Exception as e:
        # Classify error if not already classified
        error_str = str(e)
        if ":" not in error_str or error_str.split(":")[0] not in [
            "insufficient_funds", "price_moved", "below_costmin", 
            "rate_limit", "exchange_error", "network_error", "unknown_error"
        ]:
            error_type = classify_kraken_error(error_str)
            error_str = f"{error_type}: {error_str}"
        
        logger.error(f"Order execution failed: {error_str}")
        raise Exception(error_str)


def calculate_slippage(
    intended_price: Optional[float],
    executed_price: float,
    side: str,
) -> float:
    """
    Calculate slippage for an executed order.
    
    Args:
        intended_price: Intended execution price (None for market orders)
        executed_price: Actual execution price
        side: Order side ("buy" or "sell")
        
    Returns:
        Slippage amount (positive = worse execution)
        - For buys: slippage = executed_price - intended_price (if intended_price exists)
        - For sells: slippage = intended_price - executed_price (if intended_price exists)
        - For market orders without intended_price: returns 0.0
    """
    if intended_price is None:
        # Market order without intended price - cannot calculate slippage
        return 0.0
    
    if side == "buy":
        slippage = executed_price - intended_price
    else:  # sell
        slippage = intended_price - executed_price
    
    # Slippage is always positive (worse execution) or zero
    return max(0.0, slippage)

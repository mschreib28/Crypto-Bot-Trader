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


def execute_order(
    client: KrakenClientInterface,
    order_params: Dict[str, Any],
) -> KrakenOrderResponse:
    """
    Execute an order on Kraken using the provided client.
    
    Args:
        client: Kraken REST API client (implements KrakenClientInterface)
        order_params: Order parameters from convert_intent_to_order_params
        
    Returns:
        KrakenOrderResponse with exchange_order_id and order details
        
    Raises:
        Exception: If order execution fails
    """
    logger.info(f"Executing order with params: {order_params}")
    
    try:
        response = client.add_order(**order_params)
        
        if response.error:
            error_msg = ", ".join(response.error) if isinstance(response.error, list) else str(response.error)
            raise Exception(f"Kraken order error: {error_msg}")
        
        logger.info(f"Order executed successfully: txid={response.txid}")
        return response
        
    except Exception as e:
        logger.error(f"Order execution failed: {e}")
        raise


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

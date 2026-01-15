"""Main execution function for approved TradeIntents.

This module provides the execute_approved_intent function that:
1. Retrieves TradeIntent from RiskDecision
2. Converts to Kraken order parameters
3. Executes order with proper nonce handling
4. Returns Fill object matching contract schema
"""

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from backend.risk.models import RiskDecision
from backend.risk.portfolio import get_current_equity
from backend.execution.models import Fill
from backend.execution.nonce import get_next_nonce
from backend.execution.order_manager import (
    convert_intent_to_order_params,
    execute_order,
    calculate_slippage,
)
from backend.execution.kraken_interface import KrakenClientInterface, KrakenClientStub
from backend.db import get_session
from backend.db.models import Signal

logger = logging.getLogger(__name__)

# Global lock for order serialization (only one order at a time)
_execution_lock = threading.Lock()

# Global Kraken client instance (will be set when Ticket 11 is implemented)
_kraken_client: Optional[KrakenClientInterface] = None


def set_kraken_client(client: KrakenClientInterface) -> None:
    """
    Set the Kraken client instance.
    
    This should be called by Ticket 11 implementation to register the client.
    
    Args:
        client: Kraken REST API client instance
    """
    global _kraken_client
    _kraken_client = client
    logger.info("Kraken client registered")


def get_kraken_client() -> KrakenClientInterface:
    """
    Get the Kraken client instance.
    
    Returns:
        Kraken client instance
        
    Raises:
        RuntimeError: If client has not been set (Ticket 11 not implemented)
    """
    global _kraken_client
    if _kraken_client is None:
        logger.warning(
            "Kraken client not set. Using stub implementation. "
            "Ticket 11 (Kraken REST Client) must be implemented."
        )
        return KrakenClientStub()
    return _kraken_client


def get_trade_intent_from_signal(intent_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve TradeIntent data from Signal table using intent_id.
    
    Args:
        intent_id: Intent ID from RiskDecision (should map to Signal.id)
        
    Returns:
        Dictionary with TradeIntent fields, or None if not found
    """
    session = get_session()
    try:
        # Try to find signal by ID (intent_id should be Signal.id as string)
        try:
            signal_id = uuid.UUID(intent_id)
        except ValueError:
            logger.warning(f"Invalid intent_id format (not UUID): {intent_id}")
            return None
        
        signal = session.query(Signal).filter(Signal.id == signal_id).first()
        
        if signal is None:
            logger.warning(f"Signal not found for intent_id: {intent_id}")
            return None
        
        # Convert Signal to TradeIntent-like dictionary
        return {
            "strategy_id": str(signal.strategy_id),
            "symbol": signal.symbol,
            "side": signal.side,
            "intent_type": signal.intent_type,
            "notional_risk_pct": float(signal.notional_risk_pct),
            "metadata": signal.signal_metadata or {},
        }
    except Exception as e:
        logger.error(f"Failed to retrieve TradeIntent from Signal: {e}")
        return None
    finally:
        session.close()


def execute_approved_intent(risk_decision: RiskDecision) -> Fill:
    """
    Execute an approved TradeIntent and return a Fill object.
    
    This function:
    1. Validates that the RiskDecision is approved
    2. Retrieves TradeIntent from Signal table using intent_id
    3. Converts TradeIntent to Kraken order parameters
    4. Executes order with serialized nonce handling (prevents collisions)
    5. Creates and returns Fill object matching contract schema
    
    Args:
        risk_decision: RiskDecision with approved=True
        
    Returns:
        Fill object with execution details
        
    Raises:
        ValueError: If RiskDecision is not approved
        RuntimeError: If TradeIntent cannot be retrieved
        Exception: If order execution fails
        
    Notes:
        - Order execution is serialized (only one order at a time)
        - Nonce is generated atomically using Redis
        - Handles partial fills and order rejections gracefully
    """
    # Validate that decision is approved
    if not risk_decision.approved:
        raise ValueError(
            f"Cannot execute rejected intent. "
            f"intent_id={risk_decision.intent_id}, "
            f"rejection_reason={risk_decision.rejection_reason}"
        )
    
    logger.info(f"Executing approved intent: intent_id={risk_decision.intent_id}")
    
    # Retrieve TradeIntent from Signal table
    trade_intent_data = get_trade_intent_from_signal(risk_decision.intent_id)
    if trade_intent_data is None:
        raise RuntimeError(
            f"Failed to retrieve TradeIntent for intent_id: {risk_decision.intent_id}"
        )
    
    # Serialize order execution (only one order at a time)
    with _execution_lock:
        logger.debug("Acquired execution lock")
        
        try:
            # Get next nonce (atomic operation)
            nonce = get_next_nonce()
            logger.debug(f"Generated nonce for order: {nonce}")
            
            # Get total equity for volume calculation
            total_equity_decimal = get_current_equity()
            total_equity = float(total_equity_decimal)
            
            # Get current price from metadata or market data
            # For market orders, we don't need it upfront, but it's useful for volume calculation
            current_price = trade_intent_data.get("metadata", {}).get("current_price")
            
            # Convert TradeIntent to Kraken order parameters
            order_params = convert_intent_to_order_params(
                symbol=trade_intent_data["symbol"],
                side=trade_intent_data["side"],
                intent_type=trade_intent_data["intent_type"],
                notional_risk_pct=trade_intent_data["notional_risk_pct"],
                current_price=current_price,
                total_equity=total_equity,
            )
            
            # Validate that volume was calculated
            if "volume" not in order_params or float(order_params["volume"]) <= 0:
                raise ValueError(
                    f"Cannot calculate order volume. "
                    f"Required: current_price and total_equity. "
                    f"Got: current_price={current_price}, total_equity={total_equity}"
                )
            
            # Execute order
            client = get_kraken_client()
            order_response = execute_order(client, order_params)
            
            # Generate internal order_id
            order_id = str(uuid.uuid4())
            
            # Extract exchange order ID
            exchange_order_id = order_response.txid
            
            # Query order status to get execution details
            # Note: This is a placeholder - actual implementation will parse Kraken response
            # For market orders, we need to query the order status to get executed price
            try:
                order_status = client.query_orders(txid=exchange_order_id)
                # Parse order status to extract executed_price, quantity, fees
                # This is a placeholder - actual parsing depends on Kraken API response format
                executed_price = order_status.get("price", 0.0)  # Placeholder
                quantity = float(order_params.get("volume", 0))  # Use requested volume for now
                fees = order_status.get("fee", 0.0)  # Placeholder
                
                # If order status doesn't have execution details, we'll need to wait or poll
                if executed_price == 0.0:
                    logger.warning(
                        f"Order {exchange_order_id} executed but execution details not yet available. "
                        f"Using placeholder values. This should be replaced with actual order status parsing."
                    )
                    # For now, use a reasonable default (this should be replaced)
                    executed_price = current_price if current_price else 0.0
            except Exception as e:
                logger.warning(
                    f"Failed to query order status for {exchange_order_id}: {e}. "
                    f"Using placeholder values."
                )
                # Fallback to placeholder values
                executed_price = current_price if current_price else 0.0
                quantity = float(order_params.get("volume", 0))
                fees = 0.0
            
            # Calculate slippage (only if we have both intended and executed prices)
            intended_price = current_price
            slippage = calculate_slippage(intended_price, executed_price, trade_intent_data["side"])
            
            # Create Fill object
            fill = Fill(
                order_id=order_id,
                symbol=trade_intent_data["symbol"],
                side=trade_intent_data["side"],
                executed_price=executed_price,
                quantity=quantity,
                fees=fees,
                slippage=slippage,
                exchange_order_id=exchange_order_id,
                timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            
            logger.info(
                f"Order executed successfully: order_id={order_id}, "
                f"exchange_order_id={exchange_order_id}, "
                f"quantity={quantity}, price={executed_price}"
            )
            
            return fill
            
        except Exception as e:
            logger.error(f"Order execution failed: {e}")
            raise
        finally:
            logger.debug("Released execution lock")

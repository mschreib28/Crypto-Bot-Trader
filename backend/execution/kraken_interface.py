"""Minimal interface for Kraken REST API client.

This interface is designed to work with Ticket 11 (Kraken REST Client) implementation.
When Ticket 11 is implemented, it should provide a class that implements this interface.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class KrakenOrderResponse:
    """Response from Kraken AddOrder endpoint."""
    txid: str  # Transaction ID (exchange_order_id)
    descr: Dict[str, Any]  # Order description
    error: Optional[list] = None  # Error messages if order failed


class KrakenClientInterface(ABC):
    """
    Abstract interface for Kraken REST API client.
    
    This interface defines the methods needed by the Execution Engine.
    Ticket 11 should implement a concrete class that inherits from this interface.
    """
    
    @abstractmethod
    def add_order(
        self,
        pair: str,
        type: str,  # "buy" or "sell"
        ordertype: str,  # "market", "limit", etc.
        volume: float,
        **kwargs
    ) -> KrakenOrderResponse:
        """
        Place an order on Kraken.
        
        Args:
            pair: Trading pair (e.g., "XBTUSD" for BTC/USD)
            type: Order side ("buy" or "sell")
            ordertype: Order type ("market", "limit", etc.)
            volume: Order volume in base currency
            **kwargs: Additional order parameters
            
        Returns:
            KrakenOrderResponse with txid (exchange_order_id) and order details
            
        Raises:
            Exception: If order placement fails
        """
        pass
    
    @abstractmethod
    def cancel_order(self, txid: str) -> Dict[str, Any]:
        """
        Cancel an order on Kraken.
        
        Args:
            txid: Transaction ID of the order to cancel
            
        Returns:
            Response dictionary with cancellation status
            
        Raises:
            Exception: If cancellation fails
        """
        pass
    
    @abstractmethod
    def query_orders(self, txid: Optional[str] = None) -> Dict[str, Any]:
        """
        Query order status from Kraken.
        
        Args:
            txid: Optional transaction ID to query specific order
            
        Returns:
            Dictionary with order status information
            
        Raises:
            Exception: If query fails
        """
        pass


# Placeholder implementation for development/testing
# This will be replaced by the actual Ticket 11 implementation
class KrakenClientStub(KrakenClientInterface):
    """
    Stub implementation for development and testing.
    
    This class provides a minimal implementation that can be used
    until Ticket 11 (Kraken REST Client) is completed.
    """
    
    def add_order(
        self,
        pair: str,
        type: str,
        ordertype: str,
        volume: float,
        **kwargs
    ) -> KrakenOrderResponse:
        """Stub implementation - raises NotImplementedError."""
        raise NotImplementedError(
            "KrakenClientStub: Real implementation required. "
            "Ticket 11 (Kraken REST Client) must be implemented first."
        )
    
    def cancel_order(self, txid: str) -> Dict[str, Any]:
        """Stub implementation - raises NotImplementedError."""
        raise NotImplementedError(
            "KrakenClientStub: Real implementation required. "
            "Ticket 11 (Kraken REST Client) must be implemented first."
        )
    
    def query_orders(self, txid: Optional[str] = None) -> Dict[str, Any]:
        """Stub implementation - raises NotImplementedError."""
        raise NotImplementedError(
            "KrakenClientStub: Real implementation required. "
            "Ticket 11 (Kraken REST Client) must be implemented first."
        )

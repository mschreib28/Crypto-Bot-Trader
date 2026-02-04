"""Time-windowed OHLCV bar construction for market data aggregation."""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class BarBuilder:
    """Builds OHLCV bars from ticks within a time window."""
    
    def __init__(self, interval: str):
        """
        Initialize a bar builder for a specific interval.
        
        Args:
            interval: Time interval string ("4h" or "1d")
            
        Raises:
            ValueError: If interval is not supported
        """
        self.interval = interval.lower()
        self.interval_seconds = self._parse_interval(interval)
        
        # Current bar state
        self.current_bar: Optional[Dict] = None
        self.current_bar_start: Optional[datetime] = None
        
    def _parse_interval(self, interval: str) -> int:
        """Parse interval string to seconds."""
        interval = interval.lower()
        if interval == "1m":
            return 60  # 1 minute in seconds
        elif interval == "5m":
            return 5 * 60  # 5 minutes in seconds
        elif interval == "15m":
            return 15 * 60  # 15 minutes in seconds
        elif interval == "30m":
            return 30 * 60  # 30 minutes in seconds
        elif interval == "1h":
            return 3600  # 1 hour in seconds
        elif interval == "4h":
            return 4 * 3600  # 4 hours in seconds
        elif interval == "1d":
            return 24 * 3600  # 1 day in seconds
        else:
            raise ValueError(
                f"Unsupported interval: {interval}. "
                f"Supported: '1m', '5m', '15m', '30m', '1h', '4h', '1d'"
            )
    
    def _align_timestamp(self, timestamp: datetime) -> datetime:
        """
        Align timestamp to interval boundary.
        
        For 1M: aligns to minute boundary
        For 5M: aligns to 5-minute boundaries (00, 05, 10, ...)
        For 15M: aligns to 15-minute boundaries (00, 15, 30, 45)
        For 30M: aligns to 30-minute boundaries (00, 30)
        For 1H: aligns to hour boundary
        For 4H: aligns to 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC
        For 1D: aligns to 00:00 UTC
        """
        if self.interval == "1m":
            # Align to minute boundary
            return timestamp.replace(second=0, microsecond=0)
        elif self.interval == "5m":
            # Align to 5-minute boundaries
            minute = timestamp.minute
            aligned_minute = (minute // 5) * 5
            return timestamp.replace(minute=aligned_minute, second=0, microsecond=0)
        elif self.interval == "15m":
            # Align to 15-minute boundaries
            minute = timestamp.minute
            aligned_minute = (minute // 15) * 15
            return timestamp.replace(minute=aligned_minute, second=0, microsecond=0)
        elif self.interval == "30m":
            # Align to 30-minute boundaries
            minute = timestamp.minute
            aligned_minute = (minute // 30) * 30
            return timestamp.replace(minute=aligned_minute, second=0, microsecond=0)
        elif self.interval == "1h":
            # Align to hour boundary
            return timestamp.replace(minute=0, second=0, microsecond=0)
        elif self.interval == "4h":
            # Align to 4-hour boundaries: 00:00, 04:00, 08:00, 12:00, 16:00, 20:00
            hour = timestamp.hour
            aligned_hour = (hour // 4) * 4
            return timestamp.replace(hour=aligned_hour, minute=0, second=0, microsecond=0)
        elif self.interval == "1d":
            # Align to midnight UTC
            return timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            raise ValueError(f"Unsupported interval: {self.interval}")
    
    def _get_bar_start(self, timestamp: datetime) -> datetime:
        """Get the start timestamp for the bar containing this timestamp."""
        return self._align_timestamp(timestamp)
    
    def add_tick(self, price: float, volume: float, timestamp: datetime, symbol: str) -> Optional[Dict]:
        """
        Add a tick to the current bar. Returns a completed bar if the tick starts a new bar.
        
        Args:
            price: Tick price
            volume: Tick volume
            timestamp: Tick timestamp (must be timezone-aware UTC)
            symbol: Trading pair symbol
            
        Returns:
            Completed bar dict if a new bar started, None otherwise
            
        Raises:
            ValueError: If timestamp is not timezone-aware or not UTC
        """
        if timestamp.tzinfo is None:
            raise ValueError("Timestamp must be timezone-aware")
        if timestamp.tzinfo != timezone.utc:
            # Convert to UTC if needed
            timestamp = timestamp.astimezone(timezone.utc)
        
        bar_start = self._get_bar_start(timestamp)
        
        # Check if we need to start a new bar
        completed_bar = None
        if self.current_bar is None or bar_start != self.current_bar_start:
            # Save the previous bar if it exists
            if self.current_bar is not None:
                completed_bar = self.current_bar.copy()
            
            # Start a new bar
            self.current_bar = {
                "symbol": symbol,
                "interval": self.interval,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
                "timestamp": bar_start.isoformat().replace("+00:00", "Z"),
            }
            self.current_bar_start = bar_start
        else:
            # Update existing bar
            self.current_bar["high"] = max(self.current_bar["high"], price)
            self.current_bar["low"] = min(self.current_bar["low"], price)
            self.current_bar["close"] = price
            self.current_bar["volume"] += volume
        
        return completed_bar
    
    def get_current_bar(self) -> Optional[Dict]:
        """Get the current incomplete bar (if any)."""
        return self.current_bar.copy() if self.current_bar else None
    
    def flush_bar(self) -> Optional[Dict]:
        """
        Flush the current bar and return it. Useful for closing bars at end of day.
        
        Returns:
            Current bar dict if exists, None otherwise
        """
        if self.current_bar is None:
            return None
        
        completed_bar = self.current_bar.copy()
        self.current_bar = None
        self.current_bar_start = None
        return completed_bar

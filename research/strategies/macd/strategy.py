"""MACD Crossover strategy implementation for cryptocurrency trading.

This strategy generates trading signals based on MACD (Moving Average Convergence
Divergence) crossovers. Optimized for 24/7 crypto markets where continuous price
action makes momentum indicators particularly effective.

MACD Components:
- MACD Line = EMA(fast_period) - EMA(slow_period)
- Signal Line = EMA(signal_period) of MACD Line
- Histogram = MACD Line - Signal Line

A+ Setup Criteria (weighted confidence scoring):
- MACD crossover detected (trigger)
- Histogram expanding (momentum confirmation)
- Price above/below EMA(50) (trend alignment)
- ADX > 20 (trending market)
- Volume > 1.5x average (volume confirmation)

Signals:
- BUY: MACD crosses ABOVE Signal Line (histogram: negative -> positive)
- SELL: MACD crosses BELOW Signal Line (histogram: positive -> negative)
"""

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from research.strategies.base import BaseStrategy
from research.strategies.indicators import (
    calculate_adx,
    calculate_ema,
    calculate_volume_ratio,
)
from research.strategies.macd.config import MACDConfig
from research.strategies.types import MarketDataEvent, SignalResult, TradeIntent

logger = logging.getLogger(__name__)


class MACDStrategy(BaseStrategy):
    """
    MACD Crossover trading strategy for cryptocurrency markets.
    
    Generates signals based on MACD/Signal line crossovers. The strategy
    identifies momentum shifts by detecting when the MACD line crosses
    the signal line.
    
    Crypto-specific design:
    - No session gap handling needed (24/7 markets)
    - Works well with high-volatility assets
    - EMA-based (responsive to recent price action)
    
    Constraints (from MSSD):
    - In-memory indicator state only (no persistence)
    - notional_risk_pct configurable (default: 2.0%)
    """
    
    def __init__(self, config: Optional[MACDConfig] = None):
        """
        Initialize the MACDStrategy.
        
        Args:
            config: MACDConfig instance with strategy parameters.
                   If None, uses default configuration.
        """
        if config is None:
            config = MACDConfig()
        
        super().__init__(strategy_id=config.strategy_id)
        self.config = config
        
        # Validate config: fast_period must be less than slow_period
        if config.fast_period >= config.slow_period:
            raise ValueError(
                f"fast_period ({config.fast_period}) must be less than "
                f"slow_period ({config.slow_period})"
            )
        
        # In-memory state: rolling window of closing prices
        # Need enough data for slow EMA + signal EMA warm-up
        self._min_periods = config.slow_period + config.signal_period
        self._price_window: deque[float] = deque(maxlen=self._min_periods * 2)
        
        # Track previous histogram value for crossover detection
        self._prev_histogram: Optional[float] = None
        
        logger.info(
            f"Initialized MACDStrategy: "
            f"fast={config.fast_period}, slow={config.slow_period}, "
            f"signal={config.signal_period}, risk_pct={config.notional_risk_pct}%"
        )
    
    def _calculate_ema(self, prices: List[float], period: int) -> List[float]:
        """
        Calculate Exponential Moving Average using pure Python.
        
        EMA multiplier = 2 / (period + 1)
        EMA(t) = price(t) * multiplier + EMA(t-1) * (1 - multiplier)
        
        Args:
            prices: List of prices (oldest to newest)
            period: EMA period
            
        Returns:
            List of EMA values (same length as prices, early values use SMA seed)
        """
        if len(prices) < period:
            return []
        
        multiplier = 2.0 / (period + 1)
        ema = [0.0] * len(prices)
        
        # Seed EMA with SMA of first 'period' values
        ema[period - 1] = sum(prices[:period]) / period
        
        # Calculate EMA for remaining values
        for i in range(period, len(prices)):
            ema[i] = prices[i] * multiplier + ema[i - 1] * (1 - multiplier)
        
        return ema
    
    def _calculate_macd(
        self, prices: List[float]
    ) -> Optional[Dict[str, Any]]:
        """
        Calculate MACD, Signal line, and Histogram.
        
        Args:
            prices: List of closing prices (oldest to newest)
            
        Returns:
            Dict with 'macd', 'signal', 'histogram' arrays and current values,
            or None if insufficient data
        """
        # Need at least slow_period + signal_period for valid MACD
        if len(prices) < self._min_periods:
            return None
        
        # Calculate fast and slow EMAs
        fast_ema = self._calculate_ema(prices, self.config.fast_period)
        slow_ema = self._calculate_ema(prices, self.config.slow_period)
        
        if not fast_ema or not slow_ema:
            return None
        
        # MACD Line = Fast EMA - Slow EMA
        # Valid only from slow_period onwards
        macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]
        
        # Signal Line = EMA of MACD Line
        # Use only valid MACD values (from slow_period - 1 onwards)
        valid_start = self.config.slow_period - 1
        macd_for_signal = macd_line[valid_start:]
        
        if len(macd_for_signal) < self.config.signal_period:
            return None
        
        signal_line = self._calculate_ema(macd_for_signal, self.config.signal_period)
        
        if not signal_line:
            return None
        
        # Histogram = MACD - Signal
        histogram = [m - s for m, s in zip(macd_for_signal, signal_line)]
        
        # Get current values (last valid values)
        signal_valid_start = self.config.signal_period - 1
        
        if signal_valid_start >= len(histogram):
            return None
        
        return {
            "macd": macd_for_signal[-1],
            "signal": signal_line[-1],
            "histogram": histogram[-1],
            "prev_histogram": histogram[-2] if len(histogram) > 1 else None,
        }
    
    def generate_signals(self, bar: MarketDataEvent) -> Optional[TradeIntent]:
        """
        Generate trading signals from market data.
        
        Implements MACD crossover logic:
        - BUY: Histogram crosses from negative to positive (bullish crossover)
        - SELL: Histogram crosses from positive to negative (bearish crossover)
        
        Args:
            bar: MarketDataEvent containing OHLCV data for the current bar
            
        Returns:
            TradeIntent if a signal is generated, None otherwise
        """
        # Validate symbol matches expected
        if bar.symbol != self.config.symbol:
            logger.debug(
                f"Ignoring bar for symbol {bar.symbol}, expected {self.config.symbol}"
            )
            return None
        
        # Update price window with current bar's close price
        self._price_window.append(bar.close)
        
        # Calculate MACD indicators
        macd_data = self._calculate_macd(list(self._price_window))
        
        if macd_data is None:
            logger.debug("Insufficient data for MACD calculation")
            return None
        
        current_histogram = macd_data["histogram"]
        prev_histogram = self._prev_histogram
        
        # Update previous histogram for next iteration
        self._prev_histogram = current_histogram
        
        # Need previous histogram to detect crossover
        if prev_histogram is None:
            logger.debug("Waiting for previous histogram value")
            return None
        
        # Detect crossover
        signal_side = None
        intent_type = "enter"
        
        # Bullish crossover: histogram goes from negative to positive
        if prev_histogram <= 0 and current_histogram > 0:
            signal_side = "buy"
            logger.info(
                f"Bullish MACD crossover: histogram {prev_histogram:.4f} -> {current_histogram:.4f}"
            )
        
        # Bearish crossover: histogram goes from positive to negative
        elif prev_histogram >= 0 and current_histogram < 0:
            signal_side = "sell"
            logger.info(
                f"Bearish MACD crossover: histogram {prev_histogram:.4f} -> {current_histogram:.4f}"
            )
        else:
            logger.debug(
                f"No crossover: histogram {prev_histogram:.4f} -> {current_histogram:.4f}"
            )
            return None
        
        # Create TradeIntent with indicator values in metadata
        intent = TradeIntent(
            strategy_id=self.strategy_id,
            symbol=self.config.symbol,
            side=signal_side,
            intent_type=intent_type,
            notional_risk_pct=self.config.notional_risk_pct,
            metadata={
                "macd": round(macd_data["macd"], 6),
                "signal": round(macd_data["signal"], 6),
                "histogram": round(current_histogram, 6),
                "prev_histogram": round(prev_histogram, 6),
                "fast_period": self.config.fast_period,
                "slow_period": self.config.slow_period,
                "signal_period": self.config.signal_period,
                "current_price": bar.close,
                "bar_timestamp": bar.timestamp,
                "interval": bar.interval,
            },
        )
        
        return intent
    
    def _calculate_macd_from_closes(
        self, closes: List[float]
    ) -> Optional[Dict[str, Any]]:
        """
        Calculate MACD values from a list of closing prices.
        
        Args:
            closes: List of closing prices (oldest to newest)
            
        Returns:
            Dict with MACD values or None if insufficient data
        """
        return self._calculate_macd(closes)
    
    def evaluate(self, symbol: str, bars: List[MarketDataEvent]) -> SignalResult:
        """
        Evaluate MACD strategy for any symbol with A+ setup detection.
        
        Confidence scoring (weighted factors):
        - Crossover detected: 25% weight (trigger condition)
        - Histogram expanding: 15% weight (momentum confirmation)
        - EMA(50) trend alignment: 25% weight (trend filter)
        - ADX > 20: 20% weight (trending market)
        - Volume > 1.5x average: 15% weight (volume confirmation)
        
        Direction indicates which way the market is leaning (bullish/bearish).
        Signal is triggered when crossover is detected (confidence filtering done by screener).
        
        Args:
            symbol: The trading pair symbol (e.g., "SOL/USD")
            bars: List of OHLCV bars for that symbol
            
        Returns:
            SignalResult with signal_type, confidence, and indicators
        """
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        # Need minimum bars for calculations (ADX needs ~28 bars, EMA50 needs 50)
        min_required = max(self._min_periods, 50)
        if len(bars) < min_required:
            return SignalResult(
                symbol=symbol,
                signal_type="NONE",
                confidence=0.0,
                strategy_id=self.config.strategy_id,
                indicators={
                    "error": "insufficient_data",
                    "bars_available": len(bars),
                    "bars_required": min_required,
                    "direction": "neutral",
                },
                timestamp=timestamp,
            )
        
        # Extract OHLCV data (handle both dict and object formats)
        def get_bar_value(bar, key):
            if isinstance(bar, dict):
                return float(bar.get(key, 0))
            return float(getattr(bar, key, 0))
        
        closes = [get_bar_value(bar, 'close') for bar in bars]
        highs = [get_bar_value(bar, 'high') for bar in bars]
        lows = [get_bar_value(bar, 'low') for bar in bars]
        volumes = [get_bar_value(bar, 'volume') for bar in bars]
        current_price = closes[-1]
        
        # Calculate MACD
        macd_data = self._calculate_macd_from_closes(closes)
        
        if macd_data is None:
            return SignalResult(
                symbol=symbol,
                signal_type="NONE",
                confidence=0.0,
                strategy_id=self.config.strategy_id,
                indicators={"error": "macd_calculation_failed", "direction": "neutral"},
                timestamp=timestamp,
            )
        
        current_histogram = macd_data["histogram"]
        prev_histogram = macd_data["prev_histogram"]
        
        # Determine direction based on histogram sign
        direction = "bullish" if current_histogram >= 0 else "bearish"
        
        # ============================================================
        # A+ SETUP CONFIDENCE SCORING (Weighted Components)
        # ============================================================
        
        # 1. CROSSOVER DETECTION (25% weight, max 25 points)
        crossover_detected = False
        crossover_score = 0.0
        
        if prev_histogram is not None:
            # Bullish crossover
            if prev_histogram <= 0 and current_histogram > 0:
                crossover_detected = True
                crossover_score = 25.0
            # Bearish crossover
            elif prev_histogram >= 0 and current_histogram < 0:
                crossover_detected = True
                crossover_score = 25.0
        
        # 2. HISTOGRAM EXPANSION (15% weight, max 15 points)
        # Histogram should be expanding (momentum increasing)
        histogram_expanding = False
        histogram_score = 0.0
        
        if prev_histogram is not None:
            # For bullish: current should be more positive than previous
            # For bearish: current should be more negative than previous
            if direction == "bullish" and current_histogram > prev_histogram:
                histogram_expanding = True
                histogram_score = 15.0
            elif direction == "bearish" and current_histogram < prev_histogram:
                histogram_expanding = True
                histogram_score = 15.0
            else:
                # Partial credit if histogram magnitude is increasing
                if abs(current_histogram) > abs(prev_histogram):
                    histogram_score = 7.5
        
        # 3. EMA(50) TREND ALIGNMENT (25% weight, max 25 points)
        ema_50 = calculate_ema(closes, self.config.ema_trend_period)
        trend_aligned = False
        trend_score = 0.0
        
        if ema_50 is not None:
            if direction == "bullish" and current_price > ema_50:
                trend_aligned = True
                trend_score = 25.0
            elif direction == "bearish" and current_price < ema_50:
                trend_aligned = True
                trend_score = 25.0
            else:
                # Partial credit based on proximity to EMA
                distance_pct = abs(current_price - ema_50) / ema_50 * 100
                if distance_pct < 1.0:  # Within 1% of EMA
                    trend_score = 12.5
        
        # 4. ADX TREND STRENGTH (20% weight, max 20 points)
        adx = calculate_adx(highs, lows, closes, period=14)
        adx_trending = False
        adx_score = 0.0
        
        if adx is not None:
            if adx >= self.config.adx_threshold:
                adx_trending = True
                # Scale score: ADX 20=15pts, ADX 25+=20pts
                adx_score = min(20.0, 15.0 + (adx - self.config.adx_threshold) * 1.0)
            else:
                # Partial credit if ADX is close to threshold
                if adx >= self.config.adx_threshold - 5:
                    adx_score = 10.0 * (adx / self.config.adx_threshold)
        
        # 5. VOLUME CONFIRMATION (15% weight, max 15 points)
        volume_ratio = calculate_volume_ratio(volumes, period=20)
        volume_confirmed = False
        volume_score = 0.0
        
        if volume_ratio is not None:
            if volume_ratio >= self.config.volume_threshold:
                volume_confirmed = True
                # Scale score: 1.5x=15pts, higher=bonus capped at 15
                volume_score = min(15.0, 10.0 + (volume_ratio - 1.0) * 5.0)
            else:
                # Partial credit for above-average volume
                if volume_ratio >= 1.0:
                    volume_score = 7.5 * volume_ratio
        
        # ============================================================
        # TOTAL CONFIDENCE
        # ============================================================
        confidence = crossover_score + histogram_score + trend_score + adx_score + volume_score
        confidence = min(100.0, confidence)
        
        # Cap confidence at 50% if no crossover detected
        # Crossover is the primary trigger - without it, we're just seeing trend alignment
        if not crossover_detected:
            confidence = min(50.0, confidence)
        
        # Determine signal type: trigger if crossover detected
        # (confidence filtering is handled by _apply_confidence_threshold in screener)
        signal_type = "NONE"
        if crossover_detected:
            signal_type = "BUY" if direction == "bullish" else "SELL"
        
        return SignalResult(
            symbol=symbol,
            signal_type=signal_type,
            confidence=round(confidence, 2),
            strategy_id=self.config.strategy_id,
            indicators={
                "direction": direction,
                # MACD indicators
                "macd": round(macd_data["macd"], 6),
                "signal": round(macd_data["signal"], 6),
                "histogram": round(current_histogram, 6),
                "prev_histogram": round(prev_histogram, 6) if prev_histogram else None,
                # A+ criteria status
                "crossover_detected": crossover_detected,
                "histogram_expanding": histogram_expanding,
                "trend_aligned": trend_aligned,
                "adx_trending": adx_trending,
                "volume_confirmed": volume_confirmed,
                # Indicator values
                "ema_50": round(ema_50, 2) if ema_50 else None,
                "adx": round(adx, 2) if adx else None,
                "volume_ratio": round(volume_ratio, 2) if volume_ratio else None,
                # Score breakdown
                "score_crossover": round(crossover_score, 1),
                "score_histogram": round(histogram_score, 1),
                "score_trend": round(trend_score, 1),
                "score_adx": round(adx_score, 1),
                "score_volume": round(volume_score, 1),
                # Config
                "fast_period": self.config.fast_period,
                "slow_period": self.config.slow_period,
                "signal_period": self.config.signal_period,
                "current_price": current_price,
            },
            timestamp=timestamp,
        )

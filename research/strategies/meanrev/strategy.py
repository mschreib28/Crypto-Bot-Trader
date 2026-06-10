"""Mean-reversion trading strategy implementation.

This strategy uses Bollinger Bands and RSI to identify oversold and overbought conditions
for mean-reversion trading on ETH/USD.

A+ Setup Criteria (weighted confidence scoring):
- RSI extremes vs MeanReversionConfig thresholds (oversold / overbought)
- Price at outer Bollinger Bands (< 10% or > 90% position)
- ADX below adx_max_threshold (ranging market — CRITICAL for mean reversion)
- ATR ratio vs atr_min_ratio (market active, not dead)

Mean reversion FAILS in trending markets. The ADX filter is essential.
"""

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Deque, List, Optional

from research.strategies.base import BaseStrategy
from research.strategies.indicators import (
    calculate_adx,
    calculate_atr,
    calculate_atr_ratio,
)
from research.strategies.meanrev.config import MeanReversionConfig
from research.strategies.types import MarketDataEvent, SignalResult, TradeIntent

logger = logging.getLogger(__name__)


class MeanReversionStrategy(BaseStrategy):
    """
    Mean-reversion strategy for ETH/USD trading.
    
    Generates signals based on:
    - Bollinger Band deviation (price touching lower/upper bands)
    - RSI extremes (oversold/overbought conditions)
    
    Signals:
    - Buy: Price near lower Bollinger Band AND RSI < oversold_threshold
    - Sell: Price near upper Bollinger Band AND RSI > overbought_threshold
    
    Constraints (from MSSD § 4.2.2):
    - Uses in-memory indicator state only (no persistence)
    - Does not track positions or account balances
    - Does not submit or cancel orders
    """
    
    def __init__(self, config: Optional[MeanReversionConfig] = None):
        """
        Initialize the mean-reversion strategy.
        
        Args:
            config: Configuration object. If None, uses default MeanReversionConfig.
        """
        if config is None:
            config = MeanReversionConfig()
        
        super().__init__(config.strategy_id)
        self.config = config
        
        # In-memory indicator state (rolling windows)
        # Price history for moving average and Bollinger Bands
        self._price_history: Deque[float] = deque(maxlen=config.lookback_period)
        
        # Price change history for RSI calculation
        self._price_changes: Deque[float] = deque(maxlen=config.rsi_period)
        
        # Rolling window of bars for ATR + ADX calculation
        # ATR needs atr_period + 1 bars; ADX(14) needs at least 28 — keep 60 for
        # a stable smoothed ADX value.
        self._bars: Deque[MarketDataEvent] = deque(
            maxlen=max(config.atr_period + 1, config.lookback_period + 10, 60)
        )
        
        logger.info(
            f"Initialized MeanReversionStrategy: "
            f"symbol={config.symbol}, lookback={config.lookback_period}, "
            f"rsi_period={config.rsi_period}, risk_pct={config.notional_risk_pct}"
        )
    
    def _calculate_sma(self, prices: Deque[float]) -> Optional[float]:
        """
        Calculate Simple Moving Average.
        
        Args:
            prices: Deque of price values
            
        Returns:
            SMA value or None if insufficient data
        """
        if len(prices) < self.config.lookback_period:
            return None
        
        return sum(prices) / len(prices)
    
    def _calculate_std_dev(self, prices: Deque[float], mean: float) -> Optional[float]:
        """
        Calculate standard deviation.
        
        Args:
            prices: Deque of price values
            mean: Mean value (SMA)
            
        Returns:
            Standard deviation or None if insufficient data
        """
        if len(prices) < self.config.lookback_period:
            return None
        
        variance = sum((p - mean) ** 2 for p in prices) / len(prices)
        return variance ** 0.5
    
    def _calculate_bollinger_bands(
        self, current_price: float
    ) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """
        Calculate Bollinger Bands (upper, middle, lower).
        
        Args:
            current_price: Current closing price
            
        Returns:
            Tuple of (upper_band, middle_band, lower_band) or (None, None, None) if insufficient data
        """
        # Add current price to history
        self._price_history.append(current_price)
        
        if len(self._price_history) < self.config.lookback_period:
            return (None, None, None)
        
        # Calculate SMA (middle band)
        sma = self._calculate_sma(self._price_history)
        if sma is None:
            return (None, None, None)
        
        # Calculate standard deviation
        std_dev = self._calculate_std_dev(self._price_history, sma)
        if std_dev is None:
            return (None, None, None)
        
        # Calculate bands
        middle_band = sma
        upper_band = sma + (self.config.std_dev_multiplier * std_dev)
        lower_band = sma - (self.config.std_dev_multiplier * std_dev)
        
        return (upper_band, middle_band, lower_band)
    
    def _calculate_rsi(self, current_price: float, previous_price: Optional[float]) -> Optional[float]:
        """
        Calculate Relative Strength Index (RSI).
        
        Args:
            current_price: Current closing price
            previous_price: Previous closing price (None for first bar)
            
        Returns:
            RSI value (0-100) or None if insufficient data
        """
        if previous_price is not None:
            price_change = current_price - previous_price
            self._price_changes.append(price_change)
        
        if len(self._price_changes) < self.config.rsi_period:
            return None
        
        # Separate gains and losses
        gains = [change for change in self._price_changes if change > 0]
        losses = [-change for change in self._price_changes if change < 0]
        
        # Calculate average gain and average loss
        avg_gain = sum(gains) / self.config.rsi_period if gains else 0.0
        avg_loss = sum(losses) / self.config.rsi_period if losses else 0.0
        
        # Avoid division by zero
        if avg_loss == 0.0:
            return 100.0 if avg_gain > 0 else 50.0
        
        # Calculate RS and RSI
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        
        return rsi
    
    def generate_signals(self, bar: MarketDataEvent) -> Optional[TradeIntent]:
        """
        Generate trading signals from market data.
        
        Implements mean-reversion logic:
        - Buy signal: Price near lower Bollinger Band AND RSI < oversold_threshold
        - Sell signal: Price near upper Bollinger Band AND RSI > overbought_threshold
        
        Args:
            bar: MarketDataEvent containing OHLCV data for the current bar
            
        Returns:
            TradeIntent if a signal is generated, None otherwise
        """
        # Validate symbol matches configuration
        if bar.symbol != self.config.symbol:
            logger.debug(
                f"Ignoring bar for symbol {bar.symbol} "
                f"(expected {self.config.symbol})"
            )
            return None
        
        # Update bars window for ATR calculation
        self._bars.append(bar)
        
        # Store previous price for RSI calculation
        previous_price = self._price_history[-1] if self._price_history else None
        
        # Calculate Bollinger Bands
        upper_band, middle_band, lower_band = self._calculate_bollinger_bands(bar.close)
        
        # Calculate RSI
        rsi = self._calculate_rsi(bar.close, previous_price)
        
        # Need both indicators to generate signals
        if upper_band is None or middle_band is None or lower_band is None:
            logger.debug("Insufficient data for Bollinger Bands")
            return None
        
        if rsi is None:
            logger.debug("Insufficient data for RSI")
            return None
        
        # Calculate position relative to Bollinger Bands
        # Band position: -1.0 (at lower band) to +1.0 (at upper band)
        band_range = upper_band - lower_band
        if band_range == 0:
            logger.debug("Bollinger Bands have zero range")
            return None
        
        band_position = (bar.close - lower_band) / band_range

        # ADX regime gate — mean reversion fails in trending markets (module
        # docstring: "The ADX filter is essential"). Previously this gate only
        # existed in evaluate() (screener confidence scoring) and in
        # backtest.py's meanrev entry check, so live trades could fire into
        # strong trends that the backtest would have rejected. Mirrors
        # backtest.py semantics: pass when ADX is not computable (graceful),
        # block when ADX >= adx_max_threshold.
        bars_list = list(self._bars)
        if len(bars_list) >= 28:
            adx = calculate_adx(
                [b.high for b in bars_list],
                [b.low for b in bars_list],
                [b.close for b in bars_list],
                period=14,
            )
            if adx is not None and adx >= self.config.adx_max_threshold:
                logger.debug(
                    f"Signal blocked by ADX regime gate: adx={adx:.1f} >= "
                    f"{self.config.adx_max_threshold} (trending market)"
                )
                return None

        # Generate buy signal (oversold condition)
        # Price should be near lower band (band_position < 0.2) AND RSI oversold
        if band_position < 0.2 and rsi < self.config.rsi_oversold_threshold:
            logger.info(
                f"Buy signal: price={bar.close:.2f}, "
                f"lower_band={lower_band:.2f}, rsi={rsi:.2f}"
            )
            
            return TradeIntent(
                strategy_id=self.config.strategy_id,
                symbol=self.config.symbol,
                side="buy",
                intent_type="enter",
                notional_risk_pct=self.config.notional_risk_pct,
                metadata={
                    "band_position": round(band_position, 4),
                    "rsi": round(rsi, 2),
                    "upper_band": round(upper_band, 2),
                    "middle_band": round(middle_band, 2),
                    "lower_band": round(lower_band, 2),
                    "price": round(bar.close, 2),
                    "timestamp": bar.timestamp,
                },
            )
        
        if self.config.long_only:
            return None

        # Generate sell signal (overbought condition)
        # Price should be near upper band (band_position > 0.8) AND RSI overbought
        if band_position > 0.8 and rsi > self.config.rsi_overbought_threshold:
            logger.info(
                f"Sell signal: price={bar.close:.2f}, "
                f"upper_band={upper_band:.2f}, rsi={rsi:.2f}"
            )
            
            # Calculate ATR for stop-loss calculation
            bars_list = list(self._bars)
            if len(bars_list) < self.config.atr_period + 1:
                logger.debug("Insufficient bars for ATR calculation")
                return None
            
            highs = [b.high for b in bars_list]
            lows = [b.low for b in bars_list]
            closes = [b.close for b in bars_list]
            
            atr = calculate_atr(highs, lows, closes, period=self.config.atr_period)
            if atr is None or atr == 0:
                logger.debug("Could not calculate ATR")
                return None
            
            # Calculate stop-loss above upper Bollinger Band with buffer
            entry_price = bar.close
            stop_above_band = upper_band + (atr * self.config.stop_buffer_ATR)
            # Ensure minimum distance is atr * atr_stop_mult
            min_stop_distance = atr * self.config.atr_stop_mult
            stop_loss_price = max(stop_above_band, entry_price + min_stop_distance)
            
            return TradeIntent(
                strategy_id=self.config.strategy_id,
                symbol=self.config.symbol,
                side="sell",
                intent_type="enter",
                notional_risk_pct=self.config.notional_risk_pct,
                metadata={
                    "band_position": round(band_position, 4),
                    "rsi": round(rsi, 2),
                    "upper_band": round(upper_band, 2),
                    "middle_band": round(middle_band, 2),
                    "lower_band": round(lower_band, 2),
                    "price": round(bar.close, 2),
                    "timestamp": bar.timestamp,
                    "stop_loss_price": round(stop_loss_price, 8),
                    "atr": round(atr, 8),
                    "atr_stop_mult": self.config.atr_stop_mult,
                    "stop_buffer_ATR": self.config.stop_buffer_ATR,
                },
            )
        
        # No signal
        return None
    
    def _calculate_rsi_from_closes(self, closes: List[float]) -> float:
        """
        Calculate RSI from a list of closing prices.
        
        Args:
            closes: List of closing prices (oldest to newest)
            
        Returns:
            RSI value (0-100)
        """
        if len(closes) < 2:
            return 50.0  # Neutral RSI if not enough data
        
        # Calculate price changes
        changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        
        # Use the last rsi_period changes
        period = min(self.config.rsi_period, len(changes))
        recent_changes = changes[-period:]
        
        # Separate gains and losses
        gains = [c for c in recent_changes if c > 0]
        losses = [-c for c in recent_changes if c < 0]
        
        # Calculate average gain and average loss
        avg_gain = sum(gains) / period if gains else 0.0
        avg_loss = sum(losses) / period if losses else 0.0
        
        # Avoid division by zero
        if avg_loss == 0.0:
            return 100.0 if avg_gain > 0 else 50.0
        
        # Calculate RS and RSI
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        
        return rsi
    
    def _calculate_bollinger_bands_from_closes(
        self, closes: List[float]
    ) -> tuple[float, float, float]:
        """
        Calculate Bollinger Bands from a list of closing prices.
        
        Args:
            closes: List of closing prices (oldest to newest)
            
        Returns:
            Tuple of (sma, upper_band, lower_band)
        """
        # Use the last lookback_period prices
        period = min(self.config.lookback_period, len(closes))
        recent_prices = closes[-period:]
        
        # Calculate SMA
        sma = sum(recent_prices) / len(recent_prices)
        
        # Calculate standard deviation
        variance = sum((p - sma) ** 2 for p in recent_prices) / len(recent_prices)
        std_dev = variance ** 0.5
        
        # Calculate bands
        upper_band = sma + (self.config.std_dev_multiplier * std_dev)
        lower_band = sma - (self.config.std_dev_multiplier * std_dev)
        
        return (sma, upper_band, lower_band)
    
    def evaluate(self, symbol: str, bars: List[MarketDataEvent]) -> SignalResult:
        """
        Evaluate mean reversion strategy for any symbol with A+ setup detection.
        
        Confidence scoring (weighted factors):
        - RSI extreme (oversold/overbought): 30% weight
        - BB position (at outer bands): 25% weight
        - ADX < 20 (ranging market): 25% weight (CRITICAL)
        - ATR activity (market not dead): 20% weight
        
        Direction indicates which way the market is leaning (bullish/bearish).
        Signal is triggered when in ranging market (confidence filtering done by screener).
        """
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        # Need minimum bars for indicators (ADX needs ~28 bars)
        min_required = max(self.config.lookback_period, 30)
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
        current_price = closes[-1]
        
        # Calculate RSI
        rsi = self._calculate_rsi_from_closes(closes)
        
        # Calculate Bollinger Bands
        sma, upper, lower = self._calculate_bollinger_bands_from_closes(closes)
        
        # BB position: 0 = at lower band, 1 = at upper band
        bb_width = upper - lower
        bb_position = (current_price - lower) / bb_width if bb_width > 0 else 0.5
        
        # Determine direction based on RSI
        # RSI < 50 → bullish (toward BUY), RSI > 50 → bearish (toward SELL)
        direction = "bullish" if rsi < 50 else "bearish"
        
        # ============================================================
        # A+ SETUP CONFIDENCE SCORING (Weighted Components)
        # ============================================================
        
        # 1. RSI EXTREME (30% weight, max 30 points)
        # For A+ setups: RSI < 25 (oversold) or RSI > 75 (overbought)
        rsi_extreme = False
        rsi_score = 0.0
        
        if direction == "bullish":
            # For bullish: lower RSI = more oversold = higher score
            if rsi <= self.config.rsi_oversold_threshold:
                rsi_extreme = True
                # Scale: RSI 25=20pts, RSI 20=25pts, RSI 15=30pts
                rsi_score = min(30.0, 20.0 + (self.config.rsi_oversold_threshold - rsi) * 2.0)
            else:
                # Partial credit for RSI below 40
                if rsi < 40:
                    rsi_score = 15.0 * ((40 - rsi) / 15.0)
        else:
            # For bearish: higher RSI = more overbought = higher score
            if rsi >= self.config.rsi_overbought_threshold:
                rsi_extreme = True
                # Scale: RSI 75=20pts, RSI 80=25pts, RSI 85=30pts
                rsi_score = min(30.0, 20.0 + (rsi - self.config.rsi_overbought_threshold) * 2.0)
            else:
                # Partial credit for RSI above 60
                if rsi > 60:
                    rsi_score = 15.0 * ((rsi - 60) / 15.0)
        
        # 2. BB POSITION (25% weight, max 25 points)
        # For A+ setups: price at outer 10% of bands
        bb_extreme = False
        bb_score = 0.0
        
        if direction == "bullish":
            # For bullish: lower BB position = closer to lower band = higher score
            if bb_position <= 0.1:
                bb_extreme = True
                bb_score = 25.0
            elif bb_position <= 0.2:
                bb_score = 20.0
            elif bb_position <= 0.3:
                bb_score = 12.5
        else:
            # For bearish: higher BB position = closer to upper band = higher score
            if bb_position >= 0.9:
                bb_extreme = True
                bb_score = 25.0
            elif bb_position >= 0.8:
                bb_score = 20.0
            elif bb_position >= 0.7:
                bb_score = 12.5
        
        # 3. ADX RANGE FILTER (25% weight, max 25 points) - CRITICAL
        # Mean reversion ONLY works in ranging markets (ADX < 20)
        adx = calculate_adx(highs, lows, closes, period=14)
        is_ranging = False
        adx_score = 0.0
        
        if adx is not None:
            if adx < self.config.adx_max_threshold:
                is_ranging = True
                # Lower ADX = better for mean reversion
                # ADX 15=25pts, ADX 18=20pts, ADX 20=15pts
                adx_score = min(25.0, 25.0 - (adx - 10) * 1.0)
                adx_score = max(0.0, adx_score)
            else:
                # Penalty for trending markets - mean reversion fails here
                # ADX 25 = 5pts, ADX 30+ = 0pts
                if adx < 25:
                    adx_score = 10.0 * (25 - adx) / 5.0
        
        # 4. ATR ACTIVITY (20% weight, max 20 points)
        # Market should be active (ATR > average) - avoid dead markets
        atr_ratio = calculate_atr_ratio(highs, lows, closes, atr_period=14, avg_period=20)
        market_active = False
        atr_score = 0.0
        
        if atr_ratio is not None:
            if atr_ratio >= self.config.atr_min_ratio:
                market_active = True
                # Scale: ratio 1.0=15pts, ratio 1.5=20pts
                atr_score = min(20.0, 15.0 + (atr_ratio - 1.0) * 10.0)
            else:
                # Partial credit for moderate activity
                if atr_ratio >= 0.7:
                    atr_score = 10.0 * (atr_ratio / self.config.atr_min_ratio)
        
        # ============================================================
        # TOTAL CONFIDENCE
        # ============================================================
        confidence = rsi_score + bb_score + adx_score + atr_score
        confidence = min(100.0, confidence)
        
        # Determine signal type: trigger if market is ranging
        # (confidence filtering is handled by _apply_confidence_threshold in screener)
        # Without ranging market, mean reversion is dangerous
        signal_type = "NONE"
        if is_ranging:
            if direction == "bullish":
                signal_type = "BUY"
            elif not self.config.long_only:
                signal_type = "SELL"
        
        return SignalResult(
            symbol=symbol,
            signal_type=signal_type,
            confidence=round(confidence, 2),
            strategy_id=self.config.strategy_id,
            indicators={
                "direction": direction,
                # Core indicators
                "rsi": round(rsi, 2),
                "sma": round(sma, 2),
                "upper_band": round(upper, 2),
                "lower_band": round(lower, 2),
                "bb_position": round(bb_position, 4),
                # A+ criteria status
                "rsi_extreme": rsi_extreme,
                "bb_extreme": bb_extreme,
                "is_ranging": is_ranging,
                "market_active": market_active,
                # Indicator values
                "adx": round(adx, 2) if adx else None,
                "atr_ratio": round(atr_ratio, 2) if atr_ratio else None,
                # Score breakdown
                "score_rsi": round(rsi_score, 1),
                "score_bb": round(bb_score, 1),
                "score_adx": round(adx_score, 1),
                "score_atr": round(atr_score, 1),
                # Price
                "current_price": current_price,
            },
            timestamp=timestamp,
        )

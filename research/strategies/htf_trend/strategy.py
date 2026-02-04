"""HTF Trend Pullback Continuation Strategy Implementation.

Strategy 3: HTF Trend Pullback Continuation
- Trades WITH a higher timeframe trend using pullbacks into dynamic support/resistance
- Target: 50-65% win rate with strong expectancy
- HTF trend: 4h
- Entry timeframe: 1h
"""

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Tuple

from research.strategies.base import BaseStrategy
from research.strategies.indicators import (
    calculate_adx_full,
    calculate_atr,
    calculate_ema,
    calculate_ema_series,
    calculate_ema_slope,
    detect_swing_highs_lows,
)
from research.strategies.types import MarketDataEvent, SignalResult, TradeIntent
from research.strategies.htf_trend.config import HTFTrendConfig

logger = logging.getLogger(__name__)


class HTFTrendStrategy(BaseStrategy):
    """
    HTF Trend Pullback Continuation strategy.
    
    Generates signals based on:
    - HTF trend qualification (4h EMA200, slope, optional ADX)
    - Pullback detection (1h price near EMA20/EMA50)
    - Entry confirmation (1h bullish/bearish reversal pattern)
    
    Signals:
    - Buy: HTF bullish trend AND 1h pullback to EMA20/50 AND bullish reversal
    - Sell: HTF bearish trend AND 1h pullback to EMA20/50 AND bearish reversal
    """
    
    def __init__(self, config: Optional[HTFTrendConfig] = None):
        """Initialize the HTF Trend strategy."""
        if config is None:
            config = HTFTrendConfig()
        
        super().__init__(config.strategy_id)
        self.config = config
        
        # In-memory state
        self._bars: Deque[MarketDataEvent] = deque(maxlen=200)
        self._htf_bars: Deque[MarketDataEvent] = deque(maxlen=200)
        
        logger.info(
            f"Initialized HTFTrendStrategy: "
            f"symbol={config.symbol}, interval={config.interval}, "
            f"htf_interval={config.htf_interval}"
        )
    
    def _qualify_trend(self, symbol: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Qualify HTF trend direction.
        
        Returns:
            Tuple of (trend_direction, reason)
            - trend_direction: 'bullish', 'bearish', or None
            - reason: Explanation string
        """
        try:
            # Fetch HTF bars if not cached
            if len(self._htf_bars) < 50:
                htf_bars = self.fetch_htf_bars(symbol, self.config.htf_interval, count=200)
                if htf_bars:
                    self._htf_bars.extend(htf_bars)
            
            if len(self._htf_bars) < 50:
                return (None, "insufficient_htf_data")
            
            recent_htf = list(self._htf_bars)[-50:]
            htf_closes = [bar.close for bar in recent_htf]
            htf_highs = [bar.high for bar in recent_htf]
            htf_lows = [bar.low for bar in recent_htf]
            current_htf_price = htf_closes[-1]
            
            # Calculate HTF EMA200
            ema200 = calculate_ema(htf_closes, self.config.htf_ema_slow)
            if ema200 is None:
                return (None, "insufficient_htf_ema")
            
            # Calculate EMA slope
            ema_series = calculate_ema_series(htf_closes, self.config.htf_ema_slow)
            if len(ema_series) < 10:
                return (None, "insufficient_htf_ema_series")
            
            slope = calculate_ema_slope(ema_series, bars=5)
            if slope is None:
                return (None, "insufficient_htf_slope")
            
            # Optional ADX filter
            if self.config.use_adx_filter:
                adx_result = calculate_adx_full(htf_highs, htf_lows, htf_closes, period=14)
                if adx_result and adx_result['adx'] < self.config.htf_adx_threshold:
                    return (None, f"htf_adx_too_low: {adx_result['adx']:.2f}")
            
            # Check extension filter
            ema20_htf = calculate_ema(htf_closes, 20)
            if ema20_htf:
                atr_htf = calculate_atr(htf_highs, htf_lows, htf_closes, period=14)
                if atr_htf and current_htf_price > 0:
                    distance_to_ema20 = abs(current_htf_price - ema20_htf)
                    extension_atr = distance_to_ema20 / atr_htf if atr_htf > 0 else 0
                    if extension_atr > self.config.extension_ATR_mult:
                        return (None, f"htf_too_extended: {extension_atr:.2f}ATR")
            
            # Qualify trend
            slope_threshold_pct = self.config.htf_slope_threshold * 100
            
            # Bullish: price above EMA200 AND slope up
            if current_htf_price > ema200 and slope >= slope_threshold_pct:
                return ('bullish', f"htf_bullish: price={current_htf_price:.2f}, ema200={ema200:.2f}, slope={slope:.4f}%")
            
            # Bearish: price below EMA200 AND slope down
            if current_htf_price < ema200 and slope <= -slope_threshold_pct:
                return ('bearish', f"htf_bearish: price={current_htf_price:.2f}, ema200={ema200:.2f}, slope={slope:.4f}%")
            
            # Flat/choppy - no clear trend
            return (None, f"htf_flat: slope={slope:.4f}%")
            
        except Exception as e:
            logger.error(f"Error qualifying trend: {e}", exc_info=True)
            return (None, f"trend_qualification_error: {str(e)}")
    
    def _check_late_entry_filter(
        self,
        bars: List[MarketDataEvent],
        trend_direction: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Check late entry filter at entry timeframe (1h).
        
        Prevents "buying the top after a pullback already resolved."
        If distance from 1h EMA20 exceeds X * ATR, skip the signal.
        
        Args:
            bars: List of 1h bars
            trend_direction: 'bullish' or 'bearish'
            
        Returns:
            Tuple of (should_skip, reason)
        """
        if not self.config.late_entry_filter_enabled:
            return (False, None)
        
        if len(bars) < max(self.config.etf_ema_fast + 10, 20):
            return (False, None)
        
        closes = [bar.close for bar in bars]
        highs = [bar.high for bar in bars]
        lows = [bar.low for bar in bars]
        current_price = closes[-1]
        
        # Calculate 1h EMA20
        ema20 = calculate_ema(closes, self.config.etf_ema_fast)
        if ema20 is None:
            return (False, None)
        
        # Calculate ATR
        atr = calculate_atr(highs, lows, closes, period=self.config.atr_period)
        if atr is None or atr == 0:
            return (False, None)
        
        # Calculate distance from EMA20
        distance_to_ema20 = abs(current_price - ema20)
        distance_atr = distance_to_ema20 / atr if atr > 0 else 0
        
        if trend_direction == 'bullish':
            # For long: if price is too far above EMA20, skip (already extended)
            if current_price > ema20 and distance_atr > self.config.late_entry_ema20_distance_atr:
                return (True, f"late_entry_filter: price {distance_atr:.2f}ATR above EMA20 (threshold: {self.config.late_entry_ema20_distance_atr}ATR)")
        
        else:  # bearish
            # For short: if price is too far below EMA20, skip (already extended)
            if current_price < ema20 and distance_atr > self.config.late_entry_ema20_distance_atr:
                return (True, f"late_entry_filter: price {distance_atr:.2f}ATR below EMA20 (threshold: {self.config.late_entry_ema20_distance_atr}ATR)")
        
        return (False, None)
    
    def _detect_pullback(
        self,
        bars: List[MarketDataEvent],
        trend_direction: str
    ) -> Tuple[bool, Optional[float]]:
        """
        Detect pullback to EMA20/EMA50 zone.
        
        Returns:
            Tuple of (is_pullback, distance_to_ema20)
        """
        if len(bars) < max(self.config.etf_ema_slow + 10, 50):
            return (False, None)
        
        closes = [bar.close for bar in bars]
        highs = [bar.high for bar in bars]
        lows = [bar.low for bar in bars]
        current_price = closes[-1]
        
        # Calculate EMAs
        ema20 = calculate_ema(closes, self.config.etf_ema_fast)
        ema50 = calculate_ema(closes, period=self.config.etf_ema_slow)
        
        if ema20 is None or ema50 is None:
            return (False, None)
        
        # Calculate ATR
        atr = calculate_atr(highs, lows, closes, period=self.config.atr_period)
        if atr is None or atr == 0:
            return (False, None)
        
        if trend_direction == 'bullish':
            # For long: price should pull back toward EMA20/EMA50
            distance_to_ema20 = ema20 - current_price
            distance_atr = distance_to_ema20 / atr if atr > 0 else 0
            
            # Check if price is in pullback zone
            is_in_zone = (
                distance_atr >= 0 and  # Price below EMA20
                distance_atr <= self.config.pullback_max_ATR and  # Within max distance
                current_price >= ema50 * (1 - self.config.break_bps / 10000)  # Not broken below EMA50
            )
            
            return (is_in_zone, distance_atr)
        
        else:  # bearish
            # For short: price should pull back toward EMA20/EMA50 (from above)
            distance_to_ema20 = current_price - ema20
            distance_atr = distance_to_ema20 / atr if atr > 0 else 0
            
            is_in_zone = (
                distance_atr >= 0 and  # Price above EMA20
                distance_atr <= self.config.pullback_max_ATR and
                current_price <= ema50 * (1 + self.config.break_bps / 10000)
            )
            
            return (is_in_zone, distance_atr)
    
    def _check_entry_confirmation(
        self,
        bar: MarketDataEvent,
        trend_direction: str,
        ema20: float
    ) -> bool:
        """
        Check if entry confirmation pattern is present.
        
        For LONG: 1h candle closes above EMA20 with bullish reversal pattern
        For SHORT: 1h candle closes below EMA20 with bearish reversal pattern
        """
        body_size = abs(bar.close - bar.open)
        candle_range = bar.high - bar.low
        body_pct = body_size / candle_range if candle_range > 0 else 0
        
        if trend_direction == 'bullish':
            # Long: close above EMA20 AND bullish reversal
            closes_above = bar.close > ema20
            close_position = (bar.close - bar.low) / candle_range if candle_range > 0 else 0.5
            is_bullish_reversal = (
                body_pct >= self.config.reversal_body_pct and
                close_position >= self.config.reversal_close_position_long
            )
            return closes_above and is_bullish_reversal
        
        else:  # bearish
            # Short: close below EMA20 AND bearish reversal
            closes_below = bar.close < ema20
            close_position = (bar.close - bar.low) / candle_range if candle_range > 0 else 0.5
            is_bearish_reversal = (
                body_pct >= self.config.reversal_body_pct and
                close_position <= self.config.reversal_close_position_short
            )
            return closes_below and is_bearish_reversal
    
    def generate_signals(self, bar: MarketDataEvent) -> Optional[TradeIntent]:
        """Generate trading signals from market data."""
        if bar.symbol != self.config.symbol:
            return None
        
        self._bars.append(bar)
        bars_list = list(self._bars)
        
        if len(bars_list) < max(self.config.etf_ema_slow + 20, 50):
            return None
        
        # Qualify HTF trend
        trend_direction, trend_reason = self._qualify_trend(bar.symbol)
        if trend_direction is None:
            logger.debug(f"Trend not qualified: {trend_reason}")
            return None
        
        closes = [b.close for b in bars_list]
        highs = [b.high for b in bars_list]
        lows = [b.low for b in bars_list]
        
        # Calculate EMAs
        ema20 = calculate_ema(closes, self.config.etf_ema_fast)
        ema50 = calculate_ema(closes, period=self.config.etf_ema_slow)
        
        if ema20 is None or ema50 is None:
            return None
        
        # Detect pullback
        is_pullback, pullback_distance = self._detect_pullback(bars_list, trend_direction)
        if not is_pullback:
            return None
        
        # Check late entry filter (prevents buying the top after pullback resolved)
        late_entry_skip, late_entry_reason = self._check_late_entry_filter(bars_list, trend_direction)
        if late_entry_skip:
            logger.debug(f"Signal blocked by late entry filter: {late_entry_reason}")
            return None
        
        # Check entry confirmation
        if not self._check_entry_confirmation(bar, trend_direction, ema20):
            return None
        
        # Calculate ATR
        atr = calculate_atr(highs, lows, closes, period=self.config.atr_period)
        if atr is None or atr == 0:
            return None
        
        # Calculate entry, stop, and targets
        if trend_direction == 'bullish':
            side = "buy"
            entry_price = ema20 + (atr * 0.02)  # Small buffer above EMA20
            
            # Stop below pullback swing low
            swing_data = detect_swing_highs_lows(bars_list, lookback=self.config.swing_lookback_bars)
            swing_lows = swing_data['lows']
            swing_stop = min(swing_lows) if swing_lows else entry_price * 0.95
            
            # ATR stop as minimum
            atr_stop = entry_price - (atr * self.config.atr_stop_mult)
            stop_loss = min(swing_stop, atr_stop) - (atr * self.config.swing_buffer_ATR)
            
            risk = entry_price - stop_loss
            
            # Targets
            tp1_price = entry_price + (risk * self.config.tp1_R)
            tp2_price = entry_price + (risk * self.config.tp2_R)
            
            # Trend invalidation level
            htf_ema200 = None
            if len(self._htf_bars) >= 50:
                htf_closes = [b.close for b in list(self._htf_bars)[-50:]]
                htf_ema200 = calculate_ema(htf_closes, self.config.htf_ema_slow)
        
        else:  # bearish
            side = "sell"
            entry_price = ema20 - (atr * 0.02)
            
            swing_data = detect_swing_highs_lows(bars_list, lookback=self.config.swing_lookback_bars)
            swing_highs = swing_data['highs']
            swing_stop = max(swing_highs) if swing_highs else entry_price * 1.05
            
            atr_stop = entry_price + (atr * self.config.atr_stop_mult)
            stop_loss = max(swing_stop, atr_stop) + (atr * self.config.swing_buffer_ATR)
            
            risk = stop_loss - entry_price
            
            tp1_price = entry_price - (risk * self.config.tp1_R)
            tp2_price = entry_price - (risk * self.config.tp2_R)
            
            htf_ema200 = None
            if len(self._htf_bars) >= 50:
                htf_closes = [b.close for b in list(self._htf_bars)[-50:]]
                htf_ema200 = calculate_ema(htf_closes, self.config.htf_ema_slow)
        
        logger.info(
            f"{trend_direction.upper()} signal: price={bar.close:.2f}, "
            f"ema20={ema20:.2f}, entry={entry_price:.2f}, stop={stop_loss:.2f}"
        )
        
        metadata = {
            "entry_price": round(entry_price, 8),
            "stop_loss_price": round(stop_loss, 8),
            "tp1_price": round(tp1_price, 8),
            "tp2_price": round(tp2_price, 8),
            "risk": round(risk, 8),
            "tp1_R": self.config.tp1_R,
            "tp2_R": self.config.tp2_R,
            "tp1_partial_pct": self.config.tp1_partial_pct,
            "max_hours_in_trade": self.config.max_hours_in_trade,
            "trailing_stop_mode": self.config.trailing_stop_mode,
            "atr_trail_mult": self.config.atr_trail_mult,
            "invalidation_conditions": {},
            "strategy_specific": {
                "trend_direction": trend_direction,
                "htf_interval": self.config.htf_interval,
                "etf_ema20": round(ema20, 8),
                "etf_ema50": round(ema50, 8) if ema50 else None,
                "pullback_distance_atr": round(pullback_distance, 4) if pullback_distance else None,
                "atr": round(atr, 8),
            },
            "timestamp": bar.timestamp,
        }
        
        # Add trend invalidation
        if self.config.trend_invalidation_enabled and htf_ema200:
            if trend_direction == 'bullish':
                metadata["invalidation_conditions"]["htf_close_below_ema200"] = round(htf_ema200, 8)
            else:
                metadata["invalidation_conditions"]["htf_close_above_ema200"] = round(htf_ema200, 8)
        
        return TradeIntent(
            strategy_id=self.config.strategy_id,
            symbol=self.config.symbol,
            side=side,
            intent_type="enter",
            notional_risk_pct=self.config.notional_risk_pct,
            metadata=metadata,
        )
    
    def evaluate(self, symbol: str, bars: List[MarketDataEvent]) -> SignalResult:
        """Evaluate strategy for any symbol (used by screener)."""
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        min_required = max(self.config.etf_ema_slow + 20, 50)
        if len(bars) < min_required:
            return SignalResult(
                symbol=symbol,
                signal_type="NONE",
                confidence=0.0,
                strategy_id=self.config.strategy_id,
                indicators={"error": "insufficient_data"},
                timestamp=timestamp,
            )
        
        try:
            # Extract data
            def get_bar_value(bar, key):
                if isinstance(bar, dict):
                    return float(bar.get(key, 0))
                return float(getattr(bar, key, 0))
            
            closes = [get_bar_value(bar, 'close') for bar in bars]
            highs = [get_bar_value(bar, 'high') for bar in bars]
            lows = [get_bar_value(bar, 'low') for bar in bars]
            current_price = closes[-1]
            
            # Convert to MarketDataEvent list
            bar_events = []
            for bar in bars:
                if isinstance(bar, dict):
                    bar_events.append(MarketDataEvent(
                        symbol=symbol,
                        interval=self.config.interval,
                        open=get_bar_value(bar, 'open'),
                        high=get_bar_value(bar, 'high'),
                        low=get_bar_value(bar, 'low'),
                        close=get_bar_value(bar, 'close'),
                        volume=get_bar_value(bar, 'volume'),
                        timestamp=bar.get('timestamp', timestamp) if isinstance(bar, dict) else getattr(bar, 'timestamp', timestamp),
                    ))
                else:
                    bar_events.append(bar)
            
            # Qualify trend (simplified - just check HTF)
            trend_direction, trend_reason = self._qualify_trend(symbol)
            
            # Calculate EMAs
            ema20 = calculate_ema(closes, self.config.etf_ema_fast)
            ema50 = calculate_ema(closes, period=self.config.etf_ema_slow)
            
            if ema20 is None or ema50 is None:
                return SignalResult(
                    symbol=symbol,
                    signal_type="NONE",
                    confidence=0.0,
                    strategy_id=self.config.strategy_id,
                    indicators={"error": "ema_calculation_failed"},
                    timestamp=timestamp,
                )
            
            # Detect pullback
            is_pullback, pullback_distance = self._detect_pullback(bar_events, trend_direction or 'bullish')
            
            # Calculate confidence
            confidence = 0.0
            signal_type = "NONE"
            
            if trend_direction:
                # Trend qualified
                trend_score = 40.0
                confidence += trend_score
                
                if is_pullback:
                    # Pullback detected
                    pullback_score = 30.0
                    confidence += pullback_score
                    
                    # Entry confirmation (check last bar)
                    last_bar = bar_events[-1] if bar_events else None
                    if last_bar and self._check_entry_confirmation(last_bar, trend_direction, ema20):
                        confirmation_score = 30.0
                        confidence += confirmation_score
                        signal_type = "BUY" if trend_direction == 'bullish' else "SELL"
            
            return SignalResult(
                symbol=symbol,
                signal_type=signal_type,
                confidence=round(min(100.0, confidence), 2),
                strategy_id=self.config.strategy_id,
                indicators={
                    "trend_direction": trend_direction,
                    "trend_reason": trend_reason,
                    "is_pullback": is_pullback,
                    "pullback_distance_atr": round(pullback_distance, 4) if pullback_distance else None,
                    "etf_ema20": round(ema20, 8),
                    "etf_ema50": round(ema50, 8) if ema50 else None,
                    "current_price": round(current_price, 8),
                },
                timestamp=timestamp,
            )
            
        except Exception as e:
            logger.error(f"Error evaluating strategy for {symbol}: {e}", exc_info=True)
            return SignalResult(
                symbol=symbol,
                signal_type="NONE",
                confidence=0.0,
                strategy_id=self.config.strategy_id,
                indicators={"error": str(e)},
                timestamp=timestamp,
            )

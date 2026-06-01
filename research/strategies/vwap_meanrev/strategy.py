"""VWAP Mean Reversion Strategy Implementation.

Strategy 1: VWAP Mean Reversion
- Captures reversion back to fair value (VWAP / anchored VWAP) after controlled deviations
- Target: 60-75% win rate with 1.2-2.5R payoff
- Entry timeframe: 15m
- HTF filter: 1h (regime filter)
"""

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional

from research.strategies.base import BaseStrategy
from research.strategies.indicators import (
    calculate_adx_full,
    calculate_atr,
    calculate_atr_ratio,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_ema_series,
    calculate_ema_slope,
    calculate_rsi,
    calculate_vwap,
    calculate_volume_ratio,
    detect_swing_highs_lows,
)
from research.strategies.types import MarketDataEvent, SignalResult, TradeIntent
from research.strategies.vwap_meanrev.config import VWAPMeanReversionConfig

logger = logging.getLogger(__name__)


class VWAPMeanReversionStrategy(BaseStrategy):
    """
    VWAP Mean Reversion strategy for high-probability base hits.
    
    Generates signals based on:
    - Price deviation from VWAP (session or anchored)
    - RSI extremes (oversold/overbought)
    - Reversal confirmation (candle closes back above/below VWAP)
    - HTF regime filter (1h trend/range)
    
    Signals:
    - Buy: Price closes below VWAP by threshold AND RSI oversold AND reversal confirmation
    - Sell: Price closes above VWAP by threshold AND RSI overbought AND reversal confirmation
    """
    
    def __init__(self, config: Optional[VWAPMeanReversionConfig] = None):
        """
        Initialize the VWAP Mean Reversion strategy.
        
        Args:
            config: Configuration object. If None, uses default VWAPMeanReversionConfig.
        """
        if config is None:
            config = VWAPMeanReversionConfig()
        
        super().__init__(config.strategy_id)
        self.config = config
        
        # In-memory indicator state (rolling windows)
        self._bars: Deque[MarketDataEvent] = deque(maxlen=200)  # Store recent bars
        self._htf_bars: Deque[MarketDataEvent] = deque(maxlen=200)  # Cache HTF bars
        
        logger.info(
            f"Initialized VWAPMeanReversionStrategy: "
            f"symbol={config.symbol}, interval={config.interval}, "
            f"htf_interval={config.htf_interval}, risk_pct={config.notional_risk_pct}"
        )
    
    def _check_1m_green_candle(self, symbol: str) -> bool:
        """
        Check if the most recent 1-minute candle closed green (Ross Cameron spec).
        
        "MUST wait for the first 1-minute candle to close green (no falling knives)"
        
        Args:
            symbol: Trading pair symbol
            
        Returns:
            True if 1-minute green candle confirmed, False otherwise
            Returns True if 1-minute data unavailable (graceful degradation)
        """
        try:
            # Fetch 1-minute bars using base class method
            one_min_bars = self.fetch_htf_bars(symbol, "1m", count=1)
            
            if not one_min_bars or len(one_min_bars) == 0:
                # No 1-minute data available - graceful degradation: allow signal
                logger.debug(f"No 1-minute bars available for {symbol}, skipping green candle check")
                return True
            
            # Check the most recent 1-minute candle
            latest_1m = one_min_bars[-1]
            is_green = latest_1m.close > latest_1m.open
            
            if not is_green:
                logger.debug(
                    f"1-minute green candle check failed for {symbol}: "
                    f"close={latest_1m.close:.2f} <= open={latest_1m.open:.2f}"
                )
            
            return is_green
        except Exception as e:
            # Graceful degradation: if we can't check 1-minute bars, allow the signal
            logger.warning(f"Failed to check 1-minute green candle for {symbol}: {e}, allowing signal")
            return True
    
    def _check_momentum_exclusion(self, bars: List[MarketDataEvent], side: str) -> tuple[bool, Optional[str]]:
        """
        Check momentum exclusion (knife-catch prevention).
        
        If the last N candles are all large-bodied in the same direction (trend impulse),
        do not fade - skip the signal.
        
        Args:
            bars: List of recent bars
            side: 'buy' or 'sell'
            
        Returns:
            Tuple of (should_exclude, reason)
        """
        if not self.config.momentum_exclusion_enabled:
            return (False, None)
        
        if len(bars) < self.config.momentum_exclusion_bars:
            return (False, None)
        
        # Get last N candles
        recent_bars = bars[-self.config.momentum_exclusion_bars:]
        
        # Check if all candles are large-bodied in same direction
        all_bullish = True
        all_bearish = True
        
        for bar in recent_bars:
            body_size = abs(bar.close - bar.open)
            candle_range = bar.high - bar.low
            body_pct = body_size / candle_range if candle_range > 0 else 0
            
            # Must be large-bodied
            if body_pct < self.config.momentum_body_pct_threshold:
                all_bullish = False
                all_bearish = False
                break
            
            # Check direction
            is_bullish = bar.close > bar.open
            if not is_bullish:
                all_bullish = False
            if is_bullish:
                all_bearish = False
        
        # For LONG signals: exclude if all recent candles are bearish (strong downtrend)
        if side == "buy" and all_bearish:
            return (True, f"momentum_exclusion: last {self.config.momentum_exclusion_bars} candles all bearish")
        
        # For SHORT signals: exclude if all recent candles are bullish (strong uptrend)
        if side == "sell" and all_bullish:
            return (True, f"momentum_exclusion: last {self.config.momentum_exclusion_bars} candles all bullish")
        
        return (False, None)
    
    def _check_vwap_slope_guard(self, bars: List[MarketDataEvent], vwap: float, side: str) -> tuple[bool, Optional[str]]:
        """
        Check VWAP slope guard.
        
        If VWAP slope is strongly directional, fade signals should require stronger confirmation.
        Practical: "If 1h EMA slope magnitude > threshold AND 15m close is making lower lows,
        require a double confirmation candle before entry."
        
        This checks the HTF (1h) EMA slope as a proxy for VWAP trend direction,
        since VWAP itself is session-based and doesn't have a simple slope calculation.
        
        Args:
            bars: List of recent bars (15m)
            vwap: Current VWAP value
            side: 'buy' or 'sell'
            
        Returns:
            Tuple of (requires_confirmation, reason)
        """
        if not self.config.vwap_slope_guard_enabled:
            return (False, None)
        
        if len(bars) < 20:
            return (False, None)
        
        # Use HTF EMA slope as proxy for VWAP trend direction
        # Fetch HTF bars to check EMA slope
        symbol = bars[-1].symbol if bars else None
        if not symbol:
            return (False, None)
        
        try:
            # Fetch HTF bars if not cached
            if len(self._htf_bars) < 50:
                htf_bars = self.fetch_htf_bars(symbol, self.config.htf_interval, count=200)
                if htf_bars:
                    self._htf_bars.extend(htf_bars)
            
            if len(self._htf_bars) < 50:
                return (False, None)  # Not enough HTF data
            
            # Get recent HTF bars
            recent_htf = list(self._htf_bars)[-50:]
            htf_closes = [bar.close for bar in recent_htf]
            
            # Calculate HTF EMA200 series
            ema_series = calculate_ema_series(htf_closes, self.config.htf_ema_slow)
            if len(ema_series) < 10:
                return (False, None)
            
            # Calculate EMA slope
            slope = calculate_ema_slope(ema_series, bars=5)
            if slope is None:
                return (False, None)
            
            # Convert to percentage
            slope_pct = slope / 100.0  # slope is already in percentage
            
            # Check if slope is strongly directional
            slope_threshold = self.config.vwap_slope_threshold
            is_strong_bullish = slope_pct > slope_threshold
            is_strong_bearish = slope_pct < -slope_threshold
            
            # Get 15m closes for lower lows/higher highs check
            closes = [b.close for b in bars]
            
            # For LONG signals: if HTF EMA slope is strongly bearish, require confirmation
            if side == "buy" and is_strong_bearish:
                # Check if 15m close is making lower lows
                recent_closes = closes[-self.config.vwap_slope_confirmation_bars:]
                if len(recent_closes) >= 2:
                    lower_lows = all(recent_closes[i] < recent_closes[i-1] for i in range(1, len(recent_closes)))
                    if lower_lows:
                        return (True, f"vwap_slope_guard: HTF EMA slope={slope_pct:.4f}% (bearish), lower_lows=True, requires {self.config.vwap_slope_confirmation_bars} confirmation candles")
            
            # For SHORT signals: if HTF EMA slope is strongly bullish, require confirmation
            if side == "sell" and is_strong_bullish:
                # Check if 15m close is making higher highs
                recent_closes = closes[-self.config.vwap_slope_confirmation_bars:]
                if len(recent_closes) >= 2:
                    higher_highs = all(recent_closes[i] > recent_closes[i-1] for i in range(1, len(recent_closes)))
                    if higher_highs:
                        return (True, f"vwap_slope_guard: HTF EMA slope={slope_pct:.4f}% (bullish), higher_highs=True, requires {self.config.vwap_slope_confirmation_bars} confirmation candles")
            
            return (False, None)
            
        except Exception as e:
            logger.debug(f"Error checking VWAP slope guard: {e}")
            return (False, None)
    
    def _check_regime_filter(self, symbol: str) -> tuple[bool, Optional[str]]:
        """
        Check HTF regime filter.
        
        Returns:
            Tuple of (allowed, reason)
            - allowed=True: Can trade in this regime
            - allowed=False: Blocked by regime filter
        """
        try:
            # Fetch HTF bars if not cached
            if len(self._htf_bars) < 50:
                htf_bars = self.fetch_htf_bars(symbol, self.config.htf_interval, count=200)
                if htf_bars:
                    self._htf_bars.extend(htf_bars)
            
            if len(self._htf_bars) < 50:
                # Not enough HTF data - allow trade but log warning
                logger.warning(f"Insufficient HTF data for {symbol}/{self.config.htf_interval}")
                return (True, "insufficient_htf_data")
            
            # Get recent HTF bars
            recent_htf = list(self._htf_bars)[-50:]
            htf_closes = [bar.close for bar in recent_htf]
            htf_highs = [bar.high for bar in recent_htf]
            htf_lows = [bar.low for bar in recent_htf]
            
            # Calculate HTF EMAs
            ema200 = calculate_ema(htf_closes, self.config.htf_ema_slow)
            ema50 = calculate_ema(htf_closes, self.config.htf_ema_fast)
            
            if ema200 is None or ema50 is None:
                return (True, "insufficient_htf_indicators")
            
            current_htf_price = htf_closes[-1]
            
            # Check volatility filter
            atr = calculate_atr(htf_highs, htf_lows, htf_closes, period=14)
            if atr and current_htf_price > 0:
                atr_pct = (atr / current_htf_price) * 100
                avg_atr = sum([calculate_atr(
                    htf_highs[max(0, i-14):i+1],
                    htf_lows[max(0, i-14):i+1],
                    htf_closes[max(0, i-14):i+1],
                    period=14
                ) or 0 for i in range(14, len(htf_closes))]) / max(1, len(htf_closes) - 14)
                avg_atr_pct = (avg_atr / current_htf_price) * 100 if avg_atr > 0 else 0
                
                if avg_atr_pct > 0 and atr_pct > avg_atr_pct * self.config.volatility_max_ATR_mult:
                    return (False, f"htf_volatility_too_high: {atr_pct:.2f}%")
            
            # For LONG: Allow if price above EMA200 OR trend is flat (not strongly bearish)
            # For SHORT: Allow if price below EMA200 OR trend is flat (not strongly bullish)
            # Calculate EMA slope
            ema_series = calculate_ema_series(htf_closes, self.config.htf_ema_slow)
            if len(ema_series) >= 10:
                slope = calculate_ema_slope(ema_series, bars=5)
                if slope is not None:
                    # Flat trend: slope within threshold
                    is_flat = abs(slope) < self.config.regime_slope_threshold * 100
                    is_bullish = current_htf_price > ema200 or (is_flat and slope >= 0)
                    is_bearish = current_htf_price < ema200 or (is_flat and slope <= 0)
                    
                    # Allow trades if trend is favorable or flat
                    return (True, f"htf_trend_ok: price={current_htf_price:.2f}, ema200={ema200:.2f}, slope={slope:.4f}%")
            
            # Default: allow if price above EMA200 (bullish bias)
            if current_htf_price > ema200:
                return (True, "htf_price_above_ema200")
            
            return (True, "htf_filter_passed")
            
        except Exception as e:
            logger.error(f"Error checking regime filter: {e}", exc_info=True)
            return (True, f"regime_check_error: {str(e)}")
    
    def _latest_htf_rsi(self, symbol: str) -> Optional[float]:
        """RSI on ``htf_interval`` closes (uses ``rsi_period``). None if insufficient data."""
        try:
            if len(self._htf_bars) < 50:
                htf_bars = self.fetch_htf_bars(symbol, self.config.htf_interval, count=200)
                if htf_bars:
                    self._htf_bars.extend(htf_bars)
            need = self.config.rsi_period + 5
            if len(self._htf_bars) < need:
                return None
            htf_closes = [b.close for b in self._htf_bars]
            return calculate_rsi(htf_closes, period=self.config.rsi_period)
        except Exception as e:
            logger.debug("HTF RSI unavailable: %s", e)
            return None
    
    def _calculate_vwap_values(self, bars: List[MarketDataEvent]) -> tuple[Optional[float], Optional[float]]:
        """
        Calculate session VWAP and anchored VWAP.
        
        Returns:
            Tuple of (session_vwap, anchored_vwap)
        """
        if len(bars) < 20:
            return (None, None)
        
        # Calculate typical prices (HLC/3)
        typical_prices = [(bar.high + bar.low + bar.close) / 3.0 for bar in bars]
        volumes = [bar.volume for bar in bars]
        
        # Session VWAP (from start of data)
        session_vwap = calculate_vwap(typical_prices, volumes, anchor_index=None)
        
        # Anchored VWAP (from recent swing point)
        # Find swing low/high within lookback
        swing_data = detect_swing_highs_lows(bars, lookback=self.config.anchored_vwap_lookback // 4)
        anchor_index = None
        
        if swing_data['low_indices']:
            # Use most recent swing low as anchor
            anchor_index = swing_data['low_indices'][-1]
        elif swing_data['high_indices']:
            # Use most recent swing high as anchor
            anchor_index = swing_data['high_indices'][-1]
        else:
            # Fallback: use lookback bars ago
            anchor_index = max(0, len(bars) - self.config.anchored_vwap_lookback)
        
        anchored_vwap = calculate_vwap(typical_prices, volumes, anchor_index=anchor_index)
        
        return (session_vwap, anchored_vwap)
    
    def _check_reversal_confirmation(
        self,
        bar: MarketDataEvent,
        vwap: float,
        side: str
    ) -> bool:
        """
        Check if reversal confirmation is present.
        
        Args:
            bar: Current bar
            vwap: VWAP level
            side: 'buy' or 'sell'
            
        Returns:
            True if reversal confirmed
        """
        if side == "buy":
            # For LONG: candle must close above VWAP OR bullish engulfing
            closes_above = bar.close > vwap
            
            # Check for bullish reversal pattern
            body_size = abs(bar.close - bar.open)
            candle_range = bar.high - bar.low
            if candle_range > 0:
                body_pct = body_size / candle_range
                close_position = (bar.close - bar.low) / candle_range
                is_bullish_reversal = (
                    body_pct >= self.config.reversal_body_pct and
                    close_position >= (1.0 - self.config.reversal_close_position)
                )
            else:
                is_bullish_reversal = False
            
            return closes_above or is_bullish_reversal
        
        else:  # sell
            # For SHORT: candle must close below VWAP OR bearish engulfing
            closes_below = bar.close < vwap
            
            # Check for bearish reversal pattern
            body_size = abs(bar.close - bar.open)
            candle_range = bar.high - bar.low
            if candle_range > 0:
                body_pct = body_size / candle_range
                close_position = (bar.close - bar.low) / candle_range
                is_bearish_reversal = (
                    body_pct >= self.config.reversal_body_pct and
                    close_position <= self.config.reversal_close_position
                )
            else:
                is_bearish_reversal = False
            
            return closes_below or is_bearish_reversal
    
    def _calculate_stop_and_targets(
        self,
        entry_price: float,
        side: str,
        bars: List[MarketDataEvent],
        atr: float
    ) -> Dict[str, float]:
        """
        Calculate stop-loss and take-profit levels.
        
        Returns:
            Dict with keys: stop_loss_price, tp1_price, tp2_price
        """
        # Calculate swing stop
        swing_data = detect_swing_highs_lows(bars, lookback=self.config.swing_lookback_bars)
        
        if side == "buy":
            # Stop below swing low
            swing_lows = swing_data['lows']
            swing_stop = min(swing_lows) if swing_lows else entry_price * 0.95
            
            # ATR stop
            atr_stop = entry_price - (atr * self.config.atr_stop_mult)
            
            # Use wider of the two
            stop_loss = min(swing_stop, atr_stop) - (atr * self.config.stop_buffer_ATR)
            
            # Calculate R (risk)
            risk = entry_price - stop_loss
            
            # Take-profits
            tp1_price = entry_price + (risk * self.config.tp1_R)
            tp2_price = entry_price + (risk * self.config.tp2_R)
        
        else:  # sell
            # Stop above swing high
            swing_highs = swing_data['highs']
            swing_stop = max(swing_highs) if swing_highs else entry_price * 1.05
            
            # ATR stop
            atr_stop = entry_price + (atr * self.config.atr_stop_mult)
            
            # Use wider of the two
            stop_loss = max(swing_stop, atr_stop) + (atr * self.config.stop_buffer_ATR)
            
            # Calculate R (risk)
            risk = stop_loss - entry_price
            
            # Take-profits
            tp1_price = entry_price - (risk * self.config.tp1_R)
            tp2_price = entry_price - (risk * self.config.tp2_R)
        
        return {
            'stop_loss_price': stop_loss,
            'tp1_price': tp1_price,
            'tp2_price': tp2_price,
            'risk': risk
        }
    
    def generate_signals(self, bar: MarketDataEvent) -> Optional[TradeIntent]:
        """
        Generate trading signals from market data.
        
        Implements VWAP mean reversion logic:
        - LONG: Price closes below VWAP by threshold AND RSI oversold AND reversal confirmation
        - SHORT: Price closes above VWAP by threshold AND RSI overbought AND reversal confirmation
        
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
        
        # Store bar
        self._bars.append(bar)
        
        # Need sufficient data
        if len(self._bars) < max(50, self.config.volume_sma_period + 10):
            logger.debug("Insufficient bars for VWAP calculation")
            return None
        
        bars_list = list(self._bars)
        
        # Check regime filter
        allowed, reason = self._check_regime_filter(bar.symbol)
        if not allowed:
            logger.debug(f"Signal blocked by regime filter: {reason}")
            return None
        
        # Calculate indicators
        closes = [b.close for b in bars_list]
        highs = [b.high for b in bars_list]
        lows = [b.low for b in bars_list]
        volumes = [b.volume for b in bars_list]
        
        # Calculate VWAP values
        session_vwap, anchored_vwap = self._calculate_vwap_values(bars_list)
        if session_vwap is None:
            logger.debug("Could not calculate VWAP")
            return None
        
        # Use session VWAP (or anchored if available and more recent)
        vwap = anchored_vwap if anchored_vwap else session_vwap
        
        # Calculate RSI
        rsi = calculate_rsi(closes, period=self.config.rsi_period)
        if rsi is None:
            logger.debug("Could not calculate RSI")
            return None
        
        # Calculate ATR
        atr = calculate_atr(highs, lows, closes, period=self.config.atr_period)
        if atr is None or atr == 0:
            logger.debug("Could not calculate ATR")
            return None
        
        # Calculate volume SMA
        volume_sma = sum(volumes[-self.config.volume_sma_period:]) / self.config.volume_sma_period
        current_volume = volumes[-1]
        volume_ratio = current_volume / volume_sma if volume_sma > 0 else 1.0
        
        # Conservative cap on abnormally high volume (skip for long-only + min spike mode)
        if self.config.volume_filter_mode == "conservative":
            if volume_ratio > self.config.volume_max_mult:
                skip_high_vol_cap = self.config.long_min_volume_ratio is not None
                if not skip_high_vol_cap:
                    logger.debug(
                        f"Volume too high: {volume_ratio:.2f} > {self.config.volume_max_mult}"
                    )
                    return None
        
        current_price = bar.close
        
        htf_rsi_val: Optional[float] = None
        if self.config.htf_rsi_long_max is not None:
            htf_rsi_val = self._latest_htf_rsi(bar.symbol)
        
        # ============================================================
        # LONG SIGNAL LOGIC (Ross Cameron spec)
        # ============================================================
        deviation_long = vwap - current_price
        
        # Initialize deviation variables
        deviation_long_pct = None
        deviation_long_atr = None
        
        # Ross Cameron spec: Price > 2% below VWAP (percentage-based, not ATR-based)
        if self.config.use_percentage_deviation:
            deviation_long_pct = (deviation_long / vwap) * 100.0 if vwap > 0 else 0
            deviation_check = deviation_long_pct >= self.config.dev_threshold_pct
            deviation_value = deviation_long_pct
            deviation_unit = "%"
        else:
            # Legacy ATR-based deviation
            deviation_long_atr = deviation_long / atr if atr > 0 else 0
            deviation_check = deviation_long_atr >= self.config.dev_threshold_ATR
            deviation_value = deviation_long_atr
            deviation_unit = "ATR"
        
        # Check momentum exclusion (knife-catch prevention)
        momentum_exclude, momentum_reason = self._check_momentum_exclusion(bars_list, "buy")
        if momentum_exclude:
            logger.debug(f"LONG signal blocked by momentum exclusion: {momentum_reason}")
            return None
        
        # Check VWAP slope guard
        slope_requires_confirmation, slope_reason = self._check_vwap_slope_guard(bars_list, vwap, "buy")
        if slope_requires_confirmation:
            # Require double confirmation candles
            confirmation_bars = bars_list[-self.config.vwap_slope_confirmation_bars:]
            confirmation_count = sum(1 for b in confirmation_bars if self._check_reversal_confirmation(b, vwap, "buy"))
            if confirmation_count < self.config.vwap_slope_confirmation_bars:
                logger.debug(f"LONG signal blocked by VWAP slope guard: {slope_reason}, only {confirmation_count}/{self.config.vwap_slope_confirmation_bars} confirmation candles")
                return None
        
        # Ross Cameron spec: Check for 1-minute green candle confirmation
        # "MUST wait for the first 1-minute candle to close green (no falling knives)"
        one_min_green_confirmed = self._check_1m_green_candle(bar.symbol)
        
        long_vol_ok = (
            self.config.long_min_volume_ratio is None
            or volume_ratio >= self.config.long_min_volume_ratio
        )
        htf_rsi_ok = (
            self.config.htf_rsi_long_max is None
            or (
                htf_rsi_val is not None
                and htf_rsi_val <= self.config.htf_rsi_long_max
            )
        )
        
        if (
            deviation_check and
            rsi <= self.config.rsi_oversold and
            self._check_reversal_confirmation(bar, vwap, "buy") and
            one_min_green_confirmed
            and long_vol_ok
            and htf_rsi_ok
        ):
            # Calculate entry price (near VWAP retest)
            entry_price = min(current_price, vwap + (atr * self.config.entry_offset_ATR))
            
            # Calculate stop and targets
            levels = self._calculate_stop_and_targets(entry_price, "buy", bars_list, atr)
            
            logger.info(
                f"LONG signal: price={current_price:.2f}, vwap={vwap:.2f}, "
                f"deviation={deviation_value:.2f}{deviation_unit}, rsi={rsi:.2f}, "
                f"entry={entry_price:.2f}, stop={levels['stop_loss_price']:.2f}, "
                f"1m_green={one_min_green_confirmed}"
            )
            
            return TradeIntent(
                strategy_id=self.config.strategy_id,
                symbol=self.config.symbol,
                side="buy",
                intent_type="enter",
                notional_risk_pct=self.config.notional_risk_pct,
                metadata={
                    "entry_price": round(entry_price, 8),
                    "stop_loss_price": round(levels['stop_loss_price'], 8),
                    "tp1_price": round(levels['tp1_price'], 8),
                    "tp2_price": round(levels['tp2_price'], 8),
                    "risk": round(levels['risk'], 8),
                    "tp1_R": self.config.tp1_R,
                    "tp2_R": self.config.tp2_R,
                    "tp1_partial_pct": self.config.tp1_partial_pct,
                    "max_bars_in_trade": self.config.max_bars_in_trade,
                    "invalidation_conditions": {
                        "price_below_stop": levels['stop_loss_price'],
                        "price_recrosses_vwap_below": vwap,
                    },
                    "strategy_specific": {
                        "vwap": round(vwap, 8),
                        "session_vwap": round(session_vwap, 8) if session_vwap else None,
                        "anchored_vwap": round(anchored_vwap, 8) if anchored_vwap else None,
                        "rsi": round(rsi, 2),
                        "atr": round(atr, 8),
                        "deviation_pct": round(deviation_long_pct, 2) if deviation_long_pct is not None else None,
                        "deviation_atr": round(deviation_long_atr, 4) if deviation_long_atr is not None else None,
                        "1m_green_confirmed": one_min_green_confirmed,
                        "volume_ratio": round(volume_ratio, 2),
                    },
                    "timestamp": bar.timestamp,
                },
            )
        
        if self.config.long_only:
            return None

        # ============================================================
        # SHORT SIGNAL LOGIC (Ross Cameron spec)
        # ============================================================
        deviation_short = current_price - vwap
        
        # Initialize deviation variables
        deviation_short_pct = None
        deviation_short_atr = None
        
        # Ross Cameron spec: Price > 2% above VWAP (percentage-based, not ATR-based)
        if self.config.use_percentage_deviation:
            deviation_short_pct = (deviation_short / vwap) * 100.0 if vwap > 0 else 0
            deviation_check = deviation_short_pct >= self.config.dev_threshold_pct
            deviation_value = deviation_short_pct
            deviation_unit = "%"
        else:
            # Legacy ATR-based deviation
            deviation_short_atr = deviation_short / atr if atr > 0 else 0
            deviation_check = deviation_short_atr >= self.config.dev_threshold_ATR
            deviation_value = deviation_short_atr
            deviation_unit = "ATR"
        
        # Check momentum exclusion (knife-catch prevention)
        momentum_exclude, momentum_reason = self._check_momentum_exclusion(bars_list, "sell")
        if momentum_exclude:
            logger.debug(f"SHORT signal blocked by momentum exclusion: {momentum_reason}")
            return None
        
        # Check VWAP slope guard
        slope_requires_confirmation, slope_reason = self._check_vwap_slope_guard(bars_list, vwap, "sell")
        if slope_requires_confirmation:
            # Require double confirmation candles
            confirmation_bars = bars_list[-self.config.vwap_slope_confirmation_bars:]
            confirmation_count = sum(1 for b in confirmation_bars if self._check_reversal_confirmation(b, vwap, "sell"))
            if confirmation_count < self.config.vwap_slope_confirmation_bars:
                logger.debug(f"SHORT signal blocked by VWAP slope guard: {slope_reason}, only {confirmation_count}/{self.config.vwap_slope_confirmation_bars} confirmation candles")
                return None
        
        if (
            deviation_check and
            rsi >= self.config.rsi_overbought and
            self._check_reversal_confirmation(bar, vwap, "sell")
        ):
            # Calculate entry price (near VWAP retest)
            entry_price = max(current_price, vwap - (atr * self.config.entry_offset_ATR))
            
            # Calculate stop and targets
            levels = self._calculate_stop_and_targets(entry_price, "sell", bars_list, atr)
            
            logger.info(
                f"SHORT signal: price={current_price:.2f}, vwap={vwap:.2f}, "
                f"deviation={deviation_value:.2f}{deviation_unit}, rsi={rsi:.2f}, "
                f"entry={entry_price:.2f}, stop={levels['stop_loss_price']:.2f}"
            )
            
            return TradeIntent(
                strategy_id=self.config.strategy_id,
                symbol=self.config.symbol,
                side="sell",
                intent_type="enter",
                notional_risk_pct=self.config.notional_risk_pct,
                metadata={
                    "entry_price": round(entry_price, 8),
                    "stop_loss_price": round(levels['stop_loss_price'], 8),
                    "tp1_price": round(levels['tp1_price'], 8),
                    "tp2_price": round(levels['tp2_price'], 8),
                    "risk": round(levels['risk'], 8),
                    "tp1_R": self.config.tp1_R,
                    "tp2_R": self.config.tp2_R,
                    "tp1_partial_pct": self.config.tp1_partial_pct,
                    "max_bars_in_trade": self.config.max_bars_in_trade,
                    "invalidation_conditions": {
                        "price_above_stop": levels['stop_loss_price'],
                        "price_recrosses_vwap_above": vwap,
                    },
                    "strategy_specific": {
                        "vwap": round(vwap, 8),
                        "session_vwap": round(session_vwap, 8) if session_vwap else None,
                        "anchored_vwap": round(anchored_vwap, 8) if anchored_vwap else None,
                        "rsi": round(rsi, 2),
                        "atr": round(atr, 8),
                        "deviation_pct": round(deviation_short_pct, 2) if deviation_short_pct is not None else None,
                        "deviation_atr": round(deviation_short_atr, 4) if deviation_short_atr is not None else None,
                        "volume_ratio": round(volume_ratio, 2),
                    },
                    "timestamp": bar.timestamp,
                },
            )
        
        # No signal
        return None
    
    def evaluate(self, symbol: str, bars: List[MarketDataEvent]) -> SignalResult:
        """
        Evaluate VWAP Mean Reversion strategy for any symbol.
        
        Used by screener to rank opportunities across symbols.
        Returns SignalResult with confidence score based on setup quality.
        
        Args:
            symbol: Trading pair symbol
            bars: List of OHLCV bars (oldest to newest)
            
        Returns:
            SignalResult with signal_type, confidence, and indicators
        """
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        # Need minimum bars
        min_required = max(50, self.config.volume_sma_period + 20)
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
                    # Include frontend indicators as None for consistency
                    "bb_position": None,
                    "adx": None,
                    "atr_ratio": None,
                },
                timestamp=timestamp,
            )
        
        try:
            # Extract OHLCV data
            def get_bar_value(bar, key):
                if isinstance(bar, dict):
                    return float(bar.get(key, 0))
                return float(getattr(bar, key, 0))
            
            closes = [get_bar_value(bar, 'close') for bar in bars]
            highs = [get_bar_value(bar, 'high') for bar in bars]
            lows = [get_bar_value(bar, 'low') for bar in bars]
            volumes = [get_bar_value(bar, 'volume') for bar in bars]
            current_price = closes[-1]
            
            # Convert to MarketDataEvent list for VWAP calculation
            bar_events = []
            for i, bar in enumerate(bars):
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
            
            # Calculate VWAP
            session_vwap, anchored_vwap = self._calculate_vwap_values(bar_events)
            if session_vwap is None:
                return SignalResult(
                    symbol=symbol,
                    signal_type="NONE",
                    confidence=0.0,
                    strategy_id=self.config.strategy_id,
                    indicators={"error": "vwap_calculation_failed"},
                    timestamp=timestamp,
                )
            
            vwap = anchored_vwap if anchored_vwap else session_vwap
            
            # Calculate RSI
            rsi = calculate_rsi(closes, period=self.config.rsi_period)
            if rsi is None:
                return SignalResult(
                    symbol=symbol,
                    signal_type="NONE",
                    confidence=0.0,
                    strategy_id=self.config.strategy_id,
                    indicators={
                        "error": "rsi_calculation_failed",
                        # Include frontend indicators as None for consistency
                        "bb_position": None,
                        "adx": None,
                        "atr_ratio": None,
                    },
                    timestamp=timestamp,
                )
            
            # Calculate ATR
            atr = calculate_atr(highs, lows, closes, period=self.config.atr_period)
            if atr is None or atr == 0:
                return SignalResult(
                    symbol=symbol,
                    signal_type="NONE",
                    confidence=0.0,
                    strategy_id=self.config.strategy_id,
                    indicators={"error": "atr_calculation_failed"},
                    timestamp=timestamp,
                )
            
            # Calculate deviation
            deviation = current_price - vwap
            deviation_atr = deviation / atr if atr > 0 else 0
            
            # Calculate volume ratio
            volume_sma = sum(volumes[-self.config.volume_sma_period:]) / self.config.volume_sma_period
            volume_ratio = volumes[-1] / volume_sma if volume_sma > 0 else 1.0
            
            # Calculate Bollinger Bands for BB % display
            # Need at least 20 bars for BB calculation
            bb_position = None
            if len(closes) >= 20:
                bb_result = calculate_bollinger_bands(closes, period=20, std_dev_mult=2.0)
                if bb_result:
                    bb_width = bb_result['upper'] - bb_result['lower']
                    if bb_width > 0:
                        bb_position = (current_price - bb_result['lower']) / bb_width
                    else:
                        bb_position = 0.5
            
            # Calculate ADX for trend strength display (needs at least 28 bars: period*2)
            adx = None
            if len(highs) >= 28 and len(lows) >= 28 and len(closes) >= 28:
                try:
                    adx_result = calculate_adx_full(highs, lows, closes, period=14)
                    if adx_result and 'adx' in adx_result:
                        adx = adx_result['adx']
                except Exception:
                    pass  # ADX calculation failed, leave as None
            
            # Calculate ATR ratio for volatility display (needs at least 35 bars: 14+20+1)
            atr_ratio = None
            if len(highs) >= 35 and len(lows) >= 35 and len(closes) >= 35:
                try:
                    atr_ratio = calculate_atr_ratio(highs, lows, closes, atr_period=14, avg_period=20)
                except Exception:
                    pass  # ATR ratio calculation failed, leave as None
            
            # Determine direction and signal type
            direction = "bullish" if rsi < 50 else "bearish"
            signal_type = "NONE"
            confidence = 0.0
            
            htf_rsi_screen: Optional[float] = None
            if self.config.htf_rsi_long_max is not None:
                try:
                    htf_ev = self.fetch_htf_bars(symbol, self.config.htf_interval, count=200)
                    if len(htf_ev) >= self.config.rsi_period + 5:
                        hc = [b.close for b in htf_ev]
                        htf_rsi_screen = calculate_rsi(hc, period=self.config.rsi_period)
                except Exception:
                    htf_rsi_screen = None
            
            long_gate_ok = True
            if self.config.long_min_volume_ratio is not None:
                long_gate_ok = long_gate_ok and volume_ratio >= self.config.long_min_volume_ratio
            if self.config.htf_rsi_long_max is not None:
                long_gate_ok = long_gate_ok and (
                    htf_rsi_screen is not None
                    and htf_rsi_screen <= self.config.htf_rsi_long_max
                )
            
            # LONG setup scoring
            if (
                long_gate_ok
                and deviation_atr <= -self.config.dev_threshold_ATR
                and rsi <= self.config.rsi_oversold
            ):
                # Base confidence from deviation and RSI
                deviation_score = min(40.0, abs(deviation_atr) / self.config.dev_threshold_ATR * 20.0)
                rsi_score = min(30.0, (self.config.rsi_oversold - rsi) / self.config.rsi_oversold * 30.0)
                
                # Volume confirmation
                volume_score = 20.0 if volume_ratio <= self.config.volume_max_mult else 10.0
                
                # Reversal confirmation (check last bar)
                last_bar = bar_events[-1] if bar_events else None
                reversal_score = 10.0 if (last_bar and self._check_reversal_confirmation(last_bar, vwap, "buy")) else 0.0
                
                confidence = deviation_score + rsi_score + volume_score + reversal_score
                signal_type = "BUY"
            
            # SHORT setup scoring (disabled when long_only)
            elif not self.config.long_only and deviation_atr >= self.config.dev_threshold_ATR and rsi >= self.config.rsi_overbought:
                # Base confidence from deviation and RSI
                deviation_score = min(40.0, deviation_atr / self.config.dev_threshold_ATR * 20.0)
                rsi_score = min(30.0, (rsi - self.config.rsi_overbought) / (100 - self.config.rsi_overbought) * 30.0)
                
                # Volume confirmation
                volume_score = 20.0 if volume_ratio <= self.config.volume_max_mult else 10.0
                
                # Reversal confirmation
                last_bar = bar_events[-1] if bar_events else None
                reversal_score = 10.0 if (last_bar and self._check_reversal_confirmation(last_bar, vwap, "sell")) else 0.0
                
                confidence = deviation_score + rsi_score + volume_score + reversal_score
                signal_type = "SELL"
            
            # Build indicators dict - always include all keys even if None
            indicators_dict = {
                "direction": direction,
                "vwap": round(vwap, 8),
                "session_vwap": round(session_vwap, 8) if session_vwap else None,
                "anchored_vwap": round(anchored_vwap, 8) if anchored_vwap else None,
                "current_price": round(current_price, 8),
                "deviation_atr": round(deviation_atr, 4),
                "rsi": round(rsi, 2),
                "atr": round(atr, 8),
                "volume_ratio": round(volume_ratio, 2),
                "htf_rsi": round(htf_rsi_screen, 2) if htf_rsi_screen is not None else None,
                # Frontend display indicators - always include keys
                "bb_position": round(bb_position, 4) if bb_position is not None else None,
                "adx": round(adx, 2) if adx is not None else None,
                "atr_ratio": round(atr_ratio, 2) if atr_ratio is not None else None,
            }
            
            # Debug: Log indicator values to verify they're being calculated
            logger.debug(
                f"[VWAP_MEANREV:{symbol}] Indicators calculated: "
                f"bb_position={bb_position}, adx={adx}, atr_ratio={atr_ratio}, "
                f"bars={len(bars)}, closes={len(closes)}"
            )
            
            return SignalResult(
                symbol=symbol,
                signal_type=signal_type,
                confidence=round(min(100.0, confidence), 2),
                strategy_id=self.config.strategy_id,
                indicators=indicators_dict,
                timestamp=timestamp,
            )
            
        except Exception as e:
            logger.error(f"Error evaluating strategy for {symbol}: {e}", exc_info=True)
            return SignalResult(
                symbol=symbol,
                signal_type="NONE",
                confidence=0.0,
                strategy_id=self.config.strategy_id,
                indicators={
                    "error": str(e),
                    # Include frontend indicators as None for consistency
                    "bb_position": None,
                    "adx": None,
                    "atr_ratio": None,
                },
                timestamp=timestamp,
            )

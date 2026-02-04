"""Volatility Contraction → Expansion Strategy Implementation.

Strategy 2: Volatility Contraction → Expansion
- Trades post-compression breakout with confirmation + retest to reduce fakeouts
- Target: 55-65% win rate with 2-4R payoff
- Entry timeframe: 15m
- HTF filter: 1h or 4h
"""

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Tuple

from research.strategies.base import BaseStrategy
from research.strategies.indicators import (
    calculate_atr,
    calculate_bb_width,
    calculate_bollinger_bands,
    calculate_volume_ratio,
    detect_swing_highs_lows,
)
from research.strategies.types import MarketDataEvent, SignalResult, TradeIntent
from research.strategies.volatility_breakout.config import VolatilityBreakoutConfig

logger = logging.getLogger(__name__)


class VolatilityBreakoutStrategy(BaseStrategy):
    """
    Volatility Contraction → Expansion strategy.
    
    Generates signals based on:
    - Compression detection (BB Width in bottom percentile, low ATR, low volume)
    - Breakout detection (price closes above upper BB with volume spike)
    - Retest confirmation (price pulls back toward breakout level, holds, then continues)
    
    Signals:
    - Buy: Compression → Breakout above upper BB → Retest → Continuation
    - Sell: Compression → Breakout below lower BB → Retest → Continuation
    """
    
    def __init__(self, config: Optional[VolatilityBreakoutConfig] = None):
        """Initialize the Volatility Breakout strategy."""
        if config is None:
            config = VolatilityBreakoutConfig()
        
        super().__init__(config.strategy_id)
        self.config = config
        
        # In-memory state (for current session)
        self._bars: Deque[MarketDataEvent] = deque(maxlen=300)
        # Note: Breakout state is now stored in Redis for restart-safety
        # See _get_breakout_state() and _set_breakout_state() methods
        
        logger.info(
            f"Initialized VolatilityBreakoutStrategy: "
            f"symbol={config.symbol}, interval={config.interval}"
        )
    
    def _detect_compression(
        self,
        bars: List[MarketDataEvent]
    ) -> Tuple[bool, Optional[float], Optional[float]]:
        """
        Detect volatility compression (squeeze).
        
        Returns:
            Tuple of (is_compressed, bb_width, bb_width_percentile)
        """
        if len(bars) < max(self.config.squeeze_lookback_N, self.config.bb_period + 10):
            return (False, None, None)
        
        closes = [bar.close for bar in bars]
        volumes = [bar.volume for bar in bars]
        highs = [bar.high for bar in bars]
        lows = [bar.low for bar in bars]
        
        # Calculate current BB Width
        bb = calculate_bollinger_bands(
            closes,
            period=self.config.bb_period,
            std_dev_mult=self.config.bb_std_dev
        )
        if bb is None:
            return (False, None, None)
        
        current_bb_width = calculate_bb_width(
            bb['upper'], bb['lower'], bb['middle']
        )
        if current_bb_width is None:
            return (False, None, None)
        
        # Calculate historical BB Widths for percentile
        lookback_bars = min(self.config.squeeze_lookback_N, len(bars))
        historical_widths = []
        
        for i in range(lookback_bars - self.config.bb_period, lookback_bars):
            if i < 0:
                continue
            hist_closes = closes[max(0, i - self.config.bb_period):i + 1]
            if len(hist_closes) < self.config.bb_period:
                continue
            
            hist_bb = calculate_bollinger_bands(
                hist_closes,
                period=self.config.bb_period,
                std_dev_mult=self.config.bb_std_dev
            )
            if hist_bb:
                hist_width = calculate_bb_width(
                    hist_bb['upper'], hist_bb['lower'], hist_bb['middle']
                )
                if hist_width is not None:
                    historical_widths.append(hist_width)
        
        if len(historical_widths) < 10:
            return (False, current_bb_width, None)
        
        # Calculate percentile
        historical_widths.sort()
        percentile_rank = sum(1 for w in historical_widths if w <= current_bb_width) / len(historical_widths) * 100
        
        # Check compression conditions
        is_compressed = percentile_rank <= self.config.squeeze_percentile
        
        # Check ATR compression
        atr = calculate_atr(highs, lows, closes, period=self.config.atr_period)
        atr_ratio = None
        if atr and closes[-1] > 0:
            # Calculate average ATR
            atr_values = []
            for i in range(max(0, len(closes) - 20), len(closes)):
                if i >= self.config.atr_period:
                    hist_atr = calculate_atr(
                        highs[max(0, i - self.config.atr_period):i + 1],
                        lows[max(0, i - self.config.atr_period):i + 1],
                        closes[max(0, i - self.config.atr_period):i + 1],
                        period=self.config.atr_period
                    )
                    if hist_atr:
                        atr_values.append(hist_atr)
            
            if atr_values:
                avg_atr = sum(atr_values) / len(atr_values)
                atr_ratio = atr / avg_atr if avg_atr > 0 else None
        
        # Check volume compression
        volume_sma = sum(volumes[-self.config.volume_sma_period:]) / self.config.volume_sma_period
        current_volume = volumes[-1]
        volume_ratio = current_volume / volume_sma if volume_sma > 0 else 1.0
        
        # All conditions must be met
        if is_compressed:
            atr_ok = atr_ratio is None or atr_ratio <= self.config.atr_compress_threshold
            volume_ok = volume_ratio <= self.config.vol_compress_mult
            if atr_ok and volume_ok:
                return (True, current_bb_width, percentile_rank)
        
        return (False, current_bb_width, percentile_rank)
    
    def _detect_breakout(
        self,
        bar: MarketDataEvent,
        bars: List[MarketDataEvent],
        bb: Dict[str, float]
    ) -> Tuple[bool, str]:
        """
        Detect breakout above upper BB or below lower BB.
        
        Returns:
            Tuple of (is_breakout, direction) where direction is 'long' or 'short'
        """
        closes = [b.close for b in bars]
        volumes = [b.volume for b in bars]
        
        # Calculate volume SMA
        volume_sma = sum(volumes[-self.config.volume_sma_period:]) / self.config.volume_sma_period
        current_volume = bar.volume
        volume_ratio = current_volume / volume_sma if volume_sma > 0 else 1.0
        
        # Check body strength
        body_size = abs(bar.close - bar.open)
        candle_range = bar.high - bar.low
        body_pct = body_size / candle_range if candle_range > 0 else 0
        
        # Check close position
        close_position = (bar.close - bar.low) / candle_range if candle_range > 0 else 0.5
        
        # LONG breakout: closes above upper BB with volume spike
        if (
            bar.close > bb['upper'] and
            volume_ratio >= self.config.vol_breakout_mult and
            body_pct >= self.config.breakout_body_pct and
            close_position >= self.config.breakout_close_position
        ):
            return (True, 'long')
        
        # SHORT breakout: closes below lower BB with volume spike
        if (
            bar.close < bb['lower'] and
            volume_ratio >= self.config.vol_breakout_mult and
            body_pct >= self.config.breakout_body_pct and
            close_position <= (1.0 - self.config.breakout_close_position)
        ):
            return (True, 'short')
        
        return (False, 'none')
    
    def _get_breakout_state(self, symbol: str, direction: str) -> Optional[Dict[str, Any]]:
        """
        Get breakout phase state from Redis (restart-safe).
        
        Returns:
            Breakout state dictionary or None if not found
        """
        state = self.get_phase_state(symbol)
        if state and state.get('direction') == direction:
            return state
        return None
    
    def _set_breakout_state(self, symbol: str, state: Dict[str, Any]) -> None:
        """
        Store breakout phase state in Redis (restart-safe, auditable).
        
        Args:
            symbol: Trading pair symbol
            state: Breakout state dictionary
        """
        self.set_phase_state(symbol, state)
    
    def _clear_breakout_state(self, symbol: str) -> None:
        """
        Clear breakout phase state from Redis.
        
        Args:
            symbol: Trading pair symbol
        """
        self.clear_phase_state(symbol)
    
    def _check_retest(
        self,
        symbol: str,
        bar: MarketDataEvent,
        bars: List[MarketDataEvent],
        breakout_level: float,
        direction: str
    ) -> Tuple[bool, Optional[float]]:
        """
        Check if retest has occurred and is valid.
        
        Returns:
            Tuple of (retest_valid, retest_low_price)
        """
        # Get breakout state from Redis (restart-safe)
        breakout_state = self._get_breakout_state(symbol, direction)
        
        if breakout_state is None:
            return (False, None)
        
        breakout_bar_index = breakout_state.get('bar_index')
        breakout_timestamp = breakout_state.get('breakout_timestamp')
        if breakout_bar_index is None and breakout_timestamp is None:
            return (False, None)
        
        # If we have bar_index, use it; otherwise use timestamp to find position
        if breakout_bar_index is not None:
            bars_since_breakout = len(bars) - breakout_bar_index - 1
        else:
            # Find bar index from timestamp
            breakout_bar_index = None
            for i, b in enumerate(bars):
                if isinstance(b, dict):
                    bar_ts = b.get('timestamp', '')
                else:
                    bar_ts = getattr(b, 'timestamp', '')
                if bar_ts == breakout_timestamp:
                    breakout_bar_index = i
                    break
            if breakout_bar_index is None:
                # Can't find breakout bar - state may be stale
                self._clear_breakout_state(symbol)
                return (False, None)
            bars_since_breakout = len(bars) - breakout_bar_index - 1
        
        # Check if we're within retest window
        if bars_since_breakout > self.config.retest_window_bars:
            # Retest window expired
            self._clear_breakout_state(symbol)
            return (False, None)
        
        # Check if price pulled back toward breakout level
        if direction == 'long':
            # For long: price should pull back toward upper BB (breakout level)
            retest_low = min([b.low for b in bars[breakout_bar_index + 1:]])
            pullback_distance = breakout_level - retest_low
            pullback_pct = (pullback_distance / breakout_level) * 10000  # Convert to bps
            
            # Retest is valid if:
            # 1. Price pulled back toward breakout level
            # 2. Did NOT close back into range by more than fail threshold
            recent_closes = [b.close for b in bars[breakout_bar_index + 1:]]
            min_close_since_breakout = min(recent_closes) if recent_closes else breakout_level
            
            # Check if any close went back into range
            range_reentry_bps = ((breakout_level - min_close_since_breakout) / breakout_level) * 10000
            
            if range_reentry_bps > self.config.retest_fail_bps:
                # Retest failed - price closed back into range
                self._clear_breakout_state(symbol)
                return (False, None)
            
            # Retest is holding - check for continuation
            if bar.close > breakout_level:
                # Continuation confirmed
                return (True, retest_low)
        
        else:  # short
            # For short: price should pull back toward lower BB (breakout level)
            retest_high = max([b.high for b in bars[breakout_bar_index + 1:]])
            pullback_distance = retest_high - breakout_level
            pullback_pct = (pullback_distance / breakout_level) * 10000
            
            # Check if any close went back into range
            recent_closes = [b.close for b in bars[breakout_bar_index + 1:]]
            max_close_since_breakout = max(recent_closes) if recent_closes else breakout_level
            
            range_reentry_bps = ((max_close_since_breakout - breakout_level) / breakout_level) * 10000
            
            if range_reentry_bps > self.config.retest_fail_bps:
                # Retest failed
                self._clear_breakout_state(symbol)
                return (False, None)
            
            # Retest is holding - check for continuation
            if bar.close < breakout_level:
                # Continuation confirmed
                return (True, retest_high)
        
        return (False, None)
    
    def generate_signals(self, bar: MarketDataEvent) -> Optional[TradeIntent]:
        """Generate trading signals from market data."""
        if bar.symbol != self.config.symbol:
            return None
        
        self._bars.append(bar)
        bars_list = list(self._bars)
        
        if len(bars_list) < max(self.config.squeeze_lookback_N, self.config.bb_period + 20):
            return None
        
        closes = [b.close for b in bars_list]
        highs = [b.high for b in bars_list]
        lows = [b.low for b in bars_list]
        volumes = [b.volume for b in bars_list]
        
        # Calculate Bollinger Bands
        bb = calculate_bollinger_bands(
            closes,
            period=self.config.bb_period,
            std_dev_mult=self.config.bb_std_dev
        )
        if bb is None:
            return None
        
        # Check for compression
        is_compressed, bb_width, bb_percentile = self._detect_compression(bars_list)
        
        # Check for breakout
        is_breakout, direction = self._detect_breakout(bar, bars_list, bb)
        
        # If compression detected, wait for breakout
        existing_state = self._get_breakout_state(bar.symbol, direction) if direction != 'none' else None
        if is_compressed and not existing_state:
            logger.debug(f"Compression detected: BB Width percentile = {bb_percentile:.1f}%")
            # Don't generate signal yet - wait for breakout
        
        # If breakout detected, record it in Redis and wait for retest
        if is_breakout and direction != 'none':
            logger.info(
                f"Breakout detected: {direction.upper()} at {bar.close:.2f}, "
                f"upper_bb={bb['upper']:.2f}, lower_bb={bb['lower']:.2f}"
            )
            breakout_state = {
                'bar_index': len(bars_list) - 1,
                'breakout_timestamp': bar.timestamp,  # Store timestamp for restart recovery
                'breakout_level': bb['upper'] if direction == 'long' else bb['lower'],
                'breakout_price': bar.close,
                'direction': direction,
                'symbol': bar.symbol,
            }
            self._set_breakout_state(bar.symbol, breakout_state)
            return None  # Wait for retest
        
        # Check for retest if breakout state exists
        existing_state = self._get_breakout_state(bar.symbol, direction) if direction != 'none' else None
        if existing_state:
            breakout_level = existing_state['breakout_level']
            breakout_direction = existing_state['direction']
            
            retest_valid, retest_level = self._check_retest(
                bar.symbol,
                bar,
                bars_list,
                breakout_level,
                breakout_direction
            )
            
            if retest_valid and retest_level is not None:
                # Retest confirmed - generate entry signal
                logger.info(
                    f"Retest confirmed: {breakout_direction.upper()} entry at {bar.close:.2f}"
                )
                
                # Calculate ATR for stops/targets
                atr = calculate_atr(highs, lows, closes, period=self.config.atr_period)
                if atr is None or atr == 0:
                    self._clear_breakout_state(bar.symbol)
                    return None
                
                # Entry price slightly above retest level
                if breakout_direction == 'long':
                    entry_price = retest_level + (atr * 0.05)  # Small buffer above retest
                    side = "buy"
                    stop_loss = retest_level - (atr * self.config.retest_buffer_ATR)
                    
                    # Targets
                    if self.config.use_measured_move:
                        # Use range height projection
                        range_high = max([b.high for b in bars_list[-self.config.squeeze_lookback_N:]])
                        range_low = min([b.low for b in bars_list[-self.config.squeeze_lookback_N:]])
                        measured_move = range_high - range_low
                        tp1_price = breakout_level + measured_move * 0.5
                        tp2_price = breakout_level + measured_move
                    else:
                        tp1_price = entry_price + (atr * self.config.atr_target1_mult)
                        tp2_price = entry_price + (atr * self.config.atr_target2_mult)
                
                else:  # short
                    entry_price = retest_level - (atr * 0.05)
                    side = "sell"
                    stop_loss = retest_level + (atr * self.config.retest_buffer_ATR)
                    
                    if self.config.use_measured_move:
                        range_high = max([b.high for b in bars_list[-self.config.squeeze_lookback_N:]])
                        range_low = min([b.low for b in bars_list[-self.config.squeeze_lookback_N:]])
                        measured_move = range_high - range_low
                        tp1_price = breakout_level - measured_move * 0.5
                        tp2_price = breakout_level - measured_move
                    else:
                        tp1_price = entry_price - (atr * self.config.atr_target1_mult)
                        tp2_price = entry_price - (atr * self.config.atr_target2_mult)
                
                risk = abs(entry_price - stop_loss)
                
                # Clear breakout state from Redis
                self._clear_breakout_state(bar.symbol)
                
                return TradeIntent(
                    strategy_id=self.config.strategy_id,
                    symbol=self.config.symbol,
                    side=side,
                    intent_type="enter",
                    notional_risk_pct=self.config.notional_risk_pct,
                    metadata={
                        "entry_price": round(entry_price, 8),
                        "stop_loss_price": round(stop_loss, 8),
                        "tp1_price": round(tp1_price, 8),
                        "tp2_price": round(tp2_price, 8),
                        "risk": round(risk, 8),
                        "breakout_level": round(breakout_level, 8),
                        "retest_level": round(retest_level, 8),
                        "trailing_stop_mode": self.config.trailing_stop_mode,
                        "atr_trail_mult": self.config.atr_trail_mult,
                        "invalidation_conditions": {
                            "price_below_stop": stop_loss if side == "buy" else None,
                            "price_above_stop": stop_loss if side == "sell" else None,
                            "range_reentry": breakout_level,
                        },
                        "strategy_specific": {
                            "bb_width": round(bb_width, 6) if bb_width else None,
                            "bb_percentile": round(bb_percentile, 2) if bb_percentile else None,
                            "atr": round(atr, 8),
                            "breakout_direction": breakout_direction,
                        },
                        "timestamp": bar.timestamp,
                    },
                )
        
        return None
    
    def evaluate(self, symbol: str, bars: List[MarketDataEvent]) -> SignalResult:
        """Evaluate strategy for any symbol (used by screener)."""
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        min_required = max(self.config.squeeze_lookback_N, self.config.bb_period + 20)
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
            volumes = [get_bar_value(bar, 'volume') for bar in bars]
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
            
            # Calculate BB
            bb = calculate_bollinger_bands(closes, period=self.config.bb_period, std_dev_mult=self.config.bb_std_dev)
            if bb is None:
                return SignalResult(
                    symbol=symbol,
                    signal_type="NONE",
                    confidence=0.0,
                    strategy_id=self.config.strategy_id,
                    indicators={"error": "bb_calculation_failed"},
                    timestamp=timestamp,
                )
            
            # Check compression
            is_compressed, bb_width, bb_percentile = self._detect_compression(bar_events)
            
            # Check breakout
            last_bar = bar_events[-1] if bar_events else None
            if last_bar:
                is_breakout, direction = self._detect_breakout(last_bar, bar_events, bb)
            else:
                is_breakout, direction = (False, 'none')
            
            # Calculate confidence
            confidence = 0.0
            signal_type = "NONE"
            
            if is_compressed:
                # Compression detected - potential setup
                compression_score = (self.config.squeeze_percentile - (bb_percentile or 100)) / self.config.squeeze_percentile * 40.0
                confidence += max(0.0, compression_score)
            
            if is_breakout and direction != 'none':
                # Breakout detected
                breakout_score = 40.0
                confidence += breakout_score
                signal_type = "BUY" if direction == 'long' else "SELL"
            
            # Volume confirmation
            volume_sma = sum(volumes[-self.config.volume_sma_period:]) / self.config.volume_sma_period
            volume_ratio = volumes[-1] / volume_sma if volume_sma > 0 else 1.0
            if volume_ratio >= self.config.vol_breakout_mult:
                confidence += 20.0
            
            return SignalResult(
                symbol=symbol,
                signal_type=signal_type,
                confidence=round(min(100.0, confidence), 2),
                strategy_id=self.config.strategy_id,
                indicators={
                    "bb_upper": round(bb['upper'], 8),
                    "bb_middle": round(bb['middle'], 8),
                    "bb_lower": round(bb['lower'], 8),
                    "bb_width": round(bb_width, 6) if bb_width else None,
                    "bb_percentile": round(bb_percentile, 2) if bb_percentile else None,
                    "is_compressed": is_compressed,
                    "is_breakout": is_breakout,
                    "breakout_direction": direction,
                    "current_price": round(current_price, 8),
                    "volume_ratio": round(volume_ratio, 2),
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

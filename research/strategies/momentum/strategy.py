"""Momentum strategy implementation for BTC/USD.

This strategy generates trading signals based on price momentum indicators.
It uses a simple Rate of Change (ROC) calculation over a lookback period
to identify bullish or bearish momentum.

A+ Setup Criteria (weighted confidence scoring):
- ROC exceeds threshold (trigger)
- EMA stack aligned (20 > 50 > 200 for longs)
- ADX > 25 (strong trend)
- RSI in optimal range (50-75 for longs, 25-50 for shorts)
- Volume confirmation

Trend following requires strong trends - the ADX and EMA filters are essential.
"""

import logging
from collections import deque
from datetime import datetime, timezone
from typing import List, Optional

from research.strategies.base import BaseStrategy
from research.strategies.indicators import (
    calculate_adx,
    calculate_rsi,
    calculate_volume_ratio,
    check_ema_stack_bullish,
    check_ema_stack_bearish,
)
from research.strategies.momentum.config import MomentumConfig
from research.strategies.types import MarketDataEvent, SignalResult, TradeIntent

logger = logging.getLogger(__name__)


class MomentumStrategy(BaseStrategy):
    """
    Momentum-based trading strategy for BTC/USD.
    
    Generates signals based on price momentum using Rate of Change (ROC)
    over a configurable lookback period. Emits buy signals on bullish momentum
    and sell signals on bearish momentum.
    
    Constraints (from MSSD):
    - Symbol: BTC/USD
    - Intervals: 4H, 1D
    - In-memory indicator state only (no persistence)
    - notional_risk_pct configurable (default: 2.0%)
    """
    
    def __init__(self, config: Optional[MomentumConfig] = None):
        """
        Initialize the MomentumStrategy.
        
        Args:
            config: MomentumConfig instance with strategy parameters.
                   If None, uses default configuration.
        """
        if config is None:
            config = MomentumConfig()
        
        super().__init__(strategy_id=config.strategy_id)
        self.config = config
        
        # In-memory state: rolling window of closing prices
        # Used to calculate momentum indicators
        self._price_window: deque[float] = deque(maxlen=config.lookback_period + 1)
        
        logger.info(
            f"Initialized MomentumStrategy: "
            f"lookback={config.lookback_period}, "
            f"roc_threshold={config.roc_threshold}%, "
            f"risk_pct={config.notional_risk_pct}%"
        )
    
    def _calculate_roc(self) -> Optional[float]:
        """
        Calculate Rate of Change (ROC) over the lookback period.
        
        ROC = ((current_price - price_N_bars_ago) / price_N_bars_ago) * 100
        
        Returns:
            ROC percentage if enough data is available, None otherwise
        """
        if len(self._price_window) < self.config.lookback_period + 1:
            return None
        
        current_price = self._price_window[-1]
        past_price = self._price_window[0]
        
        if past_price == 0:
            logger.warning("Past price is zero, cannot calculate ROC")
            return None
        
        roc = ((current_price - past_price) / past_price) * 100.0
        return roc
    
    def generate_signals(self, bar: MarketDataEvent) -> Optional[TradeIntent]:
        """
        Generate trading signals from market data.
        
        Implements momentum logic:
        - Calculates ROC over the lookback period
        - Emits buy signal if ROC >= roc_threshold (bullish momentum)
        - Emits sell signal if ROC <= -roc_threshold (bearish momentum)
        
        Args:
            bar: MarketDataEvent containing OHLCV data for the current bar
            
        Returns:
            TradeIntent if a signal is generated, None otherwise
        """
        # Validate symbol matches expected BTC/USD
        if bar.symbol != self.config.symbol:
            logger.debug(
                f"Ignoring bar for symbol {bar.symbol}, expected {self.config.symbol}"
            )
            return None
        
        # Update price window with current bar's close price
        self._price_window.append(bar.close)
        
        # Calculate momentum indicator (ROC)
        roc = self._calculate_roc()
        
        if roc is None:
            # Not enough data yet
            logger.debug("Insufficient data for momentum calculation")
            return None
        
        # Determine signal based on ROC threshold
        signal_side = None
        intent_type = "enter"  # Momentum strategy enters positions
        
        if roc >= self.config.roc_threshold:
            # Bullish momentum: buy signal
            signal_side = "buy"
            logger.info(
                f"Bullish momentum signal: ROC={roc:.2f}% >= "
                f"threshold={self.config.roc_threshold}%"
            )
        elif roc <= -self.config.roc_threshold:
            # Bearish momentum: sell signal
            signal_side = "sell"
            logger.info(
                f"Bearish momentum signal: ROC={roc:.2f}% <= "
                f"threshold={-self.config.roc_threshold}%"
            )
        else:
            # No signal: momentum within threshold range
            logger.debug(
                f"No signal: ROC={roc:.2f}% within threshold range "
                f"[{-self.config.roc_threshold}%, {self.config.roc_threshold}%]"
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
                "roc": roc,
                "lookback_period": self.config.lookback_period,
                "roc_threshold": self.config.roc_threshold,
                "current_price": bar.close,
                "bar_timestamp": bar.timestamp,
                "interval": bar.interval,
            },
        )
        
        return intent
    
    def _calculate_roc_from_closes(self, closes: List[float]) -> Optional[float]:
        """
        Calculate Rate of Change (ROC) from a list of closing prices.
        
        ROC = ((current_price - price_N_bars_ago) / price_N_bars_ago) * 100
        
        Args:
            closes: List of closing prices (oldest to newest)
            
        Returns:
            ROC percentage if enough data is available, None otherwise
        """
        if len(closes) < self.config.lookback_period + 1:
            return None
        
        current_price = closes[-1]
        past_price = closes[-(self.config.lookback_period + 1)]
        
        if past_price == 0:
            return None
        
        roc = ((current_price - past_price) / past_price) * 100.0
        return roc
    
    def evaluate(self, symbol: str, bars: List[MarketDataEvent]) -> SignalResult:
        """
        Evaluate momentum strategy for any symbol with A+ setup detection.
        
        Confidence scoring (weighted factors):
        - ROC magnitude: 25% weight (trigger)
        - EMA stack alignment: 25% weight (trend structure)
        - ADX > 25: 25% weight (strong trend)
        - RSI optimal range: 15% weight (avoid late entries)
        - Volume confirmation: 10% weight
        
        Direction indicates which way the market is leaning (bullish/bearish).
        Signal is triggered when ROC meets threshold (confidence filtering done by screener).
        """
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        # Need minimum bars for calculations (based on slowest EMA + buffer)
        min_required = max(self.config.lookback_period + 1, self.config.ema_slow + 10)
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
        
        # Calculate ROC
        roc = self._calculate_roc_from_closes(closes)
        
        if roc is None:
            return SignalResult(
                symbol=symbol,
                signal_type="NONE",
                confidence=0.0,
                strategy_id=self.config.strategy_id,
                indicators={"error": "roc_calculation_failed", "direction": "neutral"},
                timestamp=timestamp,
            )
        
        # Determine direction based on ROC sign
        direction = "bullish" if roc >= 0 else "bearish"
        
        # ============================================================
        # A+ SETUP CONFIDENCE SCORING (Weighted Components)
        # ============================================================
        
        # 1. ROC MAGNITUDE (25% weight, max 25 points)
        roc_meets_threshold = abs(roc) >= self.config.roc_threshold
        roc_score = 0.0
        
        if roc_meets_threshold:
            # Scale: at threshold = 15pts, 2x threshold = 25pts
            roc_score = min(25.0, 15.0 + (abs(roc) / self.config.roc_threshold - 1.0) * 10.0)
        else:
            # Partial credit for approaching threshold
            roc_score = 15.0 * (abs(roc) / self.config.roc_threshold)
        
        # 2. EMA STACK ALIGNMENT (25% weight, max 25 points)
        ema_aligned = False
        ema_score = 0.0
        ema_20, ema_50, ema_200 = None, None, None
        
        if direction == "bullish":
            is_bullish, ema_20, ema_50, ema_200 = check_ema_stack_bullish(
                closes,
                fast=self.config.ema_fast,
                medium=self.config.ema_medium,
                slow=self.config.ema_slow,
            )
            if is_bullish:
                ema_aligned = True
                ema_score = 25.0
            elif ema_20 and ema_50 and current_price > ema_50:
                # Partial: price above medium EMA
                ema_score = 12.5
        else:
            is_bearish, ema_20, ema_50, ema_200 = check_ema_stack_bearish(
                closes,
                fast=self.config.ema_fast,
                medium=self.config.ema_medium,
                slow=self.config.ema_slow,
            )
            if is_bearish:
                ema_aligned = True
                ema_score = 25.0
            elif ema_20 and ema_50 and current_price < ema_50:
                # Partial: price below medium EMA
                ema_score = 12.5
        
        # 3. ADX TREND STRENGTH (25% weight, max 25 points)
        adx = calculate_adx(highs, lows, closes, period=14)
        strong_trend = False
        adx_score = 0.0
        
        if adx is not None:
            if adx >= self.config.adx_threshold:
                strong_trend = True
                # Scale: ADX 25=20pts, ADX 30+=25pts
                adx_score = min(25.0, 20.0 + (adx - self.config.adx_threshold) * 1.0)
            else:
                # Partial credit if ADX approaching threshold
                if adx >= 20:
                    adx_score = 15.0 * (adx / self.config.adx_threshold)
        
        # 4. RSI OPTIMAL RANGE (15% weight, max 15 points)
        # For longs: RSI 50-75 (not overbought, but bullish)
        # For shorts: RSI 25-50 (not oversold, but bearish)
        rsi = calculate_rsi(closes, period=14)
        rsi_optimal = False
        rsi_score = 0.0
        
        if rsi is not None:
            if direction == "bullish":
                # Optimal range for longs: 50-75
                if self.config.rsi_min_long <= rsi <= self.config.rsi_max_long:
                    rsi_optimal = True
                    rsi_score = 15.0
                elif rsi > self.config.rsi_max_long:
                    # Overbought - penalize (late entry)
                    rsi_score = max(0, 7.5 - (rsi - self.config.rsi_max_long) * 0.5)
                elif rsi > 40:
                    # Below optimal but not oversold
                    rsi_score = 7.5
            else:
                # Optimal range for shorts: 25-50
                if self.config.rsi_min_short <= rsi <= self.config.rsi_max_short:
                    rsi_optimal = True
                    rsi_score = 15.0
                elif rsi < self.config.rsi_min_short:
                    # Oversold - penalize (late entry)
                    rsi_score = max(0, 7.5 - (self.config.rsi_min_short - rsi) * 0.5)
                elif rsi < 60:
                    # Above optimal but not overbought
                    rsi_score = 7.5
        
        # 5. VOLUME CONFIRMATION (10% weight, max 10 points)
        volume_ratio = calculate_volume_ratio(volumes, period=20)
        volume_confirmed = False
        volume_score = 0.0
        
        if volume_ratio is not None:
            if volume_ratio >= self.config.volume_threshold:
                volume_confirmed = True
                volume_score = min(10.0, 7.5 + (volume_ratio - 1.0) * 2.5)
            else:
                # Partial credit for above-average volume
                if volume_ratio >= 0.8:
                    volume_score = 5.0 * volume_ratio
        
        # ============================================================
        # TOTAL CONFIDENCE
        # ============================================================
        confidence = roc_score + ema_score + adx_score + rsi_score + volume_score
        confidence = min(100.0, confidence)
        
        # Determine signal type: trigger if ROC meets threshold
        # (confidence filtering is handled by _apply_confidence_threshold in screener)
        signal_type = "NONE"
        if roc_meets_threshold:
            signal_type = "BUY" if direction == "bullish" else "SELL"
        
        return SignalResult(
            symbol=symbol,
            signal_type=signal_type,
            confidence=round(confidence, 2),
            strategy_id=self.config.strategy_id,
            indicators={
                "direction": direction,
                # Core indicator
                "roc": round(roc, 2),
                "roc_threshold": self.config.roc_threshold,
                # A+ criteria status
                "roc_meets_threshold": roc_meets_threshold,
                "ema_aligned": ema_aligned,
                "strong_trend": strong_trend,
                "rsi_optimal": rsi_optimal,
                "volume_confirmed": volume_confirmed,
                # Indicator values
                "ema_20": round(ema_20, 2) if ema_20 else None,
                "ema_50": round(ema_50, 2) if ema_50 else None,
                "ema_200": round(ema_200, 2) if ema_200 else None,
                "adx": round(adx, 2) if adx else None,
                "rsi": round(rsi, 2) if rsi else None,
                "volume_ratio": round(volume_ratio, 2) if volume_ratio else None,
                # Score breakdown
                "score_roc": round(roc_score, 1),
                "score_ema": round(ema_score, 1),
                "score_adx": round(adx_score, 1),
                "score_rsi": round(rsi_score, 1),
                "score_volume": round(volume_score, 1),
                # Config
                "lookback_period": self.config.lookback_period,
                "current_price": current_price,
            },
            timestamp=timestamp,
        )

# RETIRED May 2026 — 5yr backtest: -$82.08, negative P&L every year
# R:R 0.69 (inverted). Structural flaw: momentum exhausted by VWAP retest.

"""Pullback to VWAP Strategy Implementation.

Strategy 6: Pullback to VWAP
- After scanner finds a coin up 8%+ with RVOL spike (initial move happened)
- Wait for price to pull back to within 0.5% of VWAP on a 15m bar
- Confirm pullback volume is lower than the initial move's volume (absorption)
- Enter long. Stop: below pullback bar low. Target: 2R.
- Direction: LONG ONLY
"""

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Deque, List, Optional, Tuple

from research.strategies.base import BaseStrategy
from research.strategies.indicators import (
    calculate_atr,
    calculate_rsi,
    calculate_vwap,
    detect_swing_highs_lows,
)
from research.strategies.types import MarketDataEvent, SignalResult, TradeIntent
from research.strategies.pullback_vwap.config import PullbackVWAPConfig

logger = logging.getLogger(__name__)


class PullbackVWAPStrategy(BaseStrategy):
    """
    Pullback to VWAP strategy.

    Entry logic:
    1. Scanner-detected coin: up 8%+ with RVOL spike (initial momentum move done)
    2. Price pulls back to within 0.5% of VWAP on a 15m bar
    3. Pullback bar volume < initial move bar volume (absorption, not continuation)
    4. Enter long. Stop below pullback bar low. Target: 2R.

    Signals: BUY only (long_only permanently True).
    """

    def __init__(self, config: Optional[PullbackVWAPConfig] = None):
        """Initialize the Pullback to VWAP strategy."""
        if config is None:
            config = PullbackVWAPConfig()

        super().__init__(config.strategy_id)
        self.config = config

        self._bars: Deque[MarketDataEvent] = deque(maxlen=300)

        logger.info(
            f"Initialized PullbackVWAPStrategy: "
            f"symbol={config.symbol}, interval={config.interval}"
        )

    def _compute_vwap(self, bars: List[MarketDataEvent]) -> Optional[float]:
        """Calculate anchored VWAP, falling back to session VWAP."""
        if len(bars) < 5:
            return None

        typical = [(b.high + b.low + b.close) / 3.0 for b in bars]
        volumes = [b.volume for b in bars]

        session_vwap = calculate_vwap(typical, volumes, anchor_index=None)

        swing = detect_swing_highs_lows(bars, lookback=max(2, self.config.anchored_vwap_lookback // 4))
        if swing["low_indices"]:
            anchor = swing["low_indices"][-1]
        elif swing["high_indices"]:
            anchor = swing["high_indices"][-1]
        else:
            anchor = max(0, len(bars) - self.config.anchored_vwap_lookback)

        anchored_vwap = calculate_vwap(typical, volumes, anchor_index=anchor)
        return anchored_vwap if anchored_vwap else session_vwap

    def _find_initial_move(
        self, bars: List[MarketDataEvent]
    ) -> Tuple[Optional[int], Optional[float]]:
        """
        Scan backward for the most recent bar that represents an initial momentum move.

        Criteria:
        - Bar's close is ≥ initial_move_min_pct% above close from lookback_bars ago
        - That bar's volume ≥ initial_move_rvol_min × 20-bar volume SMA at that point

        Returns:
            (bar_index, bar_volume) of the initial move bar, or (None, None) if not found.
        """
        lookback = min(self.config.initial_move_lookback_bars, len(bars) - 1)
        vol_sma_period = self.config.volume_sma_period

        # Walk backward from current bar to find the initial move
        for offset in range(1, lookback + 1):
            idx = len(bars) - 1 - offset
            if idx < vol_sma_period + 1:
                break

            candidate = bars[idx]

            # Reference price: close from initial_move_lookback_bars before the candidate
            ref_offset = min(self.config.initial_move_lookback_bars, idx)
            ref_idx = idx - ref_offset
            if ref_idx < 0:
                continue
            ref_close = bars[ref_idx].close
            if ref_close <= 0:
                continue

            move_pct = (candidate.close - ref_close) / ref_close * 100.0
            if move_pct < self.config.initial_move_min_pct:
                continue

            # Volume spike check: this bar's volume vs 20-bar average ending just before it
            vol_window = bars[max(0, idx - vol_sma_period):idx]
            if len(vol_window) < vol_sma_period // 2:
                continue
            avg_vol = sum(b.volume for b in vol_window) / len(vol_window)
            if avg_vol <= 0:
                continue

            rvol = candidate.volume / avg_vol
            if rvol >= self.config.initial_move_rvol_min:
                return idx, candidate.volume

        return None, None

    def _near_vwap(self, price: float, vwap: float) -> bool:
        """True if price is within pullback_threshold_pct% of VWAP."""
        if vwap <= 0:
            return False
        deviation_pct = abs(price - vwap) / vwap * 100.0
        return deviation_pct <= self.config.pullback_threshold_pct

    def _absorption_confirmed(
        self,
        bar: MarketDataEvent,
        initial_move_volume: float,
        volume_sma: float,
    ) -> bool:
        """True if current bar shows absorption (low volume relative to the initial move)."""
        if not self.config.volume_absorption_check:
            return True
        vs_move = bar.volume < initial_move_volume
        vs_sma = bar.volume < volume_sma * self.config.absorption_vs_sma_max
        return vs_move and vs_sma

    def generate_signals(self, bar: MarketDataEvent) -> Optional[TradeIntent]:
        """
        Generate trading signals from market data.

        LONG only: finds the initial 8%+ RVOL move, then waits for a low-volume
        pullback to VWAP, and enters with stop below the pullback bar low.
        """
        if bar.symbol != self.config.symbol:
            return None

        self._bars.append(bar)
        bars_list = list(self._bars)

        min_bars = self.config.volume_sma_period + self.config.atr_period + 5
        if len(bars_list) < min_bars:
            return None

        # Calculate indicators
        closes  = [b.close  for b in bars_list]
        highs   = [b.high   for b in bars_list]
        lows    = [b.low    for b in bars_list]
        volumes = [b.volume for b in bars_list]

        atr = calculate_atr(highs, lows, closes, period=self.config.atr_period)
        if not atr or atr == 0:
            return None

        vwap = self._compute_vwap(bars_list)
        if vwap is None:
            return None

        # Find the initial momentum move
        move_idx, move_volume = self._find_initial_move(bars_list)
        if move_idx is None or move_volume is None:
            return None

        # Must be looking at a bar AFTER the initial move (no same-bar entries)
        if move_idx >= len(bars_list) - 1:
            return None

        # Check pullback conditions on the current bar
        if not self._near_vwap(bar.close, vwap):
            logger.debug(
                f"[{bar.symbol}] No pullback: price={bar.close:.4f} not within "
                f"{self.config.pullback_threshold_pct}% of VWAP={vwap:.4f}"
            )
            return None

        vol_sma = sum(volumes[-self.config.volume_sma_period:]) / self.config.volume_sma_period
        if not self._absorption_confirmed(bar, move_volume, vol_sma):
            logger.debug(
                f"[{bar.symbol}] Absorption failed: bar.volume={bar.volume:.0f} "
                f"vs move_volume={move_volume:.0f}, vol_sma={vol_sma:.0f}"
            )
            return None

        # Entry: current close
        entry_price = bar.close

        # Stop: below pullback bar low minus ATR buffer
        stop_loss = bar.low - atr * self.config.atr_stop_mult
        risk = entry_price - stop_loss
        if risk <= 0:
            return None

        # Enforce minimum meaningful stop (avoid noise entries)
        if risk / entry_price < 0.005:
            return None

        tp1_price = entry_price + risk * self.config.tp1_R
        tp2_price = entry_price + risk * self.config.tp2_R

        logger.info(
            f"[{bar.symbol}] PULLBACK_VWAP LONG: entry={entry_price:.4f}, "
            f"vwap={vwap:.4f}, stop={stop_loss:.4f}, "
            f"tp1={tp1_price:.4f}, tp2={tp2_price:.4f}, "
            f"initial_move_idx={move_idx}, risk_pct={risk/entry_price*100:.2f}%"
        )

        return TradeIntent(
            strategy_id=self.config.strategy_id,
            symbol=self.config.symbol,
            side="buy",
            intent_type="enter",
            notional_risk_pct=self.config.notional_risk_pct,
            metadata={
                "entry_price": round(entry_price, 8),
                "stop_loss_price": round(stop_loss, 8),
                "tp1_price": round(tp1_price, 8),
                "tp2_price": round(tp2_price, 8),
                "risk": round(risk, 8),
                "tp1_R": self.config.tp1_R,
                "tp2_R": self.config.tp2_R,
                "tp1_partial_pct": self.config.tp1_partial_pct,
                "max_bars_in_trade": self.config.max_bars_in_trade,
                "invalidation_conditions": {
                    "price_below_stop": round(stop_loss, 8),
                },
                "strategy_specific": {
                    "vwap": round(vwap, 8),
                    "atr": round(atr, 8),
                    "initial_move_bar_idx": move_idx,
                    "initial_move_volume": round(move_volume, 2),
                    "pullback_volume": round(bar.volume, 2),
                    "vol_sma": round(vol_sma, 2),
                },
                "timestamp": bar.timestamp,
            },
        )

    def evaluate(self, symbol: str, bars: List[MarketDataEvent]) -> SignalResult:
        """
        Evaluate Pullback to VWAP strategy for any symbol (used by screener).

        Confidence scoring (0–100):
        - +40: Initial 8%+ move with RVOL detected in lookback window
        - +30: Current price within 0.5% of VWAP
        - +20: Current bar volume < initial move volume (absorption)
        - +10: RSI in healthy pullback range (40–65, not collapsed)

        Signal type is BUY when score ≥ 60 AND pullback condition met. Never SELL.
        """
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        min_required = self.config.volume_sma_period + self.config.atr_period + 10
        if len(bars) < min_required:
            return SignalResult(
                symbol=symbol,
                signal_type="NONE",
                confidence=0.0,
                strategy_id=self.config.strategy_id,
                indicators={"error": "insufficient_data", "bars_available": len(bars)},
                timestamp=timestamp,
            )

        try:
            def _val(bar, key: str) -> float:
                return float(bar.get(key, 0) if isinstance(bar, dict) else getattr(bar, key, 0))

            bar_events: List[MarketDataEvent] = []
            for b in bars:
                if isinstance(b, dict):
                    bar_events.append(MarketDataEvent(
                        symbol=symbol,
                        interval=self.config.interval,
                        open=_val(b, "open"),
                        high=_val(b, "high"),
                        low=_val(b, "low"),
                        close=_val(b, "close"),
                        volume=_val(b, "volume"),
                        timestamp=b.get("timestamp", timestamp),
                    ))
                else:
                    bar_events.append(b)

            closes  = [_val(b, "close")  for b in bars]
            highs   = [_val(b, "high")   for b in bars]
            lows    = [_val(b, "low")    for b in bars]
            volumes = [_val(b, "volume") for b in bars]
            current_price = closes[-1]

            # ── Indicator 1: Initial move detection (+40) ────────────────────
            move_idx, move_volume = self._find_initial_move(bar_events)
            initial_move_score = 0.0
            has_initial_move = move_idx is not None and move_volume is not None

            if has_initial_move:
                # Scale: exactly at threshold = 30 pts, 2× threshold = 40 pts
                ref_idx = max(0, move_idx - self.config.initial_move_lookback_bars)
                ref_close = closes[ref_idx] if ref_idx < len(closes) else closes[0]
                if ref_close > 0:
                    move_pct = (closes[move_idx] - ref_close) / ref_close * 100.0  # type: ignore[index]
                    scale = min(1.0, move_pct / (self.config.initial_move_min_pct * 2))
                    initial_move_score = 30.0 + scale * 10.0

            # ── Indicator 2: Pullback to VWAP (+30) ─────────────────────────
            vwap = self._compute_vwap(bar_events)
            pullback_score = 0.0
            near_vwap = False

            if vwap and vwap > 0:
                deviation_pct = abs(current_price - vwap) / vwap * 100.0
                near_vwap = deviation_pct <= self.config.pullback_threshold_pct
                if near_vwap:
                    # Full score at threshold; proportional credit within 2× threshold
                    pullback_score = 30.0
                elif deviation_pct <= self.config.pullback_threshold_pct * 2:
                    pullback_score = 15.0 * (1.0 - (deviation_pct - self.config.pullback_threshold_pct) / self.config.pullback_threshold_pct)

            # ── Indicator 3: Volume absorption (+20) ────────────────────────
            absorption_score = 0.0
            vol_sma = sum(volumes[-self.config.volume_sma_period:]) / self.config.volume_sma_period

            if has_initial_move and move_volume is not None and move_volume > 0:
                vol_ratio = volumes[-1] / move_volume
                if vol_ratio < 1.0:
                    absorption_score = 20.0 * (1.0 - vol_ratio)
                # Partial credit if volume is low relative to SMA even without move comparison
            elif vol_sma > 0:
                sma_ratio = volumes[-1] / vol_sma
                if sma_ratio < 0.8:
                    absorption_score = 10.0

            # ── Indicator 4: RSI health (+10) ────────────────────────────────
            rsi_score = 0.0
            rsi = calculate_rsi(closes, period=self.config.rsi_period)
            if rsi is not None:
                # Healthy pullback range: RSI 40–65 (not collapsed, not overextended)
                if 40.0 <= rsi <= 65.0:
                    rsi_score = 10.0
                elif 35.0 <= rsi < 40.0 or 65.0 < rsi <= 70.0:
                    rsi_score = 5.0

            # ── Total confidence ─────────────────────────────────────────────
            confidence = initial_move_score + pullback_score + absorption_score + rsi_score
            confidence = min(100.0, confidence)

            # Signal fires when initial move detected AND price is near VWAP
            signal_type = "NONE"
            if has_initial_move and near_vwap and confidence >= 60.0:
                signal_type = "BUY"

            return SignalResult(
                symbol=symbol,
                signal_type=signal_type,
                confidence=round(confidence, 2),
                strategy_id=self.config.strategy_id,
                indicators={
                    "has_initial_move": has_initial_move,
                    "initial_move_bar_idx": move_idx,
                    "initial_move_volume": round(move_volume, 2) if move_volume else None,
                    "near_vwap": near_vwap,
                    "vwap": round(vwap, 8) if vwap else None,
                    "current_price": round(current_price, 8),
                    "deviation_pct": round(abs(current_price - vwap) / vwap * 100.0, 4) if vwap else None,
                    "rsi": round(rsi, 2) if rsi is not None else None,
                    "vol_sma": round(vol_sma, 2),
                    "current_volume": round(volumes[-1], 2),
                    "score_initial_move": round(initial_move_score, 1),
                    "score_pullback": round(pullback_score, 1),
                    "score_absorption": round(absorption_score, 1),
                    "score_rsi": round(rsi_score, 1),
                },
                timestamp=timestamp,
            )

        except Exception as e:
            logger.error(f"Error evaluating PullbackVWAP for {symbol}: {e}", exc_info=True)
            return SignalResult(
                symbol=symbol,
                signal_type="NONE",
                confidence=0.0,
                strategy_id=self.config.strategy_id,
                indicators={"error": str(e)},
                timestamp=timestamp,
            )

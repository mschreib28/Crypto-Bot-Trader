"""Screener engine for calculating indicators and signal strength across symbols.

This module implements the core screening logic based on the mean-reversion
indicator patterns from research/strategies/meanrev/strategy.py.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from backend.config import CONFIDENCE_THRESHOLD_PCT
from backend.ingestor.symbols import get_symbol_volume
from backend.screener.models import ScreenerResult, SignalResult

logger = logging.getLogger(__name__)

# Minimum bars required before evaluating indicators
# This is max(lookback_period=20, rsi_period+1=15) = 20
MIN_BARS_THRESHOLD = 20

# Default RSI period for indicator calculation
DEFAULT_RSI_PERIOD = 14


def _calculate_rvol(bars: List[Dict[str, Any]], volume_24h: Optional[float], symbol: Optional[str] = None) -> Dict[str, Optional[float]]:
    """
    Calculate Daily Relative Volume (RVOL) vs 50-Day Moving Average.

    RVOL % = (Current 24h Volume / 50-Day Daily Average Volume) × 100

    RVOL ~100% means today's volume equals the 50-day average.
    RVOL 200% means today's volume is 2x the 50-day average.

    Primary method: volume_24h / (hourly_sma_50d × 24) × 100
    Fallback (no SMA data): bar-based calculation using recent bars.

    Args:
        bars: List of OHLCV bar dictionaries with 'volume' key
        volume_24h: Current 24h volume (primary numerator)
        symbol: Trading symbol — used to fetch 50-day hourly SMA from Redis

    Returns:
        Dict with 'rvol_pct' and 'avg_volume_50d' (daily average, None if insufficient data)
    """
    result: Dict[str, Optional[float]] = {
        "rvol_pct": None,
        "avg_volume_50d": None,
    }

    if volume_24h is None:
        return result

    # Primary: 50-day daily average from Kraken REST API (real data, available from day one)
    if symbol is not None:
        try:
            from backend.screener.data_collector import fetch_daily_sma_50d
            daily_sma_50d = fetch_daily_sma_50d(symbol)
            if daily_sma_50d is not None and daily_sma_50d > 0:
                rvol_pct = (volume_24h / daily_sma_50d) * 100
                result["avg_volume_50d"] = round(daily_sma_50d, 2)
                result["rvol_pct"] = round(rvol_pct, 2)
                return result
        except Exception:
            pass

    # Fallback: bar-based average when 50-day SMA unavailable
    if len(bars) < 5:
        return result

    volumes: List[float] = []
    for bar in bars[-20:]:
        if isinstance(bar, dict):
            vol = bar.get('volume')
            if vol is not None:
                volumes.append(float(vol))
        elif hasattr(bar, 'volume'):
            volumes.append(float(bar.volume))

    if len(volumes) < 2:
        return result

    avg_volume = sum(volumes[:-1]) / (len(volumes) - 1)
    current_volume = volumes[-1]

    if avg_volume == 0:
        return result

    rvol_pct = (current_volume / avg_volume) * 100
    result["avg_volume_50d"] = round(avg_volume, 2)
    result["rvol_pct"] = round(rvol_pct, 2)

    return result


def _calculate_rsi_from_bars(bars: List[Dict[str, Any]], period: int = DEFAULT_RSI_PERIOD) -> Optional[float]:
    """
    Calculate RSI from bar data.
    
    Args:
        bars: List of OHLCV bar dictionaries with 'close' key
        period: RSI calculation period (default: 14)
        
    Returns:
        RSI value (0-100) or None if insufficient data
    """
    closes = []
    for bar in bars:
        if isinstance(bar, dict):
            close = bar.get('close')
            if close is not None:
                closes.append(float(close))
        elif hasattr(bar, 'close'):
            closes.append(float(bar.close))
    
    if len(closes) < period + 1:
        return None
    
    # Calculate price changes for the RSI period
    changes = [closes[i] - closes[i - 1] for i in range(len(closes) - period, len(closes))]
    
    gains = [c for c in changes if c > 0]
    losses = [-c for c in changes if c < 0]
    
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0 else 50.0
    
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    
    return round(rsi, 2)


def _ensure_indicators(signal_result: Any, bars: List[Dict[str, Any]], symbol: Optional[str] = None) -> None:
    """
    Ensure RSI, price, and volume_24h are present in signal result indicators.
    
    Modifies the signal_result's indicators dict in-place to ensure
    'rsi', 'price', and 'volume_24h' keys exist for frontend display.
    
    Args:
        signal_result: SignalResult object (from either backend.screener.models or research.strategies.types)
        bars: List of OHLCV bar dictionaries used for calculation
        symbol: Optional symbol name for volume lookup (falls back to signal_result.symbol)
    """
    # Get indicators dict (handle both field names for different SignalResult types)
    if hasattr(signal_result, 'indicators') and signal_result.indicators is not None:
        indicators = signal_result.indicators
    elif hasattr(signal_result, 'metadata') and signal_result.metadata is not None:
        indicators = signal_result.metadata
    else:
        return
    
    # Get symbol for volume lookup
    if symbol is None:
        symbol = getattr(signal_result, 'symbol', None)
    
    # Store bar timestamp for debouncing (use latest bar timestamp)
    if bars and 'bar_timestamp' not in indicators:
        last_bar = bars[-1]
        if isinstance(last_bar, dict):
            bar_ts = last_bar.get('timestamp')
            if bar_ts:
                indicators['bar_timestamp'] = bar_ts
        elif hasattr(last_bar, 'timestamp'):
            indicators['bar_timestamp'] = last_bar.timestamp
    
    # Ensure 'price' and 'current_price' are present (normalize from 'current_price' or bars)
    if 'current_price' not in indicators:
        if 'price' in indicators:
            indicators['current_price'] = indicators['price']
        elif bars:
            # Extract price from last bar
            last_bar = bars[-1]
            if isinstance(last_bar, dict):
                close = last_bar.get('close')
                if close is not None:
                    price_val = float(close)
                    indicators['current_price'] = price_val
                    if 'price' not in indicators:
                        indicators['price'] = price_val
            elif hasattr(last_bar, 'close'):
                price_val = float(last_bar.close)
                indicators['current_price'] = price_val
                if 'price' not in indicators:
                    indicators['price'] = price_val
    
    # Ensure 'price' is present (normalize from 'current_price')
    if 'price' not in indicators:
        if 'current_price' in indicators:
            indicators['price'] = indicators['current_price']
        elif bars:
            # Extract price from last bar
            last_bar = bars[-1]
            if isinstance(last_bar, dict):
                close = last_bar.get('close')
                if close is not None:
                    indicators['price'] = float(close)
            elif hasattr(last_bar, 'close'):
                indicators['price'] = float(last_bar.close)
    
    # Ensure 'rsi' is present
    if 'rsi' not in indicators and bars:
        rsi = _calculate_rsi_from_bars(bars)
        if rsi is not None:
            indicators['rsi'] = rsi
    
    # Ensure 'volume_24h' is present
    if 'volume_24h' not in indicators and symbol:
        volume_24h = get_symbol_volume(symbol)
        if volume_24h is not None:
            indicators['volume_24h'] = volume_24h
    
    # Ensure 'rvol_pct' and 'avg_volume_50d' are present
    if 'rvol_pct' not in indicators and bars:
        volume_24h = indicators.get('volume_24h')
        rvol_data = _calculate_rvol(bars, volume_24h, symbol)
        indicators['rvol_pct'] = rvol_data['rvol_pct']
        indicators['avg_volume_50d'] = rvol_data['avg_volume_50d']
    
    # Ensure frontend display indicators are always present (even if None)
    # These are required by the frontend ScreenerPanel component
    if 'bb_position' not in indicators:
        indicators['bb_position'] = None
    if 'adx' not in indicators:
        indicators['adx'] = None
    if 'atr_ratio' not in indicators:
        indicators['atr_ratio'] = None
    if 'change_24h_pct' not in indicators:
        indicators['change_24h_pct'] = None


def _evaluate_mean_reversion(
    strategy: Any,
    symbol: str,
    bars: List[Dict[str, Any]],
    volume_threshold: float = 1.5,
) -> Optional[SignalResult]:
    """
    Evaluate mean reversion strategy for a symbol with RVOL filtering.
    
    Skips symbols where RVOL is below the configured threshold to ensure
    only liquid/active symbols generate signals.
    
    Args:
        strategy: Strategy object with evaluate() method
        symbol: Trading symbol
        bars: OHLCV bar data
        volume_threshold: Minimum RVOL ratio (default 1.5 = 150%)
        
    Returns:
        SignalResult from strategy.evaluate(), or a SKIP result if RVOL below threshold
    """
    from backend.ingestor.symbols import get_symbol_volume
    
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    strategy_id = getattr(strategy, 'strategy_id', 'mean_reversion')
    
    # Get volume_24h for RVOL calculation
    volume_24h = get_symbol_volume(symbol)
    
    # Calculate RVOL
    rvol_data = _calculate_rvol(bars, volume_24h, symbol)
    rvol_pct = rvol_data.get("rvol_pct")
    
    # Calculate threshold as percentage (volume_threshold 1.5 = 150%)
    threshold_pct = volume_threshold * 100
    
    # Skip if RVOL is below threshold
    if rvol_pct is not None and rvol_pct < threshold_pct:
        logger.info(f"[SKIP] {symbol}: RVOL {rvol_pct}% below threshold {threshold_pct}%")
        return SignalResult(
            symbol=symbol,
            signal_type="NONE",
            confidence=0.0,
            strategy_id=strategy_id,
            indicators={
                "note": "rvol_below_threshold",
                "rvol_pct": rvol_pct,
                "threshold_pct": threshold_pct,
                "volume_threshold": volume_threshold,
            },
            timestamp=timestamp,
        )
    
    # Proceed with normal evaluation
    return strategy.evaluate(symbol, bars)


def _calculate_direction(indicators: Dict[str, Any], strategy_id: str) -> Literal["bullish", "bearish", "neutral"]:
    """
    Calculate market direction bias based on indicator values.
    
    Direction is determined by strategy type:
    - mean_reversion: RSI < 50 = bullish, RSI > 50 = bearish
    - momentum/trend_following: ROC > 0 = bullish, ROC < 0 = bearish
    - macd: histogram > 0 = bullish, histogram < 0 = bearish
    
    Args:
        indicators: Dictionary of indicator values
        strategy_id: Strategy identifier to determine which indicator to use
        
    Returns:
        Direction string: "bullish", "bearish", or "neutral"
    """
    # Check for MACD histogram
    histogram = indicators.get("histogram")
    if histogram is not None:
        if histogram > 0:
            return "bullish"
        elif histogram < 0:
            return "bearish"
        return "neutral"
    
    # Check for ROC (momentum/trend_following)
    roc = indicators.get("roc")
    if roc is not None:
        if roc > 0:
            return "bullish"
        elif roc < 0:
            return "bearish"
        return "neutral"
    
    # Check for RSI (mean_reversion)
    rsi = indicators.get("rsi")
    if rsi is not None:
        if rsi < 50:
            return "bullish"
        elif rsi > 50:
            return "bearish"
        return "neutral"
    
    return "neutral"


def _apply_confidence_threshold(
    signal_result: Any,
    confidence_buy: float = CONFIDENCE_THRESHOLD_PCT,
    confidence_sell: float = CONFIDENCE_THRESHOLD_PCT,
) -> None:
    """
    Apply confidence threshold to signal result and calculate direction.
    
    Signal determination based on confidence and direction:
    - If confidence >= threshold AND direction == bullish → BUY
    - If confidence >= threshold AND direction == bearish → SELL
    - If confidence < threshold → NONE (regardless of original signal)
    
    Direction is ALWAYS calculated for all signals (including NONE) so the
    frontend can display colored confidence bars based on market bias.
    
    Modifies signal_result in-place.
    
    Args:
        signal_result: SignalResult object to modify
        confidence_buy: Confidence threshold for BUY signals (default: 90.0)
        confidence_sell: Confidence threshold for SELL signals (default: 90.0)
    """
    # Get signal_type (handle both field names)
    signal_type = getattr(signal_result, 'signal_type', None) or getattr(signal_result, 'signal', 'NONE')
    confidence = getattr(signal_result, 'confidence', 0.0)
    strategy_id = getattr(signal_result, 'strategy_id', 'unknown')
    symbol = getattr(signal_result, 'symbol', 'unknown')
    
    # Get indicators dict
    indicators = getattr(signal_result, 'indicators', None) or getattr(signal_result, 'metadata', {})
    
    # ALWAYS calculate direction for all signals (including NONE)
    # This enables frontend to show colored confidence bars based on market bias
    direction = _calculate_direction(indicators, strategy_id)
    
    # Always add direction to indicators for display
    if indicators is not None:
        indicators['direction'] = direction
    
    signal_upper = signal_type.upper()
    original_signal = signal_upper
    new_signal = signal_upper
    
    # Determine final signal based on confidence vs threshold AND direction
    # This ensures consistent signal evaluation regardless of what strategy returned
    if direction == "bullish":
        threshold = confidence_buy
        if confidence >= threshold:
            new_signal = "BUY"
        else:
            new_signal = "NONE"
    elif direction == "bearish":
        threshold = confidence_sell
        if confidence >= threshold:
            new_signal = "SELL"
        else:
            new_signal = "NONE"
    else:
        # Neutral direction - use original signal logic for BUY/SELL
        threshold = confidence_buy if signal_upper == "BUY" else confidence_sell
        if signal_upper in ("BUY", "SELL") and confidence < threshold:
            new_signal = "NONE"
    
    # Debug logging: show confidence vs threshold evaluation
    logger.info(
        f"Symbol {symbol} confidence {confidence:.1f}% vs threshold {threshold:.1f}% -> {new_signal} "
        f"(direction={direction}, original={original_signal})"
    )
    
    # Apply signal change if needed
    if new_signal != original_signal:
        # Store original signal type in indicators for reference
        if indicators is not None:
            indicators['original_signal'] = original_signal
            indicators['threshold_filtered'] = (new_signal == "NONE")
        
        # Set signal_type (handle both field names)
        if hasattr(signal_result, 'signal_type'):
            # Need to work around dataclass immutability - use object.__setattr__
            object.__setattr__(signal_result, 'signal_type', new_signal)
        elif hasattr(signal_result, 'signal'):
            object.__setattr__(signal_result, 'signal', new_signal)


def calculate_signal_strength(
    rsi: float,
    bb_position: float,
    signal_type: Literal["BUY", "SELL"],
) -> float:
    """
    Calculate signal strength on a 0-100 scale.
    
    BUY signal strength (0-100):
    - RSI < 30: +50 points (more oversold = stronger)
    - Price below lower BB: +50 points (further below = stronger)
    
    SELL signal strength (0-100):
    - RSI > 70: +50 points (more overbought = stronger)
    - Price above upper BB: +50 points (further above = stronger)
    
    Example: RSI=25, price 2% below lower BB → strength=85
    
    Args:
        rsi: RSI value (0-100)
        bb_position: Position relative to Bollinger Bands (0=lower, 0.5=middle, 1=upper)
        signal_type: "BUY" or "SELL"
        
    Returns:
        Signal strength from 0-100
    """
    strength = 0.0
    
    if signal_type == "BUY":
        # RSI contribution: more oversold = stronger (RSI 0 = 50pts, RSI 30 = 0pts)
        if rsi < 30:
            rsi_strength = (30 - rsi) / 30 * 50
            strength += rsi_strength
        
        # BB position contribution: further below lower band = stronger
        # bb_position < 0 means below lower band, bb_position 0 means at lower band
        if bb_position < 0.2:
            bb_strength = (0.2 - bb_position) / 0.2 * 50
            strength += min(bb_strength, 50)  # Cap at 50
            
    elif signal_type == "SELL":
        # RSI contribution: more overbought = stronger (RSI 100 = 50pts, RSI 70 = 0pts)
        if rsi > 70:
            rsi_strength = (rsi - 70) / 30 * 50
            strength += rsi_strength
        
        # BB position contribution: further above upper band = stronger
        # bb_position > 1 means above upper band, bb_position 1 means at upper band
        if bb_position > 0.8:
            bb_strength = (bb_position - 0.8) / 0.2 * 50
            strength += min(bb_strength, 50)  # Cap at 50
    
    return round(min(strength, 100), 2)


class IndicatorCalculator:
    """Calculates technical indicators for a single symbol."""
    
    def __init__(
        self,
        lookback_period: int = 20,
        std_dev_multiplier: float = 2.0,
        rsi_period: int = 14,
    ):
        """
        Initialize indicator calculator.
        
        Args:
            lookback_period: Number of bars for SMA and Bollinger Bands
            std_dev_multiplier: Standard deviation multiplier for BB
            rsi_period: Number of bars for RSI calculation
        """
        self.lookback_period = lookback_period
        self.std_dev_multiplier = std_dev_multiplier
        self.rsi_period = rsi_period
    
    def calculate_sma(self, prices: List[float]) -> Optional[float]:
        """Calculate Simple Moving Average."""
        if len(prices) < self.lookback_period:
            return None
        recent = prices[-self.lookback_period:]
        return sum(recent) / len(recent)
    
    def calculate_std_dev(self, prices: List[float], mean: float) -> Optional[float]:
        """Calculate standard deviation."""
        if len(prices) < self.lookback_period:
            return None
        recent = prices[-self.lookback_period:]
        variance = sum((p - mean) ** 2 for p in recent) / len(recent)
        return variance ** 0.5
    
    def calculate_bollinger_bands(
        self, prices: List[float]
    ) -> Dict[str, Optional[float]]:
        """
        Calculate Bollinger Bands.
        
        Returns:
            Dict with keys: upper_band, middle_band, lower_band, bb_position
        """
        result = {
            "upper_band": None,
            "middle_band": None,
            "lower_band": None,
            "bb_position": None,
        }
        
        if len(prices) < self.lookback_period:
            return result
        
        current_price = prices[-1]
        sma = self.calculate_sma(prices)
        if sma is None:
            return result
        
        std_dev = self.calculate_std_dev(prices, sma)
        if std_dev is None:
            return result
        
        upper_band = sma + (self.std_dev_multiplier * std_dev)
        lower_band = sma - (self.std_dev_multiplier * std_dev)
        
        # Calculate position: 0 = at lower band, 0.5 = at middle, 1 = at upper band
        band_range = upper_band - lower_band
        if band_range > 0:
            bb_position = (current_price - lower_band) / band_range
        else:
            bb_position = 0.5
        
        result["upper_band"] = round(upper_band, 2)
        result["middle_band"] = round(sma, 2)
        result["lower_band"] = round(lower_band, 2)
        result["bb_position"] = round(bb_position, 4)
        
        return result
    
    def calculate_rsi(self, prices: List[float]) -> Optional[float]:
        """
        Calculate Relative Strength Index (RSI).
        
        Args:
            prices: List of closing prices
            
        Returns:
            RSI value (0-100) or None if insufficient data
        """
        if len(prices) < self.rsi_period + 1:
            return None
        
        # Calculate price changes
        changes = [
            prices[i] - prices[i - 1]
            for i in range(len(prices) - self.rsi_period, len(prices))
        ]
        
        gains = [c for c in changes if c > 0]
        losses = [-c for c in changes if c < 0]
        
        avg_gain = sum(gains) / self.rsi_period if gains else 0.0
        avg_loss = sum(losses) / self.rsi_period if losses else 0.0
        
        if avg_loss == 0.0:
            return 100.0 if avg_gain > 0 else 50.0
        
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        
        return round(rsi, 2)
    
    def calculate_all(self, prices: List[float]) -> Dict[str, Any]:
        """
        Calculate all indicators for a price series.
        
        Args:
            prices: List of closing prices (oldest first)
            
        Returns:
            Dictionary with all indicator values
        """
        bb = self.calculate_bollinger_bands(prices)
        rsi = self.calculate_rsi(prices)
        
        return {
            "current_price": prices[-1] if prices else None,
            "sma": bb["middle_band"],
            "upper_band": bb["upper_band"],
            "middle_band": bb["middle_band"],
            "lower_band": bb["lower_band"],
            "bb_position": bb["bb_position"],
            "rsi": rsi,
        }


class ScreenerEngine:
    """
    Scans all symbols and calculates signal strength.
    
    The screener evaluates multiple symbols using technical indicators
    (Bollinger Bands and RSI) to identify potential trading signals.
    """
    
    def __init__(
        self,
        lookback_period: int = 20,
        std_dev_multiplier: float = 2.0,
        rsi_period: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
    ):
        """
        Initialize the screener engine.
        
        Args:
            lookback_period: Number of bars for moving average
            std_dev_multiplier: Bollinger Band multiplier
            rsi_period: RSI calculation period
            rsi_oversold: RSI threshold for oversold (buy signal)
            rsi_overbought: RSI threshold for overbought (sell signal)
        """
        self.calculator = IndicatorCalculator(
            lookback_period=lookback_period,
            std_dev_multiplier=std_dev_multiplier,
            rsi_period=rsi_period,
        )
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.results: Dict[str, ScreenerResult] = {}
        
        logger.info(
            f"ScreenerEngine initialized: lookback={lookback_period}, "
            f"bb_mult={std_dev_multiplier}, rsi_period={rsi_period}, "
            f"oversold={rsi_oversold}, overbought={rsi_overbought}"
        )
    
    async def scan_symbol(
        self,
        symbol: str,
        bars: List[Dict[str, Any]],
    ) -> ScreenerResult:
        """
        Calculate indicators and signal for one symbol.
        
        Args:
            symbol: Trading pair (e.g., "ETH/USD")
            bars: List of OHLCV bar dictionaries with 'close' key
            
        Returns:
            ScreenerResult with signal type, strength, and indicators
        """
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        # Extract closing prices from bars
        prices = [float(bar.get("close", 0)) for bar in bars if bar.get("close")]
        
        min_required = max(self.calculator.lookback_period, self.calculator.rsi_period + 1)
        
        if len(prices) < min_required:
            logger.info(
                f"[SKIP] {symbol}: Insufficient data - {len(prices)} bars available, "
                f"need {min_required} (lookback={self.calculator.lookback_period}, rsi={self.calculator.rsi_period})"
            )
            return ScreenerResult(
                symbol=symbol,
                signal_type="NONE",
                signal_strength=0.0,
                indicators={"error": "insufficient_data", "bars_available": len(prices), "bars_required": min_required},
                timestamp=timestamp,
            )
        
        # Calculate all indicators
        indicators = self.calculator.calculate_all(prices)
        
        # Add 24h volume from cached ticker data
        volume_24h = get_symbol_volume(symbol)
        if volume_24h is not None:
            indicators['volume_24h'] = volume_24h
        
        # Add RVOL percentage (daily vol vs 50-day daily MA)
        rvol_data = _calculate_rvol(bars, volume_24h, symbol)
        indicators['rvol_pct'] = rvol_data['rvol_pct']
        indicators['avg_volume_50d'] = rvol_data['avg_volume_50d']

        rsi = indicators.get("rsi")
        bb_position = indicators.get("bb_position")
        
        if rsi is None or bb_position is None:
            return ScreenerResult(
                symbol=symbol,
                signal_type="NONE",
                signal_strength=0.0,
                indicators=indicators,
                timestamp=timestamp,
            )
        
        # Determine signal type
        signal_type: Literal["BUY", "SELL", "NONE"] = "NONE"
        signal_strength = 0.0
        
        # BUY signal: oversold RSI + price near lower band
        if rsi < self.rsi_oversold and bb_position < 0.2:
            signal_type = "BUY"
            signal_strength = calculate_signal_strength(rsi, bb_position, "BUY")
            
        # SELL signal: overbought RSI + price near upper band
        elif rsi > self.rsi_overbought and bb_position > 0.8:
            signal_type = "SELL"
            signal_strength = calculate_signal_strength(rsi, bb_position, "SELL")
        
        result = ScreenerResult(
            symbol=symbol,
            signal_type=signal_type,
            signal_strength=signal_strength,
            indicators=indicators,
            timestamp=timestamp,
        )
        
        # Cache result
        self.results[symbol] = result
        
        # Log evaluation result for all symbols (INFO level for visibility)
        if signal_type != "NONE":
            logger.info(
                f"[SIGNAL] {symbol}: {signal_type} (strength={signal_strength:.1f}%, "
                f"rsi={rsi:.2f}, bb_pos={bb_position:.4f})"
            )
        else:
            logger.info(
                f"[EVAL] {symbol}: NONE (rsi={rsi:.2f}, bb_pos={bb_position:.4f}, "
                f"oversold<{self.rsi_oversold}, overbought>{self.rsi_overbought})"
            )
        
        return result
    
    async def scan_all(
        self,
        symbols_bars: Dict[str, List[Dict[str, Any]]],
    ) -> List[ScreenerResult]:
        """
        Scan all provided symbols.
        
        Args:
            symbols_bars: Dictionary mapping symbol to list of OHLCV bars
            
        Returns:
            List of ScreenerResult for all symbols
        """
        results = []
        
        # Log summary of available bars per symbol
        bars_summary = {sym: len(bars) for sym, bars in symbols_bars.items()}
        total_symbols = len(symbols_bars)
        symbols_with_data = sum(1 for count in bars_summary.values() if count >= MIN_BARS_THRESHOLD)
        
        logger.info(
            f"[SCAN] Starting scan of {total_symbols} symbols "
            f"({symbols_with_data} with sufficient data >= {MIN_BARS_THRESHOLD} bars)"
        )
        logger.info(f"[SCAN] Bar counts: {bars_summary}")
        
        for symbol, bars in symbols_bars.items():
            result = await self.scan_symbol(symbol, bars)
            results.append(result)
        
        # Log scan summary
        signals = [r for r in results if r.signal_type != "NONE"]
        logger.info(
            f"[SCAN] Completed: {len(results)} symbols scanned, "
            f"{len(signals)} signals generated (BUY: {sum(1 for r in signals if r.signal_type == 'BUY')}, "
            f"SELL: {sum(1 for r in signals if r.signal_type == 'SELL')})"
        )
        
        return results
    
    def get_signals(
        self,
        signal_type: Optional[Literal["BUY", "SELL"]] = None,
        min_strength: float = 0.0,
    ) -> List[ScreenerResult]:
        """
        Get cached results filtered by signal type and minimum strength.
        
        Args:
            signal_type: Filter by signal type (None = all signals)
            min_strength: Minimum signal strength threshold
            
        Returns:
            List of matching ScreenerResult objects
        """
        results = []
        
        for result in self.results.values():
            if signal_type and result.signal_type != signal_type:
                continue
            if result.signal_type == "NONE":
                continue
            if result.signal_strength < min_strength:
                continue
            results.append(result)
        
        # Sort by strength descending
        results.sort(key=lambda r: r.signal_strength, reverse=True)
        return results


async def scan_with_strategy(
    strategy: Any,
    symbols_bars: Dict[str, List[Dict[str, Any]]],
    confidence_buy: float = 90.0,
    confidence_sell: float = 90.0,
) -> List[SignalResult]:
    """
    Scan all symbols using a strategy's evaluate method.
    
    This method integrates with T62's strategy.evaluate() interface.
    
    Args:
        strategy: Strategy object with evaluate(symbol, bars) -> SignalResult method
        symbols_bars: Dictionary mapping symbol to list of OHLCV bars
        confidence_buy: Confidence threshold for BUY signals (default: 90.0)
        confidence_sell: Confidence threshold for SELL signals (default: 90.0)
        
    Returns:
        List of SignalResult sorted by confidence descending.
        Includes ALL evaluated symbols (NONE, BUY, SELL) plus skipped symbols.
    """
    results: List[SignalResult] = []
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    strategy_id = getattr(strategy, 'strategy_id', 'unknown')
    
    logger.info(f"[STRATEGY:{strategy_id}] Starting evaluation of {len(symbols_bars)} symbols")
    
    evaluated_count = 0
    skipped_count = 0
    
    for symbol, bars in symbols_bars.items():
        bar_count = len(bars) if bars else 0
        
        if bar_count < MIN_BARS_THRESHOLD:
            logger.info(
                f"[STRATEGY:{strategy_id}] {symbol}: SKIP - {bar_count} bars < {MIN_BARS_THRESHOLD} required"
            )
            skipped_count += 1
            # Create a NONE result for skipped symbols so frontend shows all symbols
            skip_result = SignalResult(
                symbol=symbol,
                signal_type="NONE",
                confidence=0.0,
                strategy_id=strategy_id,
                indicators={
                    "note": "insufficient_data",
                    "bars_available": bar_count,
                    "bars_required": MIN_BARS_THRESHOLD,
                },
                timestamp=timestamp,
            )
            results.append(skip_result)
            continue
            
        try:
            logger.debug(f"[STRATEGY:{strategy_id}] Evaluating {symbol} with {bar_count} bars")
            
            # Call strategy.evaluate() (provided by T62)
            # This returns a SignalResult with confidence
            signal_result = strategy.evaluate(symbol, bars)
            evaluated_count += 1
            
            if signal_result is None:
                logger.info(f"[STRATEGY:{strategy_id}] {symbol}: evaluate() returned None")
                # Create a NONE result for symbols where evaluate() returned None
                none_result = SignalResult(
                    symbol=symbol,
                    signal_type="NONE",
                    confidence=0.0,
                    strategy_id=strategy_id,
                    indicators={"note": "evaluate_returned_none"},
                    timestamp=timestamp,
                )
                results.append(none_result)
                continue
            
            # Ensure timestamp is set
            if not signal_result.timestamp:
                signal_result.timestamp = timestamp
            
            # Store bar timestamp in indicators for debouncing
            latest_bar_ts = bars[-1].get("timestamp") if bars else None
            if latest_bar_ts:
                indicators = getattr(signal_result, 'indicators', None) or getattr(signal_result, 'metadata', {})
                if isinstance(indicators, dict):
                    indicators['bar_timestamp'] = latest_bar_ts
            
            # Ensure RSI, price, and volume_24h are in indicators for frontend display
            _ensure_indicators(signal_result, bars, symbol)
            
            # Apply confidence thresholds: signals below threshold become NONE
            # but preserve direction (bullish/bearish) and original confidence
            _apply_confidence_threshold(signal_result, confidence_buy, confidence_sell)
            
            # Log all results, including NONE
            signal_type = getattr(signal_result, 'signal', None) or getattr(signal_result, 'signal_type', 'UNKNOWN')
            confidence = getattr(signal_result, 'confidence', 0.0)
            
            # Get direction from indicators for logging
            indicators = getattr(signal_result, 'indicators', None) or getattr(signal_result, 'metadata', {})
            direction = indicators.get('direction', 'neutral') if indicators else 'neutral'
            
            # Include ALL results (NONE, BUY, SELL) so frontend can show evaluation status
            results.append(signal_result)
            logger.info(
                f"[STRATEGY:{strategy_id}] {symbol}: {signal_type} "
                f"(confidence={confidence:.1f}%, direction={direction})"
            )
            
            # Log SETUP_DETECTED only for actionable setups:
            # - BUY signals ONLY when no position exists (skip if already holding)
            # - SELL signals ONLY when there is an active position (skip no-shorting cases)
            if signal_type in ("BUY", "SELL"):
                try:
                    from backend.positions.tracker import get_position_tracker
                    _has_pos = get_position_tracker().has_position(symbol)
                except Exception:
                    _has_pos = False
                should_log = (signal_type == "BUY" and not _has_pos) or (signal_type == "SELL" and _has_pos)
                if should_log:
                    from backend.api.routes.events import log_activity
                    display_name = getattr(strategy, "strategy_name", None) or strategy_id
                    log_activity(
                        activity_type="SETUP_DETECTED",
                        message=f"Setup detected: {signal_type} {symbol} [{display_name}]",
                        details={
                            "symbol": symbol,
                            "signal_type": signal_type,
                            "confidence": confidence,
                            "strategy": strategy_id,
                            "direction": direction,
                            "stage": "evaluation",
                        },
                    )
                
        except Exception as e:
            logger.warning(f"[STRATEGY:{strategy_id}] {symbol}: ERROR - {e}")
            # Create a NONE result for symbols that errored
            error_result = SignalResult(
                symbol=symbol,
                signal_type="NONE",
                confidence=0.0,
                strategy_id=strategy_id,
                indicators={
                    "note": "evaluation_error", 
                    "error": str(e),
                    # Include frontend indicators as None for consistency
                    "bb_position": None,
                    "adx": None,
                    "atr_ratio": None,
                },
                timestamp=timestamp,
            )
            results.append(error_result)
            continue
    
    # Sort by: 1) has data (not insufficient_data) first, 2) confidence descending
    # This ensures BTC/ETH with real data appear at top, meme coins without data at bottom
    def sort_key(r: SignalResult) -> tuple:
        # Handle both field names for different SignalResult types
        # (backend.screener.models uses 'metadata', research.strategies.types uses 'indicators')
        metadata = getattr(r, 'metadata', None) or getattr(r, 'indicators', None) or {}
        has_insufficient_data = metadata.get("note") == "insufficient_data"
        # has_data=True (0) sorts before has_data=False (1)
        return (1 if has_insufficient_data else 0, -r.confidence)
    
    results.sort(key=sort_key)
    
    logger.info(
        f"[STRATEGY:{strategy_id}] Completed: {evaluated_count} evaluated, "
        f"{skipped_count} skipped, {len(results)} total results"
    )
    
    return results

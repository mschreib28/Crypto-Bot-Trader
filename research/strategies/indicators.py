"""Shared technical indicator calculations for strategy modules.

This module provides reusable indicator calculations used across multiple strategies
for A+ setup detection. All functions work with lists of floats (oldest to newest).

Indicators provided:
- EMA (Exponential Moving Average)
- SMA (Simple Moving Average)
- ADX (Average Directional Index) - trend strength
- ATR (Average True Range) - volatility
- RSI (Relative Strength Index) - momentum oscillator
- Volume ratio - volume confirmation
- VWAP (Volume-Weighted Average Price)
- Bollinger Band Width
- Swing High/Low detection
- EMA slope calculation
"""

from typing import Dict, List, Optional, Tuple


def calculate_sma(prices: List[float], period: int) -> Optional[float]:
    """
    Calculate Simple Moving Average.
    
    Args:
        prices: List of prices (oldest to newest)
        period: Lookback period
        
    Returns:
        SMA value or None if insufficient data
    """
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def calculate_ema(prices: List[float], period: int) -> Optional[float]:
    """
    Calculate Exponential Moving Average (current value only).
    
    Uses SMA as seed for first EMA value, then applies EMA formula.
    
    Args:
        prices: List of prices (oldest to newest)
        period: EMA period
        
    Returns:
        Current EMA value or None if insufficient data
    """
    if len(prices) < period:
        return None
    
    multiplier = 2.0 / (period + 1)
    
    # Seed with SMA
    ema = sum(prices[:period]) / period
    
    # Calculate EMA for remaining values
    for price in prices[period:]:
        ema = price * multiplier + ema * (1 - multiplier)
    
    return ema


def calculate_ema_series(prices: List[float], period: int) -> List[float]:
    """
    Calculate EMA series (all values).
    
    Args:
        prices: List of prices (oldest to newest)
        period: EMA period
        
    Returns:
        List of EMA values (same length as prices, early values are 0)
    """
    if len(prices) < period:
        return []
    
    multiplier = 2.0 / (period + 1)
    ema = [0.0] * len(prices)
    
    # Seed with SMA
    ema[period - 1] = sum(prices[:period]) / period
    
    # Calculate EMA for remaining values
    for i in range(period, len(prices)):
        ema[i] = prices[i] * multiplier + ema[i - 1] * (1 - multiplier)
    
    return ema


def calculate_rsi(prices: List[float], period: int = 14) -> Optional[float]:
    """
    Calculate Relative Strength Index.
    
    Args:
        prices: List of closing prices (oldest to newest)
        period: RSI period (default: 14)
        
    Returns:
        RSI value (0-100) or None if insufficient data
    """
    if len(prices) < period + 1:
        return None
    
    # Calculate price changes
    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    
    # Use recent changes
    recent_changes = changes[-period:]
    
    # Separate gains and losses
    gains = [c for c in recent_changes if c > 0]
    losses = [-c for c in recent_changes if c < 0]
    
    # Calculate averages
    avg_gain = sum(gains) / period if gains else 0.0
    avg_loss = sum(losses) / period if losses else 0.0
    
    # Avoid division by zero
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0 else 50.0
    
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    
    return rsi


def calculate_true_range(
    high: float, low: float, prev_close: float
) -> float:
    """
    Calculate True Range for a single bar.
    
    TR = max(high - low, abs(high - prev_close), abs(low - prev_close))
    
    Args:
        high: Current bar high
        low: Current bar low
        prev_close: Previous bar close
        
    Returns:
        True Range value
    """
    return max(
        high - low,
        abs(high - prev_close),
        abs(low - prev_close)
    )


def calculate_atr(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14
) -> Optional[float]:
    """
    Calculate Average True Range.
    
    Args:
        highs: List of high prices
        lows: List of low prices
        closes: List of close prices
        period: ATR period (default: 14)
        
    Returns:
        ATR value or None if insufficient data
    """
    if len(highs) < period + 1:
        return None
    
    # Calculate True Range series
    tr_values = []
    for i in range(1, len(highs)):
        tr = calculate_true_range(highs[i], lows[i], closes[i - 1])
        tr_values.append(tr)
    
    if len(tr_values) < period:
        return None
    
    # First ATR is SMA of TR
    atr = sum(tr_values[:period]) / period
    
    # Smooth with EMA-style calculation
    multiplier = 1.0 / period
    for tr in tr_values[period:]:
        atr = tr * multiplier + atr * (1 - multiplier)
    
    return atr


def calculate_adx(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14
) -> Optional[float]:
    """
    Calculate Average Directional Index (ADX).
    
    ADX measures trend strength regardless of direction.
    - ADX > 25: Strong trend (good for trend-following)
    - ADX < 20: Weak trend / ranging (good for mean reversion)
    
    Args:
        highs: List of high prices
        lows: List of low prices
        closes: List of close prices
        period: ADX period (default: 14)
        
    Returns:
        ADX value (0-100) or None if insufficient data
    """
    if len(highs) < period * 2:
        return None
    
    # Calculate +DM, -DM, and TR
    plus_dm = []
    minus_dm = []
    tr_values = []
    
    for i in range(1, len(highs)):
        # Directional Movement
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        
        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0.0)
        
        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0.0)
        
        # True Range
        tr = calculate_true_range(highs[i], lows[i], closes[i - 1])
        tr_values.append(tr)
    
    if len(tr_values) < period:
        return None
    
    # Smooth +DM, -DM, TR using Wilder's smoothing
    def wilder_smooth(values: List[float], period: int) -> List[float]:
        if len(values) < period:
            return []
        
        smoothed = [sum(values[:period])]
        for val in values[period:]:
            smoothed.append(smoothed[-1] - smoothed[-1] / period + val)
        return smoothed
    
    smooth_plus_dm = wilder_smooth(plus_dm, period)
    smooth_minus_dm = wilder_smooth(minus_dm, period)
    smooth_tr = wilder_smooth(tr_values, period)
    
    if not smooth_tr or smooth_tr[-1] == 0:
        return None
    
    # Calculate +DI and -DI
    plus_di = []
    minus_di = []
    
    for i in range(len(smooth_tr)):
        if smooth_tr[i] == 0:
            plus_di.append(0.0)
            minus_di.append(0.0)
        else:
            plus_di.append(100.0 * smooth_plus_dm[i] / smooth_tr[i])
            minus_di.append(100.0 * smooth_minus_dm[i] / smooth_tr[i])
    
    # Calculate DX
    dx = []
    for i in range(len(plus_di)):
        di_sum = plus_di[i] + minus_di[i]
        if di_sum == 0:
            dx.append(0.0)
        else:
            dx.append(100.0 * abs(plus_di[i] - minus_di[i]) / di_sum)
    
    if len(dx) < period:
        return None
    
    # ADX is smoothed DX
    adx = sum(dx[:period]) / period
    for d in dx[period:]:
        adx = (adx * (period - 1) + d) / period
    
    return adx


def calculate_volume_ratio(
    volumes: List[float],
    period: int = 20
) -> Optional[float]:
    """
    Calculate volume ratio (current volume / average volume).
    
    Used for volume confirmation:
    - Ratio > 1.5: Strong volume confirmation
    - Ratio < 0.5: Weak volume, potential false signal
    
    Args:
        volumes: List of volume values (oldest to newest)
        period: Lookback period for average (default: 20)
        
    Returns:
        Volume ratio or None if insufficient data
    """
    if len(volumes) < period + 1:
        return None
    
    # Average of previous 'period' bars (excluding current)
    avg_volume = sum(volumes[-(period + 1):-1]) / period
    
    if avg_volume == 0:
        return None
    
    current_volume = volumes[-1]
    return current_volume / avg_volume


def check_ema_stack_bullish(
    prices: List[float],
    fast: int = 20,
    medium: int = 50,
    slow: int = 200
) -> Tuple[bool, Optional[float], Optional[float], Optional[float]]:
    """
    Check if EMAs are stacked bullish (fast > medium > slow).
    
    Args:
        prices: List of closing prices
        fast: Fast EMA period (default: 20)
        medium: Medium EMA period (default: 50)
        slow: Slow EMA period (default: 200)
        
    Returns:
        Tuple of (is_bullish_stack, ema_fast, ema_medium, ema_slow)
    """
    ema_fast = calculate_ema(prices, fast)
    ema_medium = calculate_ema(prices, medium)
    ema_slow = calculate_ema(prices, slow)
    
    if ema_fast is None or ema_medium is None or ema_slow is None:
        return (False, ema_fast, ema_medium, ema_slow)
    
    is_bullish = ema_fast > ema_medium > ema_slow
    return (is_bullish, ema_fast, ema_medium, ema_slow)


def check_ema_stack_bearish(
    prices: List[float],
    fast: int = 20,
    medium: int = 50,
    slow: int = 200
) -> Tuple[bool, Optional[float], Optional[float], Optional[float]]:
    """
    Check if EMAs are stacked bearish (fast < medium < slow).
    
    Args:
        prices: List of closing prices
        fast: Fast EMA period (default: 20)
        medium: Medium EMA period (default: 50)
        slow: Slow EMA period (default: 200)
        
    Returns:
        Tuple of (is_bearish_stack, ema_fast, ema_medium, ema_slow)
    """
    ema_fast = calculate_ema(prices, fast)
    ema_medium = calculate_ema(prices, medium)
    ema_slow = calculate_ema(prices, slow)
    
    if ema_fast is None or ema_medium is None or ema_slow is None:
        return (False, ema_fast, ema_medium, ema_slow)
    
    is_bearish = ema_fast < ema_medium < ema_slow
    return (is_bearish, ema_fast, ema_medium, ema_slow)


def calculate_atr_ratio(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    atr_period: int = 14,
    avg_period: int = 20
) -> Optional[float]:
    """
    Calculate ATR ratio (current ATR / average ATR).
    
    Used to detect market activity:
    - Ratio > 1.0: Higher than average volatility (active market)
    - Ratio < 1.0: Lower than average volatility (quiet market)
    
    Args:
        highs: List of high prices
        lows: List of low prices
        closes: List of close prices
        atr_period: ATR calculation period
        avg_period: Period for averaging ATR
        
    Returns:
        ATR ratio or None if insufficient data
    """
    # Need enough data to calculate ATR for avg_period bars
    if len(highs) < atr_period + avg_period + 1:
        return None
    
    # Calculate current ATR
    current_atr = calculate_atr(highs, lows, closes, atr_period)
    if current_atr is None:
        return None
    
    # Calculate historical ATRs for averaging
    historical_atrs = []
    for i in range(avg_period):
        end_idx = len(highs) - i
        start_idx = max(0, end_idx - atr_period - 1)
        
        atr = calculate_atr(
            highs[start_idx:end_idx],
            lows[start_idx:end_idx],
            closes[start_idx:end_idx],
            atr_period
        )
        if atr is not None:
            historical_atrs.append(atr)
    
    if len(historical_atrs) < avg_period // 2:
        return None
    
    avg_atr = sum(historical_atrs) / len(historical_atrs)
    
    if avg_atr == 0:
        return None
    
    return current_atr / avg_atr


def calculate_vwap(
    prices: List[float],
    volumes: List[float],
    anchor_index: Optional[int] = None
) -> Optional[float]:
    """
    Calculate Volume-Weighted Average Price (VWAP).
    
    If anchor_index is provided, calculates Anchored VWAP from that point.
    Otherwise, calculates session VWAP from the start of the data.
    
    Args:
        prices: List of typical prices (usually (high + low + close) / 3)
        volumes: List of volume values (same length as prices)
        anchor_index: Optional index to anchor VWAP from (None = from start)
        
    Returns:
        VWAP value or None if insufficient data
        
    Example:
        >>> prices = [100.0, 101.0, 102.0]
        >>> volumes = [1000.0, 2000.0, 1500.0]
        >>> vwap = calculate_vwap(prices, volumes)
        >>> # VWAP = (100*1000 + 101*2000 + 102*1500) / (1000+2000+1500)
    """
    if len(prices) != len(volumes):
        return None
    
    if len(prices) == 0:
        return None
    
    start_idx = anchor_index if anchor_index is not None else 0
    if start_idx < 0 or start_idx >= len(prices):
        return None
    
    # Calculate typical price * volume for each bar
    typical_prices = prices[start_idx:]
    typical_volumes = volumes[start_idx:]
    
    if len(typical_prices) == 0:
        return None
    
    # Sum of (price * volume) and sum of volumes
    cumulative_pv = sum(p * v for p, v in zip(typical_prices, typical_volumes))
    cumulative_volume = sum(typical_volumes)
    
    if cumulative_volume == 0:
        return None
    
    return cumulative_pv / cumulative_volume


def calculate_bollinger_bands(
    prices: List[float],
    period: int = 20,
    std_dev_mult: float = 2.0
) -> Optional[Dict[str, float]]:
    """
    Calculate Bollinger Bands.
    
    Args:
        prices: List of closing prices (oldest to newest)
        period: SMA period (default: 20)
        std_dev_mult: Standard deviation multiplier (default: 2.0)
        
    Returns:
        Dict with keys 'upper', 'middle', 'lower' or None if insufficient data
    """
    if len(prices) < period:
        return None
    
    recent_prices = prices[-period:]
    
    # Calculate SMA (middle band)
    sma = sum(recent_prices) / len(recent_prices)
    
    # Calculate standard deviation
    variance = sum((p - sma) ** 2 for p in recent_prices) / len(recent_prices)
    std_dev = variance ** 0.5
    
    # Calculate bands
    upper_band = sma + (std_dev_mult * std_dev)
    lower_band = sma - (std_dev_mult * std_dev)
    
    return {
        'upper': upper_band,
        'middle': sma,
        'lower': lower_band
    }


def calculate_bb_width(
    upper_band: float,
    lower_band: float,
    middle_band: float
) -> Optional[float]:
    """
    Calculate normalized Bollinger Band width.
    
    BB Width = (upper_band - lower_band) / middle_band
    
    Used to detect volatility compression (squeeze):
    - Low BB Width = compressed volatility (potential breakout setup)
    - High BB Width = expanded volatility (breakout may have occurred)
    
    Args:
        upper_band: Upper Bollinger Band value
        lower_band: Lower Bollinger Band value
        middle_band: Middle Bollinger Band (SMA) value
        
    Returns:
        Normalized BB Width or None if middle_band is zero
    """
    if middle_band == 0:
        return None
    
    return (upper_band - lower_band) / middle_band


def calculate_adx_full(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14
) -> Optional[Dict[str, float]]:
    """
    Calculate ADX with +DI and -DI components.
    
    Returns a dictionary with 'adx', 'plus_di', and 'minus_di' values.
    
    Args:
        highs: List of high prices
        lows: List of low prices
        closes: List of close prices
        period: ADX period (default: 14)
        
    Returns:
        Dict with keys 'adx', 'plus_di', 'minus_di' or None if insufficient data
        
    Example:
        >>> result = calculate_adx_full(highs, lows, closes)
        >>> if result:
        ...     adx = result['adx']
        ...     plus_di = result['plus_di']
        ...     minus_di = result['minus_di']
    """
    if len(highs) < period * 2:
        return None
    
    # Calculate +DM, -DM, and TR
    plus_dm = []
    minus_dm = []
    tr_values = []
    
    for i in range(1, len(highs)):
        # Directional Movement
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        
        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0.0)
        
        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0.0)
        
        # True Range
        tr = calculate_true_range(highs[i], lows[i], closes[i - 1])
        tr_values.append(tr)
    
    if len(tr_values) < period:
        return None
    
    # Smooth +DM, -DM, TR using Wilder's smoothing
    def wilder_smooth(values: List[float], period: int) -> List[float]:
        if len(values) < period:
            return []
        
        smoothed = [sum(values[:period])]
        for val in values[period:]:
            smoothed.append(smoothed[-1] - smoothed[-1] / period + val)
        return smoothed
    
    smooth_plus_dm = wilder_smooth(plus_dm, period)
    smooth_minus_dm = wilder_smooth(minus_dm, period)
    smooth_tr = wilder_smooth(tr_values, period)
    
    if not smooth_tr or smooth_tr[-1] == 0:
        return None
    
    # Calculate +DI and -DI
    plus_di = []
    minus_di = []
    
    for i in range(len(smooth_tr)):
        if smooth_tr[i] == 0:
            plus_di.append(0.0)
            minus_di.append(0.0)
        else:
            plus_di.append(100.0 * smooth_plus_dm[i] / smooth_tr[i])
            minus_di.append(100.0 * smooth_minus_dm[i] / smooth_tr[i])
    
    # Calculate DX
    dx = []
    for i in range(len(plus_di)):
        di_sum = plus_di[i] + minus_di[i]
        if di_sum == 0:
            dx.append(0.0)
        else:
            dx.append(100.0 * abs(plus_di[i] - minus_di[i]) / di_sum)
    
    if len(dx) < period:
        return None
    
    # ADX is smoothed DX
    adx = sum(dx[:period]) / period
    for d in dx[period:]:
        adx = (adx * (period - 1) + d) / period
    
    return {
        'adx': adx,
        'plus_di': plus_di[-1] if plus_di else 0.0,
        'minus_di': minus_di[-1] if minus_di else 0.0
    }


def detect_swing_highs_lows(
    bars: List,
    lookback: int = 3
) -> Dict[str, List[float]]:
    """
    Detect swing highs and lows using lookback bars.
    
    A swing high is a high that is higher than N bars on each side.
    A swing low is a low that is lower than N bars on each side.
    
    Args:
        bars: List of bar objects (MarketDataEvent or dict) with 'high', 'low' attributes/keys
        lookback: Number of bars to look back/forward (default: 3)
        
    Returns:
        Dict with keys:
        - 'highs': List of swing high prices
        - 'lows': List of swing low prices
        - 'high_indices': List of indices where swing highs occurred
        - 'low_indices': List of indices where swing lows occurred
        
    Example:
        >>> bars = [
        ...     MarketDataEvent(high=100, low=95, close=98, ...),
        ...     MarketDataEvent(high=102, low=97, close=100, ...),
        ...     MarketDataEvent(high=105, low=99, close=103, ...),  # Swing high
        ... ]
        >>> result = detect_swing_highs_lows(bars, lookback=2)
    """
    if len(bars) < lookback * 2 + 1:
        return {
            'highs': [],
            'lows': [],
            'high_indices': [],
            'low_indices': []
        }
    
    # Helper to get high/low from bar (supports both dict and object)
    def get_high(bar):
        return bar['high'] if isinstance(bar, dict) else bar.high
    
    def get_low(bar):
        return bar['low'] if isinstance(bar, dict) else bar.low
    
    swing_highs = []
    swing_lows = []
    high_indices = []
    low_indices = []
    
    for i in range(lookback, len(bars) - lookback):
        current_high = get_high(bars[i])
        current_low = get_low(bars[i])
        
        # Check if current high is higher than lookback bars on each side
        is_swing_high = True
        for j in range(i - lookback, i + lookback + 1):
            if j != i and get_high(bars[j]) >= current_high:
                is_swing_high = False
                break
        
        if is_swing_high:
            swing_highs.append(current_high)
            high_indices.append(i)
        
        # Check if current low is lower than lookback bars on each side
        is_swing_low = True
        for j in range(i - lookback, i + lookback + 1):
            if j != i and get_low(bars[j]) <= current_low:
                is_swing_low = False
                break
        
        if is_swing_low:
            swing_lows.append(current_low)
            low_indices.append(i)
    
    return {
        'highs': swing_highs,
        'lows': swing_lows,
        'high_indices': high_indices,
        'low_indices': low_indices
    }


def calculate_ema_slope(
    ema_values: List[float],
    bars: int = 5
) -> Optional[float]:
    """
    Calculate EMA slope over last N bars.
    
    Slope is calculated as percentage change: (current - N_bars_ago) / N_bars_ago * 100
    
    Args:
        ema_values: List of EMA values (oldest to newest)
        bars: Number of bars to calculate slope over (default: 5)
        
    Returns:
        Slope as percentage change or None if insufficient data
        
    Example:
        >>> ema_values = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
        >>> slope = calculate_ema_slope(ema_values, bars=5)
        >>> # slope = (105 - 100) / 100 * 100 = 5.0%
    """
    if len(ema_values) < bars + 1:
        return None
    
    current_ema = ema_values[-1]
    past_ema = ema_values[-bars - 1]
    
    if past_ema == 0:
        return None
    
    slope = ((current_ema - past_ema) / past_ema) * 100.0
    return slope

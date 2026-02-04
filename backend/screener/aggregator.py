"""Bar aggregation utility for converting bars to larger timeframes."""

from typing import List, Dict, Any


INTERVAL_MINUTES = {
    '1m': 1,
    '5m': 5,
    '10m': 10,
    '15m': 15,
    '30m': 30,
    '1h': 60,
    '4h': 240,
    '1d': 1440,
}


def aggregate_bars(
    source_bars: List[Dict[str, Any]],
    target_interval: str,
    source_interval: str = None,
) -> List[Dict[str, Any]]:
    """
    Aggregate bars from a smaller timeframe to a larger timeframe.
    
    Args:
        source_bars: List of OHLCV bars (oldest first)
        target_interval: Target interval (e.g., '5m', '15m', '1h', '4h')
        source_interval: Source interval (auto-detected from bars if None)
        
    Returns:
        List of aggregated bars
    """
    if not source_bars:
        return []
    
    # Auto-detect source interval from bar data
    if source_interval is None:
        source_interval = source_bars[0].get('interval', '1m')
    
    source_minutes = INTERVAL_MINUTES.get(source_interval, 1)
    target_minutes = INTERVAL_MINUTES.get(target_interval, 5)
    
    # If source is same as target, return as-is
    if source_minutes >= target_minutes:
        return source_bars
    
    # Calculate how many source bars per target bar
    bars_per_chunk = target_minutes // source_minutes
    
    aggregated = []
    
    for i in range(0, len(source_bars), bars_per_chunk):
        chunk = source_bars[i:i + bars_per_chunk]
        if len(chunk) < bars_per_chunk:
            break  # Incomplete bar, skip
        
        agg_bar = {
            'symbol': chunk[0].get('symbol'),
            'interval': target_interval,
            'open': chunk[0].get('open'),
            'high': max(b.get('high', 0) for b in chunk),
            'low': min(b.get('low', float('inf')) for b in chunk),
            'close': chunk[-1].get('close'),
            'volume': sum(b.get('volume', 0) for b in chunk),
            'timestamp': chunk[-1].get('timestamp'),
        }
        aggregated.append(agg_bar)
    
    return aggregated

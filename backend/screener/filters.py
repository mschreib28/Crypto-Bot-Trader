"""Symbol filtering for A+ scoring system.

Filters out low-quality symbols before scoring to reduce computational load
and focus on tradeable pairs.
"""

import logging
from typing import Optional

from backend.ingestor.symbols import get_symbol_volume, get_symbol_spread, is_stablecoin_pair
from backend.screener.coingecko import get_market_data

logger = logging.getLogger(__name__)

# Minimum market cap threshold (below this = likely pump & dump or meme coin)
MIN_MARKET_CAP_USD = 50_000_000  # $50M minimum

# Minimum 24h volume threshold (already in config, but using here for consistency)
MIN_24H_VOLUME_USD = 10_000_000  # $10M minimum

# Maximum spread threshold (already in config)
MAX_SPREAD_BPS = 15.0  # 15 bps = 0.15%

# Meme coin indicators (common patterns in symbol names)
MEME_COIN_PATTERNS = {
    "DOGE", "SHIB", "FLOKI", "PEPE", "BONK", "WIF", "BOME", "MYRO", "POPCAT",
    "MEME", "TURBO", "CHAD", "GIGA", "TRUMP", "BIDEN", "MAGA", "MOON",
    # Add more as needed
}

# Known low-quality or problematic coins
BLACKLISTED_SYMBOLS = {
    # Add specific symbols to blacklist here
    # Example: "SUSPICIOUS/USD", "SCAM/USD"
}


def should_filter_symbol(
    symbol: str,
    market_cap: Optional[float] = None,
    volume_24h: Optional[float] = None,
    spread_bps: Optional[float] = None,
) -> tuple[bool, str]:
    """
    Determine if a symbol should be filtered out before A+ scoring.
    
    Args:
        symbol: Trading pair symbol (e.g., "BTC/USD")
        market_cap: Market capitalization in USD (optional, fetched if None)
        volume_24h: 24h volume in USD (optional, fetched if None)
        spread_bps: Bid-ask spread in basis points (optional, fetched if None)
        
    Returns:
        Tuple of (should_filter: bool, reason: str)
        If should_filter is True, the symbol should be excluded from scoring
    """
    # 1. Check stablecoins
    if is_stablecoin_pair(symbol):
        return True, "stablecoin"
    
    # 2. Check blacklist
    if symbol in BLACKLISTED_SYMBOLS:
        return True, "blacklisted"
    
    # 3. Check meme coin patterns
    base_asset = symbol.split("/")[0].upper()
    if base_asset in MEME_COIN_PATTERNS:
        return True, "meme_coin_pattern"
    
    # 4. Fetch missing data if needed
    if volume_24h is None:
        volume_24h = get_symbol_volume(symbol)
    
    if spread_bps is None:
        spread_bps = get_symbol_spread(symbol)
    
    if market_cap is None:
        coingecko_data = get_market_data(symbol)
        market_cap = coingecko_data.get("market_cap")
    
    # 5. Check minimum volume
    if volume_24h is not None and volume_24h < MIN_24H_VOLUME_USD:
        return True, f"low_volume_{volume_24h:.0f}"
    
    # 6. Check maximum spread
    if spread_bps is not None and spread_bps > MAX_SPREAD_BPS:
        return True, f"high_spread_{spread_bps:.1f}bps"
    
    # 7. Check minimum market cap (if available)
    if market_cap is not None:
        if market_cap < MIN_MARKET_CAP_USD:
            return True, f"low_market_cap_{market_cap:.0f}"
    
    # 8. Check for suspicious patterns (very new coins, extreme volatility, etc.)
    # This could be enhanced with additional heuristics
    
    return False, ""


MIN_SUPPLY_RATIO = 0.20  # 20% circulating / total supply

# Hard spread limit — pairs above this are effectively untradeable (fees eat any profit)
MAX_SPREAD_BPS_HARD = 50.0  # 0.5% — anything wider is junk


def static_filter_symbols(
    symbols: list[str],
    batch_market_data: dict,
    active_position_symbols: set,
) -> tuple[list[str], dict[str, str]]:
    """
    Stage 1 static filter: eliminate symbols using only pre-fetched 24h-cached data.
    No Kraken API calls, no per-symbol CoinGecko calls.

    Fail-closed: symbols with no market_cap data are skipped (no data = not worth scoring).
    Symbols in active_position_symbols always pass through regardless of scores.

    Returns:
        (survivors, skip_reasons)
    """
    survivors = []
    skip_reasons = {}

    for symbol in symbols:
        # Active positions always pass through
        if symbol in active_position_symbols:
            survivors.append(symbol)
            continue

        # Stablecoin / blacklist / meme — no data needed
        if is_stablecoin_pair(symbol):
            skip_reasons[symbol] = "stablecoin"
            continue
        if symbol in BLACKLISTED_SYMBOLS:
            skip_reasons[symbol] = "blacklisted"
            continue
        base = symbol.split("/")[0].upper()
        if base in MEME_COIN_PATTERNS:
            skip_reasons[symbol] = "meme_coin_pattern"
            continue

        data = batch_market_data.get(symbol, {})

        # Spread gate — hard limit: pairs with insane spreads are untradeable
        spread_bps = data.get("spread_bps")
        if spread_bps is not None and spread_bps > MAX_SPREAD_BPS_HARD:
            skip_reasons[symbol] = f"spread_{spread_bps:.0f}bps"
            continue

        # Market cap gate — fail-closed: no data means skip (too obscure/small to verify)
        mcap = data.get("market_cap")
        if mcap is None:
            skip_reasons[symbol] = "no_market_cap_data"
            continue
        if mcap < MIN_MARKET_CAP_USD:
            skip_reasons[symbol] = f"market_cap_${mcap:.0f}"
            continue

        # Supply ratio gate — fail-closed: no data means skip
        supply = data.get("supply_ratio")
        if supply is None:
            skip_reasons[symbol] = "no_supply_data"
            continue
        if supply < MIN_SUPPLY_RATIO:
            skip_reasons[symbol] = f"supply_ratio_{supply:.2f}"
            continue

        survivors.append(symbol)

    return survivors, skip_reasons


def filter_symbols_for_scoring(symbols: list[str]) -> tuple[list[str], dict[str, str]]:
    """
    Filter a list of symbols, removing those that should be excluded.
    
    Args:
        symbols: List of symbol strings to filter
        
    Returns:
        Tuple of (filtered_symbols: list[str], filter_reasons: dict[str, str])
        filter_reasons maps filtered symbols to their exclusion reason
    """
    filtered = []
    reasons = {}
    
    for symbol in symbols:
        should_filter, reason = should_filter_symbol(symbol)
        if should_filter:
            reasons[symbol] = reason
        else:
            filtered.append(symbol)
    
    return filtered, reasons

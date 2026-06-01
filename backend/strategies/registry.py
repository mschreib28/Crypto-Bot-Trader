"""Strategy registry — factory for creating strategy instances from DB config rows.

Maps strategy name strings (from the `strategies` table) to their corresponding
Config and Strategy classes. Handles config flattening of the nested 'parameters'
sub-dict used by the DB seed format.

Usage:
    result = create_strategy(strategy_name, db_uuid, config_data)
    if result is not None:
        config, strategy = result
"""

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Keys in the DB config JSONB that must NOT be forwarded to strategy dataclasses.
# These are envelope fields or screener-only fields with no matching config field.
_EXCLUDED_CONFIG_KEYS = frozenset(
    ("filters", "parameters", "strategy_id", "name", "max_risk_pct", "volume_threshold")
)

# Default interval when config is absent or missing the 'interval' key.
_DEFAULT_INTERVAL = "5m"


def _flatten_config(config_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Flatten a DB config dict into keyword arguments for a strategy Config dataclass.

    The DB stores strategy config as JSONB with this shape::

        {
            "symbol": "BTC/USD",
            "interval": "15m",
            "htf_interval": "1h",
            "max_risk_pct": 1.0,        # screener-only — excluded
            "volume_threshold": 1.5,    # screener-only — excluded
            "parameters": {             # nested params — merged at top level
                "atr_stop_mult": 1.8,
                ...
            },
            "filters": { ... },         # screener-only — excluded
        }

    This function returns a flat dict safe to pass as ``**kwargs`` to any
    strategy Config dataclass, with::
        - ``filters``, ``parameters``, ``strategy_id``, ``name``,
          ``max_risk_pct``, ``volume_threshold`` stripped from the top level
        - all entries from ``parameters`` merged in at the top level
        - ``strategy_id`` key stripped from ``parameters`` sub-dict too
    """
    if not config_data:
        return {}

    flat = {k: v for k, v in config_data.items() if k not in _EXCLUDED_CONFIG_KEYS}

    params = config_data.get("parameters")
    if params and isinstance(params, dict):
        # Merge parameters sub-dict, excluding the 'strategy_id' key there too
        flat.update({k: v for k, v in params.items() if k != "strategy_id"})

    return flat


def get_required_interval(config_data: Optional[Dict[str, Any]]) -> str:
    """Return the interval a strategy requires, defaulting to '5m'."""
    if not config_data:
        return _DEFAULT_INTERVAL
    return config_data.get("interval", _DEFAULT_INTERVAL)


def create_strategy(
    strategy_name: str,
    db_uuid: str,
    config_data: Optional[Dict[str, Any]],
) -> Optional[Tuple[Any, Any]]:
    """Factory that creates a (Config, Strategy) tuple for a given strategy name.

    The strategy_name is matched case-insensitively against known patterns in
    the same order the screener service uses, so both canonical names and DB seed
    names are accepted.

    Args:
        strategy_name: Name of the strategy row (e.g. 'vwap_meanreversion').
        db_uuid: UUID string of the DB row — used as strategy_id override so
                 signals carry the canonical DB identifier.
        config_data: The ``config`` JSONB column value from the DB row.

    Returns:
        A ``(config, strategy)`` tuple on success, or ``None`` if the name is
        unrecognised or strategy construction fails.
    """
    if not strategy_name:
        return None

    name_lower = strategy_name.lower()

    try:
        if "pullback_vwap" in name_lower or "pullback_to_vwap" in name_lower:
            logger.info(
                f"[registry] pullback_vwap retired — skipping '{strategy_name}' (id={db_uuid})"
            )
            return None

        flat = _flatten_config(config_data)

        # ------------------------------------------------------------------
        # Production strategies (most-specific patterns first)
        # ------------------------------------------------------------------
        # vwap_meanrev_1h matches here (substring vwap_meanrev); DB row supplies interval/htf.

        if "vwap_meanrev" in name_lower or "vwap_meanreversion" in name_lower:
            from research.strategies.vwap_meanrev.config import VWAPMeanReversionConfig
            from research.strategies.vwap_meanrev.strategy import VWAPMeanReversionStrategy

            config = VWAPMeanReversionConfig(strategy_id=db_uuid, **flat)
            strategy = VWAPMeanReversionStrategy(config)
            logger.info(
                f"[registry] Initialized VWAPMeanReversionStrategy "
                f"for '{strategy_name}' (id={db_uuid})"
            )
            return config, strategy

        if "htf_trend" in name_lower:
            from research.strategies.htf_trend.config import HTFTrendConfig
            from research.strategies.htf_trend.strategy import HTFTrendStrategy

            config = HTFTrendConfig(strategy_id=db_uuid, **flat)
            strategy = HTFTrendStrategy(config)
            logger.info(
                f"[registry] Initialized HTFTrendStrategy "
                f"for '{strategy_name}' (id={db_uuid})"
            )
            return config, strategy

        if "volatility_breakout" in name_lower:
            from research.strategies.volatility_breakout.config import VolatilityBreakoutConfig
            from research.strategies.volatility_breakout.strategy import VolatilityBreakoutStrategy

            config = VolatilityBreakoutConfig(strategy_id=db_uuid, **flat)
            strategy = VolatilityBreakoutStrategy(config)
            logger.info(
                f"[registry] Initialized VolatilityBreakoutStrategy "
                f"for '{strategy_name}' (id={db_uuid})"
            )
            return config, strategy

        if "bull_flag" in name_lower:
            from research.strategies.bull_flag.config import BullFlagConfig
            from research.strategies.bull_flag.strategy import BullFlagStrategy

            config = BullFlagConfig(strategy_id=db_uuid, **flat)
            strategy = BullFlagStrategy(config)
            logger.info(
                f"[registry] Initialized BullFlagStrategy "
                f"for '{strategy_name}' (id={db_uuid})"
            )
            return config, strategy

        # ------------------------------------------------------------------
        # Legacy strategies
        # ------------------------------------------------------------------

        if (
            "meanrev" in name_lower
            or "mean_rev" in name_lower
            or "mean-rev" in name_lower
            or "mean_reversion" in name_lower
        ):
            from research.strategies.meanrev.config import MeanReversionConfig
            from research.strategies.meanrev.strategy import MeanReversionStrategy

            symbol = (config_data or {}).get("symbol", "ETH/USD")
            lookback_period = (config_data or {}).get("lookback_period", 20)
            rsi_period = (config_data or {}).get("rsi_period", 14)

            config = MeanReversionConfig(
                strategy_id=db_uuid,
                symbol=symbol,
                lookback_period=lookback_period,
                rsi_period=rsi_period,
            )
            strategy = MeanReversionStrategy(config)
            logger.info(
                f"[registry] Initialized MeanReversionStrategy "
                f"for '{strategy_name}' (id={db_uuid})"
            )
            return config, strategy

        if (
            "momentum" in name_lower
            or "trend_follow" in name_lower
            or "trend-follow" in name_lower
        ):
            from research.strategies.momentum.config import MomentumConfig
            from research.strategies.momentum.strategy import MomentumStrategy

            symbol = (config_data or {}).get("symbol", "BTC/USD")
            lookback_period = (config_data or {}).get("lookback_period", 14)

            config = MomentumConfig(
                strategy_id=db_uuid,
                symbol=symbol,
                lookback_period=lookback_period,
            )
            strategy = MomentumStrategy(config)
            logger.info(
                f"[registry] Initialized MomentumStrategy "
                f"for '{strategy_name}' (id={db_uuid})"
            )
            return config, strategy

        logger.warning(
            f"[registry] Unknown strategy name '{strategy_name}' — skipping"
        )
        return None

    except Exception as exc:
        logger.error(
            f"[registry] Failed to create strategy '{strategy_name}' "
            f"(id={db_uuid}): {exc}",
            exc_info=True,
        )
        return None

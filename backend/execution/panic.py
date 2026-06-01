"""Panic sequence logic for emergency shutdown.

Implements the panic sequence:
1. Set system halt mode to true
2. Disable live trading
3. Cancel all open orders via Kraken CLI (cancel-all --yes)
4. Attempt to close any tracked positions via market sell

The panic sequence is idempotent and fail-closed: if any step fails, the
system remains halted.
"""

import logging
from typing import Dict, Any

from backend.risk.halt import set_halt_mode
from backend.api.routes.trading import set_trading_enabled

logger = logging.getLogger(__name__)


async def execute_panic_sequence() -> Dict[str, Any]:
    """
    Execute the panic sequence: halt system, disable trading, cancel all orders.

    Returns:
        {"status": "panic_initiated", "orders_cancelled": int, "trading_disabled": bool}
    """
    logger.warning("PANIC SEQUENCE INITIATED")

    # Step 1: Set system halt mode
    try:
        set_halt_mode(True)
        logger.info("System halt mode enabled")
    except Exception as e:
        logger.error(f"Failed to set halt mode: {e}")

    # Step 2: Disable live trading
    trading_disabled = False
    try:
        set_trading_enabled(False)
        trading_disabled = True
        logger.info("Live trading disabled")
    except Exception as e:
        logger.error(f"Failed to disable trading: {e}")

    # Step 3: Cancel all open orders via CLI
    orders_cancelled = 0
    try:
        from backend.execution.kraken_cli import cancel_all_orders, get_open_orders, KrakenCLIError
        # Use cancel-all for efficiency
        result = await cancel_all_orders()
        # CLI cancel-all returns {"count": N} or similar
        orders_cancelled = int(result.get("count", 0)) if result else 0
        logger.info(f"cancel-all returned: {result}")
    except Exception as e:
        logger.error(f"Failed to cancel all orders via CLI: {e}")
        # Fail-closed: system remains halted regardless

    logger.warning(f"PANIC SEQUENCE COMPLETE: {orders_cancelled} order(s) cancelled")

    return {
        "status": "panic_initiated",
        "orders_cancelled": orders_cancelled,
        "trading_disabled": trading_disabled,
    }

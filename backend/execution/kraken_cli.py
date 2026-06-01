"""Async wrapper for the Kraken CLI binary.

All exchange interactions go through this module in Phase 1+.
Each function spawns a short-lived `kraken ...` subprocess and returns
typed Python objects. The binary handles auth, nonce, rate limiting,
and retry internally.

Auth: KRAKEN_API_KEY / KRAKEN_API_SECRET env vars (passed through to subprocess).
Binary path: KRAKEN_BIN env var (default: /usr/local/bin/kraken).
"""

import asyncio
import json
import logging
import math
import os
from dataclasses import dataclass
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# Resolved once at import — overridable via env var for Docker
KRAKEN_BIN = os.environ.get("KRAKEN_BIN", "/usr/local/bin/kraken")


# ── Error type ───────────────────────────────────────────────────────────────


class KrakenCLIError(Exception):
    """Raised when the CLI returns a non-zero exit code or unparseable output."""

    def __init__(self, message: str, exit_code: int = -1, stderr: str = "") -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr


# ── Return types ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PaperFill:
    """Result of a paper buy or sell order."""
    order_id: str
    trade_id: str
    pair: str
    side: str        # "buy" | "sell"
    price: float
    volume: float
    cost: float
    fee: float
    action: str      # "market_order_filled" | "limit_order_placed" etc.


@dataclass(frozen=True)
class PaperAssetBalance:
    available: float
    reserved: float
    total: float


@dataclass(frozen=True)
class PaperBalance:
    """Per-asset balances from `kraken paper balance`."""
    balances: dict[str, PaperAssetBalance]
    mode: str

    @property
    def usd_available(self) -> float:
        b = self.balances.get("USD")
        return b.available if b else 0.0

    @property
    def usd_total(self) -> float:
        b = self.balances.get("USD")
        return b.total if b else 0.0


@dataclass(frozen=True)
class PaperStatus:
    """Portfolio summary from `kraken paper status`."""
    starting_balance: float
    current_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    open_orders: int
    total_trades: int
    fee_rate: float
    mode: str


@dataclass(frozen=True)
class PaperTradeRecord:
    id: str
    order_id: str
    pair: str
    side: str
    price: float
    volume: float
    cost: float
    fee: float
    status: str
    time: str


@dataclass(frozen=True)
class TickerResult:
    pair: str          # normalised: BTC/USD
    ask: float
    bid: float
    last: float
    volume_24h: float
    high_24h: float
    low_24h: float


# ── Internal runner ───────────────────────────────────────────────────────────


async def _run(
    *args: str,
    timeout: float = 30.0,
) -> Any:
    """Run `kraken <args> -o json`, return parsed JSON or raise KrakenCLIError."""
    cmd = [KRAKEN_BIN, *args, "-o", "json"]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        raise KrakenCLIError(
            f"kraken {' '.join(args)} timed out after {timeout}s",
            exit_code=-1,
        )
    except FileNotFoundError:
        raise KrakenCLIError(
            f"kraken binary not found at {KRAKEN_BIN!r}. "
            "Set KRAKEN_BIN env var or mount the binary into the container.",
            exit_code=-1,
        )

    stdout = stdout_bytes.decode().strip()
    stderr = stderr_bytes.decode().strip()

    if proc.returncode != 0:
        # CLI returns JSON error envelopes even on failure
        try:
            err = json.loads(stdout)
            msg = err.get("message", stdout)
        except Exception:
            msg = stderr or stdout or f"exit code {proc.returncode}"
        raise KrakenCLIError(
            message=f"kraken {' '.join(args)}: {msg}",
            exit_code=proc.returncode,
            stderr=stderr,
        )

    if not stdout:
        return {}

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise KrakenCLIError(
            f"kraken {' '.join(args)}: invalid JSON in stdout — {exc}",
            exit_code=0,
            stderr=stdout[:500],
        )


# ── Paper trading ─────────────────────────────────────────────────────────────


async def paper_init(balance: float = 50.0, currency: str = "USD") -> dict:
    """
    Initialise the paper account.

    Raises KrakenCLIError if already initialised (call paper_reset instead).
    Returns raw dict: {"action":"initialized","starting_balance":50.0,...}
    """
    return await _run(
        "paper", "init",
        "--balance", str(balance),
        "--currency", currency,
    )


async def paper_reset(balance: Optional[float] = None, currency: str = "USD") -> dict:
    """
    Reset the paper account (wipes history and balances).

    If balance is given, sets the new starting balance; otherwise keeps the
    current starting_balance config.
    """
    args = ["paper", "reset"]
    if balance is not None:
        args += ["--balance", str(balance), "--currency", currency]
    return await _run(*args)


async def paper_ensure_init(balance: float = 50.0, currency: str = "USD") -> None:
    """Initialise the paper account if it doesn't exist yet."""
    try:
        await paper_init(balance=balance, currency=currency)
        logger.info(f"[Paper] Account initialised with ${balance:.2f} {currency}")
    except KrakenCLIError as exc:
        if "already initialized" in str(exc).lower():
            logger.debug("[Paper] Account already initialised, skipping init")
        else:
            raise


async def paper_buy(
    pair: str,
    quantity: float,
    order_type: str = "market",
    price: Optional[float] = None,
) -> Optional[PaperFill]:
    """
    Place a paper buy order.

    Args:
        pair: CLI format, e.g. "BTCUSD" (no slash)
        quantity: Base asset quantity
        order_type: "market" or "limit"
        price: Required for limit orders

    Returns:
        PaperFill with execution details, or None if quantity is non-finite,
        rounds to zero at 8dp, or is below the minimum viable base size (1e-8).
    """
    if not math.isfinite(quantity):
        logger.error(
            "[paper_buy] skipping buy: non-finite quantity pair=%s raw_quantity=%r",
            pair,
            quantity,
        )
        return None
    # Kraken CLI paper account stores balances at 8 decimal places; sending more
    # decimal digits triggers "Insufficient balance" even when Available == Required.
    # math.floor matches paper_sell and avoids rounding up past available precision.
    qty_truncated = math.floor(quantity * 1e8) / 1e8
    if qty_truncated <= 0:
        logger.error(
            "[paper_buy] skipping buy: floored quantity <= 0 pair=%s "
            "raw_quantity=%r qty_truncated_8dp=%r",
            pair,
            quantity,
            qty_truncated,
        )
        return None
    if qty_truncated < 1e-8:
        logger.error(
            "[paper_buy] skipping buy: qty below minimum viable base (1e-8) pair=%s "
            "raw_quantity=%r qty_truncated_8dp=%r",
            pair,
            quantity,
            qty_truncated,
        )
        return None
    qty_str = f"{qty_truncated:.8f}"
    args = ["paper", "buy", pair, qty_str, "--type", order_type]
    if order_type == "limit" and price is not None:
        args += ["--price", f"{price:.8f}"]

    data = await _run(*args)
    return _parse_paper_fill(data)


async def paper_sell(pair: str, quantity: float) -> Optional[PaperFill]:
    """Place a paper market sell order.

    Returns None if quantity is non-finite or rounds to zero at 8 decimal places
    (Kraken CLI rejects zero volume); caller should treat as a skipped/failed sell.
    """
    if not math.isfinite(quantity):
        logger.error(
            "[paper_sell] skipping sell: non-finite quantity pair=%s raw_quantity=%r",
            pair,
            quantity,
        )
        return None
    # Kraken CLI truncates balances at 8 decimal places; round() can produce a
    # value 1 ULP above what the CLI stored, causing "Insufficient balance".
    # math.floor guarantees we never request more than what the CLI has.
    qty_truncated = math.floor(quantity * 1e8) / 1e8
    if qty_truncated <= 0:
        logger.error(
            "[paper_sell] skipping sell: floored quantity <= 0 pair=%s "
            "raw_quantity=%r qty_truncated_8dp=%r",
            pair,
            quantity,
            qty_truncated,
        )
        return None
    qty_str = f"{qty_truncated:.8f}"
    data = await _run("paper", "sell", pair, qty_str)
    return _parse_paper_fill(data)


async def paper_balance() -> PaperBalance:
    """Get per-asset balances from the paper account."""
    data = await _run("paper", "balance")
    raw_balances = data.get("balances", {})
    balances = {
        asset: PaperAssetBalance(
            available=float(b.get("available", 0)),
            reserved=float(b.get("reserved", 0)),
            total=float(b.get("total", 0)),
        )
        for asset, b in raw_balances.items()
    }
    return PaperBalance(balances=balances, mode=data.get("mode", "paper"))


async def paper_status() -> PaperStatus:
    """Get portfolio summary (P&L, current value, open orders)."""
    data = await _run("paper", "status")
    return PaperStatus(
        starting_balance=float(data.get("starting_balance", 0)),
        current_value=float(data.get("current_value", 0)),
        unrealized_pnl=float(data.get("unrealized_pnl", 0)),
        unrealized_pnl_pct=float(data.get("unrealized_pnl_pct", 0)),
        open_orders=int(data.get("open_orders", 0)),
        total_trades=int(data.get("total_trades", 0)),
        fee_rate=float(data.get("fee_rate", 0.0026)),
        mode=data.get("mode", "paper"),
    )


async def paper_history() -> List[PaperTradeRecord]:
    """Get all filled paper trades."""
    data = await _run("paper", "history")
    return [
        PaperTradeRecord(
            id=t.get("id", ""),
            order_id=t.get("order_id", ""),
            pair=t.get("pair", ""),
            side=t.get("side", ""),
            price=float(t.get("price", 0)),
            volume=float(t.get("volume", 0)),
            cost=float(t.get("cost", 0)),
            fee=float(t.get("fee", 0)),
            status=t.get("status", ""),
            time=t.get("time", ""),
        )
        for t in data.get("trades", [])
    ]


async def paper_orders() -> list:
    """Get open paper limit orders."""
    data = await _run("paper", "orders")
    return data.get("open_orders", [])


# ── Market data ───────────────────────────────────────────────────────────────


async def get_ticker(pair: str) -> TickerResult:
    """
    Get current ticker for a pair.

    Args:
        pair: CLI format (e.g. "BTCUSD") — no slash

    Returns:
        TickerResult with ask/bid/last/volume/high/low
    """
    data = await _run("ticker", pair)
    # Kraken returns the ticker keyed by its internal pair name (e.g. XXBTZUSD)
    # Take the first (and only) entry regardless of key name
    if not data:
        raise KrakenCLIError(f"Empty ticker response for {pair}")
    ticker = next(iter(data.values()))
    return TickerResult(
        pair=pair,
        ask=float(ticker["a"][0]),
        bid=float(ticker["b"][0]),
        last=float(ticker["c"][0]),
        volume_24h=float(ticker["v"][1]),   # 24h volume
        high_24h=float(ticker["h"][1]),
        low_24h=float(ticker["l"][1]),
    )


async def get_ohlc(pair: str, interval: int = 60) -> list:
    """
    Get historical OHLC bars.

    Args:
        pair: CLI format (e.g. "BTCUSD")
        interval: Minutes (1, 5, 15, 30, 60, 240, 1440)

    Returns:
        List of raw bar dicts from the CLI
    """
    return await _run("ohlc", pair, "--interval", str(interval))


# ── Live trading ──────────────────────────────────────────────────────────────


async def get_balance() -> dict:
    """
    Get live Kraken account balance.
    Requires KRAKEN_API_KEY / KRAKEN_API_SECRET env vars.
    """
    return await _run("balance")


def get_balance_sync() -> dict:
    """
    Synchronous live Kraken account balance fetch via subprocess.run.

    Use this from synchronous contexts (e.g., AccountTracker property).
    Returns raw dict of {currency_code: balance_string}.
    """
    import subprocess
    cmd = [KRAKEN_BIN, "balance", "-o", "json"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env=os.environ.copy(),
        )
    except FileNotFoundError:
        raise KrakenCLIError(
            f"kraken binary not found at {KRAKEN_BIN!r}. "
            "Set KRAKEN_BIN env var or mount the binary into the container.",
            exit_code=-1,
        )
    if result.returncode != 0:
        raise KrakenCLIError(
            f"kraken balance: {result.stderr or result.stdout}",
            exit_code=result.returncode,
            stderr=result.stderr,
        )
    return json.loads(result.stdout.strip()) if result.stdout.strip() else {}


# Kraken asset normalisation helpers (shared between sync and async callers)
_USD_ASSETS = {"ZUSD", "USD"}


def _normalize_kraken_asset(asset: str) -> str:
    """XXBT → BTC, XETH → ETH, ZUSD → USD, etc."""
    if len(asset) == 4 and asset[0] in ("X", "Z"):
        asset = asset[1:]
    if asset == "XBT":
        asset = "BTC"
    return asset


async def get_live_account_balance() -> dict:
    """
    Build a structured account balance dict from live Kraken data.

    Returns:
        {
            "total_usd": float,
            "available_usd": float,
            "holdings": [{"symbol": str, "quantity": float, "value_usd": float}, ...]
        }

    Uses get_balance() + get_ticker() for each crypto holding.
    """
    raw = await get_balance()
    holdings: List[dict] = []
    total_usd = 0.0

    for asset, bal_str in raw.items():
        try:
            quantity = float(bal_str)
        except (TypeError, ValueError):
            continue
        if quantity <= 0:
            continue

        symbol = _normalize_kraken_asset(asset)

        if asset in _USD_ASSETS or symbol == "USD":
            value_usd = quantity
        else:
            try:
                cli_pair = symbol_to_cli_pair(f"{symbol}/USD")
                ticker = await get_ticker(cli_pair)
                value_usd = quantity * ticker.last
            except Exception:
                value_usd = 0.0

        total_usd += value_usd
        holdings.append({
            "symbol": symbol,
            "quantity": round(quantity, 8),
            "value_usd": round(value_usd, 2),
        })

    return {
        "total_usd": round(total_usd, 2),
        "available_usd": round(total_usd, 2),  # Conservative: treat all USD as available
        "holdings": holdings,
    }


async def place_order(
    pair: str,
    side: str,
    quantity: float,
    order_type: str = "market",
    price: Optional[float] = None,
) -> dict:
    """
    Place a live order on Kraken.

    Args:
        pair: CLI format (e.g. "BTCUSD")
        side: "buy" or "sell"
        quantity: Base asset quantity
        order_type: "market" or "limit"
        price: Required for limit orders

    Returns:
        Raw dict with txid and description
    """
    args = ["order", side, pair, str(quantity), "--type", order_type]
    if order_type in ("limit", "stop-loss") and price is not None:
        args += ["--price", f"{price:.8f}"]
    return await _run(*args)


async def cancel_order(txid: str) -> dict:
    """Cancel a live order by transaction ID."""
    return await _run("order", "cancel", "--txid", txid)


async def cancel_all_orders() -> dict:
    """Cancel all open live orders."""
    return await _run("order", "cancel-all", "--yes")


async def get_open_orders() -> list:
    """Get all open live orders."""
    data = await _run("open-orders")
    return data if isinstance(data, list) else []


async def query_order(txid: str) -> dict:
    """Query status of a specific order by transaction ID."""
    return await _run("query-orders", "--txid", txid)


# ── WebSocket subprocess management ──────────────────────────────────────────


async def ws_ohlc(
    pairs: List[str],
    interval: int = 5,
) -> asyncio.subprocess.Process:
    """
    Start a long-running `kraken ws ohlc` subprocess.

    Caller is responsible for reading stdout line-by-line and terminating
    the process when done. Each line is a JSON object.

    Args:
        pairs: List of CLI-format pairs (e.g. ["BTCUSD", "ETHUSD"])
        interval: Candle interval in minutes

    Returns:
        asyncio.subprocess.Process — caller reads from .stdout
    """
    cmd = [
        KRAKEN_BIN, "ws", "ohlc",
        *pairs,
        "--interval", str(interval),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=os.environ.copy(),
    )
    return proc


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_paper_fill(data: dict) -> PaperFill:
    """Parse the JSON dict returned by `kraken paper buy/sell` into a PaperFill."""
    return PaperFill(
        order_id=data.get("order_id", ""),
        trade_id=data.get("trade_id", ""),
        pair=data.get("pair", ""),
        side=data.get("side", ""),
        price=float(data.get("price", 0)),
        volume=float(data.get("volume", 0)),
        cost=float(data.get("cost", 0)),
        fee=float(data.get("fee", 0)),
        action=data.get("action", ""),
    )


def symbol_to_cli_pair(symbol: str) -> str:
    """Convert internal symbol format to CLI pair format.

    Examples:
        "BTC/USD" -> "BTCUSD"
        "ETH/USD" -> "ETHUSD"
        "BTCUSD"  -> "BTCUSD"  (already CLI format)
    """
    return symbol.replace("/", "")

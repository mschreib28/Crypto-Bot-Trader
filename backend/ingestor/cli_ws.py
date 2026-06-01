"""CLI WebSocket ingestor using `kraken ws ohlc` subprocess.

Replaces MultiConnectionManager + Normalizer with a direct subprocess approach:
- One subprocess per interval (CLI accepts a single --interval per invocation)
- Bars published directly to Redis OHLCV streams (market:ohlcv:{symbol}:{interval})
- Exponential backoff on subprocess crash / exit

Pair format: `kraken ws ohlc` requires slash notation (BTC/USD), which is already
the internal format used throughout the codebase.
"""

import asyncio
import json
import logging
import os
from typing import List, Optional

from backend.execution.kraken_cli import KRAKEN_BIN
from backend.redis.keys import MARKET_OHLCV_STREAM
from backend.redis.streams import publish_to_stream

logger = logging.getLogger(__name__)

# Interval label → CLI minutes
INTERVAL_MINUTES: dict[str, int] = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440,
}
# CLI minutes → interval label (for bar metadata written to Redis)
MINUTES_LABEL: dict[int, str] = {v: k for k, v in INTERVAL_MINUTES.items()}


class CLIWebSocketIngestor:
    """
    Streams OHLCV bars for one interval from a `kraken ws ohlc` subprocess.

    Publishes bars to the Redis stream: market:ohlcv:{symbol}:{interval}
    Both snapshot (current in-progress bar) and update (completed bar) messages
    are published so consumers always have the latest price data.
    """

    def __init__(self, symbols: List[str], interval: str) -> None:
        if interval not in INTERVAL_MINUTES:
            raise ValueError(
                f"Unknown interval {interval!r}. "
                f"Valid options: {sorted(INTERVAL_MINUTES)}"
            )
        self.symbols = symbols
        self.interval = interval
        self.interval_minutes = INTERVAL_MINUTES[interval]
        self._running = False
        self._proc: Optional[asyncio.subprocess.Process] = None

    async def run(self) -> None:
        """Run the ingestor with exponential backoff on crashes."""
        self._running = True
        backoff = 1.0
        while self._running:
            try:
                await self._stream()
                backoff = 1.0  # Reset backoff after a clean session
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[CLIWs:{self.interval}] Stream error: {e}")
                if self._running:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60.0)

    async def stop(self) -> None:
        """Signal stop and terminate the subprocess if running."""
        self._running = False
        proc = self._proc
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()

    async def _stream(self) -> None:
        """Spawn `kraken ws ohlc` and read JSON lines until stopped or error."""
        cmd = [
            KRAKEN_BIN, "ws", "ohlc",
            *self.symbols,
            "--interval", str(self.interval_minutes),
            "-o", "json",
        ]
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        logger.info(
            f"[CLIWs:{self.interval}] Started (pid={self._proc.pid}) "
            f"— {len(self.symbols)} symbols"
        )

        assert self._proc.stdout is not None
        async for line_bytes in self._proc.stdout:
            if not self._running:
                break
            line = line_bytes.decode().strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                logger.debug(f"[CLIWs:{self.interval}] Non-JSON: {line[:100]}")
                continue

            channel = msg.get("channel")
            if channel in ("heartbeat", "status"):
                continue
            if channel != "ohlc":
                continue

            for bar in msg.get("data", []):
                self._publish_bar(bar)

        await self._proc.wait()
        rc = self._proc.returncode
        if rc != 0 and self._running:
            assert self._proc.stderr is not None
            stderr = (await self._proc.stderr.read()).decode()[:300]
            raise RuntimeError(f"kraken ws ohlc exited {rc}: {stderr}")

    def _publish_bar(self, bar: dict) -> None:
        """Publish one bar to the Redis OHLCV stream."""
        symbol: str = bar.get("symbol", "")
        if not symbol:
            return

        interval_num = int(bar.get("interval", self.interval_minutes))
        interval_label = MINUTES_LABEL.get(interval_num, self.interval)
        stream_key = MARKET_OHLCV_STREAM.format(symbol=symbol, interval=interval_label)

        # Use interval_begin (bar open time) as timestamp — matches bar_builder format
        raw_ts: str = bar.get("interval_begin", "")
        if "." in raw_ts:
            # Trim sub-second precision: "2026-04-03T19:35:00.000000000Z" → "2026-04-03T19:35:00Z"
            timestamp = raw_ts.split(".")[0] + "Z"
        else:
            timestamp = raw_ts.replace("+00:00", "Z")

        record: dict = {
            "open": str(bar.get("open", 0)),
            "high": str(bar.get("high", 0)),
            "low": str(bar.get("low", 0)),
            "close": str(bar.get("close", 0)),
            "volume": str(bar.get("volume", 0)),
            "timestamp": timestamp,
            "symbol": symbol,
            "interval": interval_label,
        }
        if "vwap" in bar:
            record["vwap"] = str(bar["vwap"])

        try:
            publish_to_stream(stream_key, record)
            logger.debug(
                f"[CLIWs:{self.interval}] Published {symbol} bar @ {timestamp}"
            )
        except Exception as e:
            logger.error(f"[CLIWs:{self.interval}] Failed to publish {symbol}: {e}")


class MultiIntervalCLIIngestor:
    """
    Manages one CLIWebSocketIngestor per configured interval.

    Drop-in replacement for (MultiConnectionManager + Normalizer) in main.py.
    Exposes the same run() / stop() / get_connection_count() interface.
    """

    def __init__(self, symbols: List[str], intervals: List[str]) -> None:
        valid = [iv for iv in intervals if iv in INTERVAL_MINUTES]
        unknown = [iv for iv in intervals if iv not in INTERVAL_MINUTES]
        if unknown:
            logger.warning(f"[MultiIntervalCLI] Skipping unknown intervals: {unknown}")
        self._ingestors: List[CLIWebSocketIngestor] = [
            CLIWebSocketIngestor(symbols=symbols, interval=iv)
            for iv in valid
        ]
        logger.info(
            f"[MultiIntervalCLI] {len(self._ingestors)} interval workers "
            f"for {len(symbols)} symbols: {valid}"
        )

    def get_connection_count(self) -> int:
        """Return number of active interval workers (matches MultiConnectionManager API)."""
        return len(self._ingestors)

    async def run(self) -> None:
        """Run all interval ingestors concurrently."""
        tasks = [asyncio.create_task(ing.run()) for ing in self._ingestors]
        await asyncio.gather(*tasks)

    async def stop(self) -> None:
        """Stop all interval ingestors."""
        for ing in self._ingestors:
            await ing.stop()

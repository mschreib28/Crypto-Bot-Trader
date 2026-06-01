# Crypto Bot Trading — Project Context

This is the master context file for the entire crypto trading bot project.
Place at the project root. Read by any Cursor/Claude Code session that needs
full context about the bot architecture, deployment, and history.

For the scoped autonomous experiment loop agent, see `experiments/AGENT_CONTEXT.md`.

## What This Is
A self-hosted crypto trading bot running in shadow (paper) mode on a home server.
Uses Kraken Pro API for market data and paper trade execution, CoinGecko for supplemental
market data. Goal: validate a profitable automated strategy ecosystem before risking real capital.

**Ultimate goal:** Put in $250, leave it running. Take out $10-15/week (one burrito).
Path: grow account via compounding + add capital over time.
The strategy edge is real — $10/week requires either ~$2,500 account or patient compounding from $250.

**Bot philosophy:** Semi-automatic sniper. Aggressive when confident, patient when not.
Runs all strategies simultaneously across 650+ pairs 24/7 without fatigue or emotion.

**Current development phase:** Backtest-driven iteration, NOT live shadow iteration.
Live shadow runs at 2-4 trades/week — too slow to validate changes. Use the experiment
framework (`experiments/`) to test 10-20 configs per day via 5-year backtests.

---

## Server & Deployment

**Server:** `ssh ark@corpus` (auto-authenticates, no password needed)
**Bot lives at:** `~/crypto-bot-trading/` on corpus
**Local lives at:** `/home/kevin/Documents/Projects/Personal/Crypto Bot Trading/`

### Deploy workflow (ALWAYS follow this order)
1. Make all changes locally first
2. Rsync to server:
   rsync -av --exclude='node_modules' --exclude='.env' --exclude='__pycache__' \
     --exclude='*.pyc' --exclude='backtest_cache/' --exclude='logs' \
     --exclude='experiments/results' --exclude='experiments/run.log' \
     "/home/kevin/Documents/Projects/Personal/Crypto Bot Trading/" \
     ark@corpus:~/crypto-bot-trading/
3. Rebuild and restart: ssh ark@corpus "cd ~/crypto-bot-trading && docker compose up --build -d"
4. NEVER edit files directly on corpus — rsync will overwrite them

### Frontend-only deploys
Frontend served via nginx bind mount (./frontend/dist:/usr/share/nginx/html:ro).
No docker rebuild needed — rsync only after build.
  rm -f frontend/dist/assets/index-*.js frontend/dist/assets/index-*.css
  cd frontend && npm run build
  rsync -av ... (same exclude list)

### Check service health
  ssh ark@corpus "cd ~/crypto-bot-trading && docker compose ps"
  ssh ark@corpus "cd ~/crypto-bot-trading && docker compose logs runner --tail=30"
  ssh ark@corpus "cd ~/crypto-bot-trading && docker compose logs api --tail=30"
  ssh ark@corpus "cd ~/crypto-bot-trading && docker compose logs supervisor --tail=30"

---

## Architecture

Docker Compose on corpus:
  api        — FastAPI backend (port 8001), screener, REST endpoints
  runner     — Strategy worker; checks supervisor gate before executing any signal
  screener   — Scans 650+ pairs via 5-pillar pipeline, feeds signals to runner
  redis      — State store: positions, shadow balance, cooldowns, OHLCV cache
  postgres   — Trade history, strategy configs
  frontend   — Nginx serving React UI (port 3001) via bind mount on ./frontend/dist
  supervisor — Meta-strategy supervisor; runs every 8h + live eval every 30min
  ingestor   — Kraken websocket feed; top 30 RVOL pairs + pinned symbols

### Key paths (local)
- Strategy I2 (VWAP Mean Rev 4h):    research/strategies/vwap_meanrev/strategy.py
- Strategy W1 (HTF Trend):           research/strategies/htf_trend/strategy.py
- Strategy I3 (Volatility Breakout): research/strategies/volatility_breakout/strategy.py
- Strategy I1 (Bull Flag):           research/strategies/bull_flag/strategy.py
- Strategy I5 (Mean Rev BB+RSI+ADX): research/strategies/meanrev/strategy.py
- Supervisor:                        backend/supervisor/
- Trade execution:                   backend/positions/monitor.py, tracker.py, executor.py
- Screener:                          backend/screener/service.py + pipeline.py
- Analytics:                         backend/analytics/store.py
- Backtester:                        backtest.py (project root)
- Experiment framework:              experiments/

---

## Strategy Ecosystem

### Strategy Performance Summary (5-year, May 2026)

| Strategy | Trades | WR | R:R | P&L | Max DD | Status |
|---|---|---|---|---|---|---|
| VWAP Mean Rev (4h) | 75 | 49.3% | 3.13 | +$12.59 | -$1.93 | ✅ ANCHOR |
| Volatility Breakout (BTC filtered) | 25 | 60.0% | 1.20 | +$23.99 | -$10.76 | ✅ STRONG |
| HTF Trend (BTC filtered) | 154 | 35.7% | 2.16 | +$7.99 | -$11.37 | ✅ POSITIVE |
| Bull Flag (4h proxy) | 35 | 40.0% | 0.80 | -$34.09 | -$42.98 | ⏳ WAIT |
| VWAP Mean Rev (1h) | 35 | 22.9% | 2.49 | -$2.20 | -$7.55 | ❌ DISABLED |
| Pullback to VWAP | 97 | 44.3% | 0.69 | -$82.08 | -$89.95 | ❌ RETIRED |

### Active strategies in supervisor
  vwap_meanrev, htf_trend, volatility_breakout, meanrev,
  bull_flag_1m, bull_flag_5m, bull_flag_1h, swing_bull_flag

### Retired / Disabled
- MACD: retired May 2026 — single indicator, no edge
- Pullback to VWAP: retired May 2026 — -$82/5yr
- VWAP MeanRev 1h: disabled May 2026 — 22.9% WR pipeline test, re-test late 2026

---

## The 5 Pillars of Crypto Pair Selection

### Stage 1: Static (cache 12-24h)
  S1 — Supply: < 5B tokens
  S2 — Price: $0.005–$10.00
  S3 — Activity: listed >30d, volume on 20+ of last 30 days

### Stage 2: Dynamic (every ~15 min)
  D1 — RVOL: > 3x 30-day average
  D2 — Momentum: up 8%+ in 24h OR up 5%+ in 4h
  D3 — Liquidity: 24h volume $500K–$50M
  D4 — BTC Health: BTC not down >4% in 4h
  Exception: meanrev bypasses D2

### Stage 2 Enhancement: E1 Float Proxy
  E1 — Float turnover: volume_24h_usd / market_cap_usd ≥ 0.05
  Soft gate: failing E1 downgrades one letter

### Pair Grading
  A+ All 4 dynamic + all static + E1 pass → full size
  A  3 of 4 dynamic + all static          → normal size
  B  2 of 4 dynamic + all static          → 50% size
  C  1 of 4 dynamic                       → watch only
  F  Failed static OR 0 dynamic           → runner blocks

---

## Confidence Gates & Position Sizing
  Scalp (S):    90%+ required (FUTURE)
  Intraday (I): 75%+ required
  Swing (W):    70%+ required

  Size scaling (LIVE mode only; SIM always full size):
    75–84%  → 50% size
    85–94%  → 75% size
    95–100% → 100% size

---

## Meta-Strategy Supervisor

### Backtest cycle (every 8 hours)
  ACTIVE    — WR ≥ 40% AND R:R ≥ 1.2 AND trades ≥ 5 → full size
  REDUCED   — WR ≥ 30% AND R:R ≥ 0.8               → 50% size
  SUSPENDED — below REDUCED or no trades             → paper only

### Drawdown Auto-Suspend
If cumulative R loss < -5.0 on any strategy:
  - Force SUSPENDED, size_factor 0.0, sticky flag
  - Clears only on manual re-enable OR backtest ACTIVE + R ≥ -5

---

## Bot Mode

- Redis key: `system:bot_mode` ("SHADOW" | "LIVE"), default SHADOW
- POST /api/v1/trading/bot-mode requires confirm: "ENABLE_LIVE_TRADING"

### Gate to real money
Do NOT enable LIVE mode until:
  - 50+ clean shadow trades on vwap_meanrev (4h)
  - WR ≥ 45% on those live trades
  - R:R ≥ 2.0 on those live trades
  - No open bugs causing ghost positions or sizing errors

---

## Trading Philosophy

Ross Cameron's Small Cap Momentum adapted for crypto. All indicators as unified toolkit.
Single-indicator strategies have no durable edge. Multi-factor confluence required.

Win rate math: 49.3% WR at 3.13 R:R = +$1.04 expected value per $1 risked.
You make money even losing more than half your trades because winners are 3x larger.

Benchmark: VWAP Mean Reversion 49.3% WR / 3.13 R:R over 5 years.

---

## Operational Rules
- Shadow balance: $500 per test run
- Position limits: unlimited SIM (1 per symbol), max 2 LIVE
- Risk per trade: 2% equity
- Per-symbol cooldown: 15 min after any exit
- Minimum hybrid exit hold: 3 bars
- Minimum position notional: $5.00
- Real money: NEVER until 50+ trades, WR ≥45%, R:R ≥2.0 on live shadow data
- Backtest before deploy: any config change must show backtest improvement first

---

## Known Fixed Bugs (May 2026)

- Float precision: math.floor 8dp on ALL paper buys and sells
- Zero-quantity buy/sell guard: rejects before position opens
- Minimum notional: $5.00 minimum
- Grade gate now fail-closed (no F-grade BTC trades)
- Frontend resource exhaustion (polling consolidation)
- Drawdown auto-suspend (Part 5)
- Stop exit reason mislabeling (breakeven_stop vs stop_loss)
- Analytics entry snapshots (vwap_distance, htf_trend)
- D2 momentum gate at BUY for momentum strategies

---

## Key Invariants

1. Bot SHADOW → paper trade always
2. Manual SIM → paper trade
3. Supervisor SUSPENDED → paper trade
4. Grade F symbols → runner skips signal
5. Zero-quantity buy/sell → rejected
6. Minimum notional $5.00 → enforced
7. VWAP MeanRev 1h → DISABLED until 365-day pipeline WR ≥40%
8. LIVE mode → only after 50+ shadow trades at WR ≥45% R:R ≥2.0
9. Drawdown -5R → forced SUSPENDED with sticky flag
10. Config changes → must backtest before deploy
11. Experiment agent → cannot modify backend/, frontend/, or strategy logic
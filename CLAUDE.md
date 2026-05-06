# Crypto Bot Trading — Claude Code Context

## What This Is
A self-hosted crypto trading bot running in shadow (paper) mode on a home server.
Uses Kraken Pro API for market data and paper trade execution, CoinGecko for supplemental
market data. Goal: validate a profitable automated trading strategy before risking real capital.

**Ultimate goal:** A fully autonomous system that:
- Runs shadow/paper trading continuously
- Uses a local Ollama model to monitor performance and suggest improvements
- Automatically enables real trading only when win rate >= 66% is sustained
- Scales capital gradually: $50 → $100 → $200 → $500 → $1000
- Target: ~$10–15/week profit (one Chipotle burrito). Then scale.

---

## Server & Deployment

**Server:** `ssh ark@corpus` (auto-authenticates, no password needed)
**Bot lives at:** `~/crypto-bot-trading/` on corpus
**Local lives at:** `/home/kevin/Documents/Projects/Personal/Crypto Bot Trading/`

### Deploy workflow (ALWAYS follow this order)
1. Make all changes locally first
2. Rsync to server:
   rsync -av --exclude='node_modules' --exclude='.env' --exclude='__pycache__' \
     --exclude='*.pyc' --exclude='backtest_cache/' \
     "/home/kevin/Documents/Projects/Personal/Crypto Bot Trading/" \
     ark@corpus:~/crypto-bot-trading/
3. Rebuild and restart: ssh ark@corpus "cd ~/crypto-bot-trading && docker compose up --build -d"
4. NEVER edit files directly on corpus — rsync will overwrite them

### Check service health
  ssh ark@corpus "cd ~/crypto-bot-trading && docker compose ps"
  ssh ark@corpus "cd ~/crypto-bot-trading && docker compose logs runner --tail=30"
  ssh ark@corpus "cd ~/crypto-bot-trading && docker compose logs api --tail=30"

---

## Architecture

Docker Compose on corpus:
  api        — FastAPI backend (port 8001), screener, REST endpoints
  runner     — Strategy worker, executes trades via vwap_meanreversion
  screener   — Scans 200+ pairs, feeds signals to runner
  redis      — State store: positions, shadow balance, cooldowns, OHLCV cache
  postgres   — Trade history, strategy configs
  frontend   — Nginx serving React UI (port 3001)

### Key paths (local)
- Strategy 1 (VWAP Mean Rev): research/strategies/vwap_meanrev/strategy.py
- Strategy 2 (HTF Trend): research/strategies/htf_trend/strategy.py
- Strategy 3 (Volatility Breakout): research/strategies/volatility_breakout/strategy.py
- Strategy 4 (MACD): research/strategies/macd/strategy.py
- Strategy 5 (Mean Rev): research/strategies/meanrev/strategy.py
- Strategy 6 (Momentum): research/strategies/momentum/strategy.py
- Strategy config: research/strategies/vwap_meanrev/config.py
- Trade execution: backend/execution/executor.py
- Position monitor: backend/execution/monitor.py
- Position tracker: backend/execution/tracker.py
- Kraken paper orders: backend/kraken/kraken_cli.py
- Screener: backend/screener/service.py
- Redis keys: backend/redis/keys.py
- Risk sizing: backend/risk/sizing.py
- Backtester: backtest.py (project root)

---

## Strategy Architecture

The bot runs multiple strategies simultaneously. When a coin passes the scanner, ALL active
strategies evaluate it and produce a confidence score (0-100%). The highest-confidence strategy
wins the position and owns it until exit. A position opened by Strategy X must be closed by
Strategy X — no cross-strategy exits.

### Active Strategies

**Strategy 1 — VWAP Mean Reversion** (`research/strategies/vwap_meanrev/`)
- Scanner fit: coins with high RVOL that have PULLED BACK (down 3-8% from recent high, oversold RSI)
- Entry: price below VWAP by >2%, RSI ≤ 30, reversal confirmation candle
- Exit: VWAP recross, RSI invalidation, stop-loss, max bars
- Direction: LONG ONLY (shorts permanently disabled)

**Strategy 2 — HTF Trend** (`research/strategies/htf_trend/strategy.py`)
- Higher timeframe trend-following with pullback entries
- Direction: LONG ONLY

**Strategy 3 — Volatility Breakout** (`research/strategies/volatility_breakout/strategy.py`)
- Enters on confirmed breakouts of key levels with volume confirmation
- Direction: LONG ONLY

**Strategy 4 — Pullback to VWAP** (TO BE BUILT)
- Scanner fit: coins the scanner finds going UP 8%+ with RVOL spike (momentum plays)
- Entry: after initial move, wait for price to pull back to within 0.5% of VWAP on 15m bar,
  confirm pullback volume is lower than initial move volume
- Exit: new high or 2R target, stop below pullback bar low
- Direction: LONG ONLY
- This strategy directly matches the scanner's D2 momentum pillar

### Signal Lead & Confidence System
- All strategies evaluate each scanner-passing coin simultaneously
- Each produces a confidence score (0-100%)
- Highest confidence strategy wins and locks the position
- Example: INJ/USD scores 60% on vwap_meanreversion, 85% on volatility_breakout
  → volatility_breakout takes the position and manages the exit

### Scanner ↔ Strategy Alignment
- Scanner D2 pillar (up 8%+ momentum) → best fit for Strategy 4 (pullback_vwap)
- Scanner finds oversold high-RVOL coins → best fit for Strategy 1 (vwap_meanreversion)
- All strategies share the same scanner output but weight criteria differently

---

## Trading Philosophy

Inspired by Ross Cameron's Small Cap Momentum strategy, adapted for crypto.

Ross Cameron's Core Insight:
Find assets with a supply/demand imbalance (low float + high relative volume + news catalyst),
then time a precise entry on the first pullback after the initial move. The math works because:
- High demand (volume spike + catalyst) pushes price
- Low supply (small float) means each buyer has outsized impact
- Pullback entry gives a defined risk point (prior candle low) and favorable R:R

Crypto Adaptation:
The same supply/demand logic applies in crypto. A coin with massive circulating supply (50B tokens)
can never have a meaningful supply squeeze regardless of demand. A coin with spiking volume,
constrained supply, and upward price movement in a healthy BTC environment mirrors the small-cap
stock setup closely. The pullback/mean-reversion entry logic transfers directly.

---

## The 5 Pillars of Crypto Pair Selection

Pairs evaluated in a 3-stage pipeline to minimize API calls:
Universe (~200 pairs) → Stage 1 Static → Stage 2 Dynamic → Stage 3 Strategy Signal

### Stage 1: Static Variables (cache 12–24 hours)
Slow-changing. Eliminates most of the universe cheaply before any expensive API calls.

  S1 — Circulating Supply: Total supply < 5 billion tokens
       Rationale: High supply dilutes demand impact. No squeeze is possible.

  S2 — Price Range: Between $0.005 and $10.00
       Rationale: Eliminates dead/micro coins and large caps with low volatility potential.

  S3 — Market Activity: Listed > 30 days, had volume on at least 20 of last 30 days
       Rationale: Filters zombie coins and brand-new listings with no track record.

Hard floor (not a pillar, absolute): 24h volume > $100K. Below this, skip entirely.

### Stage 2: Dynamic Variables (every scan cycle, ~15 min)
Only runs on Stage 1 survivors. These change fast — need fresh data each cycle.

  D1 — Relative Volume: Current volume > 3x 30-day average for same time window
       Rationale: Core demand signal. Mirrors Ross's 5x RVOL requirement.

  D2 — Price Momentum: Up 8%+ in 24h OR up 5%+ in last 4h
       Rationale: Already moving = confirmed demand entering the asset.

  D3 — Liquidity Sweet Spot: 24h volume between $500K and $50M
       Rationale: Too low = manipulation/slippage. Too high = BTC-tier, not enough edge.

  D4 — BTC Health: BTC not down more than 4% in last 4h
       Rationale: BTC dumps drag all crypto down regardless of individual strength.
       One API call covers all pairs — very cheap filter.

### Stage 3: Strategy Signal
Full VWAP, RSI, ATR, candle structure analysis. Expensive — only 2–8 pairs reach this stage.

---

## Pair Grading

  A+  All 4 dynamic pillars + all 3 static  →  Trade immediately, full size
  A   3 of 4 dynamic + all static           →  Trade, normal size
  B   2 of 4 dynamic + all static           →  Trade at 50% size
  C   1 of 4 dynamic + all static           →  Watch only, no trade
  F   Failed any static OR 0 dynamic        →  Ignore

Grade and per-pillar pass/fail stored in screener results for frontend display.

### Frontend: Scanner Criteria Info Card
An (i) button next to "Scanner" opens a modal showing:
- All pillar criteria in plain English
- Which pillars the current pair passed/failed
- Why the grade was assigned
Served by: GET /api/v1/screener/criteria (returns pillar definitions as JSON)

---

## Backtesting Philosophy

WRONG WAY: Test SNX/USD for 60 days assuming the bot trades it every day.
The live bot would never do this — it only trades when the scanner selects a pair.

RIGHT WAY: Simulate the full pipeline:
1. For each historical time window, run screener pillar logic across all pairs
2. Grade each pair, select the top-graded one
3. Run strategy only on the selected pair
4. Record result, advance time, repeat

### Current Backtester State
- backtest.py exists at project root — static symbol only (needs pipeline extension)
- Kraken OHLCV cap: 15m=7.5d | 1h=30d | 4h=120d | 1d=~2yrs
- Cache in backtest_cache/ (excluded from rsync)

### Static Backtest Baseline (1h/60 days — for reference only)
  SNX/USD:  5 trades, 40% win, 2.70 R:R, +$0.77  (small sample)
  AXS/USD:  4 trades, 50% win, 4.52 R:R, +$4.37  (small sample, promising)
  BLUR/USD: 10 trades, 20% win, 0.14 R:R, -$5.18  → DROP from universe
  CTC/USD:  2 trades,  0% win,  —  R:R, -$1.54   → DROP from universe
  BTC/USD:  0 trades — too stable for this strategy

---

## Live Shadow Trading Performance (3 weeks, ~130 trades)

  Win rate:  ~27%
  R:R ratio: ~1.0:1
  Net P&L:   ~-$9.80

Root cause: At 1.0 R:R, breakeven requires 50% win rate. Exits fire too early on winners
(1-candle VWAP invalidation) while losers ride longer. No fixes without backtest validation.

---

## Known Fixed Bugs (May 2026)
- Float precision on paper sells (math.floor to 8dp — MUST be generic, not per-symbol)
- Instant re-entry (15-min per-symbol cooldown on all exit types)
- ORCA catastrophic sizing (1% equity hard cap per trade)
- SNX cumulative loss auto-block ($1.50 threshold → 48h block)
- Exit reason missing from TRADE_PLACED log events
- _direction NameError in screener _get_signal_lead() — was silently dropping all signals
- use_scout_sizing unexpected kwarg crash in executor
- f-string format spec crash on TP2 log line

Recurring risk: float precision fix has recurred on new symbols. Verify kraken_cli.py
paper_sell uses math.floor(qty * 1e8) / 1e8 for ALL symbols, not symbol-specific patches.
- htf_trend RSI invalidation exit fix (2026-05-06): `invalidation_rsi_long_floor` changed
  40 → 35 (require deeper RSI drop before exiting longs); `invalidation_rsi_candles` raised
  4 → 6 (min 6 bars / 24h at 4h interval before RSI exit can fire). R:R improved 0.32:1 →
  1.20:1 on 5-trade sample (60d, 4h, all-pairs backtest). Changes in backtest.py only;
  live strategy files not touched. Exit reason `invalidation_rsi_long_floor` configurable
  via `cfg.get()` — other strategies default to 40 (no regression).

---

## Session Roadmap

### SESSION D — Add Strategy 4: Pullback to VWAP (NEXT)
Start a NEW session. Prompt:

"Read CLAUDE.md. I have 3 existing strategies (vwap_meanreversion, htf_trend_pullback,
volatility_breakout) with a confidence-based selection system where the highest-confidence
strategy wins and owns the position until exit. Add Strategy 4: long-only pullback_vwap.

First, read the existing strategy files to understand the exact structure and interfaces
used by the other 3 strategies — mirror that structure exactly.

Strategy 4 logic:
- Triggered when scanner finds a coin up 8%+ with RVOL spike (momentum play)
- Wait for price to pull back to within 0.5% of VWAP on a 15m bar
- Confirm pullback volume is lower than the initial move volume
- Enter long. Stop: below pullback bar low. Target: 2R.
- Long only, no shorts ever.

Also: permanently disable short entries in vwap_meanreversion (long-only flag in config).
Also: add --strategy pullback_vwap flag to backtest.py so Strategy 4 can be backtested.
Do not touch the live bot or deploy anything until I approve the plan.
Use /plan first."

### SESSION A — Rewrite Scanner with 5-Pillar Pipeline (COMPLETED)
Start a NEW session. Prompt:

"Read CLAUDE.md. Your task is to rewrite the scanner to implement the 3-stage
5-pillar filtering pipeline described there. Read backend/screener/service.py first
to understand current structure. Then:
1. Implement Stage 1 static filtering with 12-24h Redis caching
2. Implement Stage 2 dynamic filtering per scan cycle
3. Keep Stage 3 (strategy signal) for surviving pairs only
4. Store grade (A+/A/B/C/F) and per-pillar pass/fail in screener results
5. Add GET /api/v1/screener/criteria endpoint returning pillar definitions as JSON
6. Add (i) button to frontend Scanner section opening a criteria modal
Use /plan first."

### SESSION B — Extend Backtester to Simulate Scanner (after Session A deployed)
Start a NEW session. Prompt:

"Read CLAUDE.md. The backtester in backtest.py currently tests a static symbol.
Extend it to simulate the full scanner pipeline: for each historical period, run
the 5-pillar grading logic across a universe of pairs, select the top-graded pair,
run the strategy, record the result, advance time, repeat.
CLI: python backtest.py --days 60 --universe SNX/USD,AXS/USD,HNT/USD
Goal: get 20+ trades in sample using real pair selection logic.
Use /plan first."

### SESSION C — Parameter Optimization (after Session B)
Sweep dev_threshold_pct (1-3%), max_bars_in_trade (4-12), tp1_R (1.0-2.0).
Target: win_rate >= 40% AND R:R >= 1.5:1 over 50+ trades.
Only after this passes: consider re-enabling live shadow trading.

---

## Operational Rules
- Shadow balance: $500 per test run
- Max 1 open position (micro_mode)
- Risk per trade: 1% equity hard cap (~$5 on $500)
- Per-symbol cooldown: 15 min after any exit
- Real money: NEVER until win rate >= 66% sustained over 50+ backtested trades
- Log export capped at 5,000 events in UI; use SSH for full history
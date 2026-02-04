# Leader Plan: Trading Strategy Performance Improvement

## Problem Statement

**Client Report:** Bot is losing 20% per week ($8 loss on $41.67 equity). If this continues, the account will be depleted in ~5 weeks.

**Current State:**
- **Risk Settings:** 2% per trade, 5% stop-loss, 10% daily loss limit
- **Strategies Active:** Mean Reversion, MACD Crossover, Momentum
- **Confidence Thresholds:** 85-90% for buy/sell signals
- **Position Management:** Stop-loss only (5%), no take-profit
- **Account Status:** $41.67 → $32.27 (22.5% loss)

**Critical Issues Identified:**
1. **No Performance Analytics:** Cannot determine win rate, average win/loss ratio, or strategy effectiveness
2. **Wide Stop-Losses:** 5% stop-loss on small account means $2+ loss per trade (5% of $40 = $2)
3. **No Take-Profit:** Positions held indefinitely until stop-loss or strategy SELL signal
4. **Low Confidence Thresholds:** 85% may allow too many marginal trades
5. **No Strategy Filtering:** All strategies trade all symbols, no market regime detection
6. **No Position Sizing Optimization:** Fixed 2% risk regardless of signal quality

## 1. Scope

### In Scope
- **Performance Analytics:** Analyze historical trades to calculate win rate, profit factor, average win/loss
- **Strategy Parameter Optimization:** Test different confidence thresholds, stop-loss levels, take-profit targets
- **Risk Management Improvements:** Tighter stop-losses, take-profit logic, position sizing based on signal quality
- **Market Regime Detection:** Filter strategies based on market conditions (trending vs ranging)
- **Backtesting Framework:** Test strategy improvements on historical data before live deployment
- **Strategy Selection:** Identify which strategies work best and disable/optimize underperformers

### Out of Scope
- Changing core strategy logic (MACD, Momentum, Mean Reversion algorithms)
- Modifying API contracts or database schemas
- Frontend changes (focus on backend strategy performance)
- Real-time market data improvements (data quality is acceptable)

## 2. File Ownership

### Research & Analysis (research/)
- `research/analysis/performance.py` - **NEW**: Performance metrics calculation
- `research/analysis/backtest.py` - **NEW**: Backtesting framework for strategy optimization
- `research/strategies/*/strategy.py` - Review and optimize strategy parameters
- `research/strategies/*/config.py` - Adjust confidence thresholds, filters

### Backend Risk Management (backend/risk/)
- `backend/risk/sizing.py` - Add dynamic position sizing based on signal confidence
- `backend/risk/two_percent.py` - Review 2% rule effectiveness
- `backend/risk/limits.py` - Review daily loss limits

### Backend Execution (backend/execution/)
- `backend/execution/executor.py` - Ensure take-profit logic works (if implemented)

### Backend Positions (backend/positions/)
- `backend/positions/monitor.py` - Add take-profit monitoring (if not already implemented)

### Configuration (.env)
- Add new risk parameters: `TAKE_PROFIT_PCT`, `TIGHT_STOP_LOSS_PCT`, `HIGH_CONFIDENCE_THRESHOLD`

## 3. Contracts Impacted

### API Contracts
- No changes to API contracts (analysis is internal)

### Environment Variables
- `TAKE_PROFIT_PCT` (default: 10.0) - Auto-sell when profit reaches this %
- `TIGHT_STOP_LOSS_PCT` (default: 3.0) - Tighter stop-loss for high-confidence trades
- `HIGH_CONFIDENCE_THRESHOLD` (default: 95.0) - Higher confidence threshold for larger positions

### Database Schema
- No changes (use existing `orders`, `signals`, `positions` tables for analysis)

## 4. Acceptance Criteria

### Performance Analysis (TICKET-301)
- [ ] Calculate win rate from historical trades (target: >50%)
- [ ] Calculate profit factor (avg win / avg loss, target: >1.5)
- [ ] Identify worst-performing strategies (disable if win rate <40%)
- [ ] Calculate average holding time per position
- [ ] Identify most profitable symbols/timeframes

### Strategy Optimization (TICKET-302)
- [ ] Backtest different confidence thresholds (85%, 90%, 95%)
- [ ] Backtest different stop-loss levels (3%, 5%, 7%)
- [ ] Backtest take-profit levels (5%, 10%, 15%)
- [ ] Identify optimal parameters per strategy
- [ ] Document parameter recommendations

### Risk Management Improvements (TICKET-303)
- [ ] Implement take-profit auto-sell (sell when profit >= threshold)
- [ ] Implement tighter stop-losses for high-confidence trades
- [ ] Implement dynamic position sizing (larger positions for higher confidence)
- [ ] Reduce default stop-loss from 5% to 3% for small accounts
- [ ] Verify daily loss limit is enforced

### Market Regime Detection (TICKET-304)
- [ ] Detect trending vs ranging markets using ADX
- [ ] Filter Mean Reversion strategy to only trade in ranging markets (ADX < 20)
- [ ] Filter Momentum/MACD strategies to only trade in trending markets (ADX > 25)
- [ ] Log market regime in screener results

## 5. Dependencies

### Must Complete First
- **TICKET-201** (P&L Calculation) - Need accurate P&L to analyze performance
- **TICKET-204** (Account P&L) - Need account-level metrics

### Can Run Parallel
- **TICKET-301** (Performance Analysis) - Can analyze existing trade data
- **TICKET-302** (Strategy Optimization) - Can backtest independently
- **TICKET-303** (Risk Management) - Can implement improvements independently

### Must Complete Before Production
- **TICKET-304** (Market Regime Detection) - Should be tested before enabling

## Agent Launch Instructions

---

### Ticket 1: Performance Analysis & Metrics
**Agent:** `quant-research`  
**Ticket:** `TICKET-301: Analyze trading performance and identify failure modes`  
**Branch:** `research/performance-analysis`

**Prompt:**
```
Analyze the bot's trading performance to identify why it's losing 20% per week.

Requirements:
1. Create `research/analysis/performance.py`:
   - Query database for all executed trades (from `orders` or `signals` table)
   - Calculate metrics:
     * Win rate: (winning trades / total trades) * 100
     * Profit factor: average win / average loss
     * Average win size (USD)
     * Average loss size (USD)
     * Largest win
     * Largest loss
     * Average holding time per position
     * Strategy performance breakdown (per strategy_id)
     * Symbol performance breakdown (per symbol)
     * Timeframe performance breakdown (per interval)

2. Identify failure modes:
   - Which strategies have lowest win rate?
   - Which symbols are losing money?
   - Are stop-losses being hit too often?
   - Are positions held too long?
   - Are there patterns in losing trades?

3. Generate report:
   - Create `research/analysis/reports/performance_report.md`
   - Include all metrics above
   - Include recommendations (e.g., "Disable Strategy X", "Avoid Symbol Y")
   - Include charts if possible (win/loss distribution, strategy comparison)

4. Query data sources:
   - Use `backend/db/models.py` to access Order, Signal, Position models
   - Query from last 7 days (or all available data)
   - Include both realized and unrealized P&L

Files to create:
- `research/analysis/performance.py` - Performance calculation module
- `research/analysis/reports/performance_report.md` - Analysis report

Do not modify:
- Database schemas
- Production trading code
- Strategy logic (analysis only)

Acceptance criteria:
- Report shows win rate, profit factor, and strategy breakdown
- Identifies at least 3 actionable recommendations
- Metrics calculated correctly from database
```

---

### Ticket 2: Strategy Parameter Backtesting
**Agent:** `quant-research`  
**Ticket:** `TICKET-302: Backtest strategy parameter optimizations`  
**Branch:** `research/strategy-optimization`

**Prompt:**
```
Backtest different strategy parameters to find optimal settings that improve win rate and reduce losses.

Requirements:
1. Create `research/analysis/backtest.py`:
   - Load historical OHLCV data from database (use existing ingestor data)
   - Simulate trades with different parameter sets:
     * Confidence thresholds: [85%, 90%, 95%, 98%]
     * Stop-loss levels: [3%, 5%, 7%]
     * Take-profit levels: [5%, 10%, 15%, 20%]
   - Calculate performance metrics for each parameter set
   - Identify best parameters per strategy

2. Test each strategy independently:
   - Mean Reversion: Test RSI thresholds, ADX filters, stop-loss/take-profit
   - MACD Crossover: Test confidence thresholds, EMA filters, stop-loss/take-profit
   - Momentum: Test ROC thresholds, ADX filters, stop-loss/take-profit

3. Generate optimization report:
   - Create `research/analysis/reports/optimization_report.md`
   - Show parameter sensitivity (how performance changes with each parameter)
   - Recommend optimal parameters per strategy
   - Include backtest results (win rate, profit factor, max drawdown)

4. Use existing data:
   - Query OHLCV data from `backend/db/models.py` (Bar model)
   - Use last 30 days of data (or maximum available)
   - Test on multiple symbols (BTC/USD, ETH/USD, etc.)

Files to create:
- `research/analysis/backtest.py` - Backtesting framework
- `research/analysis/reports/optimization_report.md` - Optimization results

Do not modify:
- Production strategy code (backtest in isolation)
- Database schemas
- Live trading logic

Acceptance criteria:
- Backtests run successfully for all 3 strategies
- Optimal parameters identified for each strategy
- Report shows clear parameter recommendations
- Backtest results show improved win rate vs current settings
```

---

### Ticket 3: Risk Management Improvements
**Agent:** `quant-research`  
**Ticket:** `TICKET-303: Implement tighter risk management and take-profit logic`  
**Branch:** `research/risk-improvements`

**Prompt:**
```
Implement improved risk management: tighter stop-losses, take-profit auto-sell, and dynamic position sizing.

Requirements:
1. Add take-profit logic to `backend/positions/monitor.py`:
   - Read `TAKE_PROFIT_PCT` from environment (default: 10.0)
   - In `_update_all_positions()`, check if profit >= take-profit threshold
   - If profit >= threshold, create SELL TradeIntent and execute
   - Log: "Take-profit triggered: {symbol} sold at ${price:.2f} (profit: {profit:.1f}%)"

2. Implement tighter stop-losses for small accounts:
   - Modify `backend/risk/sizing.py`:
     * If account equity < $50, use 3% stop-loss instead of 5%
     * If account equity < $25, use 2% stop-loss instead of 5%
   - Update `.env` to add `TIGHT_STOP_LOSS_PCT=3.0` (for accounts < $50)

3. Implement dynamic position sizing:
   - Modify `backend/risk/sizing.py`:
     * If signal confidence >= 95%, increase position size by 1.5x (max 3% risk)
     * If signal confidence < 85%, reduce position size by 0.5x (min 1% risk)
   - Use confidence from strategy result (passed via TradeIntent metadata)

4. Update environment variables:
   - Add `TAKE_PROFIT_PCT=10.0` to `.env`
   - Add `TIGHT_STOP_LOSS_PCT=3.0` to `.env`
   - Document in `.env.example`

5. Integration:
   - Ensure take-profit checks run every 60 seconds in PositionMonitor
   - Ensure position sizing uses dynamic sizing in executor
   - Test with small account ($32 equity)

Files to modify:
- `backend/positions/monitor.py` - Add take-profit checking
- `backend/risk/sizing.py` - Add dynamic stop-loss and position sizing
- `.env` - Add new risk parameters
- `.env.example` - Document new parameters

Do not modify:
- Strategy logic (risk management only)
- Position model (use existing fields)

Acceptance criteria:
- Take-profit auto-sells positions when profit >= threshold
- Stop-losses are tighter for small accounts (< $50)
- Position sizing adjusts based on signal confidence
- All changes configurable via environment variables
```

---

### Ticket 4: Market Regime Detection & Strategy Filtering
**Agent:** `quant-research`  
**Ticket:** `TICKET-304: Add market regime detection to filter strategies`  
**Branch:** `research/market-regime-detection`

**Prompt:**
```
Add market regime detection to filter strategies based on market conditions (trending vs ranging).

Requirements:
1. Create `research/analysis/regime.py`:
   - Calculate ADX (Average Directional Index) for each symbol
   - Classify market regime:
     * Trending: ADX > 25 (strong trend)
     * Ranging: ADX < 20 (choppy/ranging)
     * Neutral: 20 <= ADX <= 25
   - Return regime classification per symbol

2. Integrate regime detection into screener:
   - Modify `backend/screener/service.py`:
     * Before running strategies, detect market regime for each symbol
     * Filter strategies based on regime:
       - Mean Reversion: Only trade in RANGING markets (ADX < 20)
       - Momentum/MACD: Only trade in TRENDING markets (ADX > 25)
     * Log regime classification: "Market regime: {symbol} = {regime} (ADX: {adx:.1f})"

3. Update strategy execution:
   - If regime doesn't match strategy, skip signal generation
   - Log: "Skipping {strategy} for {symbol}: market regime {regime} not suitable"
   - This prevents Mean Reversion from trading in trends (where it fails)

4. Use existing ADX calculation:
   - Strategies already calculate ADX (check `research/strategies/*/strategy.py`)
   - Reuse ADX calculation or add to screener engine
   - Calculate ADX with period=14 (standard)

Files to modify:
- `backend/screener/service.py` - Add regime detection and filtering
- `research/analysis/regime.py` - **NEW**: Market regime detection module

Do not modify:
- Strategy logic (filtering only, not algorithm changes)
- Database schemas

Acceptance criteria:
- Market regime detected correctly (trending vs ranging)
- Mean Reversion only trades in ranging markets
- Momentum/MACD only trade in trending markets
- Regime classification logged in screener output
- Reduces bad trades from wrong market conditions
```

---

## Execution Order

1. **TICKET-301** (Performance Analysis) - **CRITICAL PATH** - Must understand current performance first
2. **TICKET-302** (Strategy Optimization) - **HIGH PRIORITY** - Can run parallel with TICKET-301
3. **TICKET-303** (Risk Management) - **HIGH PRIORITY** - Implement immediately after analysis
4. **TICKET-304** (Market Regime) - **MEDIUM PRIORITY** - Can be done after TICKET-303

## Expected Outcomes

After implementing all tickets:
- **Win rate improvement:** From current (unknown) to >50%
- **Reduced losses:** Stop-losses tightened from 5% to 3% for small accounts
- **Profit locking:** Take-profit at 10% prevents giving back gains
- **Better trade selection:** Market regime filtering reduces bad trades
- **Strategy optimization:** Parameters tuned for current market conditions

## Risk Mitigation

- **Backtest before deploying:** All parameter changes tested on historical data
- **Gradual rollout:** Deploy one improvement at a time, monitor results
- **Fallback parameters:** Keep current parameters as fallback if new ones fail
- **Monitoring:** Track win rate daily after changes, revert if performance degrades

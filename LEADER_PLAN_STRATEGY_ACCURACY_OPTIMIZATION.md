# Leader Plan: Strategy Accuracy Optimization for Low-Risk/High-Reward Consistent Performance

## Problem Statement

**Client Goal:** Achieve highest accuracy with low-risk/high-reward ratio and consistent base hits (reliable, low-variance trades).

**Current State:**
- **Strategies:** Mean Reversion, MACD Crossover, Momentum (all use weighted confidence scoring)
- **Confidence Thresholds:** 85-90% buy/sell (configurable per strategy)
- **Risk Management:** 2% per trade, 5% stop-loss, no take-profit
- **Performance:** Losing 20% per week, unknown win rate
- **Signal Quality:** Confidence scoring exists but may not optimize for accuracy

**Critical Issues:**
1. **No Accuracy Metrics:** Cannot measure win rate or risk/reward ratio
2. **Confidence Scoring May Be Suboptimal:** Current weights may not maximize accuracy
3. **No Risk/Reward Filter:** Trades may have poor R:R ratios (< 2:1)
4. **No Consistency Metrics:** High variance in trade outcomes
5. **No Historical Validation:** Strategies not backtested for accuracy

## 1. Scope

### In Scope
- **Performance Analytics:** Calculate win rate, risk/reward ratio, consistency metrics from historical trades
- **Confidence Score Optimization:** Optimize weight distribution in confidence scoring to maximize accuracy
- **Risk/Reward Filtering:** Add R:R ratio filter (only trade if potential reward >= 2x risk)
- **Consistency Improvements:** Reduce trade variance through stricter filters
- **Backtesting Framework:** Test strategy improvements on historical data
- **Signal Quality Enhancement:** Improve A+ setup criteria to increase accuracy

### Out of Scope
- Changing core strategy algorithms (MACD, RSI, Bollinger Bands calculations)
- Modifying API contracts or database schemas
- Frontend changes (focus on backend strategy logic)
- Real-time execution improvements (focus on signal quality)

## 2. File Ownership

### Research & Analysis (research/)
- `research/analysis/performance.py` - **NEW**: Performance metrics (win rate, R:R, consistency)
- `research/analysis/backtest.py` - **NEW**: Backtesting framework with accuracy metrics
- `research/analysis/optimization.py` - **NEW**: Confidence weight optimization
- `research/strategies/meanrev/strategy.py` - Optimize confidence scoring weights
- `research/strategies/macd/strategy.py` - Optimize confidence scoring weights
- `research/strategies/momentum/strategy.py` - Optimize confidence scoring weights
- `research/strategies/meanrev/config.py` - Add R:R filter parameters
- `research/strategies/macd/config.py` - Add R:R filter parameters
- `research/strategies/momentum/config.py` - Add R:R filter parameters

### Backend Screening (backend/screener/)
- `backend/screener/engine.py` - Add risk/reward ratio filtering
- `backend/screener/service.py` - Integrate R:R checks before signal execution

### Configuration (.env)
- Add `MIN_RISK_REWARD_RATIO=2.0` (minimum R:R to accept trade)
- Add `TARGET_WIN_RATE=0.60` (target win rate for optimization)

## 3. Contracts Impacted

### API Contracts
- No changes (analysis is internal, signals remain same format)

### Environment Variables
- `MIN_RISK_REWARD_RATIO` (default: 2.0) - Minimum R:R ratio to accept trade
- `TARGET_WIN_RATE` (default: 0.60) - Target win rate for optimization (60%)

### Database Schema
- No changes (use existing `orders`, `signals`, `positions` tables for analysis)

### Strategy Config Schema
- Add `min_risk_reward_ratio` field to strategy configs (optional, defaults to 2.0)

## 4. Acceptance Criteria

### Performance Analysis (TICKET-401)
- [ ] Calculate win rate from historical trades (current baseline)
- [ ] Calculate average risk/reward ratio per strategy
- [ ] Calculate consistency metrics (standard deviation of returns, Sharpe-like ratio)
- [ ] Identify strategies with best accuracy
- [ ] Generate performance report with recommendations

### Confidence Score Optimization (TICKET-402)
- [ ] Backtest different confidence weight distributions
- [ ] Find optimal weights that maximize win rate (target: >60%)
- [ ] Test weight sensitivity (how much does accuracy change with weights)
- [ ] Document optimal weights per strategy
- [ ] Implement optimized weights in strategy code

### Risk/Reward Filtering (TICKET-403)
- [ ] Calculate potential R:R ratio for each signal (entry to stop-loss vs entry to take-profit)
- [ ] Filter signals with R:R < minimum threshold (default: 2.0)
- [ ] Only execute trades with R:R >= threshold
- [ ] Log filtered signals: "Signal rejected: R:R {ratio:.2f} < {threshold:.2f}"
- [ ] Verify R:R calculation uses actual stop-loss and take-profit levels

### Consistency Improvements (TICKET-404)
- [ ] Add stricter A+ setup filters to reduce variance
- [ ] Require multiple confirmations (e.g., ADX + Volume + Trend alignment)
- [ ] Increase confidence thresholds for "base hit" trades (target: 95%+)
- [ ] Implement consistency scoring (reward signals with multiple confirmations)
- [ ] Track consistency metrics (win rate variance, drawdown)

## 5. Dependencies

### Must Complete First
- **TICKET-201** (P&L Calculation) - Need accurate P&L to calculate win rate
- **TICKET-204** (Account P&L) - Need account-level metrics

### Can Run Parallel
- **TICKET-401** (Performance Analysis) - Can analyze existing data
- **TICKET-402** (Confidence Optimization) - Can backtest independently
- **TICKET-403** (R:R Filtering) - Can implement independently

### Must Complete Before Production
- **TICKET-404** (Consistency) - Should be tested after optimization

## Agent Launch Instructions

---

### Ticket 1: Performance Analysis & Baseline Metrics
**Agent:** `quant-research`  
**Ticket:** `TICKET-401: Analyze strategy performance and establish accuracy baseline`  
**Branch:** `research/performance-baseline`

**Prompt:**
```
Analyze the bot's trading performance to establish accuracy baseline and identify strategies with best win rate and risk/reward ratio.

Requirements:
1. Create `research/analysis/performance.py`:
   - Query database for all executed trades (from `orders` or `signals` table with execution status)
   - Calculate accuracy metrics:
     * Win rate: (winning trades / total trades) * 100
     * Risk/Reward ratio: average win size / average loss size
     * Average win (USD)
     * Average loss (USD)
     * Consistency: standard deviation of returns, coefficient of variation
     * Largest win
     * Largest loss
     * Win rate per strategy (Mean Reversion, MACD, Momentum)
     * Win rate per symbol
     * Risk/Reward ratio per strategy

2. Calculate risk/reward for each trade:
   - Risk: entry_price - stop_loss_price (for longs)
   - Reward: take_profit_price - entry_price (if take-profit exists, else use strategy exit)
   - R:R ratio: reward / risk
   - Average R:R per strategy

3. Identify best performers:
   - Which strategy has highest win rate?
   - Which strategy has best R:R ratio?
   - Which symbols/timeframes are most profitable?
   - What confidence levels correlate with wins?

4. Generate baseline report:
   - Create `research/analysis/reports/baseline_performance.md`
   - Include all metrics above
   - Include strategy comparison (win rate, R:R, consistency)
   - Include recommendations: "Strategy X has {win_rate}% win rate, focus on optimizing it"

5. Query data sources:
   - Use `backend/db/models.py` to access Order, Signal, Position models
   - Query from last 30 days (or all available data)
   - Match signals to fills to calculate actual P&L

Files to create:
- `research/analysis/performance.py` - Performance calculation module
- `research/analysis/reports/baseline_performance.md` - Baseline report

Do not modify:
- Database schemas
- Production trading code
- Strategy logic (analysis only)

Acceptance criteria:
- Report shows win rate, R:R ratio, and consistency metrics for all strategies
- Identifies best-performing strategy
- Metrics calculated correctly from database
- Baseline established for comparison after optimization
```

---

### Ticket 2: Confidence Score Weight Optimization
**Agent:** `quant-research`  
**Ticket:** `TICKET-402: Optimize confidence scoring weights to maximize accuracy`  
**Branch:** `research/confidence-optimization`

**Prompt:**
```
Optimize confidence scoring weights in all three strategies to maximize win rate while maintaining consistent performance.

Requirements:
1. Create `research/analysis/optimization.py`:
   - Load historical OHLCV data from database
   - Simulate strategy evaluation with different weight distributions
   - Test weight combinations using grid search or optimization algorithm
   - Calculate win rate for each weight combination
   - Find weights that maximize win rate (target: >60%)

2. Optimize Mean Reversion Strategy (`research/strategies/meanrev/strategy.py`):
   - Current weights: RSI (30%), BB position (25%), ADX (25%), ATR (20%)
   - Test weight ranges: [20-40%] for each component
   - Find optimal weights that maximize win rate
   - Ensure weights sum to 100%

3. Optimize MACD Strategy (`research/strategies/macd/strategy.py`):
   - Current weights: Crossover (trigger), Histogram (momentum), Trend (alignment), ADX (strength), Volume (confirmation)
   - Test different weight distributions
   - Find optimal weights for highest accuracy

4. Optimize Momentum Strategy (`research/strategies/momentum/strategy.py`):
   - Current weights: ROC (trigger), EMA (alignment), ADX (strength), RSI (range), Volume (confirmation)
   - Test different weight distributions
   - Find optimal weights for highest accuracy

5. Generate optimization report:
   - Create `research/analysis/reports/confidence_optimization.md`
   - Show current vs optimal weights for each strategy
   - Show win rate improvement (e.g., "Win rate improved from 45% to 62%")
   - Include sensitivity analysis (how much accuracy changes with weight changes)

6. Implement optimal weights:
   - Update strategy files with optimized weights
   - Add comments explaining weight choices
   - Ensure backward compatibility (weights configurable via config if needed)

Files to modify:
- `research/strategies/meanrev/strategy.py` - Update confidence scoring weights
- `research/strategies/macd/strategy.py` - Update confidence scoring weights
- `research/strategies/momentum/strategy.py` - Update confidence scoring weights

Files to create:
- `research/analysis/optimization.py` - Weight optimization module
- `research/analysis/reports/confidence_optimization.md` - Optimization results

Do not modify:
- Core indicator calculations (RSI, MACD, ROC algorithms)
- Signal generation logic (only optimize weights)
- Database schemas

Acceptance criteria:
- Optimal weights identified for all 3 strategies
- Win rate improved to >60% in backtests
- Weights implemented in strategy code
- Report shows clear improvement metrics
```

---

### Ticket 3: Risk/Reward Ratio Filtering
**Agent:** `quant-research`  
**Ticket:** `TICKET-403: Add risk/reward ratio filtering to ensure low-risk/high-reward trades`  
**Branch:** `research/risk-reward-filtering`

**Prompt:**
```
Add risk/reward ratio filtering to ensure only trades with favorable R:R ratios (>= 2:1) are executed.

Requirements:
1. Add R:R calculation to strategy evaluation:
   - Modify `research/strategies/base.py` or strategy files:
     * Calculate risk: entry_price - stop_loss_price (for longs)
     * Calculate reward: take_profit_price - entry_price (use configurable take-profit target)
     * R:R ratio: reward / risk
   - Add R:R ratio to SignalResult indicators dict

2. Add R:R filter to screener:
   - Modify `backend/screener/engine.py`:
     * Read `MIN_RISK_REWARD_RATIO` from environment (default: 2.0)
     * After confidence threshold check, verify R:R >= minimum
     * If R:R < minimum, set signal to "NONE" and log rejection
     * Log: "Signal rejected: {symbol} R:R {ratio:.2f} < {threshold:.2f}"

3. Configure take-profit targets:
   - Add `take_profit_pct` to strategy configs (default: 10.0%)
   - Mean Reversion: Use 10% take-profit (mean reversion targets quick reversals)
   - MACD: Use 15% take-profit (trend following can run longer)
   - Momentum: Use 12% take-profit (momentum trades can extend)

4. Update strategy configs:
   - `research/strategies/meanrev/config.py` - Add `take_profit_pct: float = 10.0`
   - `research/strategies/macd/config.py` - Add `take_profit_pct: float = 15.0`
   - `research/strategies/momentum/config.py` - Add `take_profit_pct: float = 12.0`

5. Calculate R:R in strategy evaluate() methods:
   - For each strategy, in `evaluate()` method:
     * Get entry_price (current_price)
     * Get stop_loss_price (entry_price * (1 - stop_loss_pct / 100))
     * Get take_profit_price (entry_price * (1 + take_profit_pct / 100))
     * Calculate risk = entry_price - stop_loss_price
     * Calculate reward = take_profit_price - entry_price
     * Calculate r_r_ratio = reward / risk
     * Add to indicators: `"risk_reward_ratio": round(r_r_ratio, 2)`

6. Update environment:
   - Add `MIN_RISK_REWARD_RATIO=2.0` to `.env`
   - Document in `.env.example`

Files to modify:
- `research/strategies/meanrev/strategy.py` - Add R:R calculation in evaluate()
- `research/strategies/macd/strategy.py` - Add R:R calculation in evaluate()
- `research/strategies/momentum/strategy.py` - Add R:R calculation in evaluate()
- `research/strategies/meanrev/config.py` - Add take_profit_pct
- `research/strategies/macd/config.py` - Add take_profit_pct
- `research/strategies/momentum/config.py` - Add take_profit_pct
- `backend/screener/engine.py` - Add R:R filtering
- `.env` - Add MIN_RISK_REWARD_RATIO

Do not modify:
- Stop-loss placement logic (already works)
- Position sizing (R:R is signal-level, not position-level)

Acceptance criteria:
- R:R ratio calculated for all signals
- Signals with R:R < 2.0 are filtered out
- R:R ratio visible in signal indicators
- Filtering logged clearly
- Take-profit targets configurable per strategy
```

---

### Ticket 4: Consistency Improvements for Base Hits
**Agent:** `quant-research`  
**Ticket:** `TICKET-404: Enhance signal filters for consistent base hits (high accuracy, low variance)`  
**Branch:** `research/consistency-improvements`

**Prompt:**
```
Enhance strategy filters to prioritize consistent "base hit" trades (high accuracy, low variance) over high-risk/high-reward trades.

Requirements:
1. Increase confidence thresholds for base hits:
   - Modify `backend/screener/service.py`:
     * Add `BASE_HIT_CONFIDENCE_THRESHOLD` environment variable (default: 95.0)
     * For "base hit" mode, require confidence >= 95% instead of 85-90%
     * Log: "Base hit mode: requiring {threshold}% confidence for consistent trades"

2. Require multiple confirmations:
   - Enhance strategy confidence scoring to reward multiple confirmations:
     * Mean Reversion: Require ALL of: RSI extreme + BB extreme + ADX < 20 + ATR active
     * MACD: Require ALL of: Crossover + Histogram expanding + Trend aligned + ADX > 20 + Volume confirmed
     * Momentum: Require ALL of: ROC threshold + EMA stack + ADX > 25 + RSI optimal + Volume confirmed
   - Add bonus points for multiple confirmations (e.g., +10% confidence if all 5 criteria met)

3. Add consistency scoring:
   - Create `research/analysis/consistency.py`:
     * Calculate consistency score based on:
       - Number of confirmations (more = higher consistency)
       - Indicator alignment (all pointing same direction = higher consistency)
       - Historical accuracy of similar setups (if data available)
     * Add consistency score to SignalResult indicators

4. Filter for consistency:
   - Modify `backend/screener/engine.py`:
     * After R:R check, verify consistency score >= threshold
     * Only execute trades with high consistency (reduces variance)
     * Log: "High consistency signal: {symbol} consistency={score:.1f}"

5. Update strategy configs for base hits:
   - Increase default confidence thresholds:
     * Mean Reversion: confidence_buy=95.0, confidence_sell=90.0
     * MACD: confidence_buy=95.0, confidence_sell=90.0
     * Momentum: confidence_buy=95.0, confidence_sell=90.0
   - Update `backend/db/seeds/strategies.sql` with new thresholds

6. Track consistency metrics:
   - In performance analysis, calculate:
     * Win rate variance (lower = more consistent)
     * Coefficient of variation of returns
     * Drawdown consistency (smaller drawdowns = more consistent)

Files to modify:
- `research/strategies/meanrev/strategy.py` - Add consistency scoring
- `research/strategies/macd/strategy.py` - Add consistency scoring
- `research/strategies/momentum/strategy.py` - Add consistency scoring
- `backend/screener/engine.py` - Add consistency filtering
- `backend/screener/service.py` - Increase confidence thresholds
- `backend/db/seeds/strategies.sql` - Update default thresholds

Files to create:
- `research/analysis/consistency.py` - Consistency scoring module

Do not modify:
- Core indicator calculations
- Signal generation logic (only enhance filtering)

Acceptance criteria:
- Confidence thresholds increased to 95% for base hits
- Multiple confirmations required for high-confidence signals
- Consistency score calculated and used for filtering
- Win rate variance reduced (more consistent trades)
- Trade frequency may decrease but accuracy increases
```

---

## Execution Order

1. **TICKET-401** (Performance Analysis) - **CRITICAL PATH** - Must establish baseline first
2. **TICKET-402** (Confidence Optimization) - **HIGH PRIORITY** - Optimize weights based on baseline
3. **TICKET-403** (R:R Filtering) - **HIGH PRIORITY** - Can run parallel with TICKET-402
4. **TICKET-404** (Consistency) - **MEDIUM PRIORITY** - Implement after optimization

## Expected Outcomes

After implementing all tickets:
- **Win Rate:** From current (unknown) to >60% (target)
- **Risk/Reward:** All trades have R:R >= 2:1 (low-risk/high-reward)
- **Consistency:** Reduced variance, more "base hits" (reliable trades)
- **Accuracy:** Optimized confidence weights maximize win rate
- **Trade Quality:** Only high-confidence, high R:R trades executed

## Success Metrics

- **Win Rate:** >60% (up from current baseline)
- **Risk/Reward Ratio:** Average R:R >= 2.5:1
- **Consistency:** Coefficient of variation < 0.5 (low variance)
- **Base Hit Rate:** >70% of trades are winners (consistent base hits)
- **Drawdown:** Maximum drawdown < 10% (low risk)

## Risk Mitigation

- **Backtest before deploying:** All optimizations tested on historical data
- **Gradual rollout:** Deploy one improvement at a time, monitor results
- **Fallback parameters:** Keep current parameters as fallback
- **Monitoring:** Track win rate daily, revert if accuracy degrades
- **A/B testing:** Compare optimized vs current strategies side-by-side

# Leader Plan: Replace Three Strategies with Production-Grade Analyzed Strategies

## Problem Statement

**Client Goal:** Replace the current three strategies (Mean Reversion, MACD Crossover, Momentum) with three new, rigorously analyzed strategies optimized for high accuracy, low-risk/high-reward ratio, and consistent base hits.

**Current State:**
- **Strategies:** Mean Reversion (5m), MACD Crossover (1h), Momentum (1h)
- **Performance:** Losing 20% per week, unknown win rate
- **Architecture:** Strategies inherit from `BaseStrategy`, emit `TradeIntent` objects
- **Risk Management:** 2% per trade, 5% stop-loss, no take-profit
- **Data Flow:** Redis streams → Strategy → TradeIntent → Risk Manager → Execution

**New Strategies Required:**
1. **VWAP Mean Reversion** (15m entry, 1h filter) - Target: 60-75% win rate, 1.2-2.5R
2. **Volatility Contraction → Expansion** (15m entry, 1h/4h filter) - Target: 55-65% win rate, 2-4R
3. **HTF Trend Pullback Continuation** (1h entry, 4h trend) - Target: 50-65% win rate, 2-3R

## 1. Scope

### In Scope
- **Strategy Implementation:** Implement three new strategy classes following `BaseStrategy` interface
- **Multi-Timeframe Support:** Add HTF (Higher Timeframe) data fetching for regime filters
- **Indicator Library:** Extend `research/strategies/indicators.py` with VWAP, Anchored VWAP, BB Width, EMA, ADX, swing detection
- **Signal Metadata:** Enhance `TradeIntent.metadata` to include stop-loss, take-profit levels, entry price, invalidation conditions
- **Configuration:** Create config classes for each strategy with all parameters from spec
- **Database Seeds:** Update `backend/db/seeds/strategies.sql` to replace old strategies with new ones
- **Backward Compatibility:** Ensure new strategies work with existing risk management, execution, and analytics systems
- **Testing:** Unit tests for each strategy, indicator calculations, and signal generation logic

### Out of Scope
- **Risk Management Changes:** Existing risk engine remains unchanged (will consume new TradeIntent metadata)
- **Execution Engine Changes:** Execution engine already handles stop-loss/take-profit from metadata
- **API Contracts:** No changes to API contracts or database schemas
- **Frontend Changes:** Frontend will automatically display new strategies via existing endpoints
- **Backtesting Framework:** Backtesting exists but may need extension for multi-timeframe analysis (future work)
- **Adaptive Parameter Updates:** Walk-forward optimization mentioned in spec is future work

## 2. File Ownership

### Research & Strategy Implementation (`research/strategies/`)
- `research/strategies/vwap_meanrev/` - **NEW**: Strategy 1 implementation
  - `strategy.py` - VWAP Mean Reversion strategy class
  - `config.py` - Configuration dataclass with all parameters
  - `__init__.py` - Module exports
  - `tests/test_strategy.py` - Unit tests
- `research/strategies/volatility_breakout/` - **NEW**: Strategy 2 implementation
  - `strategy.py` - Volatility Contraction → Expansion strategy
  - `config.py` - Configuration dataclass
  - `__init__.py` - Module exports
  - `tests/test_strategy.py` - Unit tests
- `research/strategies/htf_trend/` - **NEW**: Strategy 3 implementation
  - `strategy.py` - HTF Trend Pullback Continuation strategy
  - `config.py` - Configuration dataclass
  - `__init__.py` - Module exports
  - `tests/test_strategy.py` - Unit tests
- `research/strategies/indicators.py` - **MODIFY**: Add VWAP, Anchored VWAP, BB Width, EMA, ADX, swing detection
- `research/strategies/base.py` - **MODIFY**: Add helper method for fetching HTF data
- `research/strategies/types.py` - **MODIFY**: Enhance TradeIntent metadata structure (backward compatible)

### Database & Configuration (`backend/db/`)
- `backend/db/seeds/strategies.sql` - **MODIFY**: Replace old strategy seeds with new ones
- `backend/db/migrations/` - **NEW**: Migration to update existing strategy records (if needed)

### Backend Integration (`backend/`)
- `backend/screener/service.py` - **MODIFY**: Ensure compatibility with new strategies (should work as-is)
- `backend/runner/service.py` - **MODIFY**: Support multi-timeframe data fetching if needed

### Documentation (`docs/`)
- `docs/STRATEGY_SPECS.md` - **NEW**: Detailed specification document for each strategy
- `docs/INDICATORS.md` - **NEW**: Indicator calculation reference

## 3. Contracts Impacted

### API Contracts
- **No changes** - Strategies emit `TradeIntent` objects which already support metadata
- `TradeIntent.metadata` will be enhanced but remains backward compatible (existing code ignores unknown keys)

### Database Schema
- **No changes** - Strategies table already supports JSONB config
- Existing strategy records may need migration to new config structure (handled in seeds)

### Redis Streams
- **No changes** - Market data streams remain the same
- Strategies may consume multiple timeframe streams (already supported)

### Type Definitions (`contracts/types.md`)
- **No changes** - `TradeIntent` type already supports flexible metadata
- Documentation may be updated to clarify metadata structure

## 4. Acceptance Criteria

### Strategy 1: VWAP Mean Reversion
- ✅ Strategy class implements `BaseStrategy` interface
- ✅ Generates LONG signals when price closes below VWAP by threshold AND RSI oversold AND reversal confirmation
- ✅ Generates SHORT signals when price closes above VWAP by threshold AND RSI overbought AND reversal confirmation
- ✅ Includes HTF regime filter (1h EMA200, trend slope)
- ✅ TradeIntent metadata includes: stop_loss_price, tp1_price, tp2_price, entry_price, invalidation_conditions
- ✅ Unit tests cover all signal conditions, edge cases, and indicator calculations
- ✅ Configuration exposes all parameters from spec with sane defaults

### Strategy 2: Volatility Contraction → Expansion
- ✅ Strategy class implements `BaseStrategy` interface
- ✅ Detects compression using BB Width percentile and ATR threshold
- ✅ Generates LONG signals on breakout above upper BB with volume spike AND retest confirmation
- ✅ Generates SHORT signals on breakout below lower BB with volume spike AND retest confirmation
- ✅ Includes HTF filter (1h or 4h) to avoid major resistance/support
- ✅ TradeIntent metadata includes: stop_loss_price, tp1_price, tp2_price, entry_price, retest_level
- ✅ Unit tests cover compression detection, breakout logic, retest validation
- ✅ Configuration exposes all parameters from spec with sane defaults

### Strategy 3: HTF Trend Pullback Continuation
- ✅ Strategy class implements `BaseStrategy` interface
- ✅ Qualifies trend direction using 4h EMA200 and slope
- ✅ Generates LONG signals on pullback to EMA20/EMA50 zone with bullish reversal on 1h
- ✅ Generates SHORT signals on pullback to EMA20/EMA50 zone with bearish reversal on 1h
- ✅ Includes trend invalidation check (4h close below EMA200 for longs)
- ✅ TradeIntent metadata includes: stop_loss_price, tp1_price, tp2_price, entry_price, trend_invalidation_level
- ✅ Unit tests cover trend qualification, pullback detection, entry confirmation
- ✅ Configuration exposes all parameters from spec with sane defaults

### Integration & Compatibility
- ✅ All three strategies work with existing `ScreenerService` (no changes required)
- ✅ All three strategies work with existing `StrategyRunner` (may need HTF data support)
- ✅ TradeIntent metadata is consumed correctly by risk manager and execution engine
- ✅ Database seeds create new strategies with correct config structure
- ✅ Existing strategies can be deprecated/disabled without breaking system

### Testing
- ✅ Unit tests for each strategy cover:
  - Signal generation logic
  - Indicator calculations (VWAP, BB Width, EMA, ADX, etc.)
  - Edge cases (insufficient data, boundary conditions)
  - Multi-timeframe data handling
- ✅ Integration tests verify strategies emit valid TradeIntent objects
- ✅ All tests pass with >90% code coverage

## 5. Dependencies

### Prerequisites
- ✅ Existing `BaseStrategy` interface and `TradeIntent` type definitions
- ✅ Existing indicator library (`research/strategies/indicators.py`)
- ✅ Existing Redis stream infrastructure for market data
- ✅ Existing risk management and execution engines (consume TradeIntent)

### Implementation Order
1. **Phase 1: Indicator Library Extension** (TICKET-601)
   - Add VWAP, Anchored VWAP, BB Width, EMA, ADX, swing detection
   - Must complete before strategy implementation
   
2. **Phase 2: Base Strategy Enhancement** (TICKET-602)
   - Add HTF data fetching helper method
   - Enhance TradeIntent metadata structure (backward compatible)
   - Must complete before strategy implementation

3. **Phase 3: Strategy 1 Implementation** (TICKET-603)
   - VWAP Mean Reversion strategy
   - Can proceed in parallel with Phase 4

4. **Phase 4: Strategy 2 Implementation** (TICKET-604)
   - Volatility Contraction → Expansion strategy
   - Can proceed in parallel with Phase 3

5. **Phase 5: Strategy 3 Implementation** (TICKET-605)
   - HTF Trend Pullback Continuation strategy
   - Depends on Phase 1 and 2

6. **Phase 6: Database & Integration** (TICKET-606)
   - Update database seeds
   - Verify integration with screener and runner
   - Depends on Phases 3, 4, 5

## 6. Agent Launch Instructions

### Agent Launch Instructions:

1. Agent: `/quant-research`
   Ticket: TICKET-601 - Indicator Library Extension
   Branch: `feature/indicator-library-extension`
   Prompt:
   "Extend `research/strategies/indicators.py` to add the following indicator functions:
   
   - `calculate_vwap(prices: List[float], volumes: List[float], anchor_index: Optional[int] = None) -> Optional[float]`
     - Calculate Volume-Weighted Average Price
     - If anchor_index provided, calculate Anchored VWAP from that point
     - Return None if insufficient data
   
   - `calculate_bb_width(upper_band: float, lower_band: float, middle_band: float) -> float`
     - Calculate normalized Bollinger Band width: (upper - lower) / middle
   
   - `calculate_ema(prices: List[float], period: int) -> Optional[List[float]]`
     - Calculate Exponential Moving Average for all prices
     - Return list of EMA values (None for insufficient data points)
   
   - `calculate_adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[Dict[str, float]]`
     - Calculate Average Directional Index
     - Return dict with keys: 'adx', 'plus_di', 'minus_di'
     - Return None if insufficient data
   
   - `detect_swing_highs_lows(bars: List[MarketDataEvent], lookback: int = 3) -> Dict[str, List[float]]`
     - Detect swing highs and lows using lookback bars
     - Return dict with keys: 'highs', 'lows', 'high_indices', 'low_indices'
   
   - `calculate_ema_slope(ema_values: List[float], bars: int = 5) -> Optional[float]`
     - Calculate EMA slope over last N bars
     - Return slope as percentage change
   
   All functions must:
   - Handle edge cases (insufficient data, empty lists)
   - Include type hints
   - Include docstrings with examples
   - Match existing indicator function style in the file
   
   Add unit tests in `research/strategies/tests/test_indicators.py` covering:
   - Normal cases with sufficient data
   - Edge cases (insufficient data, boundary conditions)
   - Comparison with known reference values
   
   Reference: `research/strategies/indicators.py` for existing patterns."

2. Agent: `/backend-execute`
   Ticket: TICKET-602 - Base Strategy Enhancement for Multi-Timeframe
   Branch: `feature/base-strategy-htf-support`
   Prompt:
   "Enhance `research/strategies/base.py` to add multi-timeframe data fetching support:
   
   Add method to `BaseStrategy` class:
   ```python
   def fetch_htf_bars(self, symbol: str, htf_interval: str, count: int = 200) -> List[MarketDataEvent]:
       \"\"\"
       Fetch higher timeframe bars for regime filtering.
       
       Args:
           symbol: Trading pair symbol
           htf_interval: Higher timeframe interval (e.g., '1h', '4h')
           count: Number of bars to fetch
           
       Returns:
           List of MarketDataEvent objects, oldest first
       \"\"\"
   ```
   
   This method should:
   - Use existing `consume_market_data` infrastructure
   - Fetch bars from Redis stream `market:ohlcv:{symbol}:{htf_interval}`
   - Return bars in chronological order (oldest first)
   - Handle cases where HTF data is not available (return empty list)
   - Cache recent HTF bars in memory to avoid repeated Redis calls
   
   Also enhance `TradeIntent.metadata` documentation in `research/strategies/types.py`:
   - Add comments documenting expected metadata keys:
     - `stop_loss_price`: float - Stop loss level
     - `tp1_price`: float - First take profit target
     - `tp2_price`: Optional[float] - Second take profit target
     - `entry_price`: float - Suggested entry price
     - `invalidation_conditions`: Dict - Conditions that invalidate the signal
     - `strategy_specific`: Dict - Strategy-specific metadata
   
   Ensure backward compatibility - existing code should continue to work.
   
   Reference: `research/strategies/base.py`, `research/strategies/types.py`"

3. Agent: `/backend-execute`
   Ticket: TICKET-603 - Strategy 1: VWAP Mean Reversion Implementation
   Branch: `feature/vwap-mean-reversion-strategy`
   Prompt:
   "Implement Strategy 1: VWAP Mean Reversion in `research/strategies/vwap_meanrev/`.
   
   Create `research/strategies/vwap_meanrev/config.py`:
   - Dataclass `VWAPMeanReversionConfig` with all parameters from spec:
     - dev_threshold_ATR: float = 0.5
     - rsi_oversold: float = 30.0
     - rsi_overbought: float = 70.0
     - atr_stop_mult: float = 1.5
     - swing_lookback_bars: int = 5
     - tp1_R: float = 1.2
     - tp2_R: float = 2.5
     - max_bars_in_trade: int = 12
     - volume_filter_mode: str = 'conservative'
     - regime_slope_threshold: float = 0.001
     - And all other parameters from spec section 3
   
   Create `research/strategies/vwap_meanrev/strategy.py`:
   - Class `VWAPMeanReversionStrategy(BaseStrategy)` implementing:
     - `generate_signals(bar: MarketDataEvent) -> Optional[TradeIntent]`
     - `evaluate(symbol: str, bars: List[MarketDataEvent]) -> SignalResult`
   
   Signal Logic (LONG):
   1. Fetch 1h HTF bars for regime filter
   2. Check regime: 1h price above EMA200 OR flat trend (slope threshold)
   3. Calculate VWAP (session VWAP) and Anchored VWAP
   4. Check deviation: price closes below VWAP by dev_threshold * ATR
   5. Check RSI(14) <= rsi_oversold
   6. Check volume filter (volume <= 1.5 * vol_sma)
   7. Check reversal confirmation: candle closes above VWAP OR bullish engulfing
   8. Calculate stop-loss (swing low or ATR-based, whichever is wider)
   9. Calculate TP1 and TP2 based on R-multiples
   10. Generate TradeIntent with metadata
   
   SHORT logic mirrors LONG with inverted conditions.
   
   Use indicators from `research/strategies/indicators.py` (assume TICKET-601 complete).
   Use HTF fetching from `BaseStrategy` (assume TICKET-602 complete).
   
   Create `research/strategies/vwap_meanrev/tests/test_strategy.py`:
   - Test signal generation for LONG and SHORT
   - Test regime filtering (block signals in wrong regime)
   - Test stop-loss and take-profit calculation
   - Test edge cases (insufficient data, boundary conditions)
   
   Reference: `research/strategies/meanrev/strategy.py` for structure, `docs/STRATEGY_SPECS.md` for detailed logic."

4. Agent: `/backend-execute`
   Ticket: TICKET-604 - Strategy 2: Volatility Contraction → Expansion Implementation
   Branch: `feature/volatility-breakout-strategy`
   Prompt:
   "Implement Strategy 2: Volatility Contraction → Expansion in `research/strategies/volatility_breakout/`.
   
   Create `research/strategies/volatility_breakout/config.py`:
   - Dataclass `VolatilityBreakoutConfig` with all parameters from spec:
     - squeeze_percentile: float = 10.0
     - squeeze_lookback_N: int = 200
     - vol_compress_mult: float = 0.9
     - vol_breakout_mult: float = 1.5
     - retest_window_bars: int = 6
     - retest_fail_bps: float = 50.0
     - atr_stop_mult: float = 1.8
     - atr_target1_mult: float = 2.0
     - atr_target2_mult: float = 3.5
     - And all other parameters from spec section 4
   
   Create `research/strategies/volatility_breakout/strategy.py`:
   - Class `VolatilityBreakoutStrategy(BaseStrategy)` implementing:
     - `generate_signals(bar: MarketDataEvent) -> Optional[TradeIntent]`
     - `evaluate(symbol: str, bars: List[MarketDataEvent]) -> SignalResult`
   
   Signal Logic (LONG):
   1. Detect compression: BB Width in bottom percentile AND ATR low AND volume low
   2. Detect breakout: candle closes above upper BB AND volume spike
   3. Wait for retest: price pulls back toward breakout level within retest_window_bars
   4. Validate retest: retest candle does NOT close back into range
   5. Entry confirmation: strong continuation close after retest
   6. Calculate stop-loss below retest low
   7. Calculate TP1 and TP2 using ATR multiples or measured move
   8. Generate TradeIntent with metadata
   
   SHORT logic mirrors LONG with inverted conditions.
   
   Use indicators from `research/strategies/indicators.py` (assume TICKET-601 complete).
   Use HTF fetching from `BaseStrategy` (assume TICKET-602 complete).
   
   Create `research/strategies/volatility_breakout/tests/test_strategy.py`:
   - Test compression detection
   - Test breakout detection with volume spike
   - Test retest validation logic
   - Test stop-loss and take-profit calculation
   - Test edge cases
   
   Reference: `research/strategies/macd/strategy.py` for structure, `docs/STRATEGY_SPECS.md` for detailed logic."

5. Agent: `/backend-execute`
   Ticket: TICKET-605 - Strategy 3: HTF Trend Pullback Continuation Implementation
   Branch: `feature/htf-trend-pullback-strategy`
   Prompt:
   "Implement Strategy 3: HTF Trend Pullback Continuation in `research/strategies/htf_trend/`.
   
   Create `research/strategies/htf_trend/config.py`:
   - Dataclass `HTFTrendConfig` with all parameters from spec:
     - pullback_max_ATR: float = 1.5
     - break_bps: float = 50.0
     - atr_stop_mult: float = 1.5
     - atr_trail_mult: float = 2.0
     - extension_ATR_mult: float = 3.0
     - max_hours_in_trade: int = 24
     - And all other parameters from spec section 5
   
   Create `research/strategies/htf_trend/strategy.py`:
   - Class `HTFTrendStrategy(BaseStrategy)` implementing:
     - `generate_signals(bar: MarketDataEvent) -> Optional[TradeIntent]`
     - `evaluate(symbol: str, bars: List[MarketDataEvent]) -> SignalResult`
   
   Signal Logic (LONG):
   1. Fetch 4h HTF bars for trend qualification
   2. Qualify trend: 4h close > EMA200 AND EMA200 slope up
   3. Optional: Check ADX >= 18-25 for trend strength
   4. On 1h, detect pullback: price near EMA20/EMA50 zone
   5. Entry confirmation: 1h candle closes above EMA20 with bullish reversal pattern
   6. Calculate stop-loss below pullback swing low
   7. Calculate TP1 at prior 1h swing high or 1.5R
   8. Calculate TP2 at 4h resistance or 3R
   9. Include trend invalidation: if 4h closes below EMA200, exit
   10. Generate TradeIntent with metadata
   
   SHORT logic mirrors LONG with inverted conditions.
   
   Use indicators from `research/strategies/indicators.py` (assume TICKET-601 complete).
   Use HTF fetching from `BaseStrategy` (assume TICKET-602 complete).
   
   Create `research/strategies/htf_trend/tests/test_strategy.py`:
   - Test trend qualification (4h EMA200, slope)
   - Test pullback detection on 1h
   - Test entry confirmation logic
   - Test stop-loss and take-profit calculation
   - Test trend invalidation logic
   - Test edge cases
   
   Reference: `research/strategies/momentum/strategy.py` for structure, `docs/STRATEGY_SPECS.md` for detailed logic."

6. Agent: `/backend-execute`
   Ticket: TICKET-606 - Database Seeds & Integration Verification
   Branch: `feature/strategy-replacement-seeds`
   Prompt:
   "Update database seeds and verify integration:
   
   1. Update `backend/db/seeds/strategies.sql`:
      - Replace 'mean_reversion' strategy with 'vwap_meanreversion'
      - Replace 'macd_crossover' strategy with 'volatility_breakout'
      - Replace 'trend_following' strategy with 'htf_trend_pullback'
      - Set appropriate symbols (BTC/USD or ETH/USD), intervals (15m or 1h), and parameters
      - Keep status as 'active' for all three
      - Use ON CONFLICT DO UPDATE to allow re-running seeds
   
   2. Create migration script `backend/db/migrations/002_replace_strategies.sql`:
      - Update existing strategy records if they exist
      - Set old strategies to 'inactive' status
      - Insert new strategies with correct configs
   
   3. Verify integration:
      - Ensure `backend/screener/service.py` can instantiate new strategies
      - Ensure `backend/runner/service.py` can run new strategies
      - Test that TradeIntent objects with new metadata are handled correctly
      - Verify risk manager consumes stop-loss/take-profit from metadata
   
   4. Update `research/strategies/__init__.py`:
      - Export new strategy classes
      - Keep old strategies exported for backward compatibility (deprecated)
   
   Reference: `backend/db/seeds/strategies.sql`, `backend/screener/service.py`, `backend/runner/service.py`"

7. Agent: `/qa-verify`
   Ticket: TICKET-607 - Strategy Replacement QA Verification
   Branch: `qa/strategy-replacement-verification`
   Prompt:
   "Verify correctness, safety, and regressions for strategy replacement:
   
   1. **Findings:**
      - Review all strategy implementations for:
        - Lookahead bias (signals only on candle close)
        - Edge case handling (insufficient data, boundary conditions)
        - Indicator calculation correctness
        - Metadata structure completeness
      - Review integration points:
        - ScreenerService compatibility
        - StrategyRunner compatibility
        - Risk manager metadata consumption
        - Execution engine stop-loss/take-profit handling
   
   2. **Recommended Tests:**
      - Add integration tests in `backend/tests/integration/`:
        - Test screener with new strategies
        - Test strategy runner with new strategies
        - Test TradeIntent flow end-to-end
      - Add performance tests:
        - Measure strategy evaluation time
        - Verify no memory leaks
      - Add regression tests:
        - Ensure old strategies still work (if not removed)
        - Ensure existing risk/execution logic unchanged
   
   3. **Verification Commands:**
      - Run unit tests: `pytest research/strategies/ -v`
      - Run integration tests: `pytest backend/tests/integration/ -v`
      - Verify database seeds: `psql -d omni_bot -f backend/db/seeds/strategies.sql`
      - Check code coverage: `pytest --cov=research/strategies --cov-report=html`
   
   Reference: All strategy implementation files, `backend/screener/service.py`, `backend/runner/service.py`"

---

## 7. Risk Mitigation

### Technical Risks
- **Multi-timeframe data availability:** HTF data may not be available in Redis streams
  - **Mitigation:** Add fallback logic, log warnings, allow strategies to work without HTF filters
- **Indicator calculation performance:** Complex indicators may slow down signal generation
  - **Mitigation:** Cache indicator values, optimize calculations, add performance monitoring
- **Backward compatibility:** New strategies may break existing systems
  - **Mitigation:** Extensive testing, gradual rollout, keep old strategies available initially

### Operational Risks
- **Strategy performance:** New strategies may perform worse than expected
  - **Mitigation:** Paper trading period, performance monitoring, ability to revert
- **Configuration errors:** Incorrect parameters may cause losses
  - **Mitigation:** Parameter validation, sane defaults, documentation

## 8. Success Metrics

- ✅ All three new strategies implemented and tested
- ✅ Unit test coverage >90% for new strategies
- ✅ Integration tests pass
- ✅ Database seeds updated successfully
- ✅ Strategies work with existing screener and runner
- ✅ TradeIntent metadata correctly consumed by risk/execution engines
- ✅ Documentation complete
- ✅ Zero regressions in existing functionality

## 9. Timeline Estimate

- **TICKET-601:** 2-3 days (Indicator library extension)
- **TICKET-602:** 1-2 days (Base strategy enhancement)
- **TICKET-603:** 3-4 days (VWAP Mean Reversion)
- **TICKET-604:** 3-4 days (Volatility Breakout)
- **TICKET-605:** 3-4 days (HTF Trend Pullback)
- **TICKET-606:** 1-2 days (Database & integration)
- **TICKET-607:** 2-3 days (QA verification)

**Total:** 15-22 days

## 10. Notes

- Strategies must follow existing `BaseStrategy` interface - no changes to core architecture
- Multi-timeframe support is new but uses existing Redis stream infrastructure
- TradeIntent metadata enhancement is backward compatible
- Old strategies can remain in codebase (deprecated) for reference
- Consider adding strategy-specific documentation in `docs/STRATEGY_SPECS.md`

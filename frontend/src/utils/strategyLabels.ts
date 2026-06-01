/** DB / supervisor slugs that must never appear in supervisor UI or Strategy Setup. */
export const RETIRED_STRATEGY_SLUGS = new Set([
  'macd',
  'pullback_vwap',
  'macd_crossover',
]);

export const STRATEGY_DISPLAY_NAME: Record<string, string> = {
  vwap_meanrev: 'VWAP MeanRev (4h)',
  vwap_meanrev_1h: 'VWAP MeanRev (1h)',
  vwap_meanreversion: 'VWAP MeanRev (4h)',
  volatility_breakout: 'Volatility Breakout',
  htf_trend: 'HTF Trend (1h/4h)',
  htf_trend_pullback: 'HTF Trend (1h/4h)',
  meanrev: 'Range Mean Reversion',
  bull_flag_1m: 'Bull Flag (1min)',
  bull_flag_5m: 'Bull Flag (5min)',
  bull_flag_1h: 'Bull Flag (1hr)',
  swing_bull_flag: 'Swing Bull Flag (4h)',
};

function titleCaseSlug(slug: string): string {
  return slug
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function getStrategyDisplayName(slug: string): string {
  if (!slug) return '';
  const key = slug.trim().toLowerCase();
  if (STRATEGY_DISPLAY_NAME[key]) return STRATEGY_DISPLAY_NAME[key];
  return titleCaseSlug(slug);
}

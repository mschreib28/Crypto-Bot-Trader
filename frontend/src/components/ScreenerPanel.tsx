import { useState, useEffect } from 'react';
import { useScreener, ScreenerSignal, SignalType, ScreenerIndicators } from '../hooks/useScreener';
import { useStrategies } from '../hooks/useStrategies';
import { useShadowLive } from '../hooks/useShadowLive';
import { useTrading } from '../hooks/useTrading';

const MIN_BARS_REQUIRED = 20;

interface ColumnDef {
  key: string;
  header: string;
  accessor: (indicators: ScreenerIndicators) => number | undefined;
  format: (v: number | undefined) => string;
  align?: 'left' | 'right';
}

const formatNum = (v: number | undefined) => v != null ? v.toFixed(2) : '—';

function formatConfidence(value: number | null | undefined): string {
  if (value == null || isNaN(value)) return '0%';
  return `${Math.round(value)}%`;
}

function getRvolPctColor(rvolPct: number | null | undefined): string {
  if (rvolPct == null) return 'text-gray-400';
  if (rvolPct > 100) return 'text-green-400';
  if (rvolPct < 80) return 'text-red-400';
  return 'text-gray-400';
}

function formatPrice(value: number): string {
  if (value >= 1000) {
    return value.toLocaleString('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }
  if (value >= 1) {
    return value.toLocaleString('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
      maximumFractionDigits: 4,
    });
  }
  return value.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 4,
    maximumFractionDigits: 6,
  });
}

function formatTimestamp(isoString: string | null): string {
  if (!isoString) return '—';
  try {
    const date = new Date(isoString);
    return date.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return '—';
  }
}

function getSignalColor(signal: SignalType): string {
  switch (signal) {
    case 'BUY':
      return 'text-green-400';
    case 'SELL':
      return 'text-red-400';
    case 'NONE':
    default:
      return 'text-gray-500';
  }
}

interface StrengthBarProps {
  strength: number | null | undefined;
  signal: SignalType;
  direction?: 'bullish' | 'bearish' | null;
  showSubThreshold?: boolean; // In Shadow mode, show confidence even below execution threshold
}

function getBarColor(signal: SignalType, direction?: 'bullish' | 'bearish' | null): string {
  // If signal is BUY or SELL, use signal color
  if (signal === 'BUY') return 'bg-green-500';
  if (signal === 'SELL') return 'bg-red-500';

  // For NONE signals, use direction to determine color
  if (direction === 'bullish') return 'bg-green-500/60';  // Dimmed green
  if (direction === 'bearish') return 'bg-red-500/60';    // Dimmed red

  return 'bg-gray-500';  // Neutral
}

function StrengthBar({ strength, signal, direction, showSubThreshold = false }: StrengthBarProps) {
  const safeStrength = (strength == null || isNaN(strength)) ? 0 : strength;
  const clampedStrength = Math.max(0, Math.min(100, safeStrength));
  const barColor = getBarColor(signal, direction);
  
  // In Shadow mode, always show confidence. In Live mode, gray out sub-threshold
  const isSubThreshold = safeStrength < EXECUTION_ELIGIBLE_THRESHOLD;
  const shouldShow = showSubThreshold || !isSubThreshold || signal !== 'NONE';
  const opacity = (showSubThreshold && isSubThreshold && signal === 'NONE') ? 'opacity-50' : '';

  if (!shouldShow && !showSubThreshold) {
    return (
      <div className="flex items-center gap-1.5">
        <div className="w-16 h-1.5 bg-gray-700 rounded-full" />
        <span className="text-gray-500 text-xs font-mono w-7 text-right">—</span>
      </div>
    );
  }

  return (
    <div
      className={`flex items-center gap-1.5 ${opacity}`}
      role="progressbar"
      aria-valuenow={clampedStrength}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={`Signal strength: ${formatConfidence(strength)}`}
    >
      <div className="w-16 h-1.5 bg-gray-700 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-300 ${barColor}`}
          style={{ width: `${clampedStrength}%` }}
        />
      </div>
      <span className={`text-xs font-mono w-7 text-right ${isSubThreshold && !showSubThreshold ? 'text-gray-500' : 'text-gray-300'}`}>
        {formatConfidence(strength)}
      </span>
    </div>
  );
}

function hasInsufficientBars(signal: ScreenerSignal): boolean {
  const barsAvailable = signal.indicators.bars_available as number | undefined;
  return typeof barsAvailable === 'number' && barsAvailable < MIN_BARS_REQUIRED;
}

interface SignalRowProps {
  signal: ScreenerSignal;
  isEven: boolean;
  isShadowMode?: boolean;
}

const EXECUTION_ELIGIBLE_THRESHOLD = 90;

// A+ indicator formatting helpers
const formatAdx = (v: number | undefined) => v != null ? v.toFixed(0) : '—';
const formatVolRatio = (v: number | undefined) => v != null ? `${v.toFixed(1)}x` : '—';
const formatBand = (v: number | undefined) => v != null ? `${(v * 100).toFixed(0)}%` : '—';
const formatRoc = (v: number | undefined) => v != null ? `${v >= 0 ? '+' : ''}${v.toFixed(1)}%` : '—';

const STRATEGY_COLUMNS: Record<string, ColumnDef[]> = {
  mean_reversion: [
    { key: 'rsi', header: 'RSI', accessor: i => i.rsi, format: formatNum },
    { key: 'bb_position', header: 'BB %', accessor: i => i.bb_position as number | undefined, format: formatBand },
    { key: 'adx', header: 'ADX', accessor: i => i.adx as number | undefined, format: formatAdx },
    { key: 'atr_ratio', header: 'ATR', accessor: i => i.atr_ratio as number | undefined, format: formatVolRatio },
  ],
  momentum: [
    { key: 'roc', header: 'ROC', accessor: i => i.roc as number | undefined, format: formatRoc },
    { key: 'adx', header: 'ADX', accessor: i => i.adx as number | undefined, format: formatAdx },
    { key: 'rsi', header: 'RSI', accessor: i => i.rsi, format: formatNum },
  ],
  macd: [
    { key: 'histogram', header: 'Hist', accessor: i => i.histogram as number | undefined, format: formatNum },
    { key: 'adx', header: 'ADX', accessor: i => i.adx as number | undefined, format: formatAdx },
    { key: 'ema_50', header: 'EMA50', accessor: i => i.ema_50 as number | undefined, format: formatNum },
  ],
};

const DEFAULT_COLUMNS: ColumnDef[] = [
  { key: 'rsi', header: 'RSI', accessor: i => i.rsi, format: formatNum },
];

interface SignalRowPropsWithColumns extends SignalRowProps {
  extraColumns: ColumnDef[];
  isMacd: boolean;
  isShadowMode?: boolean;
}

function SignalRow({ signal: data, isEven, extraColumns, isMacd, isShadowMode = false }: SignalRowPropsWithColumns) {
  const signalColor = getSignalColor(data.signal_type);
  // Handle both 'price' and 'current_price' keys from different sources
  const price = data.indicators.price ?? data.indicators.current_price as number | undefined;
  const rvolPct = data.indicators['rvol_pct'] as number | undefined;
  const change24h = data.indicators.change_24h_pct as number | undefined;
  const rvolColor = getRvolPctColor(rvolPct);
  const insufficientData = hasInsufficientBars(data);
  const isExecutionEligible = data.signal_strength >= EXECUTION_ELIGIBLE_THRESHOLD;

  // Extract direction from indicators, fallback to RSI-based derivation
  const direction = (data.indicators.direction as 'bullish' | 'bearish' | undefined)
    || (data.indicators.rsi != null
      ? (data.indicators.rsi < 50 ? 'bullish' : 'bearish')
      : undefined);

  const rowBg = isEven ? 'bg-gray-800/50' : 'bg-gray-850';

  if (insufficientData) {
    return (
      <tr className={`${rowBg} border-b border-gray-700/30`}>
        <td className="py-1.5 pl-2 pr-2 text-gray-200 font-medium text-xs">{data.symbol}</td>
        <td colSpan={3 + extraColumns.length + (isMacd ? 1 : 0) + 2} className="py-1.5 text-gray-500 italic text-xs">
          Waiting for data...
        </td>
      </tr>
    );
  }

  return (
    <tr
      className={`${rowBg} border-b border-gray-700/30 hover:bg-gray-700/50 transition-colors ${
        isExecutionEligible ? 'border-l-2 border-l-green-500' : ''
      }`}
    >
      <td className="py-1.5 pl-2 pr-2 text-gray-200 font-medium text-xs">{data.symbol}</td>
      <td className={`py-1.5 pr-2 font-semibold text-xs ${signalColor}`}>{data.signal_type}</td>
      <td className="py-1.5 pr-2">
        <StrengthBar 
          strength={data.signal_strength} 
          signal={data.signal_type} 
          direction={direction}
          showSubThreshold={isShadowMode}
        />
      </td>
      {extraColumns.map((col) => (
        <td
          key={col.key}
          className={`py-1.5 pr-2 text-gray-300 font-mono text-xs ${col.align === 'right' ? 'text-right' : ''}`}
        >
          {col.format(col.accessor(data.indicators))}
        </td>
      ))}
      {isMacd && (
        <td className={`py-1.5 pr-2 text-center ${data.indicators?.crossover_detected ? 'text-green-400' : 'text-red-400'}`}>
          {data.indicators?.crossover_detected ? '✓' : '✗'}
        </td>
      )}
      <td className="py-1.5 pr-2 text-gray-300 font-mono text-xs text-right">
        {typeof price === 'number' ? formatPrice(price) : '—'}
      </td>
      <td className={`py-1.5 pr-2 font-mono text-xs text-right ${
        typeof change24h === 'number' 
          ? change24h >= 0 ? 'text-green-400' : 'text-red-400'
          : 'text-gray-400'
      }`}>
        {typeof change24h === 'number' 
          ? `${change24h >= 0 ? '+' : ''}${change24h.toFixed(2)}%` 
          : '—'}
      </td>
      <td className={`py-1.5 pr-2 font-mono text-xs text-right ${rvolColor}`}>
        {typeof rvolPct === 'number' ? `${Math.round(rvolPct)}%` : '--'}
      </td>
    </tr>
  );
}

export function ScreenerPanel() {
  const { strategies, loading: strategiesLoading } = useStrategies();
  const { shadowLive } = useShadowLive();
  const { trading } = useTrading();
  const [selectedStrategyId, setSelectedStrategyId] = useState<string | undefined>(undefined);

  const enabledStrategies = strategies.filter((s) => s.enabled);
  const isShadowMode = shadowLive?.enabled && !trading?.enabled;

  useEffect(() => {
    if (!selectedStrategyId && enabledStrategies.length > 0) {
      setSelectedStrategyId(enabledStrategies[0].strategy_id);
    }
  }, [enabledStrategies, selectedStrategyId]);

  const { signals, loading, error, lastScan } = useScreener({
    topN: 30,
    strategyId: selectedStrategyId,
  });

  // Determine strategy type for dynamic columns
  const selectedStrategy = strategies.find(s => s.strategy_id === selectedStrategyId);
  const strategyType = selectedStrategy?.name?.toLowerCase().includes('macd') ? 'macd'
    : selectedStrategy?.name?.toLowerCase().includes('mean') ? 'mean_reversion'
    : selectedStrategy?.name?.toLowerCase().includes('trend') ? 'momentum'
    : 'default';
  const extraColumns = STRATEGY_COLUMNS[strategyType] || DEFAULT_COLUMNS;

  // Show more signals (25 instead of 12)
  const topSignals = signals.slice(0, 25);

  return (
    <section
      className="bg-gray-800 rounded-lg p-3 h-full flex flex-col border border-gray-700"
      aria-labelledby="screener-panel-title"
    >
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <h2
            id="screener-panel-title"
            className="text-base font-semibold text-white"
          >
            Screener Signals
          </h2>
          <select
            value={selectedStrategyId || ''}
            onChange={(e) => setSelectedStrategyId(e.target.value || undefined)}
            disabled={strategiesLoading || enabledStrategies.length === 0}
            className="bg-gray-700 text-gray-200 text-xs rounded px-2 py-1 border border-gray-600 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-transparent"
            aria-label="Select strategy"
          >
            {strategiesLoading && <option value="">Loading...</option>}
            {!strategiesLoading && enabledStrategies.length === 0 && (
              <option value="">No strategies enabled</option>
            )}
            {enabledStrategies.map((strategy) => (
              <option key={strategy.strategy_id} value={strategy.strategy_id}>
                {strategy.name}
              </option>
            ))}
          </select>
        </div>
        {lastScan && (
          <span className="text-xs text-gray-500">
            Last: {formatTimestamp(lastScan)}
          </span>
        )}
      </div>

      {loading && (
        <div className="flex-1 flex flex-col items-center justify-center text-gray-400">
          <div className="w-5 h-5 border-2 border-gray-600 border-t-blue-500 rounded-full animate-spin mb-2" />
          <span className="text-sm">Scanning markets...</span>
        </div>
      )}

      {error && (
        <div className="rounded border border-red-800 bg-red-900/20 p-2 text-red-400 text-xs">
          Error: {error}
        </div>
      )}

      {!loading && !error && topSignals.length === 0 && (
        <div className="flex-1 flex flex-col items-center justify-center text-gray-500">
          <svg className="w-10 h-10 mb-2 opacity-40" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
          <span className="text-sm">No signals available</span>
          <span className="text-xs text-gray-600 mt-1">Waiting for market data</span>
        </div>
      )}

      {!loading && !error && topSignals.length > 0 && (
        <div className="overflow-auto flex-1">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-gray-800">
              <tr className="border-b border-gray-600 text-gray-400 text-left">
                <th className="py-1.5 pl-2 pr-2 font-medium">Symbol</th>
                <th className="py-1.5 pr-2 font-medium">Signal</th>
                <th className="py-1.5 pr-2 font-medium">Confidence</th>
                {extraColumns.map((col) => (
                  <th
                    key={col.key}
                    className={`py-1.5 pr-2 font-medium ${col.align === 'right' ? 'text-right' : ''}`}
                  >
                    {col.header}
                  </th>
                ))}
                {strategyType === 'macd' && <th className="py-1.5 pr-2 font-medium text-center">Crossover</th>}
                <th className="py-1.5 pr-2 font-medium text-right">Price</th>
                <th className="py-1.5 pr-2 font-medium text-right">24H Change %</th>
                <th className="py-1.5 pr-2 font-medium text-right">RVOL %</th>
              </tr>
            </thead>
            <tbody>
              {topSignals.map((signal, idx) => (
                <SignalRow 
                  key={signal.symbol} 
                  signal={signal} 
                  isEven={idx % 2 === 0} 
                  extraColumns={extraColumns} 
                  isMacd={strategyType === 'macd'}
                  isShadowMode={isShadowMode}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

import { useState, useMemo, memo, useCallback } from 'react';
import { useScreener, ScreenerSignal, SignalType } from '../hooks/useScreener';
import { useRealtimePositions } from '../hooks/useRealtimePositions';

// Formatting helpers
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

function getSignalStrengthColor(confidence: number | undefined, signalType: string | undefined): string {
  if (confidence === undefined || confidence === null || isNaN(confidence)) {
    return 'text-gray-500';
  }
  
  // Low confidence (< 50%): Dark grey, low visibility
  if (confidence < 50) {
    return 'text-gray-600';
  }
  
  // Medium confidence (50-79%): Muted color (Yellow/Grey)
  if (confidence < 80) {
    return 'text-yellow-500';
  }
  
  // High confidence (>= 80%): High-visibility color based on signal type
  if (signalType === 'BUY') {
    return 'text-green-400'; // Neon Green for BUY
  } else if (signalType === 'SELL') {
    return 'text-red-400'; // Red for SELL
  }
  
  return 'text-gray-500';
}

function getGradeColor(grade: string | undefined): string {
  if (!grade) return 'text-gray-400';
  switch (grade) {
    case 'A+':
      return 'text-green-400';
    case 'A':
      return 'text-green-300';
    case 'B':
      return 'text-yellow-400';
    case 'C':
      return 'text-yellow-300';
    case 'D':
      return 'text-orange-400';
    case 'F':
    default:
      return 'text-gray-400';
  }
}

function formatMarketCap(v: number | undefined): string {
  if (v == null) return '—';
  if (v >= 1_000_000_000) return `$${(v / 1_000_000_000).toFixed(2)}B`;
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  return `$${v.toLocaleString()}`;
}

function formatSupplyRatio(v: number | undefined): string {
  return v != null ? `${(v * 100).toFixed(1)}%` : '—';
}

function formatSpread(v: number | undefined): string {
  return v != null ? `${v.toFixed(1)} bps` : '—';
}

function formatVwapDist(v: number | undefined): string {
  return v != null ? `${v >= 0 ? '+' : ''}${v.toFixed(2)}%` : '—';
}

function formatHodDist(v: number | undefined): string {
  return v != null ? `${v >= 0 ? '+' : ''}${v.toFixed(2)}%` : '—';
}

function getStatusBadgeColor(status: string | undefined): string {
  switch (status) {
    case 'SCANNING':
      return 'bg-gray-700 text-white animate-pulse';
    case 'PENDING':
      return 'bg-yellow-600 text-white';
    case 'LIVE':
      return 'bg-green-600 text-white';
    case 'EXITING':
      return 'bg-orange-600 text-white';
    case 'COOLDOWN':
      return 'bg-gray-600 text-gray-300';
    case 'ERROR':
      return 'bg-red-600 text-white animate-pulse';
    default:
      return 'bg-gray-700 text-gray-400';
  }
}

interface SignalRowProps {
  signal: ScreenerSignal;
  isEven: boolean;
  realtimePosition?: { current_pnl_pct?: number; time_minutes?: number; status?: string };
}

const SignalRow = memo(function SignalRow({ signal: data, isEven, realtimePosition }: SignalRowProps) {
  // Handle rvol: prefer rvol_pct if available (already percentage), otherwise use rvol (decimal) and multiply by 100
  const rvolPct = data.indicators.rvol_pct as number | undefined;
  const rvolDecimal = data.indicators.rvol as number | undefined;
  const rvol = rvolPct ?? (rvolDecimal != null ? rvolDecimal * 100 : undefined);
  
  const change24h = data.indicators.change_24h_pct as number | undefined;
  const score = data.indicators.score ?? 0;
  const grade = data.indicators.grade as string | undefined;
  const status = realtimePosition?.status ?? data.indicators.status ?? 'SCANNING';

  // Visual logic
  const scoreHighlight = score > 0.85 ? 'border-l-4 border-l-green-500 shadow-[0_0_10px_rgba(34,197,94,0.3)]' : '';
  const rvolBold = (rvol ?? 0) > 5.0 ? 'font-bold text-green-400' : 'text-gray-300';
  const spreadMuted = (data.indicators.spread_bps ?? 0) > 15 ? 'text-gray-400 opacity-60' : 'text-gray-300';

  const rowBg = isEven ? 'bg-gray-800/50' : 'bg-gray-850';

  return (
    <tr className={`${rowBg} border-b border-gray-700/30 hover:bg-gray-700/50 transition-colors ${scoreHighlight}`}>
      {/* Pillars Group */}
      <td className={`py-1.5 pr-2 font-semibold text-xs text-right ${getGradeColor(grade)}`}>
        {grade || '—'}
      </td>
      <td className="py-1.5 pl-2 pr-2 text-gray-200 font-medium text-xs">{data.symbol}</td>
      <td className={`py-1.5 pr-2 font-mono text-xs text-right ${rvolBold}`}>
        {rvol != null ? `${rvol.toFixed(0)}%` : '—'}
      </td>
      <td className="py-1.5 pr-2 text-gray-300 font-mono text-xs text-right">
        {formatMarketCap(data.indicators.market_cap)}
      </td>
      <td className="py-1.5 pr-2 text-gray-300 font-mono text-xs text-right">
        {formatSupplyRatio(data.indicators.supply_ratio)}
      </td>
      <td className={`py-1.5 pr-2 font-mono text-xs text-right ${spreadMuted}`}>
        {formatSpread(data.indicators.spread_bps)}
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
      
      {/* Strategies Group */}
      <td className="py-1.5 pr-2 text-gray-300 font-mono text-xs text-right whitespace-nowrap">
        {formatVwapDist(data.indicators.vwap_dist_pct)}
      </td>
      <td className="py-1.5 pr-2 text-gray-300 font-mono text-xs text-right whitespace-nowrap">
        {formatHodDist(data.indicators.hod_dist_pct)}
      </td>
      <td className="py-1.5 pr-2 text-center whitespace-nowrap">
        {data.indicators.htf_trend === 'UP' ? (
          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-green-600/20 text-green-400 border border-green-600/50">
            UP
          </span>
        ) : data.indicators.htf_trend === 'DOWN' ? (
          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-red-600/20 text-red-400 border border-red-600/50">
            DOWN
          </span>
        ) : (
          <span className="text-gray-500">—</span>
        )}
      </td>
      {/* Signal Lead - Strategy Name */}
      <td className="py-1.5 pr-2 text-gray-300 text-xs whitespace-nowrap">
        <div className="max-w-[180px] truncate" title={(() => {
          const signalLead = data.indicators.signal_lead;
          if (!signalLead || signalLead === null || signalLead === undefined) return undefined;
          if (typeof signalLead === 'object' && signalLead !== null) {
            const obj = signalLead as {strategy_name?: string; signal_type?: string; confidence?: number};
            if (obj.strategy_name === 'Low Conviction' || obj.signal_type === 'NONE') {
              return `Low Conviction (${obj.confidence?.toFixed(1) || 0}%)`;
            }
            return obj.strategy_name || undefined;
          }
          return undefined;
        })()}>
          {(() => {
            const signalLead = data.indicators.signal_lead;
            if (!signalLead || signalLead === null || signalLead === undefined) {
              return 'Neutral';
            }
            if (typeof signalLead === 'object' && signalLead !== null) {
              const obj = signalLead as {strategy_name?: string; signal_type?: string};
              // Show "Low Conviction" or "Neutral" for weak signals
              if (obj.strategy_name === 'Low Conviction' || obj.signal_type === 'NONE') {
                return obj.strategy_name === 'Low Conviction' ? 'Low Conviction' : 'Neutral';
              }
              return obj.strategy_name || 'Neutral';
            }
            return 'Neutral';
          })()}
        </div>
      </td>
      {/* Signal Strength - Confidence Percentage */}
      <td 
        className={`py-1.5 pr-2 font-semibold text-xs whitespace-nowrap ${
          (() => {
            const sl = data.indicators.signal_lead;
            if (!sl) return 'text-gray-500';
            if (typeof sl === 'object' && sl !== null && 'confidence' in sl && 'signal_type' in sl) {
              const obj = sl as {confidence: number; signal_type: string};
              return getSignalStrengthColor(obj.confidence, obj.signal_type);
            }
            return 'text-gray-500';
          })()
        }`}
        data-symbol={data.symbol} 
        data-signal-strength={(() => {
          const sl = data.indicators.signal_lead;
          if (!sl) return 'none';
          if (typeof sl === 'object' && sl !== null && 'signal_type' in sl && 'confidence' in sl) {
            return `${(sl as {signal_type: string, confidence: number}).signal_type} ${(sl as {signal_type: string, confidence: number}).confidence}%`;
          }
          return typeof sl === 'string' ? sl : 'none';
        })()}
        title={(() => {
          const sl = data.indicators.signal_lead;
          if (!sl) return undefined;
          if (typeof sl === 'object' && sl !== null && 'signal_type' in sl && 'confidence' in sl) {
            const obj = sl as {signal_type: string; confidence: number; meets_execution_threshold?: boolean};
            const threshold = obj.meets_execution_threshold ? ' (Meets threshold)' : ' (Below threshold)';
            return `${obj.signal_type} ${obj.confidence}%${threshold}`;
          }
          return typeof sl === 'string' ? sl : undefined;
        })()}
      >
        {(() => {
          const signalLead = data.indicators.signal_lead;
          
          // Always return a string - never return the object itself
          if (!signalLead || signalLead === null || signalLead === undefined) {
            return '—';
          }
          
          // Handle string format (old format)
          if (typeof signalLead === 'string') {
            return String(signalLead);
          }
          
          // Handle object format (new format)
          if (typeof signalLead === 'object' && signalLead !== null) {
            const obj = signalLead as {confidence?: number; signal_type?: string; strategy_name?: string};
            if (typeof obj.confidence === 'number' && !isNaN(obj.confidence)) {
              // Show "Low Conviction" text for confidence < 50%
              if (obj.confidence < 50 || obj.strategy_name === 'Low Conviction' || obj.signal_type === 'NONE') {
                return `Low Conviction (${Math.round(obj.confidence)}%)`;
              }
              return `${Math.round(obj.confidence)}%`;
            }
          }
          
          // Fallback: convert to string to prevent React error
          return String(signalLead ?? '—');
        })()}
      </td>
      
      {/* Active Trade Group */}
      <td className="py-1.5 pr-2 whitespace-nowrap">
        <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium ${getStatusBadgeColor(status)}`}>
          {status}
        </span>
      </td>
    </tr>
  );
});

export function ScreenerPanel() {
  const [sortColumn, setSortColumn] = useState<string | null>('score');
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');

  const { signals, loading, error, lastScan } = useScreener({ topN: 100 });

  // Get symbols with active positions for real-time polling
  const activeSymbols = useMemo(() => {
    return signals
      .filter(s => {
        const status = s.indicators.status;
        return status && ['LIVE', 'PENDING', 'EXITING'].includes(status);
      })
      .map(s => s.symbol);
  }, [signals]);

  const { positions: realtimePositions } = useRealtimePositions(activeSymbols);

  // Merge realtime position data into signals
  const enrichedSignals = useMemo(() => {
    return signals.map(signal => {
      const realtimePos = realtimePositions.get(signal.symbol);
      if (realtimePos) {
        return {
          ...signal,
          indicators: {
            ...signal.indicators,  // Preserve all existing indicators including signal_lead
            current_pnl_pct: realtimePos.current_pnl_pct ?? signal.indicators.current_pnl_pct,
            time_minutes: realtimePos.time_minutes ?? signal.indicators.time_minutes,
            status: realtimePos.status ?? signal.indicators.status,
          },
        };
      }
      return signal;
    });
  }, [signals, realtimePositions]);

  // Helper function to get sort value
  const getSortValue = useCallback((signal: ScreenerSignal, columnKey: string): number | string => {
    switch (columnKey) {
      case 'symbol':
        return signal.symbol;
      case 'score':
        return signal.indicators.score ?? 0;
      case 'rvol':
        return (signal.indicators.rvol as number | undefined) ?? (signal.indicators.rvol_pct as number | undefined) ?? 0;
      case 'market_cap':
        return signal.indicators.market_cap ?? 0;
      case 'supply_ratio':
        return signal.indicators.supply_ratio ?? 0;
      case 'spread_bps':
        return signal.indicators.spread_bps ?? 0;
      case 'change_24h_pct':
        return signal.indicators.change_24h_pct ?? 0;
      case 'vwap_dist_pct':
        return signal.indicators.vwap_dist_pct ?? 0;
      case 'hod_dist_pct':
        return signal.indicators.hod_dist_pct ?? 0;
      case 'signal_lead':
      case 'signal_strength':
        return signal.indicators.signal_lead?.confidence ?? 0;
      default:
        const val = signal.indicators[columnKey];
        return typeof val === 'number' ? val : (typeof val === 'string' ? val : 0);
    }
  }, []);

  // Handle column sorting
  const handleSort = useCallback((columnKey: string) => {
    if (sortColumn === columnKey) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortColumn(columnKey);
      setSortDirection('desc');
    }
  }, [sortColumn, sortDirection]);

  // Sort signals
  const sortedSignals = useMemo(() => {
    if (!sortColumn) return enrichedSignals;
    return [...enrichedSignals].sort((a, b) => {
      const aVal = getSortValue(a, sortColumn);
      const bVal = getSortValue(b, sortColumn);
      
      if (sortColumn === 'symbol') {
        const multiplier = sortDirection === 'asc' ? 1 : -1;
        return multiplier * String(aVal).localeCompare(String(bVal));
      }
      
      if (sortColumn === 'signal_lead' || sortColumn === 'signal_strength') {
        const multiplier = sortDirection === 'asc' ? 1 : -1;
        return multiplier * ((aVal as number) - (bVal as number));
      }
      
      const multiplier = sortDirection === 'asc' ? 1 : -1;
      const numA = typeof aVal === 'number' ? aVal : 0;
      const numB = typeof bVal === 'number' ? bVal : 0;
      return multiplier * (numA - numB);
    });
  }, [enrichedSignals, sortColumn, sortDirection, getSortValue]);

  return (
    <section
      className="bg-gray-800 rounded-lg p-3 h-full flex flex-col border border-gray-700"
      aria-labelledby="screener-panel-title"
    >
      <div className="flex items-center justify-between mb-2">
        <h2
          id="screener-panel-title"
          className="text-base font-semibold text-white"
        >
          Scanner
        </h2>
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

      {!loading && !error && sortedSignals.length === 0 && (
        <div className="flex-1 flex flex-col items-center justify-center text-gray-500">
          <svg className="w-10 h-10 mb-2 opacity-40" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
          <span className="text-sm">No signals available</span>
          <span className="text-xs text-gray-600 mt-1">Waiting for market data</span>
        </div>
      )}

      {!loading && !error && sortedSignals.length > 0 && (
        <div className="overflow-auto flex-1">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-gray-800">
              <tr className="border-b border-gray-600 text-gray-400 text-left">
                {/* Pillars Group */}
                <th className="py-1.5 pr-2 font-medium text-right border-r border-gray-600">
                  <div className="text-xs text-gray-500 mb-1">PILLARS</div>
                  <div 
                    className="cursor-pointer hover:bg-gray-700 transition-colors"
                    onClick={() => handleSort('score')}
                  >
                    A+ Score {sortColumn === 'score' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                  </div>
                </th>
                <th 
                  className="py-1.5 pl-2 pr-2 font-medium cursor-pointer hover:bg-gray-700 transition-colors"
                  onClick={() => handleSort('symbol')}
                >
                  Symbol {sortColumn === 'symbol' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                </th>
                <th 
                  className="py-1.5 pr-2 font-medium text-right cursor-pointer hover:bg-gray-700 transition-colors"
                  onClick={() => handleSort('rvol')}
                >
                  RVOL (1h) {sortColumn === 'rvol' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                </th>
                <th 
                  className="py-1.5 pr-2 font-medium text-right cursor-pointer hover:bg-gray-700 transition-colors"
                  onClick={() => handleSort('market_cap')}
                >
                  Market Cap ($) {sortColumn === 'market_cap' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                </th>
                <th 
                  className="py-1.5 pr-2 font-medium text-right cursor-pointer hover:bg-gray-700 transition-colors"
                  onClick={() => handleSort('supply_ratio')}
                >
                  Supply % {sortColumn === 'supply_ratio' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                </th>
                <th 
                  className="py-1.5 pr-2 font-medium text-right cursor-pointer hover:bg-gray-700 transition-colors"
                  onClick={() => handleSort('spread_bps')}
                >
                  Spread (bps) {sortColumn === 'spread_bps' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                </th>
                <th 
                  className="py-1.5 pr-2 font-medium text-right cursor-pointer hover:bg-gray-700 transition-colors"
                  onClick={() => handleSort('change_24h_pct')}
                >
                  24h % Change {sortColumn === 'change_24h_pct' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                </th>
                
                {/* Strategies Group */}
                <th className="py-1.5 pr-2 font-medium text-right border-l border-r border-gray-600">
                  <div className="text-xs text-gray-500 mb-1">STRATEGIES</div>
                  <div 
                    className="cursor-pointer hover:bg-gray-700 transition-colors whitespace-nowrap"
                    onClick={() => handleSort('vwap_dist_pct')}
                  >
                    VWAP Dist % {sortColumn === 'vwap_dist_pct' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                  </div>
                </th>
                <th 
                  className="py-1.5 pr-2 font-medium text-right cursor-pointer hover:bg-gray-700 transition-colors whitespace-nowrap"
                  onClick={() => handleSort('hod_dist_pct')}
                >
                  HOD Distance % {sortColumn === 'hod_dist_pct' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                </th>
                <th className="py-1.5 pr-2 font-medium text-center whitespace-nowrap">HTF Trend</th>
                <th 
                  className="py-1.5 pr-2 font-medium cursor-pointer hover:bg-gray-700 transition-colors whitespace-nowrap"
                  onClick={() => handleSort('signal_lead')}
                >
                  Signal Lead {sortColumn === 'signal_lead' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                </th>
                <th 
                  className="py-1.5 pr-2 font-medium cursor-pointer hover:bg-gray-700 transition-colors whitespace-nowrap"
                  onClick={() => handleSort('signal_strength')}
                >
                  Signal Strength {sortColumn === 'signal_strength' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                </th>
                
                {/* Active Trade Group */}
                <th className="py-1.5 pr-2 font-medium border-l border-r border-gray-600">
                  <div className="text-xs text-gray-500 mb-1">ACTIVE TRADE</div>
                  <div>Status</div>
                </th>
              </tr>
            </thead>
            <tbody>
              {sortedSignals.map((signal, idx) => (
                <SignalRow 
                  key={signal.symbol} 
                  signal={signal} 
                  isEven={idx % 2 === 0}
                  realtimePosition={realtimePositions.get(signal.symbol)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

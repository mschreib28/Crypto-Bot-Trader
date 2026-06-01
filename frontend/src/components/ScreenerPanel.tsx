import { useState, useMemo, memo, useCallback, useRef, useEffect } from 'react';
import { useScreener, ScreenerSignal, ScreenerIndicators } from '../hooks/useScreener';
import { useRealtimePositions } from '../hooks/useRealtimePositions';
import { CriteriaModal } from './CriteriaModal';

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

// Removed unused getSignalColor - using getSignalStrengthColor instead

function getSignalStrengthColor(confidence: number | undefined, signalType: string | undefined): string {
  if (confidence === undefined || confidence === null || isNaN(confidence)) {
    return 'text-gray-500';
  }
  // Low Conviction (< 50%): Grey
  if (confidence < 50) {
    return 'text-gray-600';
  }
  // Buy Signal (50%+): Green
  if (signalType === 'BUY') {
    return 'text-green-400';
  }
  // Sell Signal (50%+): Red
  if (signalType === 'SELL') {
    return 'text-red-400';
  }
  // NONE or unknown with 50%+: default to grey (no direction)
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

// ── Pillar mini-badges ────────────────────────────────────────────────────────
// Shows 7 colored dots (S1 S2 S3 / D1 D2 D3 D4) representing each pipeline pillar.
// Green = pass, Red = fail, Gray = no data
const PILLAR_ORDER = ['s1_supply', 's2_price', 's3_listing', 'd1_rvol', 'd2_momentum', 'd3_volume', 'd4_btc'] as const;
const PILLAR_LABELS: Record<string, string> = {
  s1_supply: 'S', s2_price: 'P', s3_listing: 'L',
  d1_rvol: 'V', d2_momentum: 'M', d3_volume: '$', d4_btc: 'B',
};
const PILLAR_TITLES: Record<string, string> = {
  s1_supply: 'S1: Circulating Supply <5B',
  s2_price: 'S2: Price $0.005–$10',
  s3_listing: 'S3: Active 20+ of last 30 days',
  d1_rvol: 'D1: RVOL >3× (relative volume)',
  d2_momentum: 'D2: Momentum +8%/24h or +5%/4h',
  d3_volume: 'D3: Volume $500K–$50M',
  d4_btc: 'D4: BTC not down >4%/4h',
};

function PillarBadges({ pillars }: { pillars?: ScreenerIndicators['pillars'] }) {
  if (!pillars) return null;
  return (
    <div className="flex gap-0.5 mt-0.5 justify-end">
      {PILLAR_ORDER.map((key) => {
        const p = pillars[key];
        const bg = !p
          ? 'bg-gray-700'
          : p.pass
            ? 'bg-green-500'
            : 'bg-red-600';
        const title = `${PILLAR_TITLES[key]}: ${!p ? 'N/A' : p.pass ? 'Pass' : 'Fail'}${p?.value != null ? ` (${String(p.value)})` : ''}`;
        return (
          <span
            key={key}
            title={title}
            className={`inline-flex items-center justify-center w-3 h-3 rounded-sm text-[6px] font-bold text-white leading-none ${bg}`}
          >
            {PILLAR_LABELS[key]}
          </span>
        );
      })}
    </div>
  );
}

const DEFAULT_COL_WIDTHS: Record<string, number> = {
  score: 72, symbol: 88, price: 80, rvol: 80, market_cap: 90, supply_ratio: 72, spread_bps: 80,
  change_24h_pct: 90, vwap_dist_pct: 95, hod_dist_pct: 95, htf_trend: 80,
  signal_lead: 110, signal_strength: 100, status: 90,
};

const MIN_COL_WIDTH = 48;

interface SignalRowProps {
  signal: ScreenerSignal;
  isEven: boolean;
  realtimePosition?: { current_pnl_pct?: number; time_minutes?: number; status?: string };
  colWidths?: Record<string, number>;
}

const SignalRow = memo(function SignalRow({ signal: data, isEven, realtimePosition, colWidths }: SignalRowProps) {
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
  const rvolBold = (rvol ?? 0) > 500 ? 'font-bold text-green-400' : 'text-gray-300';
  const spreadMuted = (data.indicators.spread_bps ?? 0) > 15 ? 'text-gray-400 opacity-60' : 'text-gray-300';

  const rowBg = isEven ? 'bg-gray-800/50' : 'bg-gray-850';

  const cellStyle = (key: string) => colWidths ? { width: colWidths[key], minWidth: MIN_COL_WIDTH } : undefined;

  return (
    <tr className={`${rowBg} border-b border-gray-700/30 hover:bg-gray-700/50 transition-colors ${scoreHighlight}`}>
      {/* Pillars Group */}
      <td style={cellStyle('score')} className={`py-1 pr-2 font-semibold text-xs text-right ${getGradeColor(grade)}`}>
        <div>{grade || '—'}</div>
        <PillarBadges pillars={data.indicators.pillars} />
      </td>
      <td style={cellStyle('symbol')} className="py-1.5 pl-2 pr-2 text-gray-200 font-medium text-xs">{data.symbol}</td>
      <td style={cellStyle('price')} className="py-1.5 pr-2 font-mono text-xs text-right text-gray-300">
        {data.indicators.price != null ? `$${data.indicators.price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: data.indicators.price < 1 ? 4 : 2 })}` : '—'}
      </td>
      <td style={cellStyle('rvol')} className={`py-1.5 pr-2 font-mono text-xs text-right ${rvolBold}`}>
        {rvol != null ? `${rvol.toFixed(0)}%` : '—'}
      </td>
      <td style={cellStyle('market_cap')} className="py-1.5 pr-2 text-gray-300 font-mono text-xs text-right">
        {formatMarketCap(data.indicators.market_cap)}
      </td>
      <td style={cellStyle('supply_ratio')} className="py-1.5 pr-2 text-gray-300 font-mono text-xs text-right">
        {formatSupplyRatio(data.indicators.supply_ratio)}
      </td>
      <td style={cellStyle('spread_bps')} className={`py-1.5 pr-2 font-mono text-xs text-right ${spreadMuted}`}>
        {formatSpread(data.indicators.spread_bps)}
      </td>
      <td style={cellStyle('change_24h_pct')} className={`py-1.5 pr-2 font-mono text-xs text-right ${
        typeof change24h === 'number' 
          ? change24h >= 0 ? 'text-green-400' : 'text-red-400'
          : 'text-gray-400'
      }`}>
        {typeof change24h === 'number' 
          ? `${change24h >= 0 ? '+' : ''}${change24h.toFixed(2)}%` 
          : '—'}
      </td>
      
      {/* Strategies Group */}
      <td style={cellStyle('vwap_dist_pct')} className="py-1.5 pr-2 text-gray-300 font-mono text-xs text-right whitespace-nowrap">
        {formatVwapDist(data.indicators.vwap_dist_pct)}
      </td>
      <td style={cellStyle('hod_dist_pct')} className="py-1.5 pr-2 text-gray-300 font-mono text-xs text-right whitespace-nowrap">
        {formatHodDist(data.indicators.hod_dist_pct)}
      </td>
      <td style={cellStyle('htf_trend')} className="py-1.5 pr-2 text-center whitespace-nowrap">
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
      <td style={cellStyle('signal_lead')} className="py-1.5 pr-2 text-gray-300 text-xs whitespace-nowrap">
        <div className="max-w-[180px] truncate" title={(() => {
          const signalLead = data.indicators.signal_lead;
          if (!signalLead || signalLead === null || signalLead === undefined) return undefined;
          if (typeof signalLead === 'object' && signalLead !== null) {
            const obj = signalLead as {strategy_name?: string; signal_type?: string; confidence?: number; all_signals?: Array<{strategy_name: string; confidence: number; signal_type: string}>};
            // Show all strategies when available (e.g. "VWAP 7%, Volatility 4%")
            if (Array.isArray(obj.all_signals) && obj.all_signals.length > 0) {
              return obj.all_signals.map(s => `${s.strategy_name} ${Math.round(s.confidence)}%`).join(', ');
            }
            if (obj.strategy_name === 'Low Conviction') return `Low Conviction (${obj.confidence?.toFixed(1) || 0}%)`;
            if (obj.strategy_name) return `${obj.strategy_name} (${obj.confidence?.toFixed(1) ?? 0}%)`;
            return undefined;
          }
          return undefined;
        })()}>
          {(() => {
            const signalLead = data.indicators.signal_lead;
            if (!signalLead || signalLead === null || signalLead === undefined) {
              // B rank and below: show "—" (not calculated). A+ and A: show "Neutral"
              const g = data.indicators.grade as string | undefined;
              return (g === 'A+' || g === 'A') ? 'Neutral' : '—';
            }
            if (typeof signalLead === 'object' && signalLead !== null) {
              const obj = signalLead as {strategy_name?: string; signal_type?: string};
              if (obj.strategy_name === 'Low Conviction') return 'Low Conviction';
              // Always show strategy name when present (e.g. "VWAP Mean Reversion"), even for NONE signals
              return obj.strategy_name || 'Neutral';
            }
            return 'Neutral';
          })()}
        </div>
      </td>
      {/* Signal Strength - Confidence Percentage */}
      <td 
        style={cellStyle('signal_strength')}
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
            const obj = sl as {signal_type: string; confidence: number; meets_execution_threshold?: boolean; all_signals?: Array<{strategy_name: string; confidence: number; signal_type: string}>};
            const threshold = obj.meets_execution_threshold ? ' (Meets threshold)' : ' (Below threshold)';
            const base = `${obj.signal_type} ${obj.confidence}%${threshold}`;
            if (Array.isArray(obj.all_signals) && obj.all_signals.length > 1) {
              const all = obj.all_signals.map(s => `${s.strategy_name}: ${Math.round(s.confidence)}%`).join(' | ');
              return `${base}\n\nAll strategies: ${all}`;
            }
            return base;
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
            const obj = signalLead as {confidence?: number; signal_type?: string; strategy_name?: string; is_low_conviction?: boolean};
            if (typeof obj.confidence === 'number' && !isNaN(obj.confidence)) {
              // Show "Low Conviction (X%)" when explicitly marked or confidence < 50%
              if (obj.is_low_conviction === true || obj.confidence < 50) {
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
      <td style={cellStyle('status')} className="py-1.5 pr-2 whitespace-nowrap">
        <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium ${getStatusBadgeColor(status)}`}>
          {status}
        </span>
      </td>
    </tr>
  );
});

function ResizeHandle({
  columnKey,
  onResizeStart,
  isResizing,
}: {
  columnKey: string;
  onResizeStart: (e: React.MouseEvent) => void;
  isResizing: boolean;
}) {
  const handleMouseDown = (e: React.MouseEvent) => {
    onResizeStart(e);
  };
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize column"
      data-column={columnKey}
      onMouseDown={handleMouseDown}
      className={`absolute right-0 top-0 bottom-0 w-3 cursor-col-resize touch-none select-none z-30
        hover:bg-blue-500/70 ${isResizing ? 'bg-blue-500' : ''}`}
      style={{ marginRight: -12, minWidth: 12 }}
    />
  );
}

export function ScreenerPanel() {
  const [sortColumn, setSortColumn] = useState<string | null>('score');
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');
  const [colWidths, setColWidths] = useState<Record<string, number>>(() => ({ ...DEFAULT_COL_WIDTHS }));
  const [resizingColumn, setResizingColumn] = useState<string | null>(null);
  const resizeRef = useRef<{ key: string; startX: number; startW: number } | null>(null);
  const [criteriaModal, setCriteriaModal] = useState<{ symbol?: string; indicators?: ScreenerIndicators } | null>(null);

  const { signals, loading, error, lastScan } = useScreener({ topN: 100 });

  const handleResizeStart = useCallback((key: string) => (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    resizeRef.current = { key, startX: e.clientX, startW: colWidths[key] ?? DEFAULT_COL_WIDTHS[key] };
    setResizingColumn(key);
  }, [colWidths]);

  useEffect(() => {
    if (!resizingColumn) return;
    const onMove = (e: MouseEvent) => {
      const r = resizeRef.current;
      if (!r) return;
      const delta = e.clientX - r.startX;
      setColWidths((prev) => {
        const next = { ...prev };
        next[r.key] = Math.max(MIN_COL_WIDTH, r.startW + delta);
        return next;
      });
    };
    const onUp = () => {
      resizeRef.current = null;
      setResizingColumn(null);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    return () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [resizingColumn]);
  
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
  const activeLeadStrategyName = useMemo(() => {
    let best: string | undefined;
    let bestConf = -1;
    for (const s of signals) {
      const lead = s.indicators?.signal_lead;
      const name = lead?.strategy_name;
      const c = typeof lead?.confidence === 'number' ? lead.confidence : -1;
      if (name && c > bestConf) {
        bestConf = c;
        best = name;
      }
    }
    return best ?? null;
  }, [signals]);

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

  // Active-position rank: symbols with an open trade always appear at the top
  const isActivePosition = (signal: ScreenerSignal): boolean => {
    const s = signal.indicators.status;
    return s === 'LIVE' || s === 'PENDING' || s === 'EXITING';
  };

  // Sort signals
  const sortedSignals = useMemo(() => {
    const baseList = !sortColumn
      ? enrichedSignals
      : [...enrichedSignals].sort((a, b) => {
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

    // Pin in-position symbols to the top regardless of column sort
    return [...baseList].sort((a, b) => {
      const aActive = isActivePosition(a) ? 0 : 1;
      const bActive = isActivePosition(b) ? 0 : 1;
      return aActive - bActive;
    });
  }, [enrichedSignals, sortColumn, sortDirection, getSortValue]);

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
            Scanner
          </h2>
          <button
            onClick={() => setCriteriaModal({})}
            title="View scanner criteria and grading system"
            className="flex items-center justify-center w-4 h-4 rounded-full border border-gray-500 text-gray-400 hover:text-gray-200 hover:border-gray-300 transition-colors text-[10px] font-bold leading-none"
            aria-label="Scanner criteria info"
          >
            i
          </button>
        </div>
        {lastScan && (
          <span className="text-xs text-gray-500">
            Last: {formatTimestamp(lastScan)}
          </span>
        )}
      </div>

      {criteriaModal !== null && (
        <CriteriaModal
          onClose={() => setCriteriaModal(null)}
          symbol={criteriaModal.symbol}
          indicators={criteriaModal.indicators}
          activeLeadStrategyName={activeLeadStrategyName}
        />
      )}

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
          <table className="w-full text-xs table-fixed">
            <thead className="sticky top-0 bg-gray-800 z-10 [&_th]:overflow-visible">
              <tr className="border-b border-gray-600 text-gray-400 text-left">
                {/* Pillars Group */}
                <th
                  style={{ width: colWidths.score, minWidth: MIN_COL_WIDTH }}
                  className="py-1.5 pr-2 font-medium text-right border-r border-gray-600 relative"
                >
                  <div className="text-xs text-gray-500 mb-1">PILLARS</div>
                  <div 
                    className="cursor-pointer hover:bg-gray-700 transition-colors"
                    onClick={() => handleSort('score')}
                  >
                    A+ Score {sortColumn === 'score' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                  </div>
                  <ResizeHandle columnKey="score" onResizeStart={handleResizeStart('score')} isResizing={resizingColumn === 'score'} />
                </th>
                <th
                  style={{ width: colWidths.symbol, minWidth: MIN_COL_WIDTH }}
                  className="py-1.5 pl-2 pr-2 font-medium relative"
                >
                  <div
                    className="cursor-pointer hover:bg-gray-700 transition-colors"
                    onClick={() => handleSort('symbol')}
                  >
                    Symbol {sortColumn === 'symbol' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                  </div>
                  <ResizeHandle columnKey="symbol" onResizeStart={handleResizeStart('symbol')} isResizing={resizingColumn === 'symbol'} />
                </th>
                <th
                  style={{ width: colWidths.price, minWidth: MIN_COL_WIDTH }}
                  className="py-1.5 pr-2 font-medium text-right relative"
                >
                  <div>Price</div>
                  <ResizeHandle columnKey="price" onResizeStart={handleResizeStart('price')} isResizing={resizingColumn === 'price'} />
                </th>
                <th
                  style={{ width: colWidths.rvol, minWidth: MIN_COL_WIDTH }}
                  className="py-1.5 pr-2 font-medium text-right relative"
                >
                  <div
                    className="cursor-pointer hover:bg-gray-700 transition-colors"
                    onClick={() => handleSort('rvol')}
                  >
                    RVOL (D50) {sortColumn === 'rvol' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                  </div>
                  <ResizeHandle columnKey="rvol" onResizeStart={handleResizeStart('rvol')} isResizing={resizingColumn === 'rvol'} />
                </th>
                <th
                  style={{ width: colWidths.market_cap, minWidth: MIN_COL_WIDTH }}
                  className="py-1.5 pr-2 font-medium text-right relative"
                >
                  <div
                    className="cursor-pointer hover:bg-gray-700 transition-colors"
                    onClick={() => handleSort('market_cap')}
                  >
                    Market Cap ($) {sortColumn === 'market_cap' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                  </div>
                  <ResizeHandle columnKey="market_cap" onResizeStart={handleResizeStart('market_cap')} isResizing={resizingColumn === 'market_cap'} />
                </th>
                <th
                  style={{ width: colWidths.supply_ratio, minWidth: MIN_COL_WIDTH }}
                  className="py-1.5 pr-2 font-medium text-right relative"
                >
                  <div
                    className="cursor-pointer hover:bg-gray-700 transition-colors"
                    onClick={() => handleSort('supply_ratio')}
                  >
                    Supply % {sortColumn === 'supply_ratio' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                  </div>
                  <ResizeHandle columnKey="supply_ratio" onResizeStart={handleResizeStart('supply_ratio')} isResizing={resizingColumn === 'supply_ratio'} />
                </th>
                <th
                  style={{ width: colWidths.spread_bps, minWidth: MIN_COL_WIDTH }}
                  className="py-1.5 pr-2 font-medium text-right relative"
                >
                  <div
                    className="cursor-pointer hover:bg-gray-700 transition-colors"
                    onClick={() => handleSort('spread_bps')}
                  >
                    Spread (bps) {sortColumn === 'spread_bps' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                  </div>
                  <ResizeHandle columnKey="spread_bps" onResizeStart={handleResizeStart('spread_bps')} isResizing={resizingColumn === 'spread_bps'} />
                </th>
                <th
                  style={{ width: colWidths.change_24h_pct, minWidth: MIN_COL_WIDTH }}
                  className="py-1.5 pr-2 font-medium text-right relative"
                >
                  <div
                    className="cursor-pointer hover:bg-gray-700 transition-colors"
                    onClick={() => handleSort('change_24h_pct')}
                  >
                    24h % Change {sortColumn === 'change_24h_pct' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                  </div>
                  <ResizeHandle columnKey="change_24h_pct" onResizeStart={handleResizeStart('change_24h_pct')} isResizing={resizingColumn === 'change_24h_pct'} />
                </th>
                
                {/* Strategies Group */}
                <th
                  style={{ width: colWidths.vwap_dist_pct, minWidth: MIN_COL_WIDTH }}
                  className="py-1.5 pr-2 font-medium text-right border-l border-r border-gray-600 relative"
                >
                  <div className="text-xs text-gray-500 mb-1">STRATEGIES</div>
                  <div 
                    className="cursor-pointer hover:bg-gray-700 transition-colors whitespace-nowrap"
                    onClick={() => handleSort('vwap_dist_pct')}
                  >
                    VWAP Dist % {sortColumn === 'vwap_dist_pct' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                  </div>
                  <ResizeHandle columnKey="vwap_dist_pct" onResizeStart={handleResizeStart('vwap_dist_pct')} isResizing={resizingColumn === 'vwap_dist_pct'} />
                </th>
                <th
                  style={{ width: colWidths.hod_dist_pct, minWidth: MIN_COL_WIDTH }}
                  className="py-1.5 pr-2 font-medium text-right whitespace-nowrap relative"
                >
                  <div
                    className="cursor-pointer hover:bg-gray-700 transition-colors"
                    onClick={() => handleSort('hod_dist_pct')}
                  >
                    HOD Distance % {sortColumn === 'hod_dist_pct' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                  </div>
                  <ResizeHandle columnKey="hod_dist_pct" onResizeStart={handleResizeStart('hod_dist_pct')} isResizing={resizingColumn === 'hod_dist_pct'} />
                </th>
                <th
                  style={{ width: colWidths.htf_trend, minWidth: MIN_COL_WIDTH }}
                  className="py-1.5 pr-2 font-medium text-center whitespace-nowrap relative"
                >
                  <div>HTF Trend</div>
                  <ResizeHandle columnKey="htf_trend" onResizeStart={handleResizeStart('htf_trend')} isResizing={resizingColumn === 'htf_trend'} />
                </th>
                <th
                  style={{ width: colWidths.signal_lead, minWidth: MIN_COL_WIDTH }}
                  className="py-1.5 pr-2 font-medium whitespace-nowrap relative"
                >
                  <div
                    className="cursor-pointer hover:bg-gray-700 transition-colors"
                    onClick={() => handleSort('signal_lead')}
                  >
                    Signal Lead {sortColumn === 'signal_lead' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                  </div>
                  <ResizeHandle columnKey="signal_lead" onResizeStart={handleResizeStart('signal_lead')} isResizing={resizingColumn === 'signal_lead'} />
                </th>
                <th
                  style={{ width: colWidths.signal_strength, minWidth: MIN_COL_WIDTH }}
                  className="py-1.5 pr-2 font-medium whitespace-nowrap relative"
                >
                  <div
                    className="cursor-pointer hover:bg-gray-700 transition-colors"
                    onClick={() => handleSort('signal_strength')}
                  >
                    Signal Strength {sortColumn === 'signal_strength' && (sortDirection === 'asc' ? ' ↑' : ' ↓')}
                  </div>
                  <ResizeHandle columnKey="signal_strength" onResizeStart={handleResizeStart('signal_strength')} isResizing={resizingColumn === 'signal_strength'} />
                </th>
                
                {/* Active Trade Group */}
                <th
                  style={{ width: colWidths.status, minWidth: MIN_COL_WIDTH }}
                  className="py-1.5 pr-2 font-medium border-l border-r border-gray-600 relative"
                >
                  <div className="text-xs text-gray-500 mb-1">ACTIVE TRADE</div>
                  <div>Status</div>
                  <ResizeHandle columnKey="status" onResizeStart={handleResizeStart('status')} isResizing={resizingColumn === 'status'} />
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
                  colWidths={colWidths}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

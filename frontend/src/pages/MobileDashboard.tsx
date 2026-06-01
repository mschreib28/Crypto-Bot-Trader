import { useState } from 'react';
import { useTrading } from '../hooks/useTrading';
import { useShadowLive } from '../hooks/useShadowLive';
import { useAccount } from '../hooks/useAccount';
import { usePositions } from '../hooks/usePositions';
import { useScreener, ScreenerSignal } from '../hooks/useScreener';
import { useActivity, ActivityType } from '../hooks/useActivity';
import { useHealth } from '../hooks/useHealth';
import { useStrategies } from '../hooks/useStrategies';
import { getStrategyDisplayName } from '../utils/strategyLabels';
import { PanicButton } from '../components/PanicButton';

// ─── Helpers ────────────────────────────────────────────────────────────────

const fmt$ = (n: number) => `${n >= 0 ? '+' : '-'}$${Math.abs(n).toFixed(2)}`;
const fmtTime = (iso: string) =>
  new Date(iso).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' });

function getTypeColor(type: ActivityType): string {
  switch (type) {
    case 'signal':
    case 'SIGNAL_CONFIRMED': return 'text-blue-400';
    case 'EXECUTION_ALLOWED': return 'text-lime-400';
    case 'EXIT_FORCED': return 'text-red-400';
    case 'order':
    case 'TRADE_PLACED':
    case 'STOP_PLACED': return 'text-green-400';
    case 'ORDER_INTENT': return 'text-yellow-400';
    case 'STOP_INTENT': return 'text-orange-400';
    case 'TAKE_PROFIT_INTENT': return 'text-purple-400';
    case 'SETUP_DETECTED': return 'text-cyan-400';
    case 'error': return 'text-red-400';
    default: return 'text-gray-400';
  }
}

function statusColor(s?: string) {
  if (!s) return 'text-gray-500';
  if (['up', 'connected', 'healthy', 'running'].includes(s.toLowerCase())) return 'text-green-400';
  if (s.toLowerCase() === 'degraded') return 'text-yellow-400';
  return 'text-red-400';
}

function SectionHeader({
  title,
  open,
  onToggle,
}: {
  title: string;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      onClick={onToggle}
      className="w-full flex items-center justify-between px-4 py-2 bg-gray-800 border-b border-gray-700 text-left"
    >
      <span className="text-sm font-semibold text-white">{title}</span>
      <span className="text-gray-400 text-xs">{open ? '▲' : '▼'}</span>
    </button>
  );
}

// ─── Control Bar ────────────────────────────────────────────────────────────

function ControlBar() {
  const { trading, loading: tLoading, toggleTrading, refetch } = useTrading();
  const { shadowLive, loading: sLoading, toggleShadowLive } = useShadowLive();

  const isAnyOn = (trading?.enabled ?? false) || (shadowLive?.enabled ?? false);
  const isLive = (trading?.enabled ?? false) && !(shadowLive?.enabled ?? false);
  const isShadow = (shadowLive?.enabled ?? false) && !(trading?.enabled ?? false);

  const handleOnOff = async () => {
    if (trading?.enabled) await toggleTrading();
    if (shadowLive?.enabled) await toggleShadowLive();
    setTimeout(() => refetch(), 500);
  };

  const handleLiveShadow = async () => {
    if (!isAnyOn) {
      await toggleShadowLive();
    } else if (isShadow) {
      await toggleShadowLive();
      await toggleTrading();
    } else if (isLive) {
      await toggleTrading();
      await toggleShadowLive();
    }
    setTimeout(() => refetch(), 500);
  };

  const loading = tLoading || sLoading;

  // Mode banner colour
  let bannerClass = 'bg-amber-900/80 border-amber-600 text-amber-200';
  let bannerText = '⚠️ TRADING OFF — signals won\'t open positions';
  if (isShadow) {
    bannerClass = 'bg-blue-900/80 border-blue-600 text-blue-200';
    bannerText = '🟦 SHADOW MODE ACTIVE — no real orders';
  } else if (isLive) {
    bannerClass = 'bg-red-900/80 border-red-600 text-red-200';
    bannerText = '🔴 LIVE TRADING — real orders executing';
  }

  return (
    <div className="sticky top-0 z-50 bg-gray-900 border-b border-gray-700 shadow-lg">
      {/* Buttons row */}
      <div className="flex items-center gap-2 px-3 py-2">
        <span className="text-white font-bold text-sm mr-1">Omni-Bot</span>

        {/* ON / OFF */}
        <button
          onClick={handleOnOff}
          disabled={loading}
          className={`flex-1 py-2 rounded text-xs font-semibold transition-colors ${
            isAnyOn
              ? 'bg-green-600 text-white'
              : 'bg-gray-600 text-gray-300'
          } disabled:opacity-50`}
        >
          {loading ? '…' : isAnyOn ? 'ON' : 'OFF'}
        </button>

        {/* LIVE / SHADOW */}
        <button
          onClick={handleLiveShadow}
          disabled={loading}
          className={`flex-1 py-2 rounded text-xs font-semibold transition-colors ${
            isLive
              ? 'bg-red-600 text-white'
              : 'bg-blue-600 text-white'
          } disabled:opacity-50`}
        >
          {loading ? '…' : isLive ? 'LIVE' : 'SHADOW'}
        </button>

        {/* PANIC */}
        <div className="flex-1">
          <PanicButton onSuccess={refetch} />
        </div>
      </div>

      {/* Mode banner */}
      <div className={`w-full border-t ${bannerClass} px-3 py-1 text-[10px] font-medium`}>
        {bannerText}
      </div>
    </div>
  );
}

// ─── Scanner ────────────────────────────────────────────────────────────────

function gradeColor(grade?: string) {
  if (!grade) return 'text-gray-500';
  if (grade === 'A+') return 'text-emerald-400 font-bold';
  if (grade === 'A') return 'text-green-400';
  if (grade === 'B') return 'text-yellow-400';
  return 'text-gray-400';
}

function tradeStatusBadge(status?: string, pnlPct?: number) {
  if (!status || status === 'SCANNING') return null;
  let cls = 'bg-gray-700 text-gray-300';
  if (status === 'LIVE') cls = 'bg-green-800 text-green-200';
  else if (status === 'EXITING') cls = 'bg-orange-800 text-orange-200';
  else if (status === 'COOLDOWN') cls = 'bg-gray-700 text-gray-400';
  const pnl = pnlPct !== undefined && isFinite(pnlPct) ? ` ${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(1)}%` : '';
  return (
    <span className={`text-[9px] px-1 py-0.5 rounded ${cls}`}>
      {status}{pnl}
    </span>
  );
}

function ScannerSection() {
  const { signals, loading, lastScan } = useScreener({});

  // Pin LIVE positions to top
  const sorted = [...signals].sort((a, b) => {
    const aLive = a.indicators.status === 'LIVE' ? 1 : 0;
    const bLive = b.indicators.status === 'LIVE' ? 1 : 0;
    if (bLive !== aLive) return bLive - aLive;
    return (b.indicators.score ?? 0) - (a.indicators.score ?? 0);
  });

  return (
    <div className="overflow-x-auto">
      {loading && <p className="text-gray-400 text-xs px-4 py-3">Loading scanner…</p>}
      {!loading && sorted.length === 0 && (
        <p className="text-gray-500 text-xs px-4 py-3">No signals yet</p>
      )}
      {!loading && sorted.length > 0 && (
        <>
          {lastScan && (
            <p className="text-[9px] text-gray-600 px-4 pt-1">
              Last scan: {fmtTime(lastScan)}
            </p>
          )}
          <table className="w-full text-[11px]">
            <thead>
              <tr className="border-b border-gray-700 text-gray-500">
                <th className="text-left px-3 py-1.5 font-medium">Symbol</th>
                <th className="text-center px-2 py-1.5 font-medium">Grade</th>
                <th className="text-left px-2 py-1.5 font-medium">Lead</th>
                <th className="text-right px-2 py-1.5 font-medium">Str</th>
                <th className="text-center px-2 py-1.5 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((sig: ScreenerSignal) => {
                const { indicators } = sig;
                const grade = indicators.grade;
                const lead = indicators.signal_lead;
                const stratName = lead?.strategy_name ?? '—';
                const confidence = lead?.confidence ?? sig.signal_strength;
                const status = indicators.status;
                const pnlPct = indicators.current_pnl_pct as number | undefined;
                const base = sig.symbol.replace('/USD', '');
                return (
                  <tr key={sig.symbol} className="border-b border-gray-800 hover:bg-gray-800/40">
                    <td className="px-3 py-2 font-medium text-white">{base}</td>
                    <td className={`px-2 py-2 text-center ${gradeColor(grade)}`}>
                      {grade ?? '—'}
                    </td>
                    <td className="px-2 py-2 text-gray-300 truncate max-w-[80px]">
                      {stratName === '—' ? '—' : getStrategyDisplayName(stratName)}
                    </td>
                    <td className="px-2 py-2 text-right text-gray-300">
                      {confidence > 0 ? `${(confidence * 100).toFixed(0)}%` : '—'}
                    </td>
                    <td className="px-2 py-2 text-center">
                      {tradeStatusBadge(status, pnlPct) ?? (
                        <span className="text-gray-600 text-[9px]">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

// ─── Account + Positions ─────────────────────────────────────────────────────

function AccountSection() {
  const { account, loading } = useAccount();
  const { positions, closePosition } = usePositions();
  const { shadowLive } = useShadowLive();
  const [showPositions, setShowPositions] = useState(true);

  if (loading) return <p className="text-gray-400 text-xs px-4 py-3">Loading…</p>;
  if (!account) return null;

  const totalPnl = account.total_pnl ?? (account.current_equity - account.initial_equity);
  const pnlPct = account.initial_equity > 0
    ? (totalPnl / account.initial_equity) * 100
    : 0;
  const pnlColor = totalPnl >= 0 ? 'text-green-400' : 'text-red-400';
  const dailyColor = account.daily_pnl >= 0 ? 'text-green-400' : 'text-red-400';
  const isShadow = shadowLive?.enabled === true;

  return (
    <div className="px-4 py-3 space-y-3">
      {/* Equity + P&L */}
      <div className="flex justify-between items-baseline">
        <div>
          <p className="text-[10px] text-gray-500">{isShadow ? 'Shadow Equity' : 'Equity'}</p>
          <p className="text-xl font-bold text-white">${account.current_equity.toFixed(2)}</p>
        </div>
        <div className="text-right">
          <p className="text-[10px] text-gray-500">Overall P&L</p>
          <p className={`text-lg font-bold ${pnlColor}`}>{fmt$(totalPnl)}</p>
          <p className={`text-[10px] ${pnlColor}`}>
            {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%
          </p>
        </div>
      </div>

      {/* Daily P&L bar */}
      <div className="flex justify-between text-xs">
        <span className="text-gray-500">Today</span>
        <span className={`font-mono ${dailyColor}`}>
          {account.daily_pnl >= 0 ? '+' : ''}${account.daily_pnl.toFixed(2)} / limit -${account.daily_loss_limit.toFixed(2)}
        </span>
      </div>

      {/* Positions */}
      <div>
        <button
          onClick={() => setShowPositions((v) => !v)}
          className="text-xs text-gray-400 hover:text-white flex items-center gap-1"
        >
          <span>Positions ({positions.filter((p) => p.quantity > 0).length})</span>
          <span>{showPositions ? '▲' : '▼'}</span>
        </button>
        {showPositions && (
          <div className="mt-2 space-y-2">
            {positions.filter((p) => p.quantity > 0).length === 0 && (
              <p className="text-gray-600 text-xs">No open positions</p>
            )}
            {positions
              .filter((p) => p.quantity > 0)
              .map((pos) => {
                const upnl = pos.unrealized_pnl;
                const upnlColor = !isFinite(upnl) ? 'text-gray-500'
                  : upnl >= 0 ? 'text-green-400' : 'text-red-400';
                return (
                  <div key={pos.symbol} className="flex items-center justify-between bg-gray-800 rounded px-3 py-2">
                    <div>
                      <p className="text-xs font-medium text-white">{pos.symbol.replace('/USD', '')}</p>
                      <p className="text-[10px] text-gray-500">
                        {pos.quantity.toFixed(4)} @ ${pos.entry_price.toFixed(4)}
                      </p>
                    </div>
                    <div className="text-right">
                      <p className={`text-xs font-mono ${upnlColor}`}>
                        {isFinite(upnl) ? fmt$(upnl) : '—'}
                      </p>
                      <button
                        onClick={() => {
                          if (window.confirm(`Close ${pos.symbol}?`)) closePosition(pos.symbol);
                        }}
                        className="text-[9px] text-red-400 hover:text-red-300 mt-0.5"
                      >
                        Close
                      </button>
                    </div>
                  </div>
                );
              })}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Activity Log ────────────────────────────────────────────────────────────

function ActivitySection() {
  const { activities, loading, clearActivity } = useActivity();
  const recent = [...activities].reverse().slice(0, 15);

  return (
    <div className="px-4 py-3">
      <div className="flex justify-between items-center mb-2">
        <span className="text-[10px] text-gray-500">{activities.length} entries</span>
        {activities.length > 0 && (
          <button
            onClick={() => { if (window.confirm('Clear activity?')) clearActivity(); }}
            className="text-[10px] text-gray-500 hover:text-red-400"
          >
            Clear
          </button>
        )}
      </div>
      {loading && <p className="text-gray-400 text-xs">Loading…</p>}
      {!loading && recent.length === 0 && (
        <p className="text-gray-600 text-xs">No activity yet</p>
      )}
      <ul className="space-y-0.5 text-[11px]">
        {recent.map((a, i) => (
          <li key={`${a.timestamp}-${i}`} className={`${getTypeColor(a.type)} py-0.5`}>
            <span className="text-gray-600 font-mono">{fmtTime(a.timestamp)}</span>{' '}
            <span className="break-words">{a.message}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ─── Health ──────────────────────────────────────────────────────────────────

function HealthSection() {
  const { health, loading } = useHealth();

  if (loading) return <p className="text-gray-400 text-xs px-4 py-3">Loading…</p>;
  if (!health) return null;

  const { components } = health;
  const dots = [
    { label: 'Redis', status: components.redis.status },
    { label: 'DB', status: components.database.status },
    { label: 'Ingestor', status: components.ingestor.status },
    { label: 'Feed', status: components.ingestor.symbols_count > 0 ? 'running' : 'degraded' },
  ];
  const upMins = Math.floor(health.uptime_seconds / 60);

  const overallColor = health.status === 'healthy' ? 'text-green-400'
    : health.status === 'degraded' ? 'text-yellow-400' : 'text-red-400';

  return (
    <div className="px-4 py-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex gap-3">
          {dots.map(({ label, status }) => (
            <div key={label} className="flex items-center gap-1">
              <span className={`text-xs ${statusColor(status)}`}>●</span>
              <span className="text-[10px] text-gray-400">{label}</span>
            </div>
          ))}
        </div>
        <div className="text-right">
          <span className={`text-xs font-medium ${overallColor}`}>
            {health.status.charAt(0).toUpperCase() + health.status.slice(1)}
          </span>
          <p className="text-[9px] text-gray-600">{upMins}m uptime</p>
        </div>
      </div>
    </div>
  );
}

// ─── Strategies ──────────────────────────────────────────────────────────────

function StrategiesSection() {
  const { strategies, loading, toggleStrategy } = useStrategies();

  if (loading) return <p className="text-gray-400 text-xs px-4 py-3">Loading…</p>;

  return (
    <div className="px-4 py-3 space-y-2">
      {strategies.map((s) => {
        const displayName = getStrategyDisplayName(s.name);
        return (
          <div key={s.strategy_id} className="flex items-center justify-between">
            <div>
              <p className="text-xs text-white">{displayName}</p>
              <p className="text-[9px] text-gray-500">{s.interval}</p>
            </div>
            <button
              onClick={() => toggleStrategy(s.strategy_id, !s.enabled)}
              className={`px-3 py-1 rounded text-[11px] font-semibold transition-colors ${
                s.enabled
                  ? 'bg-green-800/70 text-green-300 hover:bg-green-800'
                  : 'bg-gray-700 text-gray-400 hover:bg-gray-600'
              }`}
            >
              {s.enabled ? 'ON' : 'OFF'}
            </button>
          </div>
        );
      })}
      {strategies.length === 0 && (
        <p className="text-gray-600 text-xs">No strategies loaded</p>
      )}
    </div>
  );
}

// ─── Main Page ───────────────────────────────────────────────────────────────

export function MobileDashboard() {
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({
    scanner: true,
    account: true,
    activity: true,
    health: true,
    strategies: true,
  });

  const toggle = (key: string) =>
    setOpenSections((prev) => ({ ...prev, [key]: !prev[key] }));

  return (
    <div className="bg-gray-900 text-white" style={{ WebkitOverflowScrolling: 'touch' }}>
      <ControlBar />

      {/* Scanner */}
      <div className="border-b border-gray-700">
        <SectionHeader title="Scanner" open={openSections.scanner} onToggle={() => toggle('scanner')} />
        {openSections.scanner && <ScannerSection />}
      </div>

      {/* Account & Positions */}
      <div className="border-b border-gray-700">
        <SectionHeader title="Account & Positions" open={openSections.account} onToggle={() => toggle('account')} />
        {openSections.account && <AccountSection />}
      </div>

      {/* Activity Log */}
      <div className="border-b border-gray-700">
        <SectionHeader title="Activity Log" open={openSections.activity} onToggle={() => toggle('activity')} />
        {openSections.activity && <ActivitySection />}
      </div>

      {/* System Health */}
      <div className="border-b border-gray-700">
        <SectionHeader title="System Health" open={openSections.health} onToggle={() => toggle('health')} />
        {openSections.health && <HealthSection />}
      </div>

      {/* Strategies */}
      <div className="border-b border-gray-700">
        <SectionHeader title="Strategies" open={openSections.strategies} onToggle={() => toggle('strategies')} />
        {openSections.strategies && <StrategiesSection />}
      </div>

      {/* Footer */}
      <div className="p-4 text-center">
        <a href="/" className="text-[11px] text-gray-600 hover:text-gray-400">
          ← Desktop View
        </a>
      </div>
    </div>
  );
}

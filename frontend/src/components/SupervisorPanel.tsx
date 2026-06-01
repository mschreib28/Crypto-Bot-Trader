import { useEffect, useState } from 'react';
import {
  useSupervisor,
  SupervisorStatus,
  StrategyVerdict,
  LiveStrategyVerdict,
} from '../hooks/useSupervisor';
import { useStrategyMode } from '../hooks/useStrategyMode';
import { getStrategyDisplayName, RETIRED_STRATEGY_SLUGS } from '../utils/strategyLabels';

function StatusBadge({ status }: { status: SupervisorStatus }) {
  const styles: Record<SupervisorStatus, string> = {
    ACTIVE: 'bg-green-500/20 text-green-400',
    REDUCED: 'bg-yellow-500/20 text-yellow-400',
    SUSPENDED: 'bg-red-500/20 text-red-400',
  };
  return (
    <span className={`px-1.5 py-0.5 text-[10px] font-medium rounded ${styles[status]}`}>
      {status}
    </span>
  );
}

function MetricLine({ verdict }: { verdict: StrategyVerdict }) {
  if (verdict.trades === null) {
    return <span className="text-gray-600 text-[10px]">no data</span>;
  }
  const wr = verdict.win_rate !== null ? `${verdict.win_rate.toFixed(1)}%` : '—';
  const rr = verdict.rr_ratio !== null ? `${verdict.rr_ratio.toFixed(2)}:1` : '—';
  return (
    <span className="text-gray-500 text-[10px]">
      Backtest: WR {wr} · R:R {rr} · {verdict.trades} trades
    </span>
  );
}

function effectiveSummary(m: { effective_mode: string; supervisor_status: string } | null): string {
  if (!m) return 'Loading…';
  if (m.effective_mode === 'SIM') {
    return 'Paper execution (SIM path). No Kraken live orders for new signals.';
  }
  const sz =
    m.supervisor_status === 'REDUCED' ? '50% size (supervisor REDUCED)' : 'full size (supervisor ACTIVE)';
  return `Live Kraken for new signals at ${sz}.`;
}

function live24Line(live: LiveStrategyVerdict | undefined): string {
  if (!live || live.trades == null || live.trades < 5) {
    return 'Live (24h): —';
  }
  const wr = live.win_rate != null ? `${live.win_rate.toFixed(0)}%` : '—';
  const rr = live.rr_ratio != null ? `${live.rr_ratio.toFixed(2)}:1` : '—';
  return `Live (24h): WR ${wr} · R:R ${rr} · ${live.trades} trades`;
}

function StrategyCard({
  verdict,
  liveRow,
}: {
  verdict: StrategyVerdict;
  liveRow?: LiveStrategyVerdict;
}) {
  const slug = verdict.strategy;
  const { modeData, loading, error, refetch, setManualMode } = useStrategyMode(slug);
  const [toggleErr, setToggleErr] = useState<string | null>(null);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const manual = (modeData?.manual_mode ?? 'LIVE') as 'LIVE' | 'SIM';
  const live24 = live24Line(liveRow);

  const onSetMode = async (mode: 'LIVE' | 'SIM') => {
    setToggleErr(null);
    try {
      await setManualMode(mode);
    } catch (e) {
      setToggleErr(e instanceof Error ? e.message : 'toggle failed');
    }
  };

  return (
    <div className="rounded border border-gray-600/80 bg-gray-900/40 p-2 space-y-1">
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-col gap-0.5 min-w-0">
          <span className="text-xs text-gray-200 font-medium">
            {getStrategyDisplayName(slug)}
          </span>
          <MetricLine verdict={verdict} />
        </div>
        <StatusBadge status={verdict.status} />
      </div>
      <div className="text-[10px] text-gray-500">{live24}</div>
      <div className="flex items-center justify-between gap-2 pt-0.5">
        <span className="text-[10px] text-gray-500">Manual</span>
        <div className="flex rounded overflow-hidden border border-gray-600">
          <button
            type="button"
            disabled={loading}
            onClick={() => void onSetMode('SIM')}
            className={`px-2 py-0.5 text-[10px] font-medium ${
              manual === 'SIM' ? 'bg-sky-600 text-white' : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
            }`}
          >
            SIM
          </button>
          <button
            type="button"
            disabled={loading}
            onClick={() => void onSetMode('LIVE')}
            className={`px-2 py-0.5 text-[10px] font-medium ${
              manual === 'LIVE' ? 'bg-emerald-700 text-white' : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
            }`}
          >
            LIVE
          </button>
        </div>
      </div>
      {error && <p className="text-[10px] text-red-400">{error}</p>}
      {toggleErr && <p className="text-[10px] text-red-400">{toggleErr}</p>}
      <div className="text-[10px] text-gray-400 border-t border-gray-700/80 pt-1">
        Effective: {effectiveSummary(modeData)}
      </div>
    </div>
  );
}

function isStale(lastRun: string | null): boolean {
  if (!lastRun) return false;
  const age = Date.now() - new Date(lastRun).getTime();
  return age > 12 * 60 * 60 * 1000;
}

export function SupervisorPanel() {
  const { data, liveData, loading, error } = useSupervisor();

  if (loading) {
    return (
      <div className="rounded-lg border border-gray-700 bg-gray-800 p-3">
        <h2 className="text-sm font-semibold text-white mb-2">Strategy Supervisor</h2>
        <p className="text-gray-400 text-xs">Loading...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-gray-700 bg-gray-800 p-3">
        <h2 className="text-sm font-semibold text-white mb-2">Strategy Supervisor</h2>
        <p className="text-red-400 text-xs">{error}</p>
      </div>
    );
  }

  if (!data) return null;

  const visibleStrategies = data.strategies.filter(
    (v) => !RETIRED_STRATEGY_SLUGS.has(v.strategy),
  );
  const stale = isStale(data.last_run);

  return (
    <div className="rounded-lg border border-gray-700 bg-gray-800 p-3">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-sm font-semibold text-white">Strategy Supervisor</h2>
        {stale && (
          <span className="px-1.5 py-0.5 text-[10px] font-medium rounded bg-orange-500/20 text-orange-400">
            Stale
          </span>
        )}
      </div>

      <div className="flex flex-col gap-2 max-h-72 overflow-y-auto">
        {visibleStrategies.map((verdict) => {
          const liveRow = liveData?.strategies.find((s) => s.strategy === verdict.strategy);
          return <StrategyCard key={verdict.strategy} verdict={verdict} liveRow={liveRow} />;
        })}
      </div>

      {data.last_run && (
        <div className="mt-2 pt-2 border-t border-gray-700 text-[10px] text-gray-500">
          Last run: {new Date(data.last_run).toLocaleString()}
        </div>
      )}
      {!data.last_run && (
        <div className="mt-2 pt-2 border-t border-gray-700 text-[10px] text-gray-500">
          Awaiting first evaluation cycle
        </div>
      )}
    </div>
  );
}

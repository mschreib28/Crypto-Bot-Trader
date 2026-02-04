import { useState } from 'react';
import { Strategy } from '../types/strategy';
import { useScreener } from '../hooks/useScreener';
import { useMetrics } from '../hooks/useMetrics';

interface StrategyCardProps {
  strategy: Strategy;
  onToggle?: (strategyId: string, enabled: boolean) => Promise<boolean>;
}

const formatPnL = (pnl: number) => {
  const sign = pnl >= 0 ? '+' : '';
  return `${sign}$${Math.abs(pnl).toFixed(2)}`;
};

export function StrategyCard({ strategy, onToggle }: StrategyCardProps) {
  const { name, interval, max_risk_pct, enabled, strategy_id } = strategy;
  const { totalScanned, loading } = useScreener({ strategyId: strategy_id });
  const { metrics } = useMetrics();
  const [toggling, setToggling] = useState(false);

  const strategyMetrics = metrics?.strategies[strategy_id];
  const accuracy = strategyMetrics?.accuracy_pct ?? 0;
  const pnl = strategyMetrics?.total_pnl ?? 0;

  const handleToggle = async () => {
    if (!onToggle || toggling) return;
    setToggling(true);
    try {
      await onToggle(strategy_id, !enabled);
    } finally {
      setToggling(false);
    }
  };

  // Format strategy name for better display
  const displayName = name
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (l) => l.toUpperCase());

  return (
    <div className={`rounded-lg border ${
      enabled 
        ? 'border-green-800/50 bg-gray-800/90' 
        : 'border-gray-700 bg-gray-800/60'
    } p-2.5 hover:border-gray-600 hover:bg-gray-800 transition-all`}>
      {/* Header: Name and Toggle */}
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-white truncate">
            {displayName}
          </h3>
        </div>
        <button
          type="button"
          onClick={handleToggle}
          disabled={toggling || !onToggle}
          className={`shrink-0 rounded px-2 py-1 text-[10px] font-semibold transition-all ${
            enabled
              ? 'bg-green-900/70 text-green-300 hover:bg-green-900/90'
              : 'bg-gray-700/70 text-gray-400 hover:bg-gray-700/90'
          } ${toggling ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'} ${!onToggle ? 'cursor-default' : ''}`}
          aria-label={`Toggle ${displayName}: currently ${enabled ? 'enabled' : 'disabled'}`}
          aria-pressed={enabled}
        >
          {toggling ? '...' : enabled ? 'ON' : 'OFF'}
        </button>
      </div>

      {/* Pairs count */}
      <div className="text-[10px] text-gray-500 mb-2">
        {loading ? 'Scanning...' : `• ${totalScanned} pairs`}
      </div>

      {/* Metrics Grid - Compact */}
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        <div className="flex justify-between">
          <span className="text-gray-500">Int</span>
          <span className="font-mono text-gray-300">{interval}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">Risk</span>
          <span className="font-mono text-gray-300">{max_risk_pct}%</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">Acc</span>
          <span className={`font-mono ${
            accuracy >= 60 ? 'text-green-400' : accuracy >= 40 ? 'text-yellow-400' : 'text-gray-400'
          }`}>
            {accuracy.toFixed(0)}%
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">P&L</span>
          <span className={`font-mono ${
            pnl >= 0 ? 'text-green-400' : 'text-red-400'
          }`}>
            {formatPnL(pnl)}
          </span>
        </div>
      </div>
    </div>
  );
}

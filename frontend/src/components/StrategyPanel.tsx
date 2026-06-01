import { StrategyCard } from './StrategyCard';
import { Strategy } from '../types/strategy';

import type { MetricsResponse } from '../hooks/useMetrics';

interface StrategyPanelProps {
  strategies: Strategy[];
  loading?: boolean;
  error?: string | null;
  onToggle?: (strategyId: string, enabled: boolean) => Promise<boolean>;
  totalScanned?: number;
  screenerLoading?: boolean;
  metrics?: MetricsResponse | null;
}

export function StrategyPanel({
  strategies,
  loading = false,
  error = null,
  onToggle,
  totalScanned = 0,
  screenerLoading = false,
  metrics = null,
}: StrategyPanelProps) {
  return (
    <section aria-label="Strategy toggles" className="flex flex-col min-h-0">
      {loading && (
        <div className="text-gray-400 text-xs py-2">Loading...</div>
      )}

      {error && (
        <div className="rounded border border-red-800 bg-red-900/20 p-2 text-red-400 text-xs mb-2">
          {error}
        </div>
      )}

      {!loading && !error && strategies.length === 0 && (
        <div className="rounded border border-gray-700 bg-gray-800 p-2 text-center text-gray-400 text-xs">
          No strategies
        </div>
      )}

      {!loading && !error && strategies.length > 0 && (
        <div className="flex flex-col gap-2 overflow-y-auto min-h-0 max-h-[calc(100vh-600px)] pr-1">
          {strategies.map((strategy) => (
            <StrategyCard
              key={strategy.strategy_id}
              strategy={strategy}
              onToggle={onToggle}
              totalScanned={totalScanned}
              screenerLoading={screenerLoading}
              strategyMetrics={metrics?.strategies[strategy.strategy_id]}
            />
          ))}
        </div>
      )}
    </section>
  );
}

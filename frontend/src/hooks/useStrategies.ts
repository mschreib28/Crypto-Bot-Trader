import { useState, useEffect, useCallback } from 'react';
import { Strategy, StrategyLifecycleStatus } from '../types/strategy';

function parseStrategyStatus(raw: unknown): StrategyLifecycleStatus {
  if (raw === 'active' || raw === 'paused' || raw === 'stopped') return raw;
  return 'stopped';
}

interface UseStrategiesReturn {
  strategies: Strategy[];
  loading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
  toggleStrategy: (strategyId: string, enabled: boolean) => Promise<boolean>;
}

const REFRESH_INTERVAL_MS = 60000;

export function useStrategies(): UseStrategiesReturn {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStrategies = useCallback(async () => {
    try {
      const response = await fetch('/api/v1/strategies');
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      const data = await response.json();
      
      // API returns { strategies: [...] } - extract the array
      const strategiesArray = Array.isArray(data) ? data : (data.strategies || []);
      
      // Map API fields to frontend expected format
      const mapped: Strategy[] = strategiesArray.map((s: Record<string, unknown>) => {
        const status = parseStrategyStatus(s.status);
        const enabled =
          typeof s.enabled === 'boolean' ? s.enabled : status === 'active';
        return {
          strategy_id: (s.strategy_id || s.id || '') as string,
          name: (s.name || '') as string,
          symbol: (s.symbol || 'ETH/USD') as string,
          interval: (s.interval || '5m') as string,
          max_risk_pct: (s.max_risk_pct ?? 2.0) as number,
          status,
          enabled,
          parameters: (s.parameters || {}) as Record<string, unknown>,
        };
      });
      
      setStrategies(mapped);
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch strategies';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  const toggleStrategy = useCallback(async (strategyId: string, enabled: boolean): Promise<boolean> => {
    const endpoint = enabled
      ? `/api/v1/strategies/${strategyId}/enable`
      : `/api/v1/strategies/${strategyId}/disable`;

    try {
      const response = await fetch(endpoint, { method: 'POST' });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      // Optimistically update local state
      setStrategies((prev) =>
        prev.map((s) =>
          s.strategy_id === strategyId ? { ...s, enabled } : s
        )
      );
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to toggle strategy';
      setError(message);
      return false;
    }
  }, []);

  useEffect(() => {
    fetchStrategies();

    const interval = setInterval(fetchStrategies, REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchStrategies]);

  return { strategies, loading, error, refetch: fetchStrategies, toggleStrategy };
}

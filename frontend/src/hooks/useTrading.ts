import { useState, useEffect, useCallback } from 'react';

export interface TradingState {
  enabled: boolean;
  updatedAt: string;
}

interface UseTradinReturn {
  trading: TradingState | null;
  loading: boolean;
  error: string | null;
  toggleTrading: () => Promise<boolean>;
  refetch: () => Promise<void>;
}

const POLL_INTERVAL_MS = 10000;

export function useTrading(): UseTradinReturn {
  const [trading, setTrading] = useState<TradingState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const response = await fetch('/api/v1/trading/status');
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      const data = await response.json();
      setTrading({
        enabled: data.enabled,
        updatedAt: data.updated_at || new Date().toISOString(),
      });
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch trading status';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  const toggleTrading = useCallback(async (): Promise<boolean> => {
    if (!trading) return false;

    const newEnabled = !trading.enabled;
    setLoading(true);

    try {
      const response = await fetch('/api/v1/trading/enabled', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: newEnabled }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();
      setTrading({
        enabled: data.enabled ?? newEnabled,
        updatedAt: data.updated_at || new Date().toISOString(),
      });
      setError(null);
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to toggle trading';
      setError(message);
      return false;
    } finally {
      setLoading(false);
    }
  }, [trading]);

  useEffect(() => {
    fetchStatus();

    const interval = setInterval(fetchStatus, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  return { trading, loading, error, toggleTrading, refetch: fetchStatus };
}

import { useState, useEffect, useCallback } from 'react';

export interface SystemStatus {
  halted: boolean;
  portfolio_exposure: number;
  active_strategies: number;
  redis_connected: boolean;
  db_connected: boolean;
  ingestor_healthy: boolean;
  last_updated: string;
}

interface UseSystemStatusReturn {
  status: SystemStatus | null;
  loading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
}

const REFRESH_INTERVAL_MS = 30000;

export function useSystemStatus(): UseSystemStatusReturn {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const response = await fetch('/api/v1/status');
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      const data: SystemStatus = await response.json();
      setStatus(data);
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch status';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();

    const interval = setInterval(fetchStatus, REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  return { status, loading, error, refetch: fetchStatus };
}

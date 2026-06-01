import { useState, useEffect } from 'react';

export type SupervisorStatus = 'ACTIVE' | 'REDUCED' | 'SUSPENDED';

export interface StrategyVerdict {
  strategy: string;
  status: SupervisorStatus;
  win_rate: number | null;
  rr_ratio: number | null;
  trades: number | null;
  wins: number | null;
  losses: number | null;
  size_factor: number;
  reason: string | null;
  last_evaluated: string | null;
  lookback_days: number | null;
  interval: string | null;
}

export interface SupervisorData {
  last_run: string | null;
  strategies: StrategyVerdict[];
}

/** Rolling live window row from GET /supervisor/live-status */
export interface LiveStrategyVerdict {
  strategy: string;
  status: string | null;
  win_rate: number | null;
  rr_ratio: number | null;
  trades: number | null;
  wins: number | null;
  losses: number | null;
  size_factor: number | null;
  reason: string | null;
  last_evaluated: string | null;
  source?: string;
  lookback_hours?: number | null;
}

export interface SupervisorLiveData {
  last_run: string | null;
  strategies: LiveStrategyVerdict[];
}

export function useSupervisor() {
  const [data, setData] = useState<SupervisorData | null>(null);
  const [liveData, setLiveData] = useState<SupervisorLiveData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchAll = async () => {
    try {
      const [sRes, lRes] = await Promise.all([
        fetch('/api/v1/supervisor/status'),
        fetch('/api/v1/supervisor/live-status'),
      ]);
      if (!sRes.ok) throw new Error('Failed to fetch supervisor status');
      if (!lRes.ok) throw new Error('Failed to fetch supervisor live status');
      const sJson = await sRes.json();
      const lJson = await lRes.json();
      if (sJson.success) {
        setData(sJson.data);
      } else {
        throw new Error(sJson.data?.error || 'Supervisor API error');
      }
      if (lJson.success) {
        setLiveData(lJson.data);
      } else {
        setLiveData(null);
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchAll();
    const interval = setInterval(() => void fetchAll(), 30_000);
    return () => clearInterval(interval);
  }, []);

  return { data, liveData, loading, error, refetch: fetchAll };
}

import { useState, useEffect } from 'react';

export interface StrategyMetricsItem {
  accuracy_pct: number;
  total_pnl: number;
  win_count: number;
  loss_count: number;
  open_count: number;
}

export interface MetricsResponse {
  strategies: Record<string, StrategyMetricsItem>;
  total_pnl: number;
  overall_accuracy_pct: number;
}

export function useMetrics() {
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchMetrics = async () => {
    try {
      const res = await fetch('/api/v1/metrics');
      if (!res.ok) throw new Error('Failed to fetch metrics');
      const data = await res.json();
      setMetrics(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMetrics();
    const interval = setInterval(fetchMetrics, 30000);
    return () => clearInterval(interval);
  }, []);

  return { metrics, loading, error, refetch: fetchMetrics };
}

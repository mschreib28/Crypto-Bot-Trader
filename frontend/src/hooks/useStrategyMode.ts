import { useCallback, useState } from 'react';

export interface StrategyModePayload {
  strategy: string;
  manual_mode: string;
  supervisor_status: string;
  effective_mode: string;
  updated_at: string | null;
}

export function useStrategyMode(strategySlug: string) {
  const [modeData, setModeData] = useState<StrategyModePayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const mRes = await fetch(`/api/v1/strategies/${encodeURIComponent(strategySlug)}/mode`);
      if (!mRes.ok) throw new Error(`mode: ${mRes.status}`);
      setModeData(await mRes.json());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'fetch failed');
    } finally {
      setLoading(false);
    }
  }, [strategySlug]);

  const setManualMode = useCallback(
    async (mode: 'LIVE' | 'SIM') => {
      const res = await fetch(`/api/v1/strategies/${encodeURIComponent(strategySlug)}/mode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      });
      if (!res.ok) throw new Error(await res.text());
      const json = (await res.json()) as StrategyModePayload;
      setModeData(json);
      return json;
    },
    [strategySlug],
  );

  return { modeData, loading, error, refetch: fetchAll, setManualMode };
}

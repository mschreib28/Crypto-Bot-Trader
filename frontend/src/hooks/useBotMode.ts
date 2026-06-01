import { useState, useEffect, useCallback } from 'react';

export type BotMode = 'SHADOW' | 'LIVE';

export interface BotModeState {
  mode: BotMode;
  updatedAt: string | null;
}

interface UseBotModeReturn {
  botMode: BotModeState | null;
  loading: boolean;
  error: string | null;
  setMode: (mode: BotMode, confirm?: string) => Promise<boolean>;
  refetch: () => Promise<void>;
}

const POLL_INTERVAL_MS = 10000;

export function useBotMode(): UseBotModeReturn {
  const [botMode, setBotMode] = useState<BotModeState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const response = await fetch('/api/v1/trading/bot-mode');
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      const data = await response.json();
      const m = (data.mode || 'SHADOW').toUpperCase();
      const mode: BotMode = m === 'LIVE' ? 'LIVE' : 'SHADOW';
      setBotMode({
        mode,
        updatedAt: data.updated_at ?? null,
      });
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch bot mode';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  const setMode = useCallback(
    async (mode: BotMode, confirm?: string): Promise<boolean> => {
      setLoading(true);
      try {
        const body: { mode: string; confirm?: string } = { mode };
        if (mode === 'LIVE') {
          body.confirm = confirm ?? '';
        }
        const response = await fetch('/api/v1/trading/bot-mode', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!response.ok) {
          const text = await response.text();
          throw new Error(text || `HTTP ${response.status}`);
        }
        const data = await response.json();
        const m = (data.mode || mode).toUpperCase();
        const next: BotMode = m === 'LIVE' ? 'LIVE' : 'SHADOW';
        setBotMode({
          mode: next,
          updatedAt: data.updated_at ?? null,
        });
        setError(null);
        return true;
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to set bot mode';
        setError(message);
        return false;
      } finally {
        setLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  return { botMode, loading, error, setMode, refetch: fetchStatus };
}

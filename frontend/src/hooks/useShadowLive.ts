import { useState, useEffect, useCallback } from 'react';

export interface ShadowLiveState {
  enabled: boolean;
  updatedAt: string | null;
}

interface UseShadowLiveReturn {
  shadowLive: ShadowLiveState | null;
  loading: boolean;
  error: string | null;
  toggleShadowLive: () => Promise<boolean>;
  refetch: () => Promise<void>;
}

const POLL_INTERVAL_MS = 30000;

export function useShadowLive(): UseShadowLiveReturn {
  const [shadowLive, setShadowLive] = useState<ShadowLiveState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const response = await fetch('/api/v1/trading/shadow-status');
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      const data = await response.json();
      setShadowLive({
        enabled: data.enabled,
        updatedAt: data.updated_at || new Date().toISOString(),
      });
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch shadow-live status';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  const toggleShadowLive = useCallback(async (): Promise<boolean> => {
    if (!shadowLive) return false;

    const newEnabled = !shadowLive.enabled;
    setLoading(true);

    try {
      const response = await fetch('/api/v1/trading/shadow-enabled', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: newEnabled }),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();
      setShadowLive({
        enabled: data.enabled ?? newEnabled,
        updatedAt: data.updated_at || new Date().toISOString(),
      });
      setError(null);
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to toggle shadow-live mode';
      setError(message);
      return false;
    } finally {
      setLoading(false);
    }
  }, [shadowLive]);

  useEffect(() => {
    fetchStatus();

    const interval = setInterval(fetchStatus, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  return { shadowLive, loading, error, toggleShadowLive, refetch: fetchStatus };
}

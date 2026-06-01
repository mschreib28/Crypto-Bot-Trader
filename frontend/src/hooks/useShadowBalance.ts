import { useState, useCallback } from 'react';
import { BalanceData } from './useBalance';

interface ShadowBalanceResponse extends BalanceData {
  positions_closed?: number;
}

interface UseShadowBalanceReturn {
  shadowBalance: BalanceData | null;
  loading: boolean;
  error: string | null;
  setShadowBalance: (totalUsd: number, availableUsd?: number) => Promise<ShadowBalanceResponse | null>;
  fetchShadowBalance: () => Promise<void>;
}

export function useShadowBalance(): UseShadowBalanceReturn {
  const [shadowBalance, setShadowBalanceState] = useState<BalanceData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchShadowBalance = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/v1/balance/shadow');
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      const data: BalanceData = await response.json();
      setShadowBalanceState(data);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch shadow balance';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  const setShadowBalance = useCallback(async (
    totalUsd: number,
    availableUsd?: number
  ): Promise<ShadowBalanceResponse | null> => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/v1/balance/shadow', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          total_usd: totalUsd,
          available_usd: availableUsd ?? totalUsd,
        }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: 'Failed to set shadow balance' }));
        throw new Error(errorData.detail || `HTTP ${response.status}`);
      }

      const data: ShadowBalanceResponse = await response.json();
      setShadowBalanceState(data);
      return data;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to set shadow balance';
      setError(message);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  return {
    shadowBalance,
    loading,
    error,
    setShadowBalance,
    fetchShadowBalance,
  };
}

import { useState, useEffect, useCallback } from 'react';

export interface Holding {
  symbol: string;
  quantity: number;
  value_usd: number;
}

export interface BalanceData {
  total_usd: number;
  available_usd: number;
  holdings: Holding[];
}

interface UseBalanceReturn {
  balance: BalanceData | null;
  loading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
}

// UI refresh interval: 10 seconds for near-real-time balance updates
// Matches backend POSITION_MONITOR_INTERVAL_SECONDS
const REFRESH_INTERVAL_MS = 10000;

export function useBalance(): UseBalanceReturn {
  const [balance, setBalance] = useState<BalanceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchBalance = useCallback(async () => {
    try {
      const response = await fetch('/api/v1/balance');
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      const data: BalanceData = await response.json();
      setBalance(data);
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch balance';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchBalance();

    const interval = setInterval(fetchBalance, REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchBalance]);

  return { balance, loading, error, refetch: fetchBalance };
}

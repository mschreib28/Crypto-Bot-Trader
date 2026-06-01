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
const REFRESH_INTERVAL_MS = 5000;

function normalizeBalancePayload(raw: unknown): BalanceData {
  const data = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {};
  const totalRaw = Number(data.total_usd);
  const totalUsd = Number.isFinite(totalRaw) ? totalRaw : 0;
  const availRaw = data.available_usd != null ? Number(data.available_usd) : totalUsd;
  const availableUsd = Number.isFinite(availRaw) ? availRaw : totalUsd;
  let holdings: Holding[] = [];
  if (Array.isArray(data.holdings)) {
    holdings = data.holdings.map((h: unknown) => {
      if (!h || typeof h !== 'object') {
        return { symbol: '', quantity: 0, value_usd: 0 };
      }
      const row = h as Record<string, unknown>;
      const qty = Number(row.quantity);
      const val = Number(row.value_usd);
      return {
        symbol: typeof row.symbol === 'string' ? row.symbol : String(row.symbol ?? ''),
        quantity: Number.isFinite(qty) ? qty : 0,
        value_usd: Number.isFinite(val) ? val : 0,
      };
    });
  }
  return { total_usd: totalUsd, available_usd: availableUsd, holdings };
}

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
      const raw = await response.json();
      setBalance(normalizeBalancePayload(raw));
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

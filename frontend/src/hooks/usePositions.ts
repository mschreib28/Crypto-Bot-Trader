import { useState, useEffect, useCallback } from 'react';
import { Position, PositionsResponse } from '../types/position';

interface UsePositionsReturn {
  positions: Position[];
  totalBudget: number;
  budgetUsed: number;
  loading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
  closePosition: (symbol: string) => Promise<boolean>;
}

// UI refresh interval: 10 seconds for near-real-time position/PnL updates
// Matches backend POSITION_SYNC_INTERVAL_SECONDS and POSITION_MONITOR_INTERVAL_SECONDS
const REFRESH_INTERVAL_MS = 10000;

/** Returns the value if it's a valid finite number, otherwise returns the fallback */
function sanitizeNumber(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

/** Sanitize position data, preserving nullish values as NaN for downstream "$--" display */
function sanitizePosition(raw: Partial<Position>): Position {
  return {
    symbol: raw.symbol ?? '',
    side: raw.side ?? 'long',
    quantity: sanitizeNumber(raw.quantity, NaN),
    entry_price: sanitizeNumber(raw.entry_price, NaN),
    entry_time: raw.entry_time ?? '',
    current_price: sanitizeNumber(raw.current_price, NaN),
    unrealized_pnl: sanitizeNumber(raw.unrealized_pnl, NaN),
    strategy_id: raw.strategy_id ?? null,
    strategy_name: raw.strategy_name ?? null,
  };
}

export function usePositions(): UsePositionsReturn {
  const [positions, setPositions] = useState<Position[]>([]);
  const [totalBudget, setTotalBudget] = useState<number>(0);
  const [budgetUsed, setBudgetUsed] = useState<number>(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchPositions = useCallback(async () => {
    try {
      const response = await fetch('/api/v1/positions');
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      const data: PositionsResponse = await response.json();
      const sanitizedPositions = Array.isArray(data.positions)
        ? data.positions.map(sanitizePosition)
        : [];
      setPositions(sanitizedPositions);
      setTotalBudget(sanitizeNumber(data.total_budget, 0));
      setBudgetUsed(sanitizeNumber(data.budget_used, 0));
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch positions';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  const closePosition = useCallback(async (symbol: string): Promise<boolean> => {
    try {
      const response = await fetch(`/api/v1/positions/${encodeURIComponent(symbol)}`, {
        method: 'DELETE',
      });
      
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: 'Failed to close position' }));
        throw new Error(errorData.detail || `HTTP ${response.status}`);
      }
      
      // Refresh positions after closing
      await fetchPositions();
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to close position';
      setError(message);
      return false;
    }
  }, [fetchPositions]);

  useEffect(() => {
    fetchPositions();

    const interval = setInterval(fetchPositions, REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchPositions]);

  return { positions, totalBudget, budgetUsed, loading, error, refetch: fetchPositions, closePosition };
}

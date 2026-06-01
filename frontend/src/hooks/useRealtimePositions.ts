import { useState, useEffect, useCallback } from 'react';

export interface RealtimePosition {
  symbol: string;
  current_pnl_pct?: number;
  time_minutes?: number;
  status: 'SCANNING' | 'PENDING' | 'LIVE' | 'EXITING' | 'COOLDOWN' | 'ERROR';
}

interface RealtimePositionsApiResponse {
  positions: RealtimePosition[];
  timestamp: string;
}

interface UseRealtimePositionsReturn {
  positions: Map<string, RealtimePosition>;
  loading: boolean;
  error: string | null;
}

const POLL_INTERVAL_MS = 10000;

export function useRealtimePositions(symbols: string[]): UseRealtimePositionsReturn {
  const [positions, setPositions] = useState<Map<string, RealtimePosition>>(new Map());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchPositions = useCallback(async () => {
    try {
      const response = await fetch('/api/v1/screener/positions/realtime');
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      
      const data: RealtimePositionsApiResponse = await response.json();
      
      // Convert array to Map for O(1) lookup
      const positionsMap = new Map<string, RealtimePosition>();
      if (Array.isArray(data?.positions)) {
        for (const pos of data.positions) {
          positionsMap.set(pos.symbol, pos);
        }
      }
      
      setPositions(positionsMap);
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch realtime positions';
      setError(message);
      // Don't clear positions on error - keep last known state
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Poll whenever the parent component believes there are active symbols.
    // The parent already filters for LIVE/PENDING/EXITING before calling this hook,
    // so if symbols is non-empty we should start polling immediately.
    if (symbols.length === 0) {
      return;
    }

    fetchPositions();

    const interval = setInterval(fetchPositions, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchPositions, symbols]);

  return { positions, loading, error };
}

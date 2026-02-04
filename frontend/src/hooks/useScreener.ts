import { useState, useEffect, useCallback } from 'react';

export type SignalType = 'BUY' | 'SELL' | 'NONE';

export interface ScreenerIndicators {
  rsi?: number;
  price?: number;
  bars_available?: number;
  change_24h_pct?: number;
  [key: string]: unknown;
}

export interface ScreenerSignal {
  symbol: string;
  signal_type: SignalType;
  signal_strength: number;
  indicators: ScreenerIndicators;
  timestamp: string;
}

interface ScreenerApiResponse {
  results: ScreenerSignal[];
  count: number;
  total_scanned: number;
  last_scan: string | null;
}

interface UseScreenerReturn {
  signals: ScreenerSignal[];
  loading: boolean;
  error: string | null;
  lastScan: string | null;
  totalScanned: number;
  refetch: () => Promise<void>;
}

const REFRESH_INTERVAL_MS = 30000;
const DEFAULT_TOP_N = 10;

interface UseScreenerOptions {
  topN?: number;
  strategyId?: string;
}

export function useScreener(options: UseScreenerOptions = {}): UseScreenerReturn {
  const { topN = DEFAULT_TOP_N, strategyId } = options;
  const [signals, setSignals] = useState<ScreenerSignal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastScan, setLastScan] = useState<string | null>(null);
  const [totalScanned, setTotalScanned] = useState<number>(0);

  const fetchScreener = useCallback(async () => {
    try {
      // Use strategy-specific endpoint if strategyId provided, otherwise use top endpoint
      const url = strategyId
        ? `/api/v1/screener/strategy/${strategyId}`
        : `/api/v1/screener/top?n=${topN}`;
      
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      const data: ScreenerApiResponse = await response.json();
      // Defensive check: ensure results is an array before sorting
      const results = Array.isArray(data?.results) ? data.results : [];
      // Map confidence to signal_strength (backend returns confidence, frontend expects signal_strength)
      const mapped = results.map((r: ScreenerSignal & { confidence?: number }) => ({
        ...r,
        signal_strength: r.confidence ?? r.signal_strength ?? 0,
      }));
      // Sort by strength descending
      const sorted = [...mapped].sort((a, b) => b.signal_strength - a.signal_strength);
      setSignals(sorted);
      setLastScan(data?.last_scan ?? null);
      setTotalScanned(typeof data?.total_scanned === 'number' ? data.total_scanned : 0);
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch screener';
      setError(message);
      setSignals([]); // Clear signals on error
    } finally {
      setLoading(false);
    }
  }, [topN, strategyId]);

  // Reset loading state when strategyId changes
  useEffect(() => {
    setLoading(true);
  }, [strategyId]);

  useEffect(() => {
    fetchScreener();

    const interval = setInterval(fetchScreener, REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchScreener]);

  return { signals, loading, error, lastScan, totalScanned, refetch: fetchScreener };
}

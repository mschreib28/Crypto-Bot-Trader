import { useState, useEffect, useCallback } from 'react';

export type SignalType = 'BUY' | 'SELL' | 'NONE';

export interface PillarResult {
  pass: boolean;
  value: number | null;
  value_4h?: number | null;
}

export interface ScreenerPillars {
  // Stage 1 static pillars
  s1_supply?:  PillarResult;
  s2_price?:   PillarResult;
  s3_listing?: PillarResult;
  // Stage 2 dynamic pillars
  d1_rvol?:     PillarResult;
  d2_momentum?: PillarResult;
  d3_volume?:   PillarResult;
  d4_btc?:      PillarResult;
}

export interface ScreenerIndicators {
  rsi?: number;
  price?: number;
  bars_available?: number;
  change_24h_pct?: number;
  // Pipeline scoring fields
  score?: number;              // 0.0–1.0 numeric score (derived from grade)
  grade?: string;              // "A+", "A", "B", "C", "F"
  stage1_pass?: boolean;
  dynamic_passes?: number;     // 0–4
  rvol?: number;               // RVOL ratio (volume / 50d avg)
  market_cap?: number;
  supply_ratio?: number;
  circulating_supply?: number;
  spread_bps?: number;
  // Per-pillar breakdown (pipeline)
  pillars?: ScreenerPillars;
  // Strategies (only if score > 0.55)
  vwap_dist_pct?: number;
  hod_dist_pct?: number;
  htf_trend?: 'UP' | 'DOWN';
  signal_lead?: {
    confidence: number;
    signal_type: 'BUY' | 'SELL' | 'NONE';
    strategy_name?: string;
    all_signals?: Array<{ strategy_name: string; confidence: number; signal_type: string }>;
  };
  // Active Trade (if position exists)
  status?: 'SCANNING' | 'PENDING' | 'LIVE' | 'EXITING' | 'COOLDOWN' | 'ERROR';
  entry_strategy?: string;
  current_pnl_pct?: number;
  time_minutes?: number;
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

// Poll every 15s for near-real-time signal strength display
const REFRESH_INTERVAL_MS = 10000;

interface UseScreenerOptions {
  topN?: number;
}

export function useScreener(_options: UseScreenerOptions = {}): UseScreenerReturn {
  const [signals, setSignals] = useState<ScreenerSignal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastScan, setLastScan] = useState<string | null>(null);
  const [totalScanned, setTotalScanned] = useState<number>(0);

  const fetchScreener = useCallback(async () => {
    try {
      // Always use unified endpoint
      const url = `/api/v1/screener/unified`;
      
      const response = await fetch(url);
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      const data: ScreenerApiResponse = await response.json();
      
      // Defensive check: ensure results is an array before sorting
      const results = Array.isArray(data?.results) ? data.results : [];
      // Map confidence to signal_strength (backend returns confidence, frontend expects signal_strength)
      // IMPORTANT: Preserve ALL properties including signal_lead in indicators
      const mapped = results.map((r: ScreenerSignal & { confidence?: number }) => ({
        ...r,
        indicators: { ...r.indicators },
        signal_strength: r.confidence ?? r.signal_strength ?? 0,
      }));
      // Sort by score descending (default), then by strength
      const sorted = [...mapped].sort((a, b) => {
        const scoreA = a.indicators.score ?? 0;
        const scoreB = b.indicators.score ?? 0;
        if (scoreB !== scoreA) {
          return scoreB - scoreA;
        }
        return b.signal_strength - a.signal_strength;
      });
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
  }, []);

  useEffect(() => {
    fetchScreener();

    const interval = setInterval(fetchScreener, REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchScreener]);

  return { signals, loading, error, lastScan, totalScanned, refetch: fetchScreener };
}

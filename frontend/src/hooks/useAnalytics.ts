import { useCallback } from 'react';
import { usePoll } from './usePoll';

export interface GradeAnalyticsRow {
  grade: string;
  trades: number;
  win_rate: number;
  avg_r: number;
}

export interface FactorCorrelationRow {
  factor: string;
  sample_size: number;
  correlation_win: number | null;
  correlation_r: number | null;
}

const parseTrades = (raw: unknown) => {
  const d = raw as { data?: unknown[] };
  return d.data ?? [];
};

const parseByGrade = (raw: unknown): GradeAnalyticsRow[] => {
  const d = raw as { data?: GradeAnalyticsRow[] };
  return d.data ?? [];
};

const parseCorrelation = (raw: unknown) => {
  const d = raw as { factors?: FactorCorrelationRow[]; sample_size?: number };
  return { factors: d.factors ?? [], sampleSize: d.sample_size ?? 0 };
};

export function useAnalytics() {
  const trades = usePoll('/api/v1/analytics/trades', 30_000, parseTrades);
  const byGrade = usePoll('/api/v1/analytics/by-grade', 30_000, parseByGrade);
  const correlation = usePoll(
    '/api/v1/analytics/factor-correlation',
    30_000,
    parseCorrelation
  );

  const downloadJson = useCallback(() => {
    const blob = new Blob([JSON.stringify(trades.data ?? [], null, 2)], {
      type: 'application/json',
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `trade-analytics-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }, [trades.data]);

  return {
    trades: trades.data ?? [],
    byGrade: byGrade.data ?? [],
    factors: correlation.data?.factors ?? [],
    sampleSize: correlation.data?.sampleSize ?? 0,
    loading: trades.loading || byGrade.loading || correlation.loading,
    error: trades.error || byGrade.error || correlation.error,
    downloadJson,
  };
}

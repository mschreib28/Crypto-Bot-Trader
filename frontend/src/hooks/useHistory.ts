import { useState, useCallback } from 'react';

const API_BASE = '/api/v1';

export interface HistoryItem {
  id: string;
  timestamp: string;
  type: string;
  message: string;
  details?: Record<string, unknown> | null;
  symbol?: string | null;
  strategy?: string | null;
}

export interface HistoryFilters {
  type: string;
  symbol: string;
  strategy: string;
  search: string;
  fromDate: string;
  toDate: string;
  page: number;
  perPage: number;
}

export interface HistoryResult {
  items: HistoryItem[];
  total: number;
  page: number;
  pages: number;
  per_page: number;
}

const DEFAULT_FILTERS: HistoryFilters = {
  type: '',
  symbol: '',
  strategy: '',
  search: '',
  fromDate: '',
  toDate: '',
  page: 1,
  perPage: 50,
};

function buildParams(filters: HistoryFilters): URLSearchParams {
  const p = new URLSearchParams();
  p.set('page', String(filters.page));
  p.set('per_page', String(filters.perPage));
  if (filters.type) p.set('type', filters.type);
  if (filters.symbol) p.set('symbol', filters.symbol);
  if (filters.strategy) p.set('strategy', filters.strategy);
  if (filters.search) p.set('search', filters.search);
  if (filters.fromDate) p.set('from_date', filters.fromDate);
  if (filters.toDate) p.set('to_date', filters.toDate);
  return p;
}

export function useHistory() {
  const [filters, setFilters] = useState<HistoryFilters>(DEFAULT_FILTERS);
  const [result, setResult] = useState<HistoryResult | null>(null);
  const [types, setTypes] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchHistory = useCallback(async (f: HistoryFilters = filters) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/history?${buildParams(f)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: HistoryResult = await res.json();
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load history');
    } finally {
      setLoading(false);
    }
  }, [filters]);

  const fetchTypes = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/history/types`);
      if (!res.ok) return;
      const data: { types: string[] } = await res.json();
      setTypes(data.types);
    } catch {
      // non-critical
    }
  }, []);

  const clearHistory = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/history`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setResult(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to clear history');
    }
  }, []);

  const exportHistory = useCallback(async (format: 'csv' | 'json') => {
    const params = buildParams({ ...filters, page: 1, perPage: 50 });
    params.set('format', format);
    params.delete('limit');
    const url = `${API_BASE}/history/export?${params}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = `activity_log.${format}`;
    a.click();
  }, [filters]);

  const updateFilters = useCallback((patch: Partial<HistoryFilters>) => {
    setFilters(prev => {
      const next = { ...prev, ...patch, page: patch.page ?? 1 };
      return next;
    });
  }, []);

  return {
    filters,
    updateFilters,
    result,
    types,
    loading,
    error,
    fetchHistory,
    fetchTypes,
    exportHistory,
    clearHistory,
  };
}

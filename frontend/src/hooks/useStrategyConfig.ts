import { useState, useEffect, useCallback } from 'react';

export interface StrategyFilters {
  min_volume_24h?: number;
  confidence_buy?: number;
  confidence_sell?: number;
  min_circulating_supply?: number;
  max_circulating_supply?: number | null;
  min_change_24h_pct?: number;
  max_change_24h_pct?: number | null;
}

export interface StrategyConfig {
  strategy_type: string;
  interval?: string;
  rsi_period?: number;
  rsi_overbought?: number;
  rsi_oversold?: number;
  lookback_period?: number;
  bollinger_std?: number;
  roc_threshold?: number;
  fast_period?: number;
  slow_period?: number;
  signal_period?: number;
  min_volume?: number;
  volume_threshold?: number;  // RVOL threshold (e.g., 1.5 = 150%)
  filters?: StrategyFilters;
  [key: string]: string | number | StrategyFilters | undefined;
}

interface UseStrategyConfigReturn {
  config: StrategyConfig | null;
  loading: boolean;
  error: string | null;
  updateConfig: (newConfig: StrategyConfig) => Promise<boolean>;
  refetch: () => Promise<void>;
}

export function useStrategyConfig(strategyId: string | undefined): UseStrategyConfigReturn {
  const [config, setConfig] = useState<StrategyConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchConfig = useCallback(async () => {
    if (!strategyId) {
      setConfig(null);
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`/api/v1/strategies/${strategyId}/config`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      const data = await response.json();
      // Flatten nested API response to expected StrategyConfig shape
      // Spread ALL parameters to capture new A+ filters dynamically
      const params = data.parameters ?? {};
      const apiFilters = data.filters ?? {};
      const flatConfig: StrategyConfig = {
        strategy_type: data.strategy_type ?? 'unknown',
        // Spread ALL parameters from backend (includes new A+ filters)
        ...params,
        // Ensure interval has default
        interval: params.interval ?? '5m',
        // volume_threshold is at root level (not in parameters)
        volume_threshold: data.volume_threshold,
        // common filters (legacy flat field)
        min_volume: apiFilters.min_volume_24h,
        // filters object for new UI
        filters: {
          min_volume_24h: apiFilters.min_volume_24h,
          confidence_buy: apiFilters.confidence_buy ?? 90,
          confidence_sell: apiFilters.confidence_sell ?? 90,
          min_circulating_supply: apiFilters.min_circulating_supply,
          max_circulating_supply: apiFilters.max_circulating_supply,
        },
      };
      setConfig(flatConfig);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch config';
      setError(message);
      setConfig(null);
    } finally {
      setLoading(false);
    }
  }, [strategyId]);

  const updateConfig = useCallback(async (newConfig: StrategyConfig): Promise<boolean> => {
    if (!strategyId) {
      setError('No strategy selected');
      return false;
    }

    try {
      // Extract all parameters except special fields
      const { strategy_type, filters, min_volume, volume_threshold, ...allParams } = newConfig;
      
      // Transform flat config into nested structure expected by backend
      const payload: {
        parameters: Record<string, string | number | undefined>;
        filters: Record<string, number | null | undefined>;
        volume_threshold?: number;
      } = {
        // Send ALL parameters dynamically (includes new A+ filters)
        parameters: allParams as Record<string, string | number | undefined>,
        filters: {
          min_volume_24h: filters?.min_volume_24h,
          confidence_buy: filters?.confidence_buy,
          confidence_sell: filters?.confidence_sell,
          min_circulating_supply: filters?.min_circulating_supply,
          max_circulating_supply: filters?.max_circulating_supply,
          min_change_24h_pct: filters?.min_change_24h_pct,
          max_change_24h_pct: filters?.max_change_24h_pct,
        },
        // volume_threshold is sent at root level (not in parameters)
        volume_threshold: volume_threshold as number | undefined,
      };

      // Remove undefined values from parameters
      Object.keys(payload.parameters).forEach(key => {
        if (payload.parameters[key] === undefined) {
          delete payload.parameters[key];
        }
      });

      // Remove undefined values from filters
      Object.keys(payload.filters).forEach(key => {
        if (payload.filters[key] === undefined) {
          delete payload.filters[key];
        }
      });

      const response = await fetch(`/api/v1/strategies/${strategyId}/config`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();
      // Normalize the response the same way as fetchConfig
      // Spread ALL parameters to capture new A+ filters dynamically
      const params = data.parameters ?? {};
      const apiFilters = data.filters ?? {};
      const flatConfig: StrategyConfig = {
        strategy_type: data.strategy_type ?? 'unknown',
        // Spread ALL parameters from backend (includes new A+ filters)
        ...params,
        // Ensure interval has default
        interval: params.interval ?? '5m',
        // volume_threshold is at root level (not in parameters)
        volume_threshold: data.volume_threshold,
        min_volume: apiFilters.min_volume_24h,
        filters: {
          min_volume_24h: apiFilters.min_volume_24h,
          confidence_buy: apiFilters.confidence_buy ?? 90,
          confidence_sell: apiFilters.confidence_sell ?? 90,
          min_circulating_supply: apiFilters.min_circulating_supply,
          max_circulating_supply: apiFilters.max_circulating_supply,
        },
      };
      setConfig(flatConfig);
      setError(null);
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update config';
      setError(message);
      return false;
    }
  }, [strategyId]);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  return { config, loading, error, updateConfig, refetch: fetchConfig };
}

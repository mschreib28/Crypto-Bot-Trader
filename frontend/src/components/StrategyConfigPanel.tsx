import { useState, useEffect } from 'react';
import { useStrategies } from '../hooks/useStrategies';
import { getStrategyDisplayName, RETIRED_STRATEGY_SLUGS } from '../utils/strategyLabels';
import { useStrategyConfig, StrategyConfig, StrategyFilters } from '../hooks/useStrategyConfig';

interface ConfigField {
  key: string;
  label: string;
  shortLabel: string;
  format: (value: unknown) => string;
  min?: number;
  max?: number;
  step?: number;
  options?: readonly string[];
}

function formatVolume(vol: unknown): string {
  if (vol == null) return '—';
  const n = typeof vol === 'number' ? vol : parseFloat(String(vol));
  if (Number.isNaN(n)) return typeof vol === 'string' ? vol : '—';
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)}K`;
  return String(n);
}

function formatSupply(val: unknown): string {
  if (val == null) return '—';
  const n = typeof val === 'number' ? val : parseFloat(String(val));
  if (Number.isNaN(n)) return typeof val === 'string' ? val : '—';
  if (n >= 1e12) return `${(n / 1e12).toFixed(1)}T`;
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)}K`;
  return String(n);
}

const formatNum = (v: unknown) => v != null ? String(v) : '—';

// Format helpers for new A+ parameters
const formatPct = (v: unknown) => (v != null && typeof v === 'number') ? `${v}%` : (typeof v === 'string' ? v : '—');
const formatRatio = (v: unknown) => (v != null && typeof v === 'number') ? `${Math.round(v * 100)}%` : (typeof v === 'string' ? v : '—');

const STRATEGY_FIELDS: Record<string, ConfigField[]> = {
  mean_reversion: [
    // Core RSI settings
    { key: 'rsi_period', label: 'RSI Period', shortLabel: 'RSI Per', format: formatNum, min: 2, max: 50, step: 1 },
    { key: 'rsi_overbought', label: 'RSI Overbought', shortLabel: 'RSI OB', format: formatNum, min: 50, max: 100, step: 1 },
    { key: 'rsi_oversold', label: 'RSI Oversold', shortLabel: 'RSI OS', format: formatNum, min: 0, max: 50, step: 1 },
    // Core BB settings
    { key: 'lookback_period', label: 'Lookback Period', shortLabel: 'Lookback', format: formatNum, min: 5, max: 100, step: 1 },
    { key: 'bollinger_std', label: 'Bollinger Std', shortLabel: 'BB Std', format: formatNum, min: 0.5, max: 5, step: 0.1 },
    // A+ Filters
    { key: 'adx_max_threshold', label: 'ADX Max (Range)', shortLabel: 'ADX Max', format: formatNum, min: 10, max: 30, step: 1 },
    { key: 'atr_min_ratio', label: 'ATR Min Ratio', shortLabel: 'ATR Min', format: formatRatio, min: 0.5, max: 2, step: 0.1 },
    { key: 'volume_threshold', label: 'RVOL Threshold', shortLabel: 'RVOL %', format: formatRatio, min: 1, max: 5, step: 0.1 },
  ],
  momentum: [
    // Core settings
    { key: 'lookback_period', label: 'Lookback Period', shortLabel: 'Lookback', format: formatNum, min: 5, max: 100, step: 1 },
    { key: 'roc_threshold', label: 'ROC Threshold', shortLabel: 'ROC %', format: formatPct, min: 0, max: 20, step: 0.5 },
    // EMA Stack
    { key: 'ema_fast', label: 'EMA Fast', shortLabel: 'EMA Fast', format: formatNum, min: 5, max: 50, step: 1 },
    { key: 'ema_medium', label: 'EMA Medium', shortLabel: 'EMA Med', format: formatNum, min: 20, max: 100, step: 1 },
    { key: 'ema_slow', label: 'EMA Slow', shortLabel: 'EMA Slow', format: formatNum, min: 100, max: 300, step: 10 },
    // A+ Filters
    { key: 'adx_threshold', label: 'ADX Threshold', shortLabel: 'ADX Min', format: formatNum, min: 15, max: 40, step: 1 },
    { key: 'rsi_min_long', label: 'RSI Min (Long)', shortLabel: 'RSI Min', format: formatNum, min: 30, max: 60, step: 1 },
    { key: 'rsi_max_long', label: 'RSI Max (Long)', shortLabel: 'RSI Max', format: formatNum, min: 60, max: 85, step: 1 },
    { key: 'volume_threshold', label: 'RVOL Threshold', shortLabel: 'RVOL %', format: formatRatio, min: 1, max: 3, step: 0.1 },
  ],
  macd: [
    // Core MACD settings
    { key: 'fast_period', label: 'Fast EMA', shortLabel: 'Fast', format: formatNum, min: 2, max: 50, step: 1 },
    { key: 'slow_period', label: 'Slow EMA', shortLabel: 'Slow', format: formatNum, min: 10, max: 100, step: 1 },
    { key: 'signal_period', label: 'Signal EMA', shortLabel: 'Signal', format: formatNum, min: 2, max: 50, step: 1 },
    // A+ Filters
    { key: 'ema_trend_period', label: 'Trend EMA', shortLabel: 'Trend EMA', format: formatNum, min: 20, max: 100, step: 5 },
    { key: 'adx_threshold', label: 'ADX Threshold', shortLabel: 'ADX Min', format: formatNum, min: 15, max: 30, step: 1 },
    { key: 'volume_threshold', label: 'RVOL Threshold', shortLabel: 'RVOL %', format: formatRatio, min: 1, max: 3, step: 0.1 },
  ],
};

const GRADE_OPTIONS = ['A+', 'A', 'B', 'C', 'D', 'F'] as const;
const formatGrade = (v: unknown) => (v != null ? String(v) : '—');

const FILTER_FIELDS: ConfigField[] = [
  { key: 'min_volume_24h', label: 'Min 24h Volume', shortLabel: 'Min Vol', format: formatVolume, min: 0, step: 100000 },
  { key: 'confidence_buy', label: 'Min Signal Strength (Buy) %', shortLabel: 'Buy Strength %', format: formatPct, min: 50, max: 100, step: 1 },
  { key: 'confidence_sell', label: 'Min Signal Strength (Sell) %', shortLabel: 'Sell Strength %', format: formatPct, min: 50, max: 100, step: 1 },
  { key: 'min_allowed_grade', label: 'Min Allowed Grade', shortLabel: 'Min Grade', format: formatGrade, options: GRADE_OPTIONS },
  { key: 'min_change_24h_pct', label: 'Min 24h Change %', shortLabel: 'Min 24H %', format: formatPct, min: -50, max: 50, step: 0.1 },
  { key: 'max_change_24h_pct', label: 'Max 24h Change %', shortLabel: 'Max 24H %', format: formatPct, min: -50, max: 50, step: 0.1 },
  { key: 'min_circulating_supply', label: 'Min Circulating Supply', shortLabel: 'Min Supply', format: formatSupply, min: 0, step: 1000000 },
  { key: 'max_circulating_supply', label: 'Max Circulating Supply', shortLabel: 'Max Supply', format: formatSupply, min: 0, step: 1000000 },
];

const INTERVAL_OPTIONS = ['1m', '5m', '10m', '15m', '30m', '1h', '4h', '1d'];

// Build fields dynamically for unknown strategy types
function getFieldsForConfig(config: StrategyConfig): ConfigField[] {
  const knownFields = STRATEGY_FIELDS[config.strategy_type];
  if (knownFields) return knownFields;

  // Fallback: show all numeric params from the config (excluding filters object)
  return Object.entries(config)
    .filter(([key, val]) => key !== 'strategy_type' && key !== 'filters' && typeof val === 'number')
    .map(([key]) => ({
      key,
      label: key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
      shortLabel: key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
      format: key.includes('volume') ? formatVolume : formatNum,
    }));
}

interface StrategyConfigPanelProps {
  onConfigSaved?: () => void;
}

export function StrategyConfigPanel({ onConfigSaved }: StrategyConfigPanelProps) {
  const { strategies, loading: strategiesLoading } = useStrategies();
  const [selectedStrategyId, setSelectedStrategyId] = useState<string | undefined>(undefined);
  const [editMode, setEditMode] = useState(false);
  const [editedConfig, setEditedConfig] = useState<StrategyConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [filterErrors, setFilterErrors] = useState<Record<string, string>>({});

  const activeStrategiesForSetup = strategies.filter(
    (s) => s.status === 'active' && !RETIRED_STRATEGY_SLUGS.has(s.name),
  );

  useEffect(() => {
    if (activeStrategiesForSetup.length === 0) return;
    const valid =
      selectedStrategyId &&
      activeStrategiesForSetup.some((s) => s.strategy_id === selectedStrategyId);
    if (!valid) {
      setSelectedStrategyId(activeStrategiesForSetup[0].strategy_id);
    }
  }, [activeStrategiesForSetup, selectedStrategyId]);

  const { config, loading, error, updateConfig } = useStrategyConfig(selectedStrategyId);

  useEffect(() => {
    setEditMode(false);
    setEditedConfig(null);
  }, [selectedStrategyId]);

  const handleEdit = () => {
    if (config) {
      setEditedConfig({ ...config });
      setEditMode(true);
      setFilterErrors({});
    }
  };

  const handleCancel = () => {
    setEditMode(false);
    setEditedConfig(null);
    setFilterErrors({});
  };

  const handleSave = async () => {
    if (!editedConfig) return;
    // Prevent save if there are validation errors
    if (Object.keys(filterErrors).length > 0) return;
    setSaving(true);
    const success = await updateConfig(editedConfig);
    setSaving(false);
    if (success) {
      setEditMode(false);
      setEditedConfig(null);
      setFilterErrors({});
      // Refresh strategies list to update interval display
      onConfigSaved?.();
    }
  };

  const handleInputChange = (key: string, value: string) => {
    if (!editedConfig) return;
    // Handle interval as string, other fields as numbers
    if (key === 'interval') {
      setEditedConfig({ ...editedConfig, [key]: value });
    } else {
      const numValue = parseFloat(value);
      if (!isNaN(numValue)) {
        setEditedConfig({ ...editedConfig, [key]: numValue });
      }
    }
  };

  const handleFilterChange = (key: keyof StrategyFilters, value: string) => {
    if (!editedConfig) return;
    const numValue = parseFloat(value);
    if (!isNaN(numValue)) {
      setEditedConfig({
        ...editedConfig,
        filters: { ...editedConfig.filters, [key]: numValue },
      });
      // Validate confidence fields (50-100 range)
      if (key === 'confidence_buy' || key === 'confidence_sell') {
        if (numValue < 50 || numValue > 100) {
          setFilterErrors((prev) => ({ ...prev, [key]: 'Must be 50-100' }));
        } else {
          setFilterErrors((prev) => {
            const { [key]: _, ...rest } = prev;
            return rest;
          });
        }
      }
    }
  };

  const handleFilterSelectChange = (key: keyof StrategyFilters, value: string) => {
    if (!editedConfig) return;
    setEditedConfig({
      ...editedConfig,
      filters: { ...editedConfig.filters, [key]: value },
    });
  };

  const handleStrategyChange = (strategyId: string) => {
    setSelectedStrategyId(strategyId || undefined);
    setEditMode(false);
    setEditedConfig(null);
  };

  const displayConfig = editMode ? editedConfig : config;
  const fields = displayConfig ? getFieldsForConfig(displayConfig) : [];

  return (
    <section
      className="bg-gray-800 rounded-lg p-3 border border-gray-700 flex flex-col min-h-0 overflow-hidden"
      aria-labelledby="strategy-config-title"
    >
      <div className="flex items-center justify-between mb-2">
        <h2
          id="strategy-config-title"
          className="text-sm font-semibold text-white"
        >
          Strategy Setup
        </h2>
        <select
          value={selectedStrategyId || ''}
          onChange={(e) => handleStrategyChange(e.target.value)}
          disabled={strategiesLoading || activeStrategiesForSetup.length === 0}
          className="bg-gray-700 text-gray-200 text-xs rounded px-1.5 py-0.5 border border-gray-600 focus:outline-none focus:ring-1 focus:ring-blue-500"
          aria-label="Select strategy"
        >
          {strategiesLoading && <option value="">Loading...</option>}
          {!strategiesLoading && activeStrategiesForSetup.length === 0 && (
            <option value="">None</option>
          )}
          {activeStrategiesForSetup.map((strategy) => (
            <option key={strategy.strategy_id} value={strategy.strategy_id}>
              {getStrategyDisplayName(strategy.name)}
            </option>
          ))}
        </select>
      </div>

      {loading && <div className="text-gray-400 text-xs py-2">Loading...</div>}

      {error && (
        <div className="rounded border border-red-800 bg-red-900/20 p-2 text-red-400 text-xs mb-2">
          {error}
        </div>
      )}

      {!loading && !error && !displayConfig && selectedStrategyId && (
        <div className="text-gray-400 text-xs py-2">No config</div>
      )}

      {!loading && displayConfig && (
        <div className="flex flex-col min-h-0">
          <div className="min-h-0 max-h-32 overflow-y-auto pr-1 space-y-1.5">
          {/* Strategy Settings Section */}
          <div className="text-[10px] text-blue-400 uppercase tracking-wide mb-1 font-semibold">Strategy Settings</div>
          
          {/* Interval Dropdown */}
          <div className="flex justify-between items-center text-xs">
            <span className="text-gray-500">Interval</span>
            {editMode ? (
              <select
                value={editedConfig?.interval || '5m'}
                onChange={(e) => handleInputChange('interval', e.target.value)}
                className="bg-gray-700 text-gray-200 text-xs rounded px-1.5 py-0.5 border border-gray-600 focus:outline-none focus:ring-1 focus:ring-blue-500"
                aria-label="Interval"
              >
                {INTERVAL_OPTIONS.map(opt => (
                  <option key={opt} value={opt}>{opt}</option>
                ))}
              </select>
            ) : (
              <span className="text-gray-300 font-mono">{displayConfig?.interval || '5m'}</span>
            )}
          </div>

          {/* Strategy-specific Parameters */}
          {fields.map((field) => (
            <div key={field.key} className="flex justify-between items-center text-xs">
              <span className="text-gray-500">{field.shortLabel}</span>
              {editMode ? (
                <input
                  type="number"
                  value={(editedConfig?.[field.key] as number | string | undefined) ?? ''}
                  onChange={(e) => handleInputChange(field.key, e.target.value)}
                  min={field.min}
                  max={field.max}
                  step={field.step}
                  className="bg-gray-700 text-gray-200 text-xs font-mono rounded px-1.5 py-0.5 w-20 border border-gray-600 focus:outline-none focus:ring-1 focus:ring-blue-500 text-right"
                  aria-label={field.label}
                />
              ) : (
                <span className="text-gray-300 font-mono">
                  {field.format(displayConfig[field.key] as number | undefined)}
                </span>
              )}
            </div>
          ))}

          {/* Screener Settings Section */}
          <div className="text-[10px] text-blue-400 uppercase tracking-wide mb-1 mt-3 font-semibold">Screener Settings</div>
          {FILTER_FIELDS.map((field) => {
            const filterValue = displayConfig.filters?.[field.key as keyof StrategyFilters];
            const editedFilterValue = editedConfig?.filters?.[field.key as keyof StrategyFilters];
            const hasError = filterErrors[field.key];
            const isSelect = field.options != null;
            return (
              <div key={field.key} className="flex flex-col text-xs">
                <div className="flex justify-between items-center">
                  <span className="text-gray-500" title={field.label}>{field.shortLabel}</span>
                  {editMode ? (
                    isSelect ? (
                      <select
                        value={String(editedFilterValue ?? field.options?.[0] ?? '')}
                        onChange={(e) => handleFilterSelectChange(field.key as keyof StrategyFilters, e.target.value)}
                        className="bg-gray-700 text-gray-200 text-xs rounded px-1.5 py-0.5 border border-gray-600 focus:outline-none focus:ring-1 focus:ring-blue-500"
                        aria-label={field.label}
                      >
                        {field.options?.map((opt) => (
                          <option key={opt} value={opt}>{opt}</option>
                        ))}
                      </select>
                    ) : (
                      <input
                        type="number"
                        value={editedFilterValue ?? ''}
                        onChange={(e) => handleFilterChange(field.key as keyof StrategyFilters, e.target.value)}
                        min={field.min}
                        max={field.max}
                        step={field.step}
                        className={`bg-gray-700 text-gray-200 text-xs font-mono rounded px-1.5 py-0.5 w-20 border focus:outline-none focus:ring-1 text-right ${
                          hasError
                            ? 'border-red-500 focus:ring-red-500'
                            : 'border-gray-600 focus:ring-blue-500'
                        }`}
                        aria-label={field.label}
                        aria-invalid={!!hasError}
                      />
                    )
                  ) : (
                    <span className="text-gray-300 font-mono">
                      {field.format(filterValue)}
                    </span>
                  )}
                </div>
                {hasError && editMode && (
                  <span className="text-red-400 text-[10px] text-right mt-0.5">{hasError}</span>
                )}
              </div>
            );
          })}
          </div>

          <div className="flex shrink-0 justify-end gap-1.5 pt-2 border-t border-gray-700 mt-2">
            {editMode ? (
              <>
                <button
                  onClick={handleCancel}
                  disabled={saving}
                  className="px-2 py-1 text-xs rounded bg-gray-700 text-gray-300 hover:bg-gray-600 disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  disabled={saving || Object.keys(filterErrors).length > 0}
                  className="px-2 py-1 text-xs rounded bg-blue-600 text-white hover:bg-blue-500 disabled:opacity-50"
                >
                  {saving ? '...' : 'Save'}
                </button>
              </>
            ) : (
              <button
                onClick={handleEdit}
                disabled={!config}
                className="px-2 py-1 text-xs rounded bg-gray-700 text-gray-300 hover:bg-gray-600 disabled:opacity-50"
              >
                Edit
              </button>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

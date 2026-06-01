import { useEffect, useState, useCallback } from 'react';
import { ScreenerIndicators, ScreenerPillars } from '../hooks/useScreener';

interface PillarDef {
  id: string;
  label: string;
  description: string;
  rationale: string;
}

interface StageDef {
  label: string;
  pillars: PillarDef[];
}

interface GradeDef {
  grade: string;
  condition: string;
  action: string;
}

interface CriteriaData {
  hard_floor: { label: string; description: string; rationale: string };
  stage1: StageDef;
  stage2: StageDef;
  grades: GradeDef[];
}

function isMeanrevLeadStrategy(name?: string | null): boolean {
  if (!name) return false;
  const n = name.toLowerCase();
  return (
    n.includes('meanrev') ||
    n.includes('mean_rev') ||
    n.includes('mean-rev') ||
    n.includes('mean_reversion')
  );
}

interface CriteriaModalProps {
  onClose: () => void;
  symbol?: string;
  indicators?: ScreenerIndicators;
  /** Highest-confidence signal lead strategy across screener rows (for contextual notes). */
  activeLeadStrategyName?: string | null;
}

const GRADE_COLORS: Record<string, string> = {
  'A+': 'text-green-400',
  A:   'text-green-300',
  B:   'text-yellow-400',
  C:   'text-yellow-300',
  F:   'text-gray-400',
};

const GRADE_BG: Record<string, string> = {
  'A+': 'bg-green-900/40 border-green-700',
  A:   'bg-green-900/30 border-green-800',
  B:   'bg-yellow-900/30 border-yellow-800',
  C:   'bg-yellow-900/20 border-yellow-900',
  F:   'bg-gray-800/40 border-gray-700',
};

function PassBadge({ pass, value }: { pass?: boolean; value?: string | null }) {
  if (pass === undefined) {
    return <span className="text-gray-500 text-xs">—</span>;
  }
  return (
    <span className={`flex items-center gap-1 text-xs font-medium ${pass ? 'text-green-400' : 'text-red-400'}`}>
      {pass ? '✓' : '✗'}
      {value && <span className="text-gray-300 font-normal">{value}</span>}
    </span>
  );
}

function formatPillarValue(id: string, pillar?: { pass: boolean; value: number | null; value_4h?: number | null }): string | null {
  if (!pillar || pillar.value == null) return null;
  const v = pillar.value;
  switch (id) {
    case 's1_supply':
      if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
      if (v >= 1e6) return `${(v / 1e6).toFixed(0)}M`;
      return v.toLocaleString();
    case 's2_price':
      return `$${v.toFixed(v < 0.01 ? 5 : v < 1 ? 4 : 2)}`;
    case 's3_listing':
      return `${v}/30 days active`;
    case 'd1_rvol':
      return `${v.toFixed(1)}×`;
    case 'd2_momentum': {
      const parts = [`${v >= 0 ? '+' : ''}${v.toFixed(1)}% 24h`];
      if (pillar.value_4h != null) {
        parts.push(`${pillar.value_4h >= 0 ? '+' : ''}${pillar.value_4h.toFixed(1)}% 4h`);
      }
      return parts.join(' / ');
    }
    case 'd3_volume':
      if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
      return `$${(v / 1e3).toFixed(0)}K`;
    case 'd4_btc':
      return `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`;
    default:
      return String(v);
  }
}

function PillarRow({
  def,
  pillar,
}: {
  def: PillarDef;
  pillar?: { pass: boolean; value: number | null; value_4h?: number | null };
}) {
  const formatted = formatPillarValue(def.id, pillar);
  return (
    <div className="flex items-start gap-3 py-2 border-b border-gray-800 last:border-b-0">
      <div className="w-5 pt-0.5 flex-shrink-0">
        <PassBadge pass={pillar?.pass} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className="text-sm font-medium text-gray-200">{def.label}</span>
          <span className="text-xs text-gray-400">{def.description}</span>
          {formatted && (
            <span className={`text-xs font-mono ${pillar?.pass ? 'text-green-300' : 'text-red-300'}`}>
              → {formatted}
            </span>
          )}
        </div>
        <p className="text-xs text-gray-500 mt-0.5">{def.rationale}</p>
      </div>
    </div>
  );
}

export function CriteriaModal({
  onClose,
  symbol,
  indicators,
  activeLeadStrategyName,
}: CriteriaModalProps) {
  const [criteria, setCriteria] = useState<CriteriaData | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchCriteria = useCallback(async () => {
    try {
      const resp = await fetch('/api/v1/screener/criteria');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data: CriteriaData = await resp.json();
      setCriteria(data);
    } catch {
      // show static fallback if endpoint unreachable
      setCriteria(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchCriteria();
  }, [fetchCriteria]);

  // Close on Escape key
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  const pillars = indicators?.pillars as ScreenerPillars | undefined;
  const grade = indicators?.grade;
  const dynamicPasses = indicators?.dynamic_passes;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative bg-gray-900 border border-gray-700 rounded-lg shadow-2xl w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800">
          <div>
            <h2 className="text-base font-semibold text-gray-100">Scanner Criteria</h2>
            {symbol && grade && (
              <p className="text-xs text-gray-400 mt-0.5">
                {symbol} —{' '}
                <span className={`font-bold ${GRADE_COLORS[grade] ?? 'text-gray-300'}`}>
                  Grade {grade}
                </span>
                {dynamicPasses !== undefined && (
                  <span className="text-gray-500 ml-1">({dynamicPasses}/4 dynamic)</span>
                )}
              </p>
            )}
            {symbol && !grade && (
              <p className="text-xs text-gray-500 mt-0.5">{symbol}</p>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-gray-200 transition-colors text-lg leading-none"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {loading && (
          <div className="px-5 py-8 text-center text-gray-500 text-sm">Loading criteria…</div>
        )}

        {!loading && criteria && (
          <div className="px-5 py-4 space-y-5">
            {/* Hard floor */}
            <div>
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1">
                Hard Floor (absolute minimum)
              </p>
              <div className="text-xs text-gray-300 bg-gray-800/50 rounded px-3 py-2">
                {criteria.hard_floor.description}
                <span className="text-gray-500 ml-1">— {criteria.hard_floor.rationale}</span>
              </div>
            </div>

            {/* Stage 1 */}
            <div>
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
                {criteria.stage1.label}
              </p>
              <div className="space-y-0">
                {criteria.stage1.pillars.map((p) => (
                  <PillarRow
                    key={p.id}
                    def={p}
                    pillar={pillars?.[p.id as keyof ScreenerPillars] as { pass: boolean; value: number | null } | undefined}
                  />
                ))}
              </div>
            </div>

            {/* Stage 2 */}
            <div>
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
                {criteria.stage2.label}
              </p>
              <div className="space-y-0">
                {criteria.stage2.pillars.map((p) => (
                  <PillarRow
                    key={p.id}
                    def={p}
                    pillar={pillars?.[p.id as keyof ScreenerPillars] as { pass: boolean; value: number | null; value_4h?: number | null } | undefined}
                  />
                ))}
              </div>
              {isMeanrevLeadStrategy(activeLeadStrategyName) && (
                <p className="mt-2 text-xs text-amber-100/90 bg-amber-900/25 border border-amber-800/40 rounded px-3 py-2">
                  <span className="font-semibold text-amber-200">Mean reversion: </span>
                  D2 momentum is not used for meanrev evaluation. Gate: 4h RSI &lt; 40, price at or below lower
                  Bollinger Band, ADX &lt; 30.
                </p>
              )}
            </div>

            {/* Grade table */}
            <div>
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
                Grade Scale
              </p>
              <div className="space-y-1.5">
                {criteria.grades.map((g) => (
                  <div
                    key={g.grade}
                    className={`flex items-start gap-3 rounded px-3 py-2 border ${
                      grade === g.grade
                        ? GRADE_BG[g.grade] ?? 'bg-gray-800/40 border-gray-700'
                        : 'bg-transparent border-transparent'
                    }`}
                  >
                    <span className={`font-bold text-sm w-5 flex-shrink-0 ${GRADE_COLORS[g.grade] ?? 'text-gray-300'}`}>
                      {g.grade}
                    </span>
                    <div className="min-w-0">
                      <span className="text-xs text-gray-300">{g.condition}</span>
                      <span className="text-xs text-gray-500 ml-2">→ {g.action}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {!loading && !criteria && (
          <div className="px-5 py-8 text-center text-gray-500 text-sm">
            Could not load criteria. Check that the backend is running.
          </div>
        )}
      </div>
    </div>
  );
}

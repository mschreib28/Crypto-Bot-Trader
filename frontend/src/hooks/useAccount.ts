import { usePoll } from './usePoll';
import { AccountState } from '../types/account';

function parseAccount(raw: unknown): AccountState | null {
  if (raw == null || typeof raw !== 'object') return null;
  const d = raw as Record<string, unknown>;
  const n = (key: string, fallback: number): number => {
    const v = Number(d[key]);
    return Number.isFinite(v) ? v : fallback;
  };
  const base = { ...d } as unknown as AccountState;
  return {
    ...base,
    initial_equity: n('initial_equity', 0),
    realized_pnl: n('realized_pnl', 0),
    unrealized_pnl: n('unrealized_pnl', 0),
    current_equity: n('current_equity', 0),
    total_pnl: n('total_pnl', 0),
    pnl_percent: n('pnl_percent', 0),
    daily_pnl: n('daily_pnl', 0),
    max_risk_per_trade: n('max_risk_per_trade', 0),
    daily_loss_limit: Math.max(n('daily_loss_limit', 10), 1e-6),
    risk_pct: n('risk_pct', 0),
    available_usd: d.available_usd != null ? n('available_usd', 0) : base.available_usd,
    holdings: Array.isArray(d.holdings) ? (d.holdings as AccountState['holdings']) : base.holdings,
  };
}

export function useAccount() {
  const { data, loading, error, refetch } = usePoll(
    '/api/v1/account',
    5000,
    parseAccount
  );
  return { account: data, loading, error, refetch };
}

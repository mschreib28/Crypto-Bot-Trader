/**
 * Account state as returned by GET /api/v1/account.
 * Matches the account response from backend/api/routes/account.py.
 */
export interface MicroModeStatus {
  active: boolean;
  equity: number;
  threshold: number;
  min_stop_atr: number;
  min_notional: number;
  max_positions: number;
  message: string | null;
}

export interface AccountState {
  initial_equity: number;
  realized_pnl: number;
  current_equity: number;
  total_pnl: number;
  pnl_percent: number;
  daily_pnl: number;
  max_risk_per_trade: number;
  daily_loss_limit: number;
  risk_pct: number;
  micro_mode?: MicroModeStatus;
  live_slots_active?: number;
  live_slots_max?: number;
}

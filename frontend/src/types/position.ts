/**
 * Position as returned by GET /api/v1/positions.
 * Matches the Position model from backend/positions/models.py.
 */
export interface Position {
  symbol: string;
  side: 'long' | 'short';
  quantity: number;
  entry_price: number;
  entry_time: string;
  unrealized_pnl: number;
  current_price: number;
  strategy_id?: string | null;
  strategy_name?: string | null;
}

/**
 * Response from GET /api/v1/positions endpoint.
 */
export interface PositionsResponse {
  positions: Position[];
  total_budget: number;
  budget_used: number;
}

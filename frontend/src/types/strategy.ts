/**
 * Strategy configuration as returned by GET /api/v1/strategies.
 * Matches the StrategyConfig schema from contracts/openapi.yaml.
 */
export interface Strategy {
  strategy_id: string;
  name: string;
  symbol: string;
  interval: string;
  max_risk_pct: number;
  enabled: boolean;
  parameters: Record<string, unknown>;
}

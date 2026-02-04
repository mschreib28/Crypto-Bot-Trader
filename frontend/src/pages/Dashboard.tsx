import { Layout } from '../components/Layout';
import { AccountPanel } from '../components/AccountPanel';
import { ActivityLog } from '../components/ActivityLog';
import { BalancePanel } from '../components/BalancePanel';
import { HealthPanel } from '../components/HealthPanel';
import { PositionPanel } from '../components/PositionPanel';
import { ScreenerPanel } from '../components/ScreenerPanel';
import { StrategyConfigPanel } from '../components/StrategyConfigPanel';
import { StrategyPanel } from '../components/StrategyPanel';
import { ExecutionPreviewPanel } from '../components/ExecutionPreviewPanel';
import { useStrategies } from '../hooks/useStrategies';
import { useShadowLive } from '../hooks/useShadowLive';
import { useTrading } from '../hooks/useTrading';

export function Dashboard() {
  const { strategies, loading, error, refetch: refetchStrategies, toggleStrategy } = useStrategies();
  const { shadowLive } = useShadowLive();
  const { trading } = useTrading();
  
  const isShadowMode = shadowLive?.enabled && !trading?.enabled;

  return (
    <Layout>
      <div className="grid grid-cols-12 gap-3 h-full overflow-hidden">
        {/* LEFT COLUMN: Balance, Account, Positions, Activity */}
        <div className="col-span-12 lg:col-span-3 flex flex-col gap-3 min-h-0 overflow-auto">
          <BalancePanel />
          <AccountPanel />
          {isShadowMode && <ExecutionPreviewPanel />}
          <div className="rounded-lg border border-gray-700 bg-gray-800 p-3 flex-1 min-h-0 overflow-hidden">
            <PositionPanel />
          </div>
          <div className="flex-1 min-h-0">
            <ActivityLog />
          </div>
        </div>

        {/* CENTER COLUMN: Screener (HERO - Widest) */}
        <div className="col-span-12 lg:col-span-6 min-h-0">
          <ScreenerPanel />
        </div>

        {/* RIGHT COLUMN: Setup, Strategies, Health */}
        <div className="col-span-12 lg:col-span-3 flex flex-col gap-3 h-full min-h-0">
          <StrategyConfigPanel onConfigSaved={refetchStrategies} />
          <StrategyPanel 
            strategies={strategies} 
            loading={loading} 
            error={error} 
            onToggle={toggleStrategy}
          />
          <div className="shrink-0">
            <HealthPanel />
          </div>
        </div>
      </div>
    </Layout>
  );
}

import { useState } from 'react';
import { Layout } from '../components/Layout';
import { ActivityLog } from '../components/ActivityLog';
import { PanelErrorBoundary } from '../components/PanelErrorBoundary';
import { ShadowAccountPanel } from '../components/ShadowAccountPanel';
import { HealthPanel } from '../components/HealthPanel';
import { HistoryPanel } from '../components/HistoryPanel';
import { PositionPanel } from '../components/PositionPanel';
import { ScreenerPanel } from '../components/ScreenerPanel';
import { StrategyConfigPanel } from '../components/StrategyConfigPanel';
import { StrategyPanel } from '../components/StrategyPanel';
import { SupervisorPanel } from '../components/SupervisorPanel';
// ExecutionPreviewPanel removed - component not implemented
import { useStrategies } from '../hooks/useStrategies';
import { useScreener } from '../hooks/useScreener';
import { useMetrics } from '../hooks/useMetrics';
import { AnalyticsPanel } from '../components/AnalyticsPanel';

export function Dashboard() {
  const { strategies, loading, error, refetch: refetchStrategies, toggleStrategy } = useStrategies();
  const { totalScanned, loading: screenerLoading } = useScreener({});
  const { metrics } = useMetrics();
  const [showHistory, setShowHistory] = useState(false);

  return (
    <Layout>
      <div className="grid grid-cols-12 gap-3 h-full overflow-hidden">
        {/* LEFT COLUMN: Balance, Account, Positions, Activity */}
        <div className="col-span-12 lg:col-span-3 flex flex-col gap-3 min-h-0 overflow-auto">
          <PanelErrorBoundary title="Account & positions">
            <div className="flex flex-col gap-3 min-h-0">
              <ShadowAccountPanel />
              {/* ExecutionPreviewPanel removed - component not implemented */}
              <div className="rounded-lg border border-gray-700 bg-gray-800 p-3 flex-1 min-h-0 overflow-hidden">
                <PositionPanel />
              </div>
            </div>
          </PanelErrorBoundary>
          <div className="flex-1 min-h-0">
            <ActivityLog />
          </div>
          <button
            onClick={() => setShowHistory(true)}
            className="w-full py-1 text-xs bg-gray-700 hover:bg-gray-600 text-gray-300 hover:text-white rounded border border-gray-600 transition-colors"
          >
            View Full History
          </button>
        </div>

        {/* CENTER COLUMN: Screener (HERO - Widest) */}
        <div className="col-span-12 lg:col-span-6 min-h-0">
          <ScreenerPanel />
        </div>

        {/* RIGHT COLUMN: Setup, Strategies, Supervisor, Health */}
        <div className="col-span-12 lg:col-span-3 flex flex-col gap-3 h-full min-h-0">
          <StrategyConfigPanel onConfigSaved={refetchStrategies} />
          <StrategyPanel
            strategies={strategies}
            loading={loading}
            error={error}
            onToggle={toggleStrategy}
            totalScanned={totalScanned}
            screenerLoading={screenerLoading}
            metrics={metrics}
          />
          <AnalyticsPanel />
          <div className="shrink-0">
            <SupervisorPanel />
          </div>
          <div className="shrink-0">
            <HealthPanel />
          </div>
        </div>
      </div>
      {showHistory && <HistoryPanel onClose={() => setShowHistory(false)} />}
    </Layout>
  );
}

import { useAccount } from '../hooks/useAccount';
import { useMetrics } from '../hooks/useMetrics';
import { useShadowLive } from '../hooks/useShadowLive';
import { useTrading } from '../hooks/useTrading';

const WALLET_BASE_AMOUNT = 31.80;

const formatPnL = (pnl: number) => {
  const sign = pnl >= 0 ? '+' : '';
  return `${sign}$${Math.abs(pnl).toFixed(2)}`;
};

export function AccountPanel() {
  const { account, loading, error } = useAccount();
  const { metrics } = useMetrics();
  const { shadowLive } = useShadowLive();
  const { trading } = useTrading();
  
  const isShadowMode = shadowLive?.enabled && !trading?.enabled;

  if (loading) return <div className="p-3 bg-gray-800 rounded-lg border border-gray-700 text-gray-400 text-xs">Loading...</div>;
  if (error) return <div className="p-3 bg-gray-800 rounded-lg border border-gray-700 text-red-400 text-xs">Error: {error}</div>;
  if (!account) return null;

  // Calculate overall P&L: current equity - initial equity
  // Always calculate directly to ensure accuracy
  // Use Number() to ensure values are treated as numbers, not strings
  const currentEquity = Number(account.current_equity) || 0;
  const initialEquity = Number(account.initial_equity) || 0;
  
  // Force calculation - ensure we're not using realized_pnl which might be 0
  // This is the true overall P&L from account start
  const calculatedPnl = currentEquity - initialEquity;
  const pnlPercent = initialEquity > 0 ? (calculatedPnl / initialEquity) * 100 : 0;
  
  // Calculate profit % of wallet base amount ($31.80)
  const profitPctOfWallet = ((currentEquity - WALLET_BASE_AMOUNT) / WALLET_BASE_AMOUNT) * 100;
  
  // Debug: Log calculation to console (temporary - remove after verification)
  // Version: 2026-01-30-17:15 - Force cache bust
  if (typeof window !== 'undefined') {
    console.log('[AccountPanel] P&L Calculation Debug:', {
      current_equity_raw: account.current_equity,
      initial_equity_raw: account.initial_equity,
      current_equity_parsed: currentEquity,
      initial_equity_parsed: initialEquity,
      calculated_pnl: calculatedPnl,
      pnl_percent: pnlPercent,
      account_total_pnl: account.total_pnl,
      account_realized_pnl: account.realized_pnl,
      account_object: account
    });
  }
  const totalPnlColor = calculatedPnl >= 0 ? 'text-green-400' : 'text-red-400';
  const profitPctOfWalletColor = profitPctOfWallet >= 0 ? 'text-green-400' : profitPctOfWallet < 0 ? 'text-red-400' : 'text-gray-400';
  const dailyPnlColor = account.daily_pnl >= 0 ? 'text-green-400' : 'text-red-400';
  const lossProgress = Math.min(100, (Math.abs(Math.min(0, account.daily_pnl)) / account.daily_loss_limit) * 100);
  const progressColor = lossProgress > 80 ? 'bg-red-500' : lossProgress > 50 ? 'bg-yellow-500' : 'bg-green-500';

  return (
    <div className={`rounded-lg p-3 border ${
      isShadowMode 
        ? 'bg-gray-800/50 border-blue-700/50' 
        : 'bg-gray-800 border-gray-700'
    }`}>
      <div className="flex items-center justify-between mb-2">
        <h3 className={`text-sm font-semibold ${
          isShadowMode ? 'text-blue-300 italic' : 'text-white'
        }`}>
          Account
        </h3>
        {isShadowMode && (
          <span className="text-[10px] text-blue-400 italic">(Shadow Mode)</span>
        )}
      </div>
      
      <div className="flex justify-between items-baseline mb-1">
        <span className="text-gray-500 text-xs">Equity</span>
        <span className="font-mono text-base text-white">${(account.current_equity ?? 0).toFixed(2)}</span>
      </div>
      
      <div className="text-xs text-gray-500 mb-2">
        Init: ${(account.initial_equity ?? 0).toFixed(2)}
      </div>
      
      {/* Overall P&L Section - Prominent */}
      <div className="border-t border-gray-700 pt-2 pb-2 mb-2 bg-gray-900/50 rounded px-2 -mx-2">
        <div className="flex justify-between items-baseline mb-1">
          <span className="text-gray-400 text-xs font-medium">Overall P&L</span>
          <div className="flex items-baseline gap-2">
            <span className={`font-mono text-base font-bold ${totalPnlColor}`}>
              {formatPnL(calculatedPnl)}
            </span>
            <span className={`text-xs font-mono ${totalPnlColor}`}>
              ({pnlPercent >= 0 ? '+' : ''}{pnlPercent.toFixed(1)}%)
            </span>
          </div>
        </div>
        <div className="flex justify-between items-baseline">
          <span className="text-gray-400 text-xs font-medium">Growth</span>
          <span className={`text-xs font-mono font-semibold ${profitPctOfWalletColor}`}>
            {profitPctOfWallet >= 0 ? '+' : ''}{profitPctOfWallet.toFixed(2)}% of $31.80 base
          </span>
        </div>
      </div>
      
      <div className="border-t border-gray-700 pt-2 space-y-1">
        <div className="flex justify-between text-xs">
          <span className="text-gray-500">Win Rate</span>
          <span className="font-mono text-gray-300">
            {metrics && metrics.overall_accuracy_pct != null ? `${metrics.overall_accuracy_pct.toFixed(1)}%` : '—'}
          </span>
        </div>
        
        <div className="flex justify-between text-xs">
          <span className="text-gray-500">Risk ({account.risk_pct ?? 0}%)</span>
          <span className="font-mono text-gray-300">${(account.max_risk_per_trade ?? 0).toFixed(2)}/trade</span>
        </div>
      </div>
      
      <div className="border-t border-gray-700 pt-2 mt-2">
        <div className="flex justify-between text-xs mb-1">
          <span className="text-gray-500">Today</span>
          <span className={`font-mono ${dailyPnlColor}`}>
            {(account.daily_pnl ?? 0) >= 0 ? '+' : ''}${(account.daily_pnl ?? 0).toFixed(2)}
          </span>
        </div>
        <div className="w-full bg-gray-700 rounded-full h-1.5">
          <div 
            className={`h-1.5 rounded-full ${progressColor}`}
            style={{ width: `${100 - lossProgress}%` }}
          />
        </div>
        <div className="text-[10px] text-gray-500 text-right mt-0.5">
          Limit: -${(account.daily_loss_limit ?? 0).toFixed(2)}
        </div>
      </div>
    </div>
  );
}

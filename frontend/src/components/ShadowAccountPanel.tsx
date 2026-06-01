import { useState } from 'react';
import { useBalance, Holding } from '../hooks/useBalance';
import { useAccount } from '../hooks/useAccount';
import { useMetrics } from '../hooks/useMetrics';
import { useShadowLive } from '../hooks/useShadowLive';
import { useTrading } from '../hooks/useTrading';
import { useShadowBalance } from '../hooks/useShadowBalance';
import { usePositions } from '../hooks/usePositions';
import { ShadowBalanceModal } from './ShadowBalanceModal';

function formatCurrency(value: number): string {
  const n = Number.isFinite(value) ? value : 0;
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n);
}

function formatAmount(value: number): string {
  if (value < 0.0001) return value.toExponential(2);
  if (value < 1) return value.toFixed(4);
  if (value < 1000) return value.toFixed(2);
  return value.toLocaleString('en-US', { maximumFractionDigits: 0 });
}

const formatPnL = (pnl: number) => {
  const sign = pnl >= 0 ? '+' : '';
  return `${sign}$${Math.abs(pnl).toFixed(2)}`;
};

const MIN_HOLDING_VALUE = 0.01;

function isUsdCashSymbol(symbol: string): boolean {
  return symbol === 'USD' || symbol === 'ZUSD';
}

function HoldingRow({ holding }: { holding: Holding }) {
  const { symbol, quantity, value_usd } = holding;
  return (
    <div className="flex justify-between text-xs py-0.5">
      <span className="text-gray-500">
        {symbol}: <span className="text-gray-300 font-mono">{formatAmount(quantity)}</span>
      </span>
      <span className="text-gray-300 font-mono">{formatCurrency(value_usd)}</span>
    </div>
  );
}

export function ShadowAccountPanel() {
  const { balance, loading: balanceLoading, error: balanceError, refetch: refetchBalance } =
    useBalance();
  const { account, loading: accountLoading, error: accountError, refetch: refetchAccount } =
    useAccount();
  const { metrics, refetch: refetchMetrics } = useMetrics();
  const { shadowLive } = useShadowLive();
  const { trading } = useTrading();
  const { setShadowBalance, loading: settingBalance } = useShadowBalance();
  const { refetch: refetchPositions } = usePositions();
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [showModal, setShowModal] = useState(false);

  const isShadowMode = shadowLive?.enabled === true;
  const isLiveMode = trading?.enabled && !shadowLive?.enabled;

  const hasCoreData = balance !== null && account !== null;
  const showInitialLoading =
    !hasCoreData && (balanceLoading || accountLoading);

  const initialEquity = account ? Number(account.initial_equity) || 0 : 0;
  const totalPnl = account
    ? (() => {
        const fromApi = Number(account.total_pnl);
        if (Number.isFinite(fromApi)) return fromApi;
        const eq = Number(account.current_equity);
        return Number.isFinite(eq) ? eq - initialEquity : 0;
      })()
    : 0;
  const pnlPercent = account && initialEquity > 0 ? (totalPnl / initialEquity) * 100 : 0;
  const totalPnlColor = totalPnl >= 0 ? 'text-green-400' : 'text-red-400';
  const dailyPnl = account != null ? Number(account.daily_pnl) : 0;
  const dailyLossLimit =
    account != null && Number.isFinite(Number(account.daily_loss_limit)) && Number(account.daily_loss_limit) > 0
      ? Number(account.daily_loss_limit)
      : 1;
  const dailyPnlColor =
    account != null && Number.isFinite(dailyPnl) && dailyPnl >= 0 ? 'text-green-400' : 'text-red-400';
  const lossProgress = account
    ? Math.min(100, (Math.abs(Math.min(0, dailyPnl)) / dailyLossLimit) * 100)
    : 0;
  const progressColor =
    lossProgress > 80 ? 'bg-red-500' : lossProgress > 50 ? 'bg-yellow-500' : 'bg-green-500';

  const nonUsdHoldings = (balance?.holdings ?? []).filter(
    (h) => h && typeof h.symbol === 'string' && !isUsdCashSymbol(h.symbol) && Number(h.value_usd) >= MIN_HOLDING_VALUE
  );

  const onModalConfirm = async (amount: number) => {
    const result = await setShadowBalance(amount);
    if (result) {
      await refetchBalance();
      await refetchAccount();
      await refetchPositions();
      await refetchMetrics();
      return {
        success: true,
        positionsClosed: result.positions_closed ?? 0,
      };
    }
    return { success: false, positionsClosed: 0 };
  };

  const titleId = 'shadow-account-panel-title';

  return (
    <section
      className={`rounded-lg p-3 border ${
        isShadowMode
          ? 'bg-gray-800/50 border-blue-700/50'
          : isLiveMode
            ? 'bg-gray-800 border-gray-700'
            : 'bg-gray-800 border-gray-700'
      }`}
      aria-labelledby={titleId}
    >
      <div className="flex justify-between items-center gap-2 mb-2">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 min-w-0">
          <h2
            id={titleId}
            className={`text-sm font-semibold ${
              isShadowMode ? 'text-blue-300 italic' : 'text-white'
            }`}
          >
            {isShadowMode ? 'Shadow Account' : 'Live Account'}
          </h2>
          {isShadowMode && (
            <span className="text-[10px] text-blue-400 italic shrink-0">(Shadow Mode)</span>
          )}
        </div>
        {isShadowMode && (
          <button
            type="button"
            onClick={() => setShowResetConfirm(true)}
            className="text-xs px-2 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1 focus:ring-offset-gray-800 font-medium shrink-0"
            title="Reset shadow balance (will reset all shadow positions)"
          >
            Reset
          </button>
        )}
      </div>

      {showResetConfirm && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="shadow-reset-confirm-title"
        >
          <div
            className="fixed inset-0 bg-black/70 transition-opacity"
            onClick={() => setShowResetConfirm(false)}
            aria-hidden="true"
          />
          <div className="relative z-10 w-full max-w-sm rounded-lg bg-gray-800 p-5 shadow-xl border border-gray-700">
            <h3 id="shadow-reset-confirm-title" className="text-sm font-semibold text-white mb-3">
              Reset shadow account
            </h3>
            <p className="text-sm text-gray-300 mb-5">
              This will close all open positions and reset all stats. Are you sure?
            </p>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowResetConfirm(false)}
                className="rounded-md bg-gray-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-gray-500 focus:outline-none focus:ring-2 focus:ring-gray-400 focus:ring-offset-2 focus:ring-offset-gray-800"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  setShowResetConfirm(false);
                  setShowModal(true);
                }}
                className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 focus:ring-offset-gray-800"
              >
                Continue
              </button>
            </div>
          </div>
        </div>
      )}

      <ShadowBalanceModal
        isOpen={showModal}
        currentBalance={balance?.total_usd}
        onConfirm={onModalConfirm}
        onCancel={() => setShowModal(false)}
        loading={settingBalance}
      />

      {showInitialLoading && (
        <div className="text-gray-400 text-xs">Loading...</div>
      )}

      {balanceError && (
        <div className="rounded border border-red-800 bg-red-900/20 p-2 text-red-400 text-xs mb-2">
          Balance: {balanceError}
        </div>
      )}
      {accountError && (
        <div className="rounded border border-red-800 bg-red-900/20 p-2 text-red-400 text-xs mb-2">
          Account: {accountError}
        </div>
      )}

      {hasCoreData && account && balance && (
        <div className="space-y-2">
          <div className="flex justify-between items-baseline">
            <span className="text-gray-500 text-xs">Equity</span>
            <span className="font-mono text-base text-white">
              {formatCurrency(Number(account.current_equity) || 0)}
            </span>
          </div>

          <div className="flex justify-between items-baseline">
            <span
              className={`text-xs ${isShadowMode ? 'text-gray-400 italic' : 'text-gray-500'}`}
            >
              Available
            </span>
            <span
              className={`font-mono text-sm ${isShadowMode ? 'text-blue-300' : 'text-gray-300'}`}
            >
              {formatCurrency(
                Number(balance.available_usd ?? balance.total_usd) || 0
              )}
            </span>
          </div>

          <div className="border-t border-gray-700 pt-2 flex justify-between items-baseline gap-2">
            <span className="text-gray-400 text-xs font-medium shrink-0">P&L</span>
            <div className="flex items-baseline gap-2 flex-wrap justify-end">
              <span className={`font-mono text-sm font-bold ${totalPnlColor}`}>
                {formatPnL(totalPnl)}
              </span>
              <span className={`text-xs font-mono ${totalPnlColor}`}>
                ({pnlPercent >= 0 ? '+' : ''}
                {pnlPercent.toFixed(1)}%)
              </span>
            </div>
          </div>

          {nonUsdHoldings.length > 0 && (
            <div className="border-t border-gray-700 pt-2">
              <span className="text-gray-500 text-xs block mb-1">Holdings</span>
              <div className="space-y-0.5 max-h-24 overflow-y-auto">
                {nonUsdHoldings.map((holding) => (
                  <HoldingRow key={holding.symbol} holding={holding} />
                ))}
              </div>
            </div>
          )}

          <div className="border-t border-gray-700 pt-2">
            <div className="flex justify-between items-center gap-2 text-xs min-w-0">
              <span className="text-gray-500 truncate">
                Win Rate{' '}
                <span className="font-mono text-gray-300">
                  {metrics != null && Number.isFinite(metrics.overall_accuracy_pct)
                    ? `${metrics.overall_accuracy_pct.toFixed(1)}%`
                    : '—'}
                </span>
              </span>
              <span className="text-gray-500 truncate text-right">
                Risk ({Number(account.risk_pct) || 0}%){' '}
                <span className="font-mono text-gray-300">
                  {formatCurrency(Number(account.max_risk_per_trade) || 0)}/trade
                </span>
              </span>
            </div>
          </div>

          <div className="border-t border-gray-700 pt-2">
            <div className="flex justify-between text-xs mb-1">
              <span className="text-gray-500">Today</span>
              <span className={`font-mono ${dailyPnlColor}`}>
                {dailyPnl >= 0 ? '+' : ''}${Number.isFinite(dailyPnl) ? dailyPnl.toFixed(2) : '0.00'}
              </span>
            </div>
            <div className="w-full bg-gray-700 rounded-full h-1.5">
              <div
                className={`h-1.5 rounded-full ${progressColor}`}
                style={{ width: `${100 - lossProgress}%` }}
              />
            </div>
            <div className="text-[10px] text-gray-500 text-right mt-0.5">
              Limit: -${dailyLossLimit.toFixed(2)}
            </div>
          </div>
        </div>
      )}

      {!showInitialLoading && !hasCoreData && !balanceError && !accountError && (
        <div className="text-gray-400 text-xs">No data</div>
      )}
    </section>
  );
}

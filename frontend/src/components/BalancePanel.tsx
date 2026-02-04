import { useState } from 'react';
import { useBalance, Holding } from '../hooks/useBalance';
import { useShadowLive } from '../hooks/useShadowLive';
import { useTrading } from '../hooks/useTrading';
import { useShadowBalance } from '../hooks/useShadowBalance';

function formatCurrency(value: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function formatAmount(value: number): string {
  if (value < 0.0001) return value.toExponential(2);
  if (value < 1) return value.toFixed(4);
  if (value < 1000) return value.toFixed(2);
  return value.toLocaleString('en-US', { maximumFractionDigits: 0 });
}

interface HoldingRowProps {
  holding: Holding;
}

function HoldingRow({ holding }: HoldingRowProps) {
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

export function BalancePanel() {
  const { balance, loading, error, refetch } = useBalance();
  const { shadowLive } = useShadowLive();
  const { trading } = useTrading();
  const { setShadowBalance, loading: settingBalance } = useShadowBalance();
  const [showSetBalance, setShowSetBalance] = useState(false);
  const [balanceInput, setBalanceInput] = useState('');
  
  const isShadowMode = shadowLive?.enabled && !trading?.enabled;
  const isLiveMode = trading?.enabled && !shadowLive?.enabled;
  
  const handleSetBalance = async () => {
    const value = parseFloat(balanceInput);
    if (isNaN(value) || value < 0) {
      return;
    }
    const success = await setShadowBalance(value);
    if (success) {
      setShowSetBalance(false);
      setBalanceInput('');
      refetch(); // Refresh balance display
    }
  };

  return (
    <section
      className={`rounded-lg p-3 border ${
        isShadowMode 
          ? 'bg-gray-800/50 border-blue-700/50' 
          : isLiveMode
          ? 'bg-gray-800 border-gray-700'
          : 'bg-gray-800 border-gray-700'
      }`}
      aria-labelledby="balance-panel-title"
    >
      <div className="flex justify-between items-center mb-2">
        <h2
          id="balance-panel-title"
          className={`text-sm font-semibold ${
            isShadowMode 
              ? 'text-blue-300 italic' 
              : 'text-white'
          }`}
        >
          {isShadowMode ? 'Reference Balance (Shadow)' : 'Live Balance'}
        </h2>
        {isShadowMode && (
          <button
            onClick={() => setShowSetBalance(!showSetBalance)}
            className="text-xs px-2 py-1 bg-blue-600 hover:bg-blue-700 text-white rounded transition-colors"
            title="Set shadow balance"
          >
            Set
          </button>
        )}
      </div>

      {isShadowMode && showSetBalance && (
        <div className="mb-2 p-2 bg-gray-700/50 rounded border border-blue-600/50">
          <div className="flex gap-2 items-center">
            <input
              type="number"
              step="0.01"
              min="0"
              placeholder="Balance (USD)"
              value={balanceInput}
              onChange={(e) => setBalanceInput(e.target.value)}
              className="flex-1 px-2 py-1 bg-gray-800 border border-gray-600 rounded text-xs text-white placeholder-gray-500"
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  handleSetBalance();
                } else if (e.key === 'Escape') {
                  setShowSetBalance(false);
                  setBalanceInput('');
                }
              }}
            />
            <button
              onClick={handleSetBalance}
              disabled={settingBalance || !balanceInput}
              className="px-2 py-1 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 disabled:cursor-not-allowed text-white text-xs rounded transition-colors"
            >
              {settingBalance ? '...' : 'Set'}
            </button>
            <button
              onClick={() => {
                setShowSetBalance(false);
                setBalanceInput('');
              }}
              className="px-2 py-1 bg-gray-600 hover:bg-gray-700 text-white text-xs rounded transition-colors"
            >
              ×
            </button>
          </div>
        </div>
      )}

      {loading && (
        <div className="text-gray-400 text-xs">Loading...</div>
      )}

      {error && (
        <div className="rounded border border-red-800 bg-red-900/20 p-2 text-red-400 text-xs">
          {error}
        </div>
      )}

      {!loading && !error && balance && (
        <div className="space-y-1">
          <div className="flex justify-between items-baseline">
            <span className={`text-xs ${isShadowMode ? 'text-gray-400 italic' : 'text-gray-500'}`}>
              {isShadowMode ? 'Reference Total' : 'Total'}
            </span>
            <span className={`font-mono text-lg ${isShadowMode ? 'text-blue-200' : 'text-white'}`}>
              {formatCurrency(balance.total_usd)}
            </span>
          </div>

          <div className="flex justify-between items-baseline">
            <span className={`text-xs ${isShadowMode ? 'text-gray-400 italic' : 'text-gray-500'}`}>
              {isShadowMode ? 'Reference Available' : 'Available'}
            </span>
            <span className={`font-mono text-sm ${isShadowMode ? 'text-blue-300' : 'text-gray-300'}`}>
              {formatCurrency(balance.available_usd)}
            </span>
          </div>

          {balance.holdings.length > 0 && (
            <div className="border-t border-gray-700 pt-2 mt-2">
              <span className="text-gray-500 text-xs block mb-1">Holdings</span>
              <div className="space-y-0.5 max-h-24 overflow-y-auto">
                {balance.holdings.map((holding) => (
                  <HoldingRow key={holding.symbol} holding={holding} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {!loading && !error && !balance && (
        <div className="text-gray-400 text-xs">No data</div>
      )}
    </section>
  );
}

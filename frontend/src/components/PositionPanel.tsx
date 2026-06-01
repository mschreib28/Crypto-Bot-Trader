import { usePositions } from '../hooks/usePositions';
import { useAccount } from '../hooks/useAccount';
import { useShadowLive } from '../hooks/useShadowLive';
import { useTrading } from '../hooks/useTrading';
import { Position } from '../types/position';
import { getStrategyDisplayName } from '../utils/strategyLabels';

function isValidNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function formatCurrency(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '$0.00';
  return `$${value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatPnlPercent(pnl: number, entryPrice: number, quantity: number): string {
  if (!isValidNumber(pnl) || !isValidNumber(entryPrice) || !isValidNumber(quantity)) return '--%';
  const entryValue = entryPrice * quantity;
  if (entryValue === 0) return '0%';
  const pct = (pnl / entryValue) * 100;
  if (!Number.isFinite(pct)) return '--%';
  const sign = pct >= 0 ? '+' : '';
  return `${sign}${pct.toFixed(1)}%`;
}

interface PositionRowProps {
  position: Position;
  isEven: boolean;
  onClose: (symbol: string) => void;
  isShadowMode: boolean;
}

function PositionRow({ position, isEven, onClose, isShadowMode }: PositionRowProps) {
  const { symbol, side, quantity, entry_price, unrealized_pnl, strategy_name } = position;
  const hasPnl = isValidNumber(unrealized_pnl);
  const isProfit = hasPnl && unrealized_pnl >= 0;
  const pnlColor = hasPnl ? (isProfit ? 'text-green-400' : 'text-red-400') : 'text-gray-400';
  const sideColor = side === 'long' ? 'text-green-400' : 'text-red-400';
  const rowBg = isEven ? 'bg-gray-800/50' : '';
  const totalCost = isValidNumber(quantity) && isValidNumber(entry_price) ? quantity * entry_price : null;

  const displayStrategy = strategy_name ? getStrategyDisplayName(strategy_name) : '—';

  return (
    <tr className={`border-b border-gray-700/30 ${rowBg}`}>
      <td className="py-1.5 pr-2 text-gray-200 text-xs font-medium">
        {(symbol || '').split('/')[0] || '—'}
      </td>
      <td className={`py-1.5 pr-2 text-xs capitalize font-medium ${sideColor}`}>{side}</td>
      <td className="py-1.5 pr-2 text-gray-300 text-xs text-right font-mono">
        {isValidNumber(quantity) ? (
          <>
            <div>{quantity.toFixed(4)}</div>
            <div className="text-[10px] text-gray-500">
              @ {isValidNumber(entry_price) ? formatCurrency(entry_price) : '—'}
            </div>
          </>
        ) : (
          <>
            <div>—</div>
            <div className="text-[10px] text-gray-500">@ —</div>
          </>
        )}
      </td>
      <td className="py-1.5 pr-2 text-gray-200 text-xs text-right font-mono font-semibold">
        {totalCost !== null ? formatCurrency(totalCost) : '—'}
      </td>
      <td className={`py-1.5 pr-2 text-xs ${
        strategy_name
          ? 'text-gray-200 font-medium'
          : 'text-gray-500 italic'
      }`}>
        {displayStrategy}
      </td>
      <td className={`py-1.5 pr-2 text-xs text-right font-mono font-semibold ${pnlColor}`}>
        {hasPnl ? (
          <>
            <div>{unrealized_pnl >= 0 ? '+' : ''}{formatCurrency(unrealized_pnl)}</div>
            <div className="text-[10px]">{formatPnlPercent(unrealized_pnl, entry_price, quantity)}</div>
          </>
        ) : '—'}
      </td>
      <td className="py-1.5 text-right">
        <button
          onClick={() => onClose(symbol)}
          className={`text-xs px-2 py-0.5 rounded transition-colors ${
            isShadowMode
              ? 'bg-red-600/50 hover:bg-red-600 text-red-200'
              : 'bg-red-600 hover:bg-red-700 text-white'
          }`}
          title={`Close ${symbol} position`}
        >
          ×
        </button>
      </td>
    </tr>
  );
}

export function PositionPanel() {
  const { positions, loading, error, refetch, closePosition } = usePositions();
  const { account } = useAccount();
  const { shadowLive } = useShadowLive();
  const { trading } = useTrading();

  const isShadowMode = shadowLive?.enabled && !trading?.enabled;
  
  const handleClosePosition = async (symbol: string) => {
    if (confirm(`Close position ${symbol}?`)) {
      if (closePosition) {
        const success = await closePosition(symbol);
        if (success) {
          refetch(); // Refresh positions list
        }
      }
    }
  };

  const totalPositionValue = positions.reduce((sum, p) => {
    const qty = isValidNumber(p.quantity) ? p.quantity : 0;
    const price = isValidNumber(p.current_price) ? p.current_price : (isValidNumber(p.entry_price) ? p.entry_price : 0);
    return sum + qty * price;
  }, 0);

  const accountEquity = account?.current_equity ?? null;
  const percent = accountEquity && accountEquity > 0 ? Math.min((totalPositionValue / accountEquity) * 100, 100) : 0;

  const totalPnl = positions.reduce((sum, p) => {
    const pnl = isValidNumber(p.unrealized_pnl) ? p.unrealized_pnl : 0;
    return sum + pnl;
  }, 0);
  const hasValidPnl = positions.some(p => isValidNumber(p.unrealized_pnl));
  const pnlColor = hasValidPnl ? (totalPnl >= 0 ? 'text-green-400' : 'text-red-400') : 'text-gray-400';

  return (
    <section aria-labelledby="position-panel-title" className="h-full flex flex-col overflow-hidden">
      <h2 
        id="position-panel-title" 
        className={`mb-2 text-sm font-semibold shrink-0 ${
          isShadowMode ? 'text-blue-300 italic' : 'text-white'
        }`}
      >
        {isShadowMode ? 'Simulated Positions' : 'Positions'}
      </h2>

      {loading && <div className="text-gray-400 text-xs">Loading...</div>}

      {error && (
        <div className="rounded border border-red-800 bg-red-900/20 p-2 text-red-400 text-xs">
          {error}
        </div>
      )}

      {!loading && !error && positions.length === 0 && (
        <div className="text-gray-400 text-xs flex-1">No positions</div>
      )}

      {!loading && !error && positions.length > 0 && (
        <div className="overflow-auto flex-1 min-h-0">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-gray-800 z-10">
              <tr className="text-left text-gray-400 border-b border-gray-600">
                <th className="pb-2 pr-2 font-semibold text-xs uppercase tracking-wide">Asset</th>
                <th className="pb-2 pr-2 font-semibold text-xs uppercase tracking-wide">Side</th>
                <th className="pb-2 pr-2 font-semibold text-xs uppercase tracking-wide text-right">Qty @ Entry</th>
                <th className="pb-2 pr-2 font-semibold text-xs uppercase tracking-wide text-right">Cost</th>
                <th className="pb-2 pr-2 font-semibold text-xs uppercase tracking-wide">Strategy</th>
                <th className="pb-2 pr-2 font-semibold text-xs uppercase tracking-wide text-right">P&L</th>
                <th className="pb-2 font-semibold text-xs uppercase tracking-wide text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((position, idx) => (
                <PositionRow 
                  key={position.symbol} 
                  position={position} 
                  isEven={idx % 2 === 0}
                  onClose={handleClosePosition}
                  isShadowMode={isShadowMode ?? false}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Budget bar */}
      <div className="pt-2 border-t border-gray-700 shrink-0">
        <div className="flex justify-between text-xs mb-1">
          <span className="text-gray-500">Exposure</span>
          <span className={pnlColor}>{hasValidPnl ? `${totalPnl >= 0 ? '+' : ''}${formatCurrency(totalPnl)}` : '—'}</span>
        </div>
        <div className="w-full bg-gray-700 rounded-full h-1.5">
          <div
            className="h-1.5 rounded-full bg-blue-500 transition-all"
            style={{ width: `${percent}%` }}
          />
        </div>
        <div className="text-[10px] text-gray-500 text-right mt-0.5">
          {formatCurrency(totalPositionValue)} / {accountEquity ? formatCurrency(accountEquity) : '—'}
        </div>
      </div>
    </section>
  );
}

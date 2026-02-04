import { useOrders, Order } from '../hooks/useOrders';

function formatTime(isoString: string | null | undefined): string {
  if (!isoString) return '--:--';
  try {
    const date = new Date(isoString);
    if (isNaN(date.getTime())) return '--:--';
    return date.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    });
  } catch {
    return '--:--';
  }
}

function formatPrice(value: number): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--';
  return value.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function formatQuantity(value: number): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--';
  return value.toFixed(4);
}

interface OrderRowProps {
  order: Order;
  isEven: boolean;
}

function OrderRow({ order, isEven }: OrderRowProps) {
  const sideColor = order.side === 'buy' ? 'text-green-400' : 'text-red-400';
  const rowBg = isEven ? 'bg-gray-800/50' : '';

  return (
    <tr className={`border-b border-gray-700/30 ${rowBg}`}>
      <td className="py-1 pr-2 text-gray-400 text-xs">{formatTime(order.executed_at)}</td>
      <td className="py-1 pr-2 text-gray-200 text-xs">{order.symbol}</td>
      <td className={`py-1 pr-2 text-xs capitalize ${sideColor}`}>{order.side}</td>
      <td className="py-1 pr-2 text-gray-300 text-xs text-right font-mono">{formatQuantity(order.quantity)}</td>
      <td className="py-1 text-gray-300 text-xs text-right font-mono">{formatPrice(order.price)}</td>
    </tr>
  );
}

export function OrderPanel() {
  const { orders, loading, error } = useOrders();
  const displayOrders = orders.slice(0, 8);

  return (
    <div className="rounded-lg border border-gray-700 bg-gray-800 p-3 h-full flex flex-col overflow-hidden">
      <h2 className="mb-2 text-sm font-semibold text-white shrink-0">Orders</h2>

      {loading && (
        <p className="text-xs text-gray-400">Loading...</p>
      )}

      {error && (
        <div className="rounded border border-red-800 bg-red-900/20 p-2 text-xs text-red-400">
          {error}
        </div>
      )}

      {!loading && !error && displayOrders.length === 0 && (
        <p className="text-xs text-gray-400">No orders yet</p>
      )}

      {!loading && !error && displayOrders.length > 0 && (
        <div className="overflow-auto flex-1 min-h-0">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-gray-800">
              <tr className="text-left text-gray-500 border-b border-gray-600">
                <th className="pb-1 pr-2 font-medium">Time</th>
                <th className="pb-1 pr-2 font-medium">Pair</th>
                <th className="pb-1 pr-2 font-medium">Side</th>
                <th className="pb-1 pr-2 font-medium text-right">Qty</th>
                <th className="pb-1 font-medium text-right">Price</th>
              </tr>
            </thead>
            <tbody>
              {displayOrders.map((order, idx) => (
                <OrderRow key={order.id} order={order} isEven={idx % 2 === 0} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

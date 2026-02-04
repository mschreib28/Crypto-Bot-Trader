import { useEffect, useState } from 'react';
import { useActivity } from '../hooks/useActivity';
import { useStrategies } from '../hooks/useStrategies';

interface ExecutionPreview {
  symbol: string;
  side: string;
  quantity: number;
  price: number;
  position_size_usd: number;
  max_risk_usd: number;
  stop_loss_price: number;
  stop_loss_pct: number;
  strategy: string;
  equity: number;
  tp1_price?: number;
  tp2_price?: number;
  timestamp: string;
}

export function ExecutionPreviewPanel() {
  const { activities } = useActivity();
  const { strategies } = useStrategies();
  const [previews, setPreviews] = useState<ExecutionPreview[]>([]);

  // Build UUID -> name map from strategies
  const strategyMap = new Map<string, string>(
    strategies.map((s) => [s.strategy_id, s.name])
  );

  useEffect(() => {
    // Extract ORDER_INTENT entries from activity log
    const intents = activities
      .filter((activity) => activity.type === 'ORDER_INTENT')
      .map((activity) => {
        const details = activity.details as Record<string, unknown> | null;
        if (!details) return null;

        const strategyId = details.strategy as string | undefined;
        const strategyName = strategyId ? strategyMap.get(strategyId) || strategyId : 'Unknown';

        // Safe numeric extraction with null checks
        const safeNumber = (val: unknown, fallback: number = 0): number => {
          if (typeof val === 'number' && Number.isFinite(val)) return val;
          return fallback;
        };

        return {
          symbol: (details.symbol as string) || 'Unknown',
          side: (details.side as string) || 'buy',
          quantity: safeNumber(details.quantity),
          price: safeNumber(details.price),
          position_size_usd: safeNumber(details.position_size_usd),
          max_risk_usd: safeNumber(details.max_risk_usd),
          stop_loss_price: safeNumber(details.stop_loss_price),
          stop_loss_pct: safeNumber(details.stop_loss_pct),
          strategy: strategyName,
          equity: safeNumber(details.equity),
          tp1_price: details.tp1_price != null ? safeNumber(details.tp1_price) : undefined,
          tp2_price: details.tp2_price != null ? safeNumber(details.tp2_price) : undefined,
          timestamp: activity.timestamp,
        } as ExecutionPreview;
      })
      .filter((p): p is ExecutionPreview => p !== null)
      .slice(-5) // Show last 5 intents
      .reverse(); // Newest first

    setPreviews(intents);
  }, [activities, strategyMap]);

  if (previews.length === 0) {
    return null;
  }

  return (
    <section className="bg-gray-800 rounded-lg p-3 border border-blue-700/50">
      <h3 className="text-sm font-semibold text-blue-300 mb-2">
        Execution Preview (Shadow)
      </h3>
      <div className="space-y-2 max-h-64 overflow-y-auto">
        {previews.map((preview, idx) => (
          <div
            key={`${preview.timestamp}-${idx}`}
            className="bg-gray-900/50 rounded p-2 border border-blue-800/30 text-xs"
          >
            <div className="flex items-center justify-between mb-1">
              <span className="font-semibold text-blue-200">
                {preview.side.toUpperCase()} {preview.symbol}
              </span>
              <span className="text-gray-400 text-[10px]">
                {new Date(preview.timestamp).toLocaleTimeString('en-US', {
                  hour: '2-digit',
                  minute: '2-digit',
                })}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-1 text-gray-300">
              <div>
                <span className="text-gray-500">Size:</span>{' '}
                <span className="font-mono">
                  ${(preview.position_size_usd ?? 0).toFixed(2)} ({(preview.quantity ?? 0).toFixed(4)})
                </span>
              </div>
              <div>
                <span className="text-gray-500">Entry:</span>{' '}
                <span className="font-mono">${(preview.price ?? 0).toFixed(2)}</span>
              </div>
              <div>
                <span className="text-gray-500">Stop:</span>{' '}
                <span className="font-mono text-orange-300">
                  ${(preview.stop_loss_price ?? 0).toFixed(2)} ({(preview.stop_loss_pct ?? 0).toFixed(1)}%)
                </span>
              </div>
              <div>
                <span className="text-gray-500">Risk:</span>{' '}
                <span className="font-mono text-yellow-300">
                  ${(preview.max_risk_usd ?? 0).toFixed(2)}
                </span>
              </div>
              {(preview.tp1_price != null || preview.tp2_price != null) && (
                <div className="col-span-2">
                  <span className="text-gray-500">TP:</span>{' '}
                  <span className="font-mono text-purple-300">
                    TP1: {preview.tp1_price != null ? `$${preview.tp1_price.toFixed(2)}` : 'N/A'}
                    {preview.tp2_price != null && `, TP2: $${preview.tp2_price.toFixed(2)}`}
                  </span>
                </div>
              )}
              <div className="col-span-2">
                <span className="text-gray-500">Strategy:</span>{' '}
                <span className="text-blue-300">{preview.strategy}</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

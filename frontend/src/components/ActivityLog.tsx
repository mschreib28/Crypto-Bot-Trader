import { useEffect, useRef, useState } from 'react';
import { useActivity, ActivityType } from '../hooks/useActivity';
import { useStrategies } from '../hooks/useStrategies';
import { useShadowLive } from '../hooks/useShadowLive';
import { useTrading } from '../hooks/useTrading';

const formatTime = (iso: string): string =>
  new Date(iso).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' });

const TRUNCATE_THRESHOLD = 80;

// UUID pattern: 8-4-4-4-12 hex characters (full or partial with '...')
const UUID_PATTERN = /\b([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\b|\b([a-f0-9]{8}-\.{3})\b/gi;

function isTruncated(message: string): boolean {
  return message.length > TRUNCATE_THRESHOLD || message.includes('...');
}

function replaceUuidsWithNames(
  message: string,
  strategyMap: Map<string, string>
): string {
  return message.replace(UUID_PATTERN, (match, fullUuid, partialUuid) => {
    if (fullUuid) {
      const name = strategyMap.get(fullUuid);
      return name || match;
    }
    if (partialUuid) {
      // Handle partial UUIDs like "b7edab92-..."
      const prefix = partialUuid.replace('-...', '');
      for (const [uuid, name] of strategyMap) {
        if (uuid.startsWith(prefix)) {
          return name;
        }
      }
    }
    return match;
  });
}

function getTypeColor(type: ActivityType): string {
  switch (type) {
    case 'signal':
    case 'SIGNAL_CONFIRMED':
      return 'text-blue-400';
    case 'EXECUTION_ALLOWED':
      return 'text-lime-400'; // Lime green for execution allowed (passed all gates)
    case 'EXIT_FORCED':
      return 'text-red-400'; // Red for forced exits (max hold, invalidation)
    case 'order':
    case 'TRADE_PLACED':
    case 'STOP_PLACED':
      return 'text-green-400';
    case 'ORDER_INTENT':
      return 'text-yellow-400'; // Yellow for shadow intents
    case 'STOP_INTENT':
      return 'text-orange-400'; // Orange for stop intents
    case 'TAKE_PROFIT_INTENT':
      return 'text-purple-400'; // Purple for TP intents
    case 'SETUP_DETECTED':
      return 'text-cyan-400'; // Cyan for setup detection
    case 'PREVIEW: LIVE_ORDER_PENDING':
      return 'text-orange-400 font-bold'; // Orange/bold for live execution preview
    case 'error':
      return 'text-red-400';
    case 'system':
    default:
      return 'text-gray-400';
  }
}

export function ActivityLog() {
  const { activities, loading, error, clearActivity } = useActivity();
  const { strategies } = useStrategies();
  const { shadowLive } = useShadowLive();
  const { trading } = useTrading();
  const listRef = useRef<HTMLUListElement>(null);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  const isShadowMode = shadowLive?.enabled && !trading?.enabled;

  // Build UUID -> name map from strategies
  const strategyMap = new Map<string, string>(
    strategies.map((s) => [s.strategy_id, s.name])
  );

  // Add SHADOW_ prefix to activity messages when in shadow mode
  const formatActivityMessage = (type: ActivityType, message: string): string => {
    if (!isShadowMode) return message;
    
    // Shadow-specific activity types that need prefix
    const shadowTypes: ActivityType[] = [
      'ORDER_INTENT',
      'STOP_INTENT',
      'TAKE_PROFIT_INTENT',
      'SIGNAL_CONFIRMED',
      'SETUP_DETECTED',
      'EXECUTION_ALLOWED',
      'EXIT_FORCED',
      'TRADE_PLACED',
    ];
    
    if (shadowTypes.includes(type)) {
      return `SHADOW_${message}`;
    }
    
    return message;
  };

  const handleExportAll = async () => {
    try {
      const res = await fetch('/api/v1/events/export?limit=0');
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `HTTP ${res.status}`);
      }
      const cd = res.headers.get('Content-Disposition');
      let name = 'events_export.json';
      if (cd) {
        const m = cd.match(/filename="([^"]+)"/i);
        if (m?.[1]) name = m[1];
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = name;
      a.rel = 'noopener';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error('[ActivityLog] Export failed:', e);
      alert(e instanceof Error ? e.message : 'Export failed');
    }
  };

  const handleClear = async () => {
    if (window.confirm('Clear all activity entries?')) {
      console.log('[ActivityLog] Clearing activity...');
      const success = await clearActivity();
      console.log('[ActivityLog] Clear result:', success);
      if (!success) {
        alert('Failed to clear activity log. Check console for details.');
      }
    }
  };

  const toggleExpanded = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  // Reverse to show oldest at top, newest at bottom
  const reversedActivities = [...activities].reverse();

  // Auto-scroll to bottom when new activities arrive
  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [activities]);

  return (
    <section
      className="bg-gray-800 rounded-lg border border-gray-700 p-3 h-full flex flex-col overflow-hidden"
      aria-labelledby="activity-log-title"
    >
      <div className="flex items-center justify-between mb-2 shrink-0">
        <h2
          id="activity-log-title"
          className="text-sm font-semibold text-white"
        >
          Activity Log
        </h2>
        {activities.length > 0 && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void handleExportAll()}
              className="text-xs text-gray-400 hover:text-sky-400 transition-colors"
              title="Download full events log as JSON"
            >
              Export All
            </button>
            <button
              type="button"
              onClick={handleClear}
              className="text-xs text-gray-400 hover:text-red-400 transition-colors"
              title="Clear all activity"
            >
              Clear
            </button>
          </div>
        )}
      </div>

      {loading && (
        <div className="text-gray-400 text-xs">Loading...</div>
      )}

      {error && (
        <div className="rounded border border-red-800 bg-red-900/20 p-2 text-red-400 text-xs">
          {error}
        </div>
      )}

      {!loading && !error && activities.length === 0 && (
        <div className="flex-1 flex items-center justify-center text-gray-500 text-xs">
          No activity yet
        </div>
      )}

      {!loading && !error && activities.length > 0 && (
        <ul ref={listRef} className="overflow-y-auto flex-1 min-h-0 text-xs pr-1">
          {reversedActivities.map((activity, idx) => {
            const itemId = `${activity.timestamp}-${idx}`;
            let rawMessage = replaceUuidsWithNames(activity.message, strategyMap);
            // Prepend reason from details when present (for signal rejections)
            const reason = (activity.details as Record<string, unknown> | null)?.reason as string | undefined;
            if (reason && activity.type === 'signal' && !rawMessage.startsWith('[')) {
              rawMessage = `[${reason}] ${rawMessage}`;
            }
            const displayMessage = formatActivityMessage(activity.type, rawMessage);
            const truncated = isTruncated(displayMessage);
            const isExpanded = expandedIds.has(itemId);
            
            return (
              <li 
                key={itemId} 
                className={`${getTypeColor(activity.type)} py-0.5 px-1 -mx-1 rounded transition-colors ${
                  isExpanded 
                    ? 'bg-gray-700/50' 
                    : 'hover:bg-gray-700/30'
                } ${truncated && !isExpanded ? 'truncate' : ''} ${
                  truncated ? 'cursor-pointer' : ''
                }`}
                onClick={truncated ? () => toggleExpanded(itemId) : undefined}
                title={truncated ? displayMessage : undefined}
                role={truncated ? 'button' : undefined}
                tabIndex={truncated ? 0 : undefined}
                onKeyDown={truncated ? (e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    toggleExpanded(itemId);
                  }
                } : undefined}
              >
                <span className="text-gray-500 font-mono">{formatTime(activity.timestamp)}</span>{' '}
                {isExpanded ? (
                  <span className="whitespace-pre-wrap break-words">{displayMessage}</span>
                ) : (
                  <>
                    {displayMessage}
                    {truncated && !isExpanded && (
                      <span className="text-gray-500 ml-1" aria-hidden="true">▸</span>
                    )}
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

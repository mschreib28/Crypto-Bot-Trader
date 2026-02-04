import { useHealth } from '../hooks/useHealth';

function formatUptime(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function StatusDot({ status, title }: { status: string; title?: string }) {
  const normalized = status.toLowerCase();
  let color = '#ef4444'; // red - unhealthy
  if (['up', 'connected', 'healthy', 'running', 'no_clients'].includes(normalized)) {
    color = '#22c55e'; // green - healthy (no_clients is fine, just no frontend WS connections)
  } else if (normalized === 'degraded') {
    color = '#eab308'; // yellow - degraded
  }
  return (
    <span
      className="inline-block w-1.5 h-1.5 rounded-full"
      style={{ backgroundColor: color }}
      aria-label={status}
      title={title || status}
    />
  );
}

// Derive data feed status from ingestor - if ingestor is running with symbols, data is flowing
function getDataFeedStatus(ingestorStatus: string, symbolsCount: number): string {
  if (ingestorStatus.toLowerCase() === 'running' && symbolsCount > 0) {
    return 'running';
  }
  if (ingestorStatus.toLowerCase() === 'running') {
    return 'degraded'; // running but no symbols
  }
  return ingestorStatus;
}

function OverallStatusBadge({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  let bgColor = 'bg-red-500/20';
  let textColor = 'text-red-400';
  
  if (normalized === 'healthy') {
    bgColor = 'bg-green-500/20';
    textColor = 'text-green-400';
  } else if (normalized === 'degraded') {
    bgColor = 'bg-yellow-500/20';
    textColor = 'text-yellow-400';
  }
  
  return (
    <span className={`px-1.5 py-0.5 text-[10px] font-medium rounded ${bgColor} ${textColor}`}>
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  );
}

export function HealthPanel() {
  const { health, loading, error } = useHealth();

  if (loading) {
    return (
      <div className="rounded-lg border border-gray-700 bg-gray-800 p-3">
        <h2 className="text-sm font-semibold text-white mb-2">System Health</h2>
        <p className="text-gray-400 text-xs">Loading...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-gray-700 bg-gray-800 p-3">
        <h2 className="text-sm font-semibold text-white mb-2">System Health</h2>
        <p className="text-red-400 text-xs">{error}</p>
      </div>
    );
  }

  if (!health) return null;

  const { components } = health;

  return (
    <div className="rounded-lg border border-gray-700 bg-gray-800 p-3">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-sm font-semibold text-white">System Health</h2>
        <OverallStatusBadge status={health.status} />
      </div>
      
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        <div className="flex items-center gap-1.5">
          <StatusDot status={components.redis.status} />
          <span className="text-gray-400">Redis</span>
        </div>
        <div className="flex items-center gap-1.5">
          <StatusDot status={components.database.status} />
          <span className="text-gray-400">Database</span>
        </div>
        <div className="flex items-center gap-1.5">
          <StatusDot 
            status={components.ingestor.status} 
            title={`Status: ${components.ingestor.status}, Symbols: ${components.ingestor.symbols_count}`}
          />
          <span className="text-gray-400">Ingestor</span>
        </div>
        <div className="flex items-center gap-1.5">
          <StatusDot 
            status={getDataFeedStatus(components.ingestor.status, components.ingestor.symbols_count)} 
            title={`Ingestor: ${components.ingestor.status}, Symbols: ${components.ingestor.symbols_count}`}
          />
          <span className="text-gray-400">Data Feed</span>
        </div>
      </div>
      
      <div className="mt-2 pt-2 border-t border-gray-700 text-[10px] text-gray-500">
        Uptime: {formatUptime(health.uptime_seconds)}
      </div>
    </div>
  );
}

import { useState, useEffect } from 'react';

interface ComponentStatus {
  status: string;
}

interface RedisStatus extends ComponentStatus {
  latency_ms: number;
}

interface DatabaseStatus extends ComponentStatus {
  latency_ms: number;
}

interface IngestorStatus extends ComponentStatus {
  symbols_count: number;
}

interface WebSocketStatus extends ComponentStatus {
  last_message: string;
}

interface HealthComponents {
  redis: RedisStatus;
  database: DatabaseStatus;
  ingestor: IngestorStatus;
  websocket: WebSocketStatus;
}

export interface HealthDetailed {
  status: 'healthy' | 'degraded' | 'unhealthy';
  components: HealthComponents;
  uptime_seconds: number;
}

export function useHealth() {
  const [health, setHealth] = useState<HealthDetailed | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchHealth = async () => {
    try {
      const res = await fetch('/api/v1/health/detailed');
      if (!res.ok) throw new Error('Failed to fetch health status');
      const data = await res.json();
      setHealth(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchHealth();
    const interval = setInterval(fetchHealth, 15000);
    return () => clearInterval(interval);
  }, []);

  return { health, loading, error, refetch: fetchHealth };
}

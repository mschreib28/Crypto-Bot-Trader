import { useState, useEffect, useCallback, useRef } from 'react';

export type ActivityType = 'signal' | 'order' | 'error' | 'system' | 'ORDER_INTENT' | 'STOP_INTENT' | 'TAKE_PROFIT_INTENT' | 'SETUP_DETECTED' | 'SIGNAL_CONFIRMED' | 'EXECUTION_ALLOWED' | 'EXIT_FORCED' | 'TRADE_PLACED' | 'STOP_PLACED' | 'PREVIEW: LIVE_ORDER_PENDING';

export interface ActivityItem {
  timestamp: string;
  type: ActivityType;
  message: string;
  details?: Record<string, unknown> | null;
}

interface ActivityResponse {
  activities: ActivityItem[];
}

interface UseActivityReturn {
  activities: ActivityItem[];
  loading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
  clearActivity: () => Promise<boolean>;
}

const REFRESH_INTERVAL_MS = 10000;

export function useActivity(): UseActivityReturn {
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const clearingRef = useRef(false);

  const fetchActivity = useCallback(async () => {
    // Skip fetch if we're in the middle of clearing
    if (clearingRef.current) return;
    
    try {
      const response = await fetch('/api/v1/events?limit=20');
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      const data: ActivityResponse = await response.json();
      const items = Array.isArray(data.activities) ? data.activities : [];
      // Only update if not clearing
      if (!clearingRef.current) {
        setActivities(items);
        setError(null);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch activity';
      if (!clearingRef.current) {
        setError(message);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const clearActivity = useCallback(async (): Promise<boolean> => {
    console.log('[useActivity] clearActivity called');
    clearingRef.current = true;
    try {
      console.log('[useActivity] Sending DELETE request...');
      const response = await fetch('/api/v1/events', { method: 'DELETE' });
      console.log('[useActivity] DELETE response status:', response.status);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const data = await response.json();
      console.log('[useActivity] DELETE response data:', data);
      setActivities([]);
      setError(null);
      return true;
    } catch (err) {
      console.error('[useActivity] Failed to clear activity:', err);
      return false;
    } finally {
      // Small delay to ensure the clear has propagated before allowing fetches
      setTimeout(() => { clearingRef.current = false; }, 500);
    }
  }, []);

  useEffect(() => {
    fetchActivity();

    const interval = setInterval(fetchActivity, REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchActivity]);

  return { activities, loading, error, refetch: fetchActivity, clearActivity };
}

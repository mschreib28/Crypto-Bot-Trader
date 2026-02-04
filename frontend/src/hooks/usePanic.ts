import { useState, useCallback } from 'react';

export interface PanicResponse {
  status: string;
  orders_cancelled: number;
}

interface UsePanicReturn {
  triggerPanic: () => Promise<PanicResponse>;
  loading: boolean;
  error: string | null;
  result: PanicResponse | null;
  reset: () => void;
}

export function usePanic(): UsePanicReturn {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PanicResponse | null>(null);

  const triggerPanic = useCallback(async (): Promise<PanicResponse> => {
    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const response = await fetch('/api/v1/panic', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data: PanicResponse = await response.json();
      setResult(data);
      return data;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to trigger panic';
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  const reset = useCallback(() => {
    setError(null);
    setResult(null);
  }, []);

  return { triggerPanic, loading, error, result, reset };
}

import { useCallback, useEffect, useRef, useState } from 'react';
import { refetchPoll, subscribePoll } from '../lib/pollManager';

export function usePoll<T>(
  url: string,
  intervalMs: number,
  parse: (raw: unknown) => T
) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const parseRef = useRef(parse);
  parseRef.current = parse;

  useEffect(() => {
    const unsub = subscribePoll<unknown>(url, intervalMs, (raw, err, isLoading) => {
      setLoading(isLoading);
      setError(err);
      if (raw != null && !err) {
        try {
          setData(parseRef.current(raw));
        } catch (e) {
          setError(e instanceof Error ? e.message : 'Parse error');
        }
      }
    });
    return unsub;
  }, [url, intervalMs]);

  const refetch = useCallback(() => {
    refetchPoll(url);
  }, [url]);

  return { data, loading, error, refetch };
}

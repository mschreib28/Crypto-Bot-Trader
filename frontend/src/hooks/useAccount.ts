import { useState, useEffect } from 'react';
import { AccountState } from '../types/account';

export function useAccount() {
  const [account, setAccount] = useState<AccountState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchAccount = async () => {
    try {
      const res = await fetch('/api/v1/account');
      if (!res.ok) throw new Error('Failed to fetch account');
      const data = await res.json();
      setAccount(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAccount();
    // TICKET-609: Real-time updates (1-2 second polling)
    const interval = setInterval(fetchAccount, 2000); // 2 seconds for real-time P&L updates
    return () => clearInterval(interval);
  }, []);

  return { account, loading, error, refetch: fetchAccount };
}

import { useState, useEffect, useCallback } from 'react';

export interface Order {
  id: string;
  symbol: string;
  side: 'buy' | 'sell';
  quantity: number;
  price: number;
  status: 'pending' | 'filled' | 'cancelled';
  strategy_id?: string | null;
  executed_at?: string | null;
}

interface OrdersResponse {
  orders: Order[];
}

interface UseOrdersReturn {
  orders: Order[];
  loading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
}

const REFRESH_INTERVAL_MS = 30000;

export function useOrders(): UseOrdersReturn {
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchOrders = useCallback(async () => {
    try {
      const response = await fetch('/api/v1/orders');
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      const data: OrdersResponse = await response.json();
      const orderList = Array.isArray(data.orders) ? data.orders : [];
      setOrders(orderList);
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch orders';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchOrders();

    const interval = setInterval(fetchOrders, REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchOrders]);

  return { orders, loading, error, refetch: fetchOrders };
}

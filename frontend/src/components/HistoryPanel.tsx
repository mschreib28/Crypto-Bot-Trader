import { useEffect, useState } from 'react';
import { useHistory, HistoryItem } from '../hooks/useHistory';

interface Props {
  onClose: () => void;
}

const TYPE_COLORS: Record<string, string> = {
  TRADE_PLACED: 'text-green-400',
  SIGNAL_CONFIRMED: 'text-blue-400',
  EXECUTION_ALLOWED: 'text-lime-400',
  EXIT_FORCED: 'text-red-400',
  ORDER_INTENT: 'text-yellow-400',
  STOP_INTENT: 'text-orange-400',
  TAKE_PROFIT_INTENT: 'text-purple-400',
  SETUP_DETECTED: 'text-cyan-400',
  error: 'text-red-500',
};

function typeColor(type: string): string {
  return TYPE_COLORS[type] ?? 'text-gray-400';
}

function formatTs(iso: string): string {
  try {
    return new Date(iso).toLocaleString('en-US', {
      month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
      hour12: false,
    });
  } catch {
    return iso;
  }
}

function ExpandableRow({ item }: { item: HistoryItem }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr
        className="border-b border-gray-700 hover:bg-gray-750 cursor-pointer"
        onClick={() => item.details && setOpen(o => !o)}
      >
        <td className="px-2 py-1 text-xs text-gray-400 whitespace-nowrap">{formatTs(item.timestamp)}</td>
        <td className={`px-2 py-1 text-xs font-mono whitespace-nowrap ${typeColor(item.type)}`}>{item.type}</td>
        <td className="px-2 py-1 text-xs text-yellow-300 whitespace-nowrap">{item.symbol ?? '—'}</td>
        <td className="px-2 py-1 text-xs text-purple-300 max-w-[140px] truncate">{item.strategy ?? '—'}</td>
        <td className="px-2 py-1 text-xs text-gray-200">{item.message}</td>
      </tr>
      {open && item.details && (
        <tr className="bg-gray-900">
          <td colSpan={5} className="px-4 py-2">
            <pre className="text-xs text-gray-300 whitespace-pre-wrap break-all">
              {JSON.stringify(item.details, null, 2)}
            </pre>
          </td>
        </tr>
      )}
    </>
  );
}

export function HistoryPanel({ onClose }: Props) {
  const {
    filters, updateFilters, result, types, loading, error,
    fetchHistory, fetchTypes, exportHistory, clearHistory,
  } = useHistory();

  async function handleClear() {
    if (!confirm('Clear all activity history from the database? This cannot be undone.')) return;
    await clearHistory();
    fetchHistory(filters);
  }

  useEffect(() => {
    fetchTypes();
    fetchHistory(filters);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleSearch() {
    const next = { ...filters, page: 1 };
    updateFilters({ page: 1 });
    fetchHistory(next);
  }

  function handlePage(p: number) {
    const next = { ...filters, page: p };
    updateFilters({ page: p });
    fetchHistory(next);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70">
      <div className="w-full max-w-6xl max-h-[90vh] flex flex-col rounded-xl border border-gray-600 bg-gray-900 shadow-2xl">

        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
          <h2 className="text-white font-semibold text-base">Activity History</h2>
          <div className="flex gap-2">
            <button
              onClick={() => exportHistory('csv')}
              className="px-3 py-1 text-xs bg-gray-700 hover:bg-gray-600 text-white rounded"
            >
              Export CSV
            </button>
            <button
              onClick={() => exportHistory('json')}
              className="px-3 py-1 text-xs bg-gray-700 hover:bg-gray-600 text-white rounded"
            >
              Export JSON
            </button>
            <button
              onClick={handleClear}
              className="px-3 py-1 text-xs bg-yellow-700 hover:bg-yellow-600 text-white rounded"
            >
              Clear History
            </button>
            <button onClick={onClose} className="px-3 py-1 text-xs bg-red-700 hover:bg-red-600 text-white rounded">
              Close
            </button>
          </div>
        </div>

        {/* Filters */}
        <div className="flex flex-wrap gap-2 px-4 py-2 border-b border-gray-700 bg-gray-850">
          <select
            value={filters.type}
            onChange={e => updateFilters({ type: e.target.value })}
            className="bg-gray-800 border border-gray-600 text-gray-200 text-xs rounded px-2 py-1"
          >
            <option value="">All Types</option>
            {types.map(t => <option key={t} value={t}>{t}</option>)}
          </select>

          <input
            type="text"
            placeholder="Symbol"
            value={filters.symbol}
            onChange={e => updateFilters({ symbol: e.target.value })}
            className="bg-gray-800 border border-gray-600 text-gray-200 text-xs rounded px-2 py-1 w-28"
          />

          <input
            type="text"
            placeholder="Strategy"
            value={filters.strategy}
            onChange={e => updateFilters({ strategy: e.target.value })}
            className="bg-gray-800 border border-gray-600 text-gray-200 text-xs rounded px-2 py-1 w-36"
          />

          <input
            type="text"
            placeholder="Search message…"
            value={filters.search}
            onChange={e => updateFilters({ search: e.target.value })}
            className="bg-gray-800 border border-gray-600 text-gray-200 text-xs rounded px-2 py-1 w-44"
          />

          <input
            type="datetime-local"
            value={filters.fromDate.slice(0, 16)}
            onChange={e => updateFilters({ fromDate: e.target.value ? e.target.value + ':00Z' : '' })}
            className="bg-gray-800 border border-gray-600 text-gray-200 text-xs rounded px-2 py-1"
          />
          <span className="text-gray-500 text-xs self-center">→</span>
          <input
            type="datetime-local"
            value={filters.toDate.slice(0, 16)}
            onChange={e => updateFilters({ toDate: e.target.value ? e.target.value + ':00Z' : '' })}
            className="bg-gray-800 border border-gray-600 text-gray-200 text-xs rounded px-2 py-1"
          />

          <button
            onClick={handleSearch}
            className="px-3 py-1 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded"
          >
            Search
          </button>

          <select
            value={filters.perPage}
            onChange={e => updateFilters({ perPage: Number(e.target.value) })}
            className="bg-gray-800 border border-gray-600 text-gray-200 text-xs rounded px-2 py-1 ml-auto"
          >
            {[25, 50, 100, 200].map(n => <option key={n} value={n}>{n}/page</option>)}
          </select>
        </div>

        {/* Table */}
        <div className="flex-1 overflow-auto">
          {loading && (
            <div className="flex items-center justify-center h-32 text-gray-400 text-sm">Loading…</div>
          )}
          {error && (
            <div className="p-4 text-red-400 text-sm">{error}</div>
          )}
          {!loading && !error && result && (
            <table className="w-full text-left border-collapse">
              <thead className="sticky top-0 bg-gray-800 z-10">
                <tr>
                  <th className="px-2 py-1 text-xs text-gray-400 font-medium whitespace-nowrap">Time</th>
                  <th className="px-2 py-1 text-xs text-gray-400 font-medium">Type</th>
                  <th className="px-2 py-1 text-xs text-gray-400 font-medium">Symbol</th>
                  <th className="px-2 py-1 text-xs text-gray-400 font-medium">Strategy</th>
                  <th className="px-2 py-1 text-xs text-gray-400 font-medium">Message</th>
                </tr>
              </thead>
              <tbody>
                {result.items.length === 0 ? (
                  <tr><td colSpan={5} className="px-4 py-6 text-center text-gray-500 text-sm">No results</td></tr>
                ) : (
                  result.items.map(item => <ExpandableRow key={item.id} item={item} />)
                )}
              </tbody>
            </table>
          )}
        </div>

        {/* Pagination */}
        {result && result.pages > 1 && (
          <div className="flex items-center justify-between px-4 py-2 border-t border-gray-700 bg-gray-850">
            <span className="text-xs text-gray-400">
              {result.total.toLocaleString()} total · Page {result.page} of {result.pages}
            </span>
            <div className="flex gap-1">
              <button
                onClick={() => handlePage(result.page - 1)}
                disabled={result.page <= 1}
                className="px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 text-white rounded disabled:opacity-40"
              >
                ← Prev
              </button>
              {/* Show page numbers around current */}
              {Array.from({ length: Math.min(5, result.pages) }, (_, i) => {
                const start = Math.max(1, result.page - 2);
                const p = start + i;
                if (p > result.pages) return null;
                return (
                  <button
                    key={p}
                    onClick={() => handlePage(p)}
                    className={`px-2 py-1 text-xs rounded ${p === result.page ? 'bg-blue-600 text-white' : 'bg-gray-700 hover:bg-gray-600 text-white'}`}
                  >
                    {p}
                  </button>
                );
              })}
              <button
                onClick={() => handlePage(result.page + 1)}
                disabled={result.page >= result.pages}
                className="px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 text-white rounded disabled:opacity-40"
              >
                Next →
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

import { useAnalytics } from '../hooks/useAnalytics';

export function AnalyticsPanel() {
  const { byGrade, factors, sampleSize, loading, error, downloadJson } = useAnalytics();

  if (loading && byGrade.length === 0) {
    return (
      <div className="rounded-lg border border-gray-700 bg-gray-800 p-3 text-xs text-gray-400">
        Loading analytics…
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-gray-700 bg-gray-800 p-3 flex flex-col gap-3 min-h-0">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-200">Analytics</h3>
        <button
          type="button"
          onClick={downloadJson}
          className="text-xs px-2 py-1 rounded bg-gray-700 hover:bg-gray-600 text-gray-200"
        >
          Export JSON
        </button>
      </div>

      {error && (
        <p className="text-xs text-red-400">{error}</p>
      )}

      <div>
        <p className="text-xs text-gray-400 mb-1">Win rate by grade (n={sampleSize})</p>
        <table className="w-full text-xs text-left">
          <thead>
            <tr className="text-gray-500 border-b border-gray-700">
              <th className="py-1">Grade</th>
              <th className="py-1">Trades</th>
              <th className="py-1">WR%</th>
              <th className="py-1">Avg R</th>
            </tr>
          </thead>
          <tbody>
            {byGrade.map((row) => (
              <tr key={row.grade} className="border-b border-gray-800 text-gray-300">
                <td className="py-1">{row.grade}</td>
                <td className="py-1">{row.trades}</td>
                <td className="py-1">{row.win_rate.toFixed(1)}</td>
                <td className="py-1">{row.avg_r.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div>
        <p className="text-xs text-gray-400 mb-1">Factor correlation (predicts wins)</p>
        <table className="w-full text-xs text-left">
          <thead>
            <tr className="text-gray-500 border-b border-gray-700">
              <th className="py-1">Factor</th>
              <th className="py-1">N</th>
              <th className="py-1">vs Win</th>
              <th className="py-1">vs R</th>
            </tr>
          </thead>
          <tbody>
            {factors.map((f) => (
              <tr key={f.factor} className="border-b border-gray-800 text-gray-300">
                <td className="py-1">{f.factor}</td>
                <td className="py-1">{f.sample_size}</td>
                <td className="py-1">
                  {f.correlation_win != null ? f.correlation_win.toFixed(3) : '—'}
                </td>
                <td className="py-1">
                  {f.correlation_r != null ? f.correlation_r.toFixed(3) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

import { useCallback, useState } from 'react';
import { PanicButton } from './PanicButton';
import { useBotMode } from '../hooks/useBotMode';
import { useAccount } from '../hooks/useAccount';

interface StatusIndicatorProps {
  halted: boolean | null;
  loading: boolean;
  error: string | null;
}

function StatusIndicator({ halted, loading, error }: StatusIndicatorProps) {
  if (loading) {
    return (
      <div className="flex items-center gap-2">
        <div className="h-3 w-3 rounded-full bg-gray-500 animate-pulse" />
        <span className="text-sm text-gray-400">Loading...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center gap-2">
        <div className="h-3 w-3 rounded-full bg-yellow-500" />
        <span className="text-sm text-yellow-400">Error</span>
      </div>
    );
  }

  const isHealthy = halted === false;

  return (
    <div className="flex items-center gap-2">
      <div
        className={`h-3 w-3 rounded-full ${
          isHealthy ? 'bg-green-500' : 'bg-red-500'
        }`}
        aria-label={isHealthy ? 'System healthy' : 'System halted'}
      />
      <span className={`text-sm ${isHealthy ? 'text-green-400' : 'text-red-400'}`}>
        {isHealthy ? 'Healthy' : 'Halted'}
      </span>
    </div>
  );
}

interface HeaderProps {
  halted: boolean | null;
  loading: boolean;
  error: string | null;
}

function MicroModeBanner({ active, message }: { active: boolean; message: string | null }) {
  if (!active || !message) return null;

  return (
    <div className="bg-yellow-900/50 border border-yellow-700 rounded px-3 py-1.5 text-xs text-yellow-200">
      <div className="flex items-center gap-2">
        <span className="font-semibold">⚠️ MICRO MODE ACTIVE</span>
        <span className="text-yellow-300">{message}</span>
      </div>
    </div>
  );
}

export function Header({ halted, loading, error }: HeaderProps) {
  const { botMode, loading: modeLoading, error: modeError, setMode, refetch } = useBotMode();
  const { account } = useAccount();
  const microMode = account?.micro_mode;
  const [pending, setPending] = useState(false);

  const mode = botMode?.mode ?? 'SHADOW';

  const selectShadow = useCallback(async () => {
    if (mode === 'SHADOW') return;
    setPending(true);
    try {
      await setMode('SHADOW');
    } finally {
      setPending(false);
    }
  }, [mode, setMode]);

  const selectLive = useCallback(async () => {
    if (mode === 'LIVE') return;
    const ok = window.confirm(
      'Switch to LIVE mode? The bot will place real orders on Kraken with real funds.'
    );
    if (!ok) return;
    setPending(true);
    try {
      await setMode('LIVE', 'ENABLE_LIVE_TRADING');
    } finally {
      setPending(false);
    }
  }, [mode, setMode]);

  const busy = modeLoading || pending;

  return (
    <header className="border-b border-gray-800 bg-gray-900 px-4 py-2">
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-4">
            <h1 className="text-xl font-bold text-white">Omni-Bot</h1>

            <div className="flex flex-col gap-1">
              <span className="text-xs font-medium uppercase tracking-wide text-gray-400">
                Bot mode
              </span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={busy}
                  onClick={selectShadow}
                  className={`
                    rounded-lg px-4 py-2 text-sm font-semibold transition-colors
                    focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-gray-900
                    ${
                      mode === 'SHADOW'
                        ? 'bg-slate-600 text-white ring-2 ring-blue-400'
                        : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
                    }
                    ${busy ? 'opacity-50 cursor-not-allowed' : ''}
                  `}
                  aria-pressed={mode === 'SHADOW'}
                >
                  SHADOW
                </button>
                <span className="text-gray-500 text-xs">|</span>
                <button
                  type="button"
                  disabled={busy}
                  onClick={selectLive}
                  className={`
                    rounded-lg px-4 py-2 text-sm font-semibold transition-colors
                    focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-gray-900
                    ${
                      mode === 'LIVE'
                        ? 'bg-red-700 text-white ring-2 ring-red-400'
                        : 'bg-slate-800 text-slate-300 hover:bg-red-900/40'
                    }
                    ${busy ? 'opacity-50 cursor-not-allowed' : ''}
                  `}
                  aria-pressed={mode === 'LIVE'}
                >
                  LIVE
                </button>
              </div>
              {modeError && (
                <span className="text-xs text-yellow-400 max-w-md">{modeError}</span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-4">
            <PanicButton onSuccess={() => void refetch()} />
            <StatusIndicator halted={halted} loading={loading} error={error} />
          </div>
        </div>
        {microMode?.active && (
          <MicroModeBanner active={microMode.active} message={microMode.message} />
        )}
      </div>
    </header>
  );
}

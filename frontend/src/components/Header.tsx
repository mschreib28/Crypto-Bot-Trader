import { PanicButton } from './PanicButton';
import { useTrading } from '../hooks/useTrading';
import { useShadowLive } from '../hooks/useShadowLive';
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

interface ToggleButtonProps {
  enabled: boolean;
  loading: boolean;
  onToggle: () => void;
  enabledText: string;
  disabledText: string;
  enabledColor: string;
  disabledColor: string;
  ariaLabel: string;
  title: string;
}

function ToggleButton({
  enabled,
  loading,
  onToggle,
  enabledText,
  disabledText,
  enabledColor,
  disabledColor,
  ariaLabel,
  title,
}: ToggleButtonProps) {
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={loading}
      className={`
        relative inline-flex items-center h-8 px-3 rounded-full
        transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-gray-900
        ${enabled ? enabledColor : disabledColor}
        ${loading ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}
      `}
      aria-pressed={enabled}
      aria-label={ariaLabel}
      title={title}
    >
      {loading ? (
        <span className="text-white text-xs font-medium">...</span>
      ) : (
        <span className="text-white text-xs font-semibold">
          {enabled ? enabledText : disabledText}
        </span>
      )}
    </button>
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
  const { trading, loading: tradingLoading, toggleTrading, refetch } = useTrading();
  const { shadowLive, loading: shadowLiveLoading, toggleShadowLive } = useShadowLive();
  const { account } = useAccount();
  const microMode = account?.micro_mode;

  // Determine if we're in Live or Shadow mode
  const isLiveMode = trading?.enabled && !shadowLive?.enabled;
  const isShadowMode = shadowLive?.enabled && !trading?.enabled;
  const isOff = !trading?.enabled && !shadowLive?.enabled;

  const handleLiveShadowToggle = async () => {
    if (isOff) {
      // If both are off, enable shadow mode
      await toggleShadowLive();
    } else if (isShadowMode) {
      // Switch from Shadow to Live
      await toggleShadowLive();
      await toggleTrading();
    } else if (isLiveMode) {
      // Switch from Live to Shadow
      await toggleTrading();
      await toggleShadowLive();
    }
    setTimeout(() => {
      refetch();
    }, 500);
  };

  return (
    <header className="border-b border-gray-800 bg-gray-900 px-4 py-2">
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-bold text-white">Omni-Bot</h1>
            
            {/* ON/OFF Button */}
            <ToggleButton
              enabled={(trading?.enabled ?? false) || (shadowLive?.enabled ?? false)}
              loading={tradingLoading || shadowLiveLoading}
              onToggle={async () => {
                if (trading?.enabled) {
                  await toggleTrading();
                }
                if (shadowLive?.enabled) {
                  await toggleShadowLive();
                }
                setTimeout(() => refetch(), 500);
              }}
              enabledText="ON"
              disabledText="OFF"
              enabledColor="bg-green-600 focus:ring-green-500"
              disabledColor="bg-gray-600 focus:ring-gray-500"
              ariaLabel={`Trading ${(trading?.enabled ?? false) || (shadowLive?.enabled ?? false) ? 'enabled' : 'disabled'}. Click to toggle.`}
              title={(trading?.enabled ?? false) || (shadowLive?.enabled ?? false) ? 'Trading enabled. Click to disable.' : 'Trading disabled. Click to enable.'}
            />

            {/* Live/Shadow Button */}
            <ToggleButton
              enabled={isLiveMode ?? false}
              loading={tradingLoading || shadowLiveLoading}
              onToggle={handleLiveShadowToggle}
              enabledText="LIVE"
              disabledText="SHADOW"
              enabledColor="bg-red-600 focus:ring-red-500"
              disabledColor="bg-blue-600 focus:ring-blue-500"
              ariaLabel={`Mode: ${isLiveMode ? 'Live' : isShadowMode ? 'Shadow' : 'Off'}. Click to toggle.`}
              title={
                isLiveMode
                  ? 'Live Trading mode: Executes real orders on exchange. Click to switch to Shadow mode.'
                  : isShadowMode
                  ? 'Shadow mode: Logs ORDER_INTENT, STOP_INTENT, TAKE_PROFIT_INTENT without executing orders. Click to switch to Live Trading.'
                  : 'Trading disabled. Click ON/OFF first, then toggle between Live/Shadow.'
              }
            />
          </div>
          <div className="flex items-center gap-4">
            <PanicButton onSuccess={refetch} />
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

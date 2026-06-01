import { ReactNode } from 'react';
import { Header } from './Header';
import { useSystemStatus } from '../hooks/useSystemStatus';
import { useShadowLive, ShadowLiveState } from '../hooks/useShadowLive';
import { useTrading, TradingState } from '../hooks/useTrading';

interface LayoutProps {
  children: ReactNode;
}

interface BannerProps {
  trading: TradingState | null;
  shadowLive: ShadowLiveState | null;
}

function TradingOffBanner({ trading, shadowLive }: BannerProps) {
  
  const isOff = !trading?.enabled && !shadowLive?.enabled;
  
  if (!isOff) return null;
  
  return (
    <div className="w-full bg-amber-900/80 border-b-2 border-amber-600 px-4 py-2">
      <div className="flex items-center gap-3">
        <span className="font-bold text-amber-200 text-sm">⚠️ TRADING OFF</span>
        <span className="text-amber-300 text-xs">
          — BUY signals will not open positions — Enable Shadow mode to simulate trades
        </span>
      </div>
    </div>
  );
}

function ShadowModeBanner({ trading, shadowLive }: BannerProps) {
  
  const isShadowMode = shadowLive?.enabled && !trading?.enabled;
  
  if (!isShadowMode) return null;
  
  return (
    <div className="w-full bg-blue-900/80 border-b-2 border-blue-600 px-4 py-2">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <div className="h-3 w-3 rounded-full bg-blue-400 animate-pulse" />
          <span className="font-bold text-blue-200 text-sm">🟦 SHADOW MODE ACTIVE</span>
        </div>
        <span className="text-blue-300 text-xs">
          — NO REAL ORDERS WILL BE SENT — All signals, sizing, and stops are simulated and logged for verification
        </span>
      </div>
    </div>
  );
}

function LiveModeBanner({ trading, shadowLive }: BannerProps) {
  
  const isLiveMode = trading?.enabled && !shadowLive?.enabled;
  
  if (!isLiveMode) return null;
  
  return (
    <div className="w-full bg-red-900/80 border-b-2 border-red-600 px-4 py-2">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <div className="h-3 w-3 rounded-full bg-red-400 animate-ping" />
          <span className="font-bold text-red-200 text-sm">🔴 LIVE TRADING ACTIVE</span>
        </div>
        <span className="text-red-300 text-xs">
          — REAL ORDERS WILL BE EXECUTED — Trading with real capital
        </span>
      </div>
    </div>
  );
}

export function Layout({ children }: LayoutProps) {
  const { status, loading, error } = useSystemStatus();
  const { trading } = useTrading();
  const { shadowLive } = useShadowLive();

  return (
    <div className="h-screen bg-gray-900 text-white flex flex-col overflow-hidden">
      <Header
        halted={status?.halted ?? null}
        loading={loading}
        error={error}
      />
      <TradingOffBanner trading={trading} shadowLive={shadowLive} />
      <ShadowModeBanner trading={trading} shadowLive={shadowLive} />
      <LiveModeBanner trading={trading} shadowLive={shadowLive} />
      <main className="p-3 flex-1 min-h-0 overflow-hidden">{children}</main>
    </div>
  );
}

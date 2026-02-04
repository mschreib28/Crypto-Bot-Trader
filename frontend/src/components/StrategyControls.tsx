import { useState } from 'react';
import { Strategy } from '../types/strategy';

interface StrategyToggleProps {
  strategy: Strategy;
  onToggle: (strategyId: string, enabled: boolean) => Promise<boolean>;
}

function StrategyToggle({ strategy, onToggle }: StrategyToggleProps) {
  const [toggling, setToggling] = useState(false);

  const handleToggle = async () => {
    setToggling(true);
    try {
      await onToggle(strategy.strategy_id, !strategy.enabled);
    } finally {
      setToggling(false);
    }
  };

  return (
    <div className="flex items-center justify-between py-1.5 px-2 -mx-2 rounded hover:bg-gray-700/50 transition-colors">
      <span className="text-xs text-gray-300">{strategy.name}</span>
      <button
        type="button"
        role="switch"
        aria-checked={strategy.enabled}
        aria-label={`${strategy.name}: ${strategy.enabled ? 'enabled' : 'disabled'}`}
        disabled={toggling}
        onClick={handleToggle}
        className={`
          relative inline-flex h-4 w-8 items-center rounded-full transition-colors
          focus:outline-none focus:ring-1 focus:ring-offset-1 focus:ring-offset-gray-800
          ${strategy.enabled ? 'bg-green-600 focus:ring-green-500' : 'bg-gray-600 focus:ring-gray-500'}
          ${toggling ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}
        `}
      >
        <span
          className={`
            inline-block h-3 w-3 transform rounded-full bg-white transition-transform
            ${strategy.enabled ? 'translate-x-4' : 'translate-x-0.5'}
          `}
        />
      </button>
    </div>
  );
}

interface StrategyControlsProps {
  strategies: Strategy[];
  onStrategyToggle: (strategyId: string, enabled: boolean) => Promise<boolean>;
}

export function StrategyControls({ strategies, onStrategyToggle }: StrategyControlsProps) {
  return (
    <section className="bg-gray-800 rounded-lg p-3 border border-gray-700" aria-labelledby="strategy-controls-title">
      <h2 id="strategy-controls-title" className="text-sm font-semibold text-white mb-2">
        Controls
      </h2>

      <div className="space-y-1">
        {strategies.length === 0 ? (
          <p className="text-gray-400 text-xs">No strategies</p>
        ) : (
          strategies.map((strategy) => (
            <StrategyToggle
              key={strategy.strategy_id}
              strategy={strategy}
              onToggle={onStrategyToggle}
            />
          ))
        )}
      </div>
    </section>
  );
}

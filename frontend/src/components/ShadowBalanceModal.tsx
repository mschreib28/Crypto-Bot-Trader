import { useEffect, useRef, useState } from 'react';

interface ShadowBalanceModalProps {
  isOpen: boolean;
  currentBalance?: number;
  onConfirm: (amount: number) => Promise<boolean>;
  onCancel: () => void;
  loading?: boolean;
}

export function ShadowBalanceModal({
  isOpen,
  currentBalance,
  onConfirm,
  onCancel,
  loading = false,
}: ShadowBalanceModalProps) {
  const [amount, setAmount] = useState('');
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const cancelButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (isOpen) {
      // Reset form when modal opens
      setAmount(currentBalance?.toFixed(2) || '');
      setError(null);
      // Focus input after a brief delay to ensure modal is rendered
      setTimeout(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
      }, 100);
    }
  }, [isOpen, currentBalance]);

  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        onCancel();
      }
    };

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [isOpen, onCancel]);

  const handleSubmit = async () => {
    const value = parseFloat(amount);
    
    if (isNaN(value) || value < 0) {
      setError('Please enter a valid amount (must be >= 0)');
      return;
    }

    setError(null);
    const success = await onConfirm(value);
    
    if (success) {
      setAmount('');
      onCancel();
    } else {
      setError('Failed to set shadow balance. Please try again.');
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleSubmit();
    }
  };

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title"
    >
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/70 transition-opacity"
        onClick={onCancel}
        aria-hidden="true"
      />

      {/* Modal */}
      <div className="relative z-10 w-full max-w-md rounded-lg bg-gray-800 p-6 shadow-xl">
        <h2
          id="modal-title"
          className="text-xl font-semibold text-white mb-2"
        >
          Set Shadow Balance
        </h2>

        <p className="text-sm text-gray-400 mb-4">
          Enter the amount of money for the shadow portfolio.
        </p>

        <div className="space-y-4">
          <div>
            <label htmlFor="balance-input" className="block text-sm font-medium text-gray-300 mb-2">
              Amount (USD)
            </label>
            <input
              ref={inputRef}
              id="balance-input"
              type="number"
              step="0.01"
              min="0"
              placeholder="0.00"
              value={amount}
              onChange={(e) => {
                setAmount(e.target.value);
                setError(null);
              }}
              onKeyDown={handleKeyDown}
              disabled={loading}
              className="w-full px-4 py-2 bg-gray-700 border border-gray-600 rounded-md text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-50 disabled:cursor-not-allowed"
            />
            {error && (
              <p className="mt-1 text-sm text-red-400">{error}</p>
            )}
          </div>

          {currentBalance !== undefined && (
            <div className="text-xs text-gray-500">
              Current balance: ${currentBalance.toFixed(2)}
            </div>
          )}
        </div>

        <div className="mt-6 flex justify-end gap-3">
          <button
            ref={cancelButtonRef}
            type="button"
            onClick={onCancel}
            disabled={loading}
            className="rounded-md bg-gray-600 px-4 py-2 text-sm font-medium text-white hover:bg-gray-500 focus:outline-none focus:ring-2 focus:ring-gray-400 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={loading || !amount}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 focus:ring-offset-gray-800 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? 'Setting...' : 'Set Balance'}
          </button>
        </div>
      </div>
    </div>
  );
}

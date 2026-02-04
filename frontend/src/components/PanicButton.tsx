import { useState } from 'react';
import { ConfirmModal } from './ConfirmModal';
import { usePanic } from '../hooks/usePanic';

type PanicState = 'idle' | 'confirming' | 'success' | 'error';

interface PanicButtonProps {
  onSuccess?: () => void;
}

export function PanicButton({ onSuccess }: PanicButtonProps) {
  const [state, setState] = useState<PanicState>('idle');
  const { triggerPanic, loading, error, result, reset } = usePanic();

  const handlePanicClick = () => {
    setState('confirming');
  };

  const handleCancel = () => {
    setState('idle');
    reset();
  };

  const handleConfirm = async () => {
    try {
      await triggerPanic();
      setState('success');
      onSuccess?.();
    } catch {
      setState('error');
    }
  };

  const handleDismissResult = () => {
    setState('idle');
    reset();
  };

  const getModalContent = () => {
    switch (state) {
      case 'confirming':
        return {
          title: 'Emergency Stop',
          message: 'This will cancel all open orders and disable live trading. Continue?',
          confirmLabel: 'Confirm',
          onConfirm: handleConfirm,
          onCancel: handleCancel,
          confirmDisabled: loading,
        };
      case 'success':
        return {
          title: 'Panic Initiated',
          message: `${result?.orders_cancelled ?? 0} orders cancelled. Trading disabled.`,
          confirmLabel: 'OK',
          onConfirm: handleDismissResult,
          onCancel: handleDismissResult,
          confirmDisabled: false,
        };
      case 'error':
        return {
          title: 'Error',
          message: error ?? 'Failed to trigger panic. Please try again.',
          confirmLabel: 'OK',
          onConfirm: handleDismissResult,
          onCancel: handleDismissResult,
          confirmDisabled: false,
        };
      default:
        return null;
    }
  };

  const modalContent = getModalContent();

  return (
    <>
      <button
        type="button"
        onClick={handlePanicClick}
        disabled={loading}
        className="rounded-md bg-red-600 px-4 py-2 text-sm font-bold uppercase tracking-wide text-white shadow-lg hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2 focus:ring-offset-gray-900 disabled:cursor-not-allowed disabled:opacity-50"
        aria-label="Emergency panic button - cancels all open orders"
      >
        {loading ? 'Processing...' : 'PANIC'}
      </button>

      {modalContent && (
        <ConfirmModal
          isOpen={state !== 'idle'}
          title={modalContent.title}
          message={modalContent.message}
          confirmLabel={modalContent.confirmLabel}
          cancelLabel={state === 'confirming' ? 'Cancel' : undefined}
          onConfirm={modalContent.onConfirm}
          onCancel={modalContent.onCancel}
          confirmDisabled={modalContent.confirmDisabled}
          variant={state === 'confirming' ? 'danger' : 'default'}
        />
      )}
    </>
  );
}

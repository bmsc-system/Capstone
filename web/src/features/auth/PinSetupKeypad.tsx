import { useState, type ReactNode } from 'react';
import { getErrorMessage } from '../../api/client';
import PinKeypad from './PinKeypad';

// Same rules as the server (api/app/utils/passwords.py validate_pin), checked
// on the first entry so the worker is not asked to type a refused PIN twice.
const OBVIOUS_PINS = new Set(['1212', '1122', '1313', '2323', '1010', '0101', '2000', '1004', '6969', '2580', '0852']);

function isObviousPin(pin: string): boolean {
  return (
    OBVIOUS_PINS.has(pin) ||
    new Set(pin).size === 1 ||
    '0123456789'.includes(pin) ||
    '9876543210'.includes(pin)
  );
}

type PinSetupKeypadProps = {
  busy?: boolean;
  onSave: (pin: string) => Promise<void>;
  footer?: ReactNode;
};

/** Two-pass PIN creation: Create a PIN → Confirm your PIN. */
export default function PinSetupKeypad({ busy = false, onSave, footer }: PinSetupKeypadProps) {
  const [phase, setPhase] = useState<'create' | 'confirm'>('create');
  const [firstPin, setFirstPin] = useState('');
  const [error, setError] = useState('');
  const [resetSignal, setResetSignal] = useState(0);
  const [failSignal, setFailSignal] = useState(0);
  const [saving, setSaving] = useState(false);

  const restart = (message: string) => {
    setError(message);
    setFirstPin('');
    setPhase('create');
    setFailSignal((n) => n + 1);
  };

  const onComplete = async (pin: string) => {
    if (phase === 'create') {
      if (isObviousPin(pin)) {
        restart('That PIN is too easy to guess. Pick 4 numbers that are not in a row or repeated.');
        return;
      }
      setFirstPin(pin);
      setPhase('confirm');
      setError('');
      setResetSignal((n) => n + 1);
      return;
    }

    if (pin !== firstPin) {
      restart('PINs do not match. Enter a new PIN.');
      return;
    }

    setSaving(true);
    setError('');
    try {
      await onSave(pin);
    } catch (err) {
      restart(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <PinKeypad
      title={phase === 'create' ? 'Create a PIN' : 'Confirm your PIN'}
      error={error}
      busy={busy || saving}
      resetSignal={resetSignal}
      failSignal={failSignal}
      onComplete={onComplete}
      footer={footer}
    />
  );
}

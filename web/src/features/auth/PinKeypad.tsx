import { useEffect, useRef, useState, type ReactNode } from 'react';
import './PinKeypad.css';
import { PIN_LENGTH } from '../../auth/pinHint';

const KEYS: (string | null)[] = ['1', '2', '3', '4', '5', '6', '7', '8', '9', null, '0', 'back'];

const LETTERS: Record<string, string> = {
  '2': 'ABC',
  '3': 'DEF',
  '4': 'GHI',
  '5': 'JKL',
  '6': 'MNO',
  '7': 'PQRS',
  '8': 'TUV',
  '9': 'WXYZ',
};

function BackspaceIcon() {
  return (
    <svg width="26" height="20" viewBox="0 0 26 20" aria-hidden focusable="false">
      <path
        d="M8.6 2h13.9A2.5 2.5 0 0 1 25 4.5v11a2.5 2.5 0 0 1-2.5 2.5H8.6L1.6 10l7-8z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
      <path d="M12.5 6.5l7 7M19.5 6.5l-7 7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

export type PinKeypadProps = {
  title: string;
  error?: string;
  busy?: boolean;
  /** Increment to clear digits (no shake). */
  resetSignal?: number;
  /** Increment to clear digits and shake the indicator. */
  failSignal?: number;
  onComplete: (pin: string) => void;
  footer?: ReactNode;
};

/** On-screen PIN keypad (4 digits). Digits are never displayed. */
export default function PinKeypad({
  title,
  error,
  busy = false,
  resetSignal = 0,
  failSignal = 0,
  onComplete,
  footer,
}: PinKeypadProps) {
  const [digits, setDigits] = useState('');
  const [shaking, setShaking] = useState(false);
  // The digits typed so far, kept in a ref so a fast tap never reads a stale
  // render, and so the PIN is handed over once, from the tap that finished it.
  const digitsRef = useRef('');
  const completingRef = useRef(false);

  const setTyped = (next: string) => {
    digitsRef.current = next;
    setDigits(next);
  };

  const clearDigits = (withShake: boolean) => {
    setTyped('');
    completingRef.current = false;
    if (!withShake) return;
    setShaking(true);
    window.setTimeout(() => setShaking(false), 420);
  };

  useEffect(() => {
    if (resetSignal === 0) return;
    clearDigits(false);
  }, [resetSignal]);

  useEffect(() => {
    if (failSignal === 0) return;
    clearDigits(true);
  }, [failSignal]);

  const press = (key: string) => {
    if (busy || completingRef.current) return;
    const current = digitsRef.current;
    if (key === 'back') {
      setTyped(current.slice(0, -1));
      return;
    }
    if (current.length >= PIN_LENGTH) return;
    const next = current + key;
    setTyped(next);
    if (next.length === PIN_LENGTH) {
      completingRef.current = true;
      onComplete(next);
    }
  };

  return (
    <div className="pin-keypad" aria-label="PIN entry">
      <div className="pin-keypad__title">{title}</div>

      <div
        className={`pin-keypad__indicator${shaking ? ' pin-keypad__indicator--shake' : ''}`}
        role="status"
        aria-label={`${digits.length} of ${PIN_LENGTH} digits entered`}
      >
        {Array.from({ length: PIN_LENGTH }, (_, i) => (
          <span
            key={i}
            className={`pin-keypad__dot${i < digits.length ? ' pin-keypad__dot--filled' : ''}`}
          />
        ))}
      </div>

      {error ? <div className="pin-keypad__error">{error}</div> : <div className="pin-keypad__error" />}

      <div className="pin-keypad__grid">
        {KEYS.map((key, idx) => {
          if (key === null) {
            return <div key={`empty-${idx}`} className="pin-keypad__spacer" aria-hidden />;
          }
          if (key === 'back') {
            return (
              <button
                key="back"
                type="button"
                className="pin-keypad__key pin-keypad__key--action"
                aria-label="Backspace"
                disabled={busy}
                onClick={() => press('back')}
              >
                <BackspaceIcon />
              </button>
            );
          }
          return (
            <button
              key={key}
              type="button"
              className="pin-keypad__key"
              disabled={busy}
              onClick={() => press(key)}
            >
              <span className="pin-keypad__digit">{key}</span>
              {LETTERS[key] ? <span className="pin-keypad__letters">{LETTERS[key]}</span> : null}
            </button>
          );
        })}
      </div>

      {footer ? <div className="pin-keypad__footer">{footer}</div> : null}
    </div>
  );
}

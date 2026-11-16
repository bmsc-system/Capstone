import { useEffect, useState } from 'react';
import axios from 'axios';
import { authApi } from '../../api/auth.api';
import { getErrorCode, getErrorMessage } from '../../api/client';
import { useAuth } from '../../hooks/useAuth';
import { PIN_HINT_KEY } from '../../auth/pinHint';
import {
  MAX_LOCK_FAILURES,
  awayTooLong,
  checkPinLocally,
  clearHidden,
  clearPinCheck,
  isUnlockedThisRun,
  markLocked,
  markUnlocked,
  noteHidden,
  noteLockFailure,
  readHiddenAt,
  savePinCheck,
  shouldLockOnOpen,
} from '../../auth/appLock';
import PinKeypad from './PinKeypad';
import './AppLock.css';

function phoneHasPin(): boolean {
  try {
    return localStorage.getItem(PIN_HINT_KEY) === '1';
  } catch {
    return false;
  }
}

/**
 * Covers the worker app with the PIN keypad when it is reopened after being
 * closed, or after a minute in the background. Work keeps syncing underneath.
 */
export default function AppLock() {
  const { user, logout } = useAuth();
  const [locked, setLocked] = useState(() =>
    shouldLockOnOpen({
      hasPin: phoneHasPin(),
      unlockedThisRun: isUnlockedThisRun(),
      now: Date.now(),
      hiddenAt: readHiddenAt(),
    }),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [failSignal, setFailSignal] = useState(0);

  // Away for a while: lock on return. Closing the app also passes through here.
  useEffect(() => {
    const onHide = () => noteHidden();
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') {
        onHide();
        return;
      }
      if (phoneHasPin() && awayTooLong(Date.now(), readHiddenAt())) {
        setError('');
        setLocked(true);
        return;
      }
      clearHidden();
    };
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('pagehide', onHide);
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('pagehide', onHide);
    };
  }, []);

  useEffect(() => {
    if (locked) markLocked();
  }, [locked]);

  // The PIN may have been turned off elsewhere (password changed, PIN removed).
  useEffect(() => {
    if (!locked || !navigator.onLine) return;
    let cancelled = false;
    authApi
      .pinStatus()
      .then(({ data }) => {
        if (cancelled || data.hasPin) return;
        localStorage.removeItem(PIN_HINT_KEY);
        clearPinCheck();
        markUnlocked();
        setLocked(false);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [locked]);

  if (!locked || !user) return null;

  const unlock = () => {
    markUnlocked();
    setError('');
    setLocked(false);
  };

  /** Leave for the login page. `forgetPin` after too many wrong tries. */
  const signOut = (forgetPin: boolean) => {
    if (forgetPin) {
      localStorage.removeItem(PIN_HINT_KEY);
      clearPinCheck();
    }
    setLocked(false);
    logout();
    window.location.href = forgetPin ? '/login?reason=pin-locked' : '/login?mode=password';
  };

  const wrong = (message = 'Wrong PIN. Try again.') => {
    const fails = noteLockFailure();
    if (fails >= MAX_LOCK_FAILURES) {
      signOut(true);
      return;
    }
    const left = MAX_LOCK_FAILURES - fails;
    setError(`${message} ${left} ${left === 1 ? 'try' : 'tries'} left.`);
    setFailSignal((n) => n + 1);
  };

  const onComplete = async (pin: string) => {
    setBusy(true);
    setError('');
    try {
      if (navigator.onLine) {
        // Online, the server decides: the PIN may have been changed on another phone.
        try {
          const { data } = await authApi.unlockWithPin(pin);
          if (data.user.id !== user.id) {
            signOut(false);
            return;
          }
          localStorage.setItem('accessToken', data.accessToken);
          localStorage.setItem('refreshToken', data.refreshToken);
          await savePinCheck(pin);
          unlock();
          return;
        } catch (err) {
          if (!axios.isAxiosError(err) || err.response) {
            const code = getErrorCode(err);
            if (code === 'PIN_LOCKED' || code === 'PIN_DEVICE_UNKNOWN') {
              signOut(true);
              return;
            }
            setError(getErrorMessage(err));
            setFailSignal((n) => n + 1);
            return;
          }
          // No answer: fall through to the copy kept on this phone.
        }
      }
      const local = await checkPinLocally(pin);
      if (local === false) {
        wrong();
        return;
      }
      // Right, or nothing to check against offline: let the worker keep working.
      unlock();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="app-lock" role="dialog" aria-modal="true" aria-label="Enter your PIN">
      <div className="app-lock__inner">
        <div className="app-lock__who">{user.fullName}</div>
        <PinKeypad
          title="Enter your PIN"
          error={error}
          busy={busy}
          failSignal={failSignal}
          onComplete={onComplete}
          footer={
            <button
              type="button"
              className="pin-keypad__link"
              onClick={() => signOut(false)}
            >
              Use password instead
            </button>
          }
        />
      </div>
    </div>
  );
}

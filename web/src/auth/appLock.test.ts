import { describe, expect, it } from 'vitest';
import {
  LOCK_AFTER_MS,
  awayTooLong,
  checkPinLocally,
  clearPinCheck,
  isUnlockedThisRun,
  markLocked,
  markUnlocked,
  noteHidden,
  readHiddenAt,
  savePinCheck,
  shouldLockOnOpen,
} from './appLock';

function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() {
      return m.size;
    },
    clear: () => m.clear(),
    getItem: (k) => (m.has(k) ? (m.get(k) as string) : null),
    key: (i) => [...m.keys()][i] ?? null,
    removeItem: (k) => void m.delete(k),
    setItem: (k, v) => void m.set(k, String(v)),
  };
}

describe('app lock rules', () => {
  const now = 1_800_000_000_000;

  it('locks a fresh start of the app on a phone with a PIN', () => {
    expect(shouldLockOnOpen({ hasPin: true, unlockedThisRun: false, now, hiddenAt: null })).toBe(true);
    expect(shouldLockOnOpen({ hasPin: false, unlockedThisRun: false, now, hiddenAt: null })).toBe(false);
  });

  it('does not lock a quick switch, but locks after a minute away', () => {
    expect(awayTooLong(now, now - 20_000)).toBe(false);
    expect(awayTooLong(now, now - LOCK_AFTER_MS)).toBe(true);
    expect(shouldLockOnOpen({ hasPin: true, unlockedThisRun: true, now, hiddenAt: now - 5_000 })).toBe(false);
    expect(shouldLockOnOpen({ hasPin: true, unlockedThisRun: true, now, hiddenAt: now - 120_000 })).toBe(true);
  });

  it('remembers unlocking for this run, and forgets it while locked', () => {
    const session = memoryStorage();
    const local = memoryStorage();
    noteHidden(now, local);
    expect(readHiddenAt(local)).toBe(now);
    markUnlocked(session, local);
    expect(isUnlockedThisRun(session)).toBe(true);
    expect(readHiddenAt(local)).toBeNull();
    markLocked(session);
    expect(isUnlockedThisRun(session)).toBe(false);
  });

  it('checks the PIN against a salted hash kept on the phone', async () => {
    const local = memoryStorage();
    expect(await checkPinLocally('4826', local)).toBeNull();
    await savePinCheck('4826', local);
    expect(local.getItem('bmsc.pinCheck')).not.toContain('4826');
    expect(await checkPinLocally('4826', local)).toBe(true);
    expect(await checkPinLocally('4827', local)).toBe(false);
    clearPinCheck(local);
    expect(await checkPinLocally('4826', local)).toBeNull();
  });
});

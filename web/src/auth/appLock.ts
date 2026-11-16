/**
 * App lock for the worker app: a phone with a PIN asks for it again when the
 * app is reopened after being closed, or after it has been in the background
 * for a minute. A quick switch (reading a message, checking the time) does not
 * lock. The session itself stays; this only guards the screen.
 */

/** How long the app may sit in the background before it asks for the PIN. */
export const LOCK_AFTER_MS = 60 * 1000;

/** Wrong PINs on the lock screen before the phone signs out completely. */
export const MAX_LOCK_FAILURES = 5;

/** When the app was last hidden (survives the app being closed). */
export const HIDDEN_AT_KEY = 'bmsc.hiddenAt';
/** Set once the PIN (or a sign-in) was entered in this run of the app. */
export const UNLOCKED_KEY = 'bmsc.unlocked';
/** Salted hash of this phone's PIN, so the lock also works offline. */
export const PIN_CHECK_KEY = 'bmsc.pinCheck';
export const LOCK_FAILS_KEY = 'bmsc.lockFails';

function read(storage: Storage, key: string): string | null {
  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

function write(storage: Storage, key: string, value: string | null) {
  try {
    if (value == null) storage.removeItem(key);
    else storage.setItem(key, value);
  } catch {
    /* storage blocked: the lock simply does not persist */
  }
}

export function readHiddenAt(storage: Storage = localStorage): number | null {
  const raw = read(storage, HIDDEN_AT_KEY);
  const value = raw == null ? NaN : Number(raw);
  return Number.isFinite(value) ? value : null;
}

export function noteHidden(now = Date.now(), storage: Storage = localStorage) {
  write(storage, HIDDEN_AT_KEY, String(now));
}

export function clearHidden(storage: Storage = localStorage) {
  write(storage, HIDDEN_AT_KEY, null);
}

export function markUnlocked(session: Storage = sessionStorage, local: Storage = localStorage) {
  write(session, UNLOCKED_KEY, '1');
  write(local, LOCK_FAILS_KEY, null);
  clearHidden(local);
}

/** While locked, a reload must stay locked. */
export function markLocked(session: Storage = sessionStorage) {
  write(session, UNLOCKED_KEY, null);
}

export function isUnlockedThisRun(session: Storage = sessionStorage): boolean {
  return read(session, UNLOCKED_KEY) === '1';
}

/** True when the app was away long enough to ask for the PIN again. */
export function awayTooLong(now: number, hiddenAt: number | null, lockAfterMs = LOCK_AFTER_MS): boolean {
  return hiddenAt != null && now - hiddenAt >= lockAfterMs;
}

/**
 * Lock when the app opens: a phone with a PIN, and either a fresh start of the
 * app (nothing unlocked in this run) or a return after the grace period.
 */
export function shouldLockOnOpen(opts: {
  hasPin: boolean;
  unlockedThisRun: boolean;
  now: number;
  hiddenAt: number | null;
}): boolean {
  if (!opts.hasPin) return false;
  if (!opts.unlockedThisRun) return true;
  return awayTooLong(opts.now, opts.hiddenAt);
}

export function lockFailures(storage: Storage = localStorage): number {
  const n = Number(read(storage, LOCK_FAILS_KEY) ?? 0);
  return Number.isFinite(n) ? n : 0;
}

export function noteLockFailure(storage: Storage = localStorage): number {
  const n = lockFailures(storage) + 1;
  write(storage, LOCK_FAILS_KEY, String(n));
  return n;
}

function toHex(buf: ArrayBuffer): string {
  return Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, '0')).join('');
}

function fromHex(hex: string): Uint8Array<ArrayBuffer> {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i += 1) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

async function derive(pin: string, salt: Uint8Array<ArrayBuffer>): Promise<string> {
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(pin), 'PBKDF2', false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt, iterations: 150_000 }, key, 256);
  return toHex(bits);
}

/** Keep a salted hash of the PIN on this phone (never the PIN itself). */
export async function savePinCheck(pin: string, storage: Storage = localStorage): Promise<void> {
  try {
    const salt = crypto.getRandomValues(new Uint8Array(16));
    const hash = await derive(pin, salt);
    write(storage, PIN_CHECK_KEY, JSON.stringify({ salt: toHex(salt.buffer), hash }));
  } catch {
    /* no WebCrypto: the lock checks the PIN with the server instead */
  }
}

/** true / false, or null when this phone has no stored check yet. */
export async function checkPinLocally(pin: string, storage: Storage = localStorage): Promise<boolean | null> {
  const raw = read(storage, PIN_CHECK_KEY);
  if (!raw) return null;
  try {
    const { salt, hash } = JSON.parse(raw) as { salt: string; hash: string };
    return (await derive(pin, fromHex(salt))) === hash;
  } catch {
    return null;
  }
}

export function clearPinCheck(storage: Storage = localStorage) {
  write(storage, PIN_CHECK_KEY, null);
  write(storage, LOCK_FAILS_KEY, null);
}

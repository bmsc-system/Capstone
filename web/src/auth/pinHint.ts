/** PINs are 4 digits. */
export const PIN_LENGTH = 4;

/**
 * Set on a phone that has a PIN, so the login page opens on the keypad.
 * The key changed when PINs went from 6 to 4 digits: a phone with an old
 * 6-digit PIN opens on the password form and is offered a new PIN after login.
 */
export const PIN_HINT_KEY = 'bmsc_has_pin4';
export const OLD_PIN_HINT_KEY = 'bmsc_has_pin';

/**
 * The PIN is a phone feature: the login keypad, the PIN offer and the app lock
 * only appear on phones and tablets. A desktop always signs in with the password,
 * even after a worker has used it.
 */
export function isPinDevice(
  ua: string = typeof navigator === 'undefined' ? '' : navigator.userAgent,
  touchPoints: number = typeof navigator === 'undefined' ? 0 : navigator.maxTouchPoints || 0,
): boolean {
  if (/Android|iPhone|iPod|iPad|Mobile|Tablet/i.test(ua)) return true;
  // iPadOS reports itself as a Mac; a Mac has no touch screen.
  return /Macintosh/.test(ua) && touchPoints > 1;
}

/** True when this phone should open on the PIN keypad. */
export function phoneHasPinHint(): boolean {
  try {
    return isPinDevice() && localStorage.getItem(PIN_HINT_KEY) === '1';
  } catch {
    return false;
  }
}

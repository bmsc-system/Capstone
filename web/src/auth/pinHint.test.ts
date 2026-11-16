import { describe, expect, it } from 'vitest';
import { isPinDevice } from './pinHint';

const UA = {
  android: 'Mozilla/5.0 (Linux; Android 14; SM-A155F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Mobile Safari/537.36',
  iphone: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
  ipadOs: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15',
  windows: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36',
};

describe('isPinDevice', () => {
  it('is true on phones and tablets', () => {
    expect(isPinDevice(UA.android, 5)).toBe(true);
    expect(isPinDevice(UA.iphone, 5)).toBe(true);
    expect(isPinDevice(UA.ipadOs, 5)).toBe(true);
  });

  it('is false on a desktop, so the login page never opens on the keypad there', () => {
    expect(isPinDevice(UA.windows, 0)).toBe(false);
    expect(isPinDevice(UA.ipadOs, 0)).toBe(false); // a Mac
  });
});

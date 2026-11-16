import { AxiosError } from 'axios';
import { describe, expect, it, vi } from 'vitest';

const data = new Map<string, string>();
vi.stubGlobal('localStorage', {
  getItem: (k: string) => data.get(k) ?? null,
  setItem: (k: string, v: string) => void data.set(k, v),
  removeItem: (k: string) => void data.delete(k),
  clear: () => data.clear(),
});

const { getErrorMessage, NETWORK_MESSAGE, TIMEOUT_MESSAGE } = await import('./client');

describe('getErrorMessage', () => {
  it('says the server could not be reached instead of "Network Error"', () => {
    expect(getErrorMessage(new AxiosError('Network Error', 'ERR_NETWORK'))).toBe(NETWORK_MESSAGE);
  });

  it('keeps the timeout message and the server\'s own reason', () => {
    expect(getErrorMessage(new AxiosError('timeout of 20000ms exceeded', 'ECONNABORTED'))).toBe(TIMEOUT_MESSAGE);
    const refused = new AxiosError('Request failed', 'ERR_BAD_REQUEST', undefined, undefined, {
      status: 409,
      statusText: 'Conflict',
      headers: {},
      config: {} as never,
      data: { error: { message: 'Materials have not arrived.' } },
    });
    expect(getErrorMessage(refused)).toBe('Materials have not arrived.');
  });
});

import { AxiosError, type InternalAxiosRequestConfig } from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const data = new Map<string, string>();
vi.stubGlobal('localStorage', {
  getItem: (k: string) => data.get(k) ?? null,
  setItem: (k: string, v: string) => void data.set(k, v),
  removeItem: (k: string) => void data.delete(k),
  clear: () => data.clear(),
});
const { default: apiClient, forgetRecentWrites, REPEAT_WINDOW_MS } = await import('./client');
vi.stubGlobal('window', { location: { href: '' } });

/** Counts what reaches the network; `fail` makes the next answers errors. */
function serve(opts: { fail?: number; delayMs?: number } = {}) {
  let failures = opts.fail ?? 0;
  const sent: string[] = [];
  apiClient.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
    sent.push(`${config.method} ${config.url} ${config.data ?? ''}`);
    if (opts.delayMs) await new Promise((r) => setTimeout(r, opts.delayMs));
    const response = { status: 200, statusText: '', headers: {}, config, data: { id: sent.length } };
    if (failures > 0) {
      failures -= 1;
      throw new AxiosError('Request failed', 'ERR_BAD_REQUEST', config, undefined, { ...response, status: 400 });
    }
    return response;
  };
  return sent;
}

describe('a double-click sends a save once', () => {
  beforeEach(() => {
    forgetRecentWrites();
    data.clear();
  });

  it('an identical write while the first is on its way gets the same answer', async () => {
    const sent = serve({ delayMs: 20 });
    const body = { title: 'Shaft', clientId: 'c1' };
    const [a, b] = await Promise.all([apiClient.post('/job-orders', body), apiClient.post('/job-orders', body)]);
    expect(sent).toHaveLength(1);
    expect(a.data).toEqual(b.data);
  });

  it('an identical write right after a success is not sent again', async () => {
    const sent = serve();
    await apiClient.post('/consumables/purchases', { toolId: 't1', quantity: 2 });
    await apiClient.post('/consumables/purchases', { toolId: 't1', quantity: 2 });
    expect(sent).toHaveLength(1);
  });

  it('sends again once the moment has passed', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    try {
      const sent = serve();
      await apiClient.post('/tools/scan', { code: 'VERNIER-002' });
      vi.setSystemTime(Date.now() + REPEAT_WINDOW_MS + 50);
      await apiClient.post('/tools/scan', { code: 'VERNIER-002' });
      expect(sent).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it('a different body or address is a different save', async () => {
    const sent = serve();
    await apiClient.post('/consumables/purchases', { toolId: 't1', quantity: 2 });
    await apiClient.post('/consumables/purchases', { toolId: 't1', quantity: 3 });
    await apiClient.patch('/users/u1', { fullName: 'A' });
    await apiClient.patch('/users/u2', { fullName: 'A' });
    expect(sent).toHaveLength(4);
  });

  it('a failed write can be tried again at once', async () => {
    const sent = serve({ fail: 1 });
    await expect(apiClient.post('/job-orders', { title: 'Shaft' })).rejects.toBeInstanceOf(AxiosError);
    await apiClient.post('/job-orders', { title: 'Shaft' });
    expect(sent).toHaveLength(2);
  });

  it('deletes are guarded too', async () => {
    const sent = serve({ delayMs: 20 });
    await Promise.all([apiClient.delete('/consumables/purchases/p1'), apiClient.delete('/consumables/purchases/p1')]);
    expect(sent).toHaveLength(1);
  });
});

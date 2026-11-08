import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios';
import { connectivity } from '../offline/connectivity';
import { readDeviceId } from './device';
import { PIN_HINT_KEY } from '../auth/pinHint';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api/v1';

/** Sign-in keys only: the phone's waiting offline actions must survive. */
function clearSession() {
  localStorage.removeItem('accessToken');
  localStorage.removeItem('refreshToken');
  localStorage.removeItem('user');
}

/** Why the login page was opened; it shows a matching note. */
export type SessionEndReason = 'signed-out' | 'expired';

function endSession(reason?: SessionEndReason) {
  clearSession();
  if (reason === 'signed-out') localStorage.removeItem(PIN_HINT_KEY);
  window.location.href = reason ? `/login?reason=${reason}` : '/login';
}

function responseCode(error: unknown): string | undefined {
  if (!axios.isAxiosError(error)) return undefined;
  return (error.response?.data as { error?: { code?: string } } | undefined)?.error?.code;
}

export const REQUEST_TIMEOUT_MS = 20_000;
export const TIMEOUT_MESSAGE =
  'The server took too long to answer. Check your connection and try again.';

const apiClient = axios.create({
  baseURL: API_BASE,
  headers: { 'Content-Type': 'application/json' },
  timeout: REQUEST_TIMEOUT_MS,
});

const inflightGets = new Map<string, Promise<unknown>>();
const getOnce = apiClient.get.bind(apiClient);
apiClient.get = ((url: string, config?: object) => {
  const key = `${url}?${JSON.stringify((config as { params?: unknown } | undefined)?.params ?? {})}`;
  const pending = inflightGets.get(key);
  if (pending) return pending;
  const request = getOnce(url, config).finally(() => inflightGets.delete(key));
  inflightGets.set(key, request);
  return request;
}) as typeof apiClient.get;

/**
 * The same save sent twice in a row is a double-click, not two decisions.
 * While a write is on its way, and for a moment after it succeeds, an
 * identical write (same method, address and body) gets the first one's answer
 * instead of being sent again, so a panicked double-click cannot create two
 * job orders or record a purchase twice. Failures are not remembered, so
 * trying again after an error still sends.
 */
export const REPEAT_WINDOW_MS = 1500;
const recentWrites = new Map<string, { request: Promise<unknown>; doneAt?: number }>();

function writeKey(method: string, url: string, data: unknown): string | null {
  if (typeof FormData !== 'undefined' && data instanceof FormData) return null;
  if (typeof Blob !== 'undefined' && data instanceof Blob) return null;
  try {
    return `${method} ${url} ${JSON.stringify(data ?? null)}`;
  } catch {
    return null;
  }
}

function once<T>(key: string | null, send: () => Promise<T>): Promise<T> {
  if (!key) return send();
  const now = Date.now();
  for (const [k, v] of recentWrites) {
    if (v.doneAt !== undefined && now - v.doneAt > REPEAT_WINDOW_MS) recentWrites.delete(k);
  }
  const hit = recentWrites.get(key);
  if (hit) return hit.request as Promise<T>;
  const request = send();
  const entry: { request: Promise<unknown>; doneAt?: number } = { request };
  recentWrites.set(key, entry);
  request.then(
    () => {
      entry.doneAt = Date.now();
    },
    () => {
      if (recentWrites.get(key) === entry) recentWrites.delete(key);
    }
  );
  return request;
}

for (const method of ['post', 'put', 'patch'] as const) {
  const send = apiClient[method].bind(apiClient);
  apiClient[method] = ((url: string, data?: unknown, config?: object) =>
    once(writeKey(method, url, data), () => send(url, data, config))) as (typeof apiClient)[typeof method];
}
const sendDelete = apiClient.delete.bind(apiClient);
apiClient.delete = ((url: string, config?: { data?: unknown }) =>
  once(writeKey('delete', url, config?.data), () => sendDelete(url, config))) as typeof apiClient.delete;

/** Test hook: forget remembered writes. */
export function forgetRecentWrites() {
  recentWrites.clear();
}

apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = localStorage.getItem('accessToken');
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

let isRefreshing = false;
let failedQueue: Array<{
  resolve: (token: string) => void;
  reject: (err: unknown) => void;
}> = [];

const processQueue = (error: unknown, token: string | null = null) => {
  failedQueue.forEach((prom) => {
    if (error) prom.reject(error);
    else if (token) prom.resolve(token);
  });
  failedQueue = [];
};

apiClient.interceptors.response.use(
  (response) => {
    connectivity.reportReachable();
    return response;
  },
  async (error: AxiosError) => {
    if (error.response) connectivity.reportReachable();
    else if (!axios.isCancel(error)) connectivity.reportUnreachable();
    const originalRequest = error.config as InternalAxiosRequestConfig & { _retry?: boolean };

    if (error.response?.status === 401 && !originalRequest._retry) {
      const url = originalRequest.url || '';
      if (
        url.includes('/auth/login') ||
        url.includes('/auth/pin/unlock') ||
        url.includes('/auth/invitation/') ||
        url.includes('/auth/password-reset/')
      ) {
        return Promise.reject(error);
      }

      if (responseCode(error) === 'DEVICE_SIGNED_OUT') {
        endSession('signed-out');
        return Promise.reject(error);
      }

      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          failedQueue.push({
            resolve: (token: string) => {
              if (originalRequest.headers) {
                originalRequest.headers.Authorization = `Bearer ${token}`;
              }
              resolve(apiClient(originalRequest));
            },
            reject,
          });
        });
      }

      originalRequest._retry = true;
      isRefreshing = true;

      const refreshToken = localStorage.getItem('refreshToken');
      if (!refreshToken) {
        isRefreshing = false;
        endSession();
        return Promise.reject(error);
      }

      try {
        const { data } = await axios.post<{ accessToken: string; refreshToken?: string }>(
          `${API_BASE}/auth/refresh`,
          { deviceId: readDeviceId() },
          { headers: { Authorization: `Bearer ${refreshToken}` } }
        );
        localStorage.setItem('accessToken', data.accessToken);
        if (data.refreshToken) localStorage.setItem('refreshToken', data.refreshToken);
        processQueue(null, data.accessToken);
        if (originalRequest.headers) {
          originalRequest.headers.Authorization = `Bearer ${data.accessToken}`;
        }
        return apiClient(originalRequest);
      } catch (refreshError) {
        processQueue(refreshError, null);
        if (axios.isAxiosError(refreshError) && !refreshError.response) {
          // No connection: stay signed in; the request fails as offline.
          connectivity.reportUnreachable();
          return Promise.reject(refreshError);
        }
        endSession(responseCode(refreshError) === 'DEVICE_SIGNED_OUT' ? 'signed-out' : 'expired');
        return Promise.reject(refreshError);
      } finally {
        isRefreshing = false;
      }
    }

    return Promise.reject(error);
  }
);

export default apiClient;

/**
 * Tell the server this person signed out, for the Admin's audit log. Sent with
 * the token read now, before it is cleared; failures (offline) are ignored.
 */
export function recordSignOut(): void {
  const token = localStorage.getItem('accessToken');
  if (!token) return;
  axios
    .post(`${API_BASE}/auth/logout`, {}, { headers: { Authorization: `Bearer ${token}` }, timeout: 5000 })
    .catch(() => undefined);
}

export function getErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (!error.response && (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT')) {
      return TIMEOUT_MESSAGE;
    }
    const data = error.response?.data as { error?: { message?: string } };
    const apiMsg = data?.error?.message || error.message;
    if (apiMsg) return apiMsg;
  }
  return 'Something went wrong. Please try again, or ask the office if it keeps happening.';
}

export function getErrorCode(error: unknown): string | undefined {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data as { error?: { code?: string } };
    return data?.error?.code;
  }
  return undefined;
}

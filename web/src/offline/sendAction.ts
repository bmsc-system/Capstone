import axios from 'axios';
import apiClient from '../api/client';
import type { QueuedAction, SendResult } from './actionQueue';

const TIMEOUT_MS = 20_000;

/** Server refusals stop the queue; no reply, an expired session, or a server
 * fault leave the action waiting to be sent again. */
export function classifySendError(err: unknown): SendResult {
  if (!axios.isAxiosError(err) || !err.response) return { ok: false, kind: 'offline' };
  const { status } = err.response;
  if (status === 401) return { ok: false, kind: 'auth' };
  if (status >= 500 || status === 408 || status === 429) return { ok: false, kind: 'server' };
  const data = err.response.data as { error?: { code?: string; message?: string } } | undefined;
  return {
    ok: false,
    kind: 'refused',
    error: {
      status,
      code: data?.error?.code,
      message: data?.error?.message || `The server refused this action (${status}).`,
    },
  };
}

export function postAction(a: QueuedAction) {
  const body = { clientActionId: a.id, timestamp: a.at };
  const opts = { timeout: TIMEOUT_MS };
  switch (a.kind) {
    case 'start':
    case 'resume':
    case 'complete':
      return apiClient.post(`/operations/${a.operationId}/${a.kind}`, body, opts);
    case 'pause':
      return apiClient.post(
        `/operations/${a.operationId}/pause`,
        { ...body, reason: a.reason, ...(a.note ? { note: a.note } : {}) },
        opts
      );
    case 'breakdown':
      return apiClient.post(
        `/operations/machine-units/${a.machineUnitId}/downtime`,
        {
          clientActionId: a.id,
          startedAt: a.at,
          category: a.category,
          note: a.note,
          operationId: a.operationId,
        },
        opts
      );
    case 'consumable_scan':
      return apiClient.post(
        '/tools/scan',
        {
          code: a.scanCode,
          clientActionId: a.id,
          confirmRepeat: Boolean(a.confirmRepeat),
        },
        opts
      );
  }
}

export async function sendAction(a: QueuedAction): Promise<SendResult> {
  try {
    await postAction(a);
    return { ok: true };
  } catch (err) {
    return classifySendError(err);
  }
}

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import { jobOrdersApi } from '../api/jobOrders.api';
import { operationsApi } from '../api/operations.api';
import { useAuth } from '../hooks/useAuth';
import {
  createActionQueue,
  syncQueue,
  type NewQueuedAction,
  type QueuedAction,
} from './actionQueue';
import { useOnline } from './connectivity';
import { offlineCache } from './offlineCache';
import { classifySendError, postAction, sendAction } from './sendAction';

export const actionQueue = createActionQueue(localStorage);

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (e.key === null || e.key.startsWith('bmsc.offlineQueue')) actionQueue.reload();
  });
}

/** crypto.randomUUID needs HTTPS; getRandomValues works on plain HTTP too. */
function newActionId(): string {
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

const SYNC_RETRY_MS = 30_000;
const CACHE_REFRESH_MS = 5 * 60_000;

/** How long a repeated tap on the same action is treated as the same tap. */
const REPEAT_TAP_MS = 2000;

export type RecordInput = Omit<NewQueuedAction, 'id' | 'userId' | 'at'>;

export interface OfflineContextValue {
  online: boolean;
  userId: string | null;
  lastUpdated: string | null;
  /** This worker's actions still on the phone, oldest first. */
  pending: QueuedAction[];
  refused: QueuedAction | null;
  /** Actions on this phone belonging to someone else, sent when they sign in. */
  otherUsersPending: number;
  syncing: boolean;
  /** Bumps when server data changed (actions sent, cache refreshed). */
  version: number;
  /** Sends now when online with nothing waiting; otherwise keeps it on the
   * phone. Throws the server's refusal when sent directly. */
  record: (input: RecordInput) => Promise<'sent' | 'queued'>;
  retry: (id: string) => void;
  discard: (id: string) => void;
  syncNow: () => Promise<void>;
}

const OfflineContext = createContext<OfflineContextValue | null>(null);

function useQueueSnapshot(): string {
  return useSyncExternalStore(actionQueue.subscribe, () =>
    JSON.stringify(actionQueue.list())
  );
}

export function OfflineProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const userId = user?.id ?? null;
  const online = useOnline();
  const snapshot = useQueueSnapshot();
  const [version, setVersion] = useState(0);
  const [syncing, setSyncing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<string | null>(() =>
    userId ? offlineCache.read(userId).updatedAt : null
  );
  const lastRefresh = useRef(0);

  const all = useMemo(() => JSON.parse(snapshot) as QueuedAction[], [snapshot]);
  const pending = useMemo(() => all.filter((a) => a.userId === userId), [all, userId]);
  const refused = pending.find((a) => a.state === 'refused') || null;

  const refreshCache = useCallback(
    async (force = false) => {
      if (!userId) return;
      if (!force && Date.now() - lastRefresh.current < 60_000) return;
      lastRefresh.current = Date.now();
      try {
        const { data } = await operationsApi.mine();
        offlineCache.saveMine(userId, data);
        for (const jobId of [...new Set(data.map((o) => o.jobOrderId))]) {
          const { data: job } = await jobOrdersApi.get(jobId);
          offlineCache.saveJob(userId, job);
        }
        setLastUpdated(offlineCache.read(userId).updatedAt);
      } catch {
        /* offline or refused: keep what the phone has */
      }
    },
    [userId]
  );

  const syncNow = useCallback(async () => {
    if (!userId || !online || actionQueue.list(userId).length === 0) return;
    setSyncing(true);
    try {
      const result = await syncQueue(actionQueue, userId, sendAction);
      if (result.sent) {
        await refreshCache(true);
        setVersion((v) => v + 1);
      }
    } finally {
      setSyncing(false);
    }
  }, [userId, online, refreshCache]);

  useEffect(() => {
    setLastUpdated(userId ? offlineCache.read(userId).updatedAt : null);
  }, [userId]);

  // Send when the connection returns, on sign-in, and on coming back to the app.
  useEffect(() => {
    if (!online || !userId) return;
    void syncNow();
    void refreshCache().then(() => setVersion((v) => v + 1));
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        void syncNow();
        void refreshCache();
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    const retry = window.setInterval(() => void syncNow(), SYNC_RETRY_MS);
    const refresh = window.setInterval(() => void refreshCache(true), CACHE_REFRESH_MS);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.clearInterval(retry);
      window.clearInterval(refresh);
    };
  }, [online, userId, syncNow, refreshCache]);

  useEffect(() => {
    // Ask the browser not to evict the phone's saved actions under storage pressure.
    void navigator.storage?.persist?.().catch(() => undefined);
  }, []);

  // A second tap on Start, Resume or Complete while the first is still going
  // (or just finished) is the same decision: answer it with the first result
  // instead of sending a second action the server would refuse.
  const recentActions = useRef(new Map<string, { result: Promise<'sent' | 'queued'>; doneAt?: number }>());

  const recordOnce = useCallback(
    async (input: RecordInput): Promise<'sent' | 'queued'> => {
      if (!userId) throw new Error('Sign in again to record this.');
      const action: NewQueuedAction = {
        ...input,
        id: newActionId(),
        userId,
        at: new Date().toISOString(),
      };
      if (online && actionQueue.list(userId).length === 0) {
        try {
          await postAction({ ...action, seq: 0, state: 'waiting' });
          setVersion((v) => v + 1);
          void refreshCache(true);
          return 'sent';
        } catch (err) {
          const result = classifySendError(err);
          if (!result.ok && result.kind === 'refused') throw err;
        }
      }
      actionQueue.add(action);
      if (online) void syncNow();
      return 'queued';
    },
    [userId, online, refreshCache, syncNow]
  );

  const record = useCallback(
    (input: RecordInput): Promise<'sent' | 'queued'> => {
      const a = input as RecordInput & { operationId?: string; machineUnitId?: string; scanCode?: string };
      const key = [a.kind, a.operationId ?? '', a.machineUnitId ?? '', a.scanCode ?? ''].join(':');
      const now = Date.now();
      const seen = recentActions.current;
      for (const [k, v] of seen) if (v.doneAt !== undefined && now - v.doneAt > REPEAT_TAP_MS) seen.delete(k);
      const hit = seen.get(key);
      if (hit) return hit.result;
      const result = recordOnce(input);
      const entry: { result: Promise<'sent' | 'queued'>; doneAt?: number } = { result };
      seen.set(key, entry);
      result.then(
        () => {
          entry.doneAt = Date.now();
        },
        () => {
          if (seen.get(key) === entry) seen.delete(key);
        }
      );
      return result;
    },
    [recordOnce]
  );

  const retry = useCallback(
    (id: string) => {
      actionQueue.markWaiting(id);
      void syncNow();
    },
    [syncNow]
  );

  const discard = useCallback((id: string) => {
    actionQueue.remove(id);
    setVersion((v) => v + 1);
  }, []);

  const value: OfflineContextValue = {
    online,
    userId,
    lastUpdated,
    pending,
    refused,
    otherUsersPending: all.length - pending.length,
    syncing,
    version,
    record,
    retry,
    discard,
    syncNow,
  };

  return <OfflineContext.Provider value={value}>{children}</OfflineContext.Provider>;
}

/** Null outside the worker shell (e.g. Account security). */
export function useOfflineOptional(): OfflineContextValue | null {
  return useContext(OfflineContext);
}

export function useOffline(): OfflineContextValue {
  const ctx = useContext(OfflineContext);
  if (!ctx) throw new Error('useOffline must be used within OfflineProvider');
  return ctx;
}

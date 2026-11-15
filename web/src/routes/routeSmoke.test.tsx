/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ConfigProvider } from 'antd';
import type { User } from '../types';
import { TestAuthProvider } from '../hooks/useAuth';
import { NavMemoryProvider } from '../navigation/navMemory';
import ShopDetailsPage from '../features/settings/ShopDetailsPage';
import { ROUTE_SMOKES, type SmokeRole } from './routeSmokeManifest';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    default: {
      get: vi.fn(() => Promise.resolve({ data: [] })),
      post: vi.fn(() => Promise.resolve({ data: {} })),
      put: vi.fn(() => Promise.resolve({ data: {} })),
      patch: vi.fn(() => Promise.resolve({ data: {} })),
      delete: vi.fn(() => Promise.resolve({ data: {} })),
    },
  };
});

vi.mock('../api/shopDetails.api', () => ({
  shopDetailsApi: {
    get: () =>
      Promise.resolve({
        data: {
          shopName: 'Test Shop',
          tagline: '',
          address: '1 Main',
          telephone: '',
          mobileNumbers: '',
          email: '',
          poApproverName: '',
          poApproverTitle: '',
          joApproverName: '',
          joApproverTitle: '',
          updatedAt: null,
        },
      }),
    update: (data: unknown) => Promise.resolve({ data }),
  },
}));

vi.mock('../api/notifications.api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/notifications.api')>();
  return {
    ...actual,
    notificationsApi: {
      list: () => Promise.resolve({ data: [] }),
        resend: () => Promise.resolve({ data: {} }),
    },
  };
});

vi.mock('../api/schedule.api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/schedule.api')>();
  return {
    ...actual,
    scheduleApi: {
      board: () =>
        Promise.resolve({
          data: {
            from: '2026-10-19',
            to: '2026-10-25',
            machines: [],
            workers: [],
            operations: [],
            downtimes: [],
            shopDays: [],
          },
        }),
    },
  };
});

vi.mock('../api/jobOrders.api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/jobOrders.api')>();
  const empty = () => Promise.resolve({ data: [] });
  const one = () => Promise.resolve({ data: {} });
  return {
    ...actual,
    jobOrdersApi: new Proxy(
      {},
      { get: () => empty }
    ),
    clientsApi: { list: empty, get: one },
  };
});

vi.mock('../api/tools.api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/tools.api')>();
  return {
    ...actual,
    toolsApi: new Proxy({}, { get: () => () => Promise.resolve({ data: [] }) }),
  };
});

vi.mock('../api/users.api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/users.api')>();
  const empty = () => Promise.resolve({ data: [] });
  return {
    ...actual,
    usersApi: new Proxy({}, { get: () => empty }),
    workerProfileApi: new Proxy(
      {},
      { get: () => () => Promise.resolve({ data: { skills: [], schedule: [] } }) }
    ),
  };
});

vi.mock('../api/auth.api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/auth.api')>();
  return {
    ...actual,
    getOrCreateDeviceId: () => 'test-device',
    authApi: {
      ...actual.authApi,
      me: () => Promise.resolve({ data: {} }),
      logout: () => undefined,
      login: () => Promise.resolve({ data: { accessToken: 't', refreshToken: 'r', user: {} } }),
    },
  };
});

vi.mock('../api/operations.api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/operations.api')>();
  const empty = () => Promise.resolve({ data: [] });
  return {
    ...actual,
    operationsApi: new Proxy(actual.operationsApi, {
      get: (target, prop, receiver) => {
        if (prop in target) {
          const v = Reflect.get(target, prop, receiver);
          if (typeof v === 'function') return empty;
        }
        return empty;
      },
    }),
  };
});

vi.mock('../api/calendar.api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/calendar.api')>();
  return {
    ...actual,
    calendarApi: new Proxy(
      {},
      {
        get: () => () =>
          Promise.resolve({
            data: { days: [], exceptions: [], breakStart: '12:00', breakEnd: '13:00' },
          }),
      }
    ),
  };
});

vi.mock('../offline/OfflineProvider', () => {
  const value = {
    online: true,
    pendingCount: 0,
    needsConnection: false,
    userId: 'u-test',
    version: 0,
    pending: [],
    flush: async () => undefined,
  };
  return {
    OfflineProvider: ({ children }: { children: React.ReactNode }) => children,
    useOffline: () => value,
    useOfflineOptional: () => value,
    actionQueue: { enqueue: () => undefined, list: () => [] },
  };
});

vi.mock('../offline/offlineCache', () => ({
  offlineCache: {
    get: async () => null,
    set: async () => undefined,
    read: () => ({ mine: [] }),
  },
}));

vi.mock('../features/my-assignments/useMyOperations', () => {
  const value = {
    loaded: [],
    operations: [],
    loading: false,
    error: '',
    reload: () => undefined,
  };
  return {
    MyOperationsProvider: ({ children }: { children: React.ReactNode }) => children,
    useMyOperations: () => value,
    useMyOperationsOptional: () => value,
  };
});

vi.mock('../offline/overlay', () => ({
  overlayOperations: (ops: unknown) => ops ?? [],
}));

function stubUser(role: SmokeRole): User {
  return {
    id: `u-${role}`,
    email: `${role.toLowerCase()}@test.local`,
    fullName: role,
    nickname: role,
    role,
    active: true,
    status: 'ACTIVE',
  } as User;
}

describe('route smoke (every page mounts without throwing)', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
  });

  for (const route of ROUTE_SMOKES) {
    for (const role of route.roles) {
      it(`${route.path} as ${role}`, () => {
        expect(() => {
          render(
            <ConfigProvider>
              <MemoryRouter initialEntries={[route.path]}>
                <TestAuthProvider user={stubUser(role)}>
                  <NavMemoryProvider>
                    <route.Page />
                  </NavMemoryProvider>
                </TestAuthProvider>
              </MemoryRouter>
            </ConfigProvider>
          );
        }).not.toThrow();
      });
    }
  }

  it('Shop details does not throw useBlocker under a non-data router', () => {
    expect(() => {
      render(
        <ConfigProvider>
          <MemoryRouter>
            <TestAuthProvider user={stubUser('ADMIN')}>
              <ShopDetailsPage />
            </TestAuthProvider>
          </MemoryRouter>
        </ConfigProvider>
      );
    }).not.toThrow();
  });
});

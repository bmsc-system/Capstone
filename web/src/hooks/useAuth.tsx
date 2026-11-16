import axios from 'axios';
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { authApi } from '../api/auth.api';
import { recordSignOut } from '../api/client';
import { markUnlocked } from '../auth/appLock';
import type { LoginResponse, User } from '../types';

interface AuthContextType {
  user: User | null;
  loading: boolean;
  login: (identifier: string, password: string) => Promise<void>;
  applySession: (data: LoginResponse) => void;
  logout: () => void;
  isAdmin: boolean;
  isOfficeStaff: boolean;
  /** Office Staff write actions only; Admin views Office pages read-only. */
  canOfficeWrite: boolean;
  /** Add and edit client and supplier records: Office Staff and Admin. */
  canManageContacts: boolean;
  isWorker: boolean;
}

export const AuthContext = createContext<AuthContextType | null>(null);

/** Test / Story helpers: provide a fixed session without touching localStorage. */
export function TestAuthProvider({
  user,
  children,
}: {
  user: User;
  children: ReactNode;
}) {
  const role = user.role;
  return (
    <AuthContext.Provider
      value={{
        user,
        loading: false,
        login: async () => undefined,
        applySession: () => undefined,
        logout: () => undefined,
        isAdmin: role === 'ADMIN',
        isOfficeStaff: role === 'OFFICE_STAFF',
        canOfficeWrite: role === 'OFFICE_STAFF',
        canManageContacts: role === 'OFFICE_STAFF' || role === 'ADMIN',
        isWorker: role === 'PRODUCTION_WORKER',
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(() => {
    const stored = localStorage.getItem('user');
    return stored ? JSON.parse(stored) : null;
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem('accessToken');
    if (!token) {
      setLoading(false);
      return;
    }
    authApi
      .me()
      .then(({ data }) => {
        setUser(data);
        localStorage.setItem('user', JSON.stringify(data));
      })
      .catch((err) => {
        // Offline: keep the stored user so assignments open from the phone.
        if (axios.isAxiosError(err) && !err.response) return;
        authApi.logout();
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, []);

  const applySession = (data: LoginResponse) => {
    localStorage.setItem('accessToken', data.accessToken);
    localStorage.setItem('refreshToken', data.refreshToken);
    localStorage.setItem('user', JSON.stringify(data.user));
    // Signing in is proof enough: the app lock does not ask again in this run.
    markUnlocked();
    setUser(data.user);
  };

  const login = async (identifier: string, password: string) => {
    const { data } = await authApi.login(identifier, password);
    applySession(data);
  };

  const logout = () => {
    recordSignOut();
    authApi.logout();
    setUser(null);
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        login,
        applySession,
        logout,
        isAdmin: user?.role === 'ADMIN',
        isOfficeStaff: user?.role === 'OFFICE_STAFF',
        canOfficeWrite: user?.role === 'OFFICE_STAFF',
        canManageContacts: user?.role === 'OFFICE_STAFF' || user?.role === 'ADMIN',
        isWorker: user?.role === 'PRODUCTION_WORKER',
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}

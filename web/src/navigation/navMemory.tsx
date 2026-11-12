import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { useLocation } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';

/** Top-level sidebar / tab sections (longest prefixes first for matching). */
export const NAV_SECTIONS = [
  '/job-orders',
  '/my-assignments',
  '/worker-setup',
  '/attendance',
  '/audit-log',
  '/settings/shop',
  '/analytics',
  '/dashboard',
  '/schedule',
  '/machines',
  '/work-calendar',
  '/clients',
  '/supplier-orders',
  '/suppliers',
  '/tools',
  '/users',
  '/scan',
  '/home',
  '/inventory',
  '/account',
  '/notifications',
] as const;

export type NavSection = (typeof NAV_SECTIONS)[number];

const STORAGE_KEY = 'bmsc.navMemory.v1';

type Stored = {
  lastBySection: Record<string, string>;
};

function loadStored(): Stored {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return { lastBySection: {} };
    const parsed = JSON.parse(raw) as Stored;
    return { lastBySection: parsed.lastBySection || {} };
  } catch {
    return { lastBySection: {} };
  }
}

function saveStored(lastBySection: Record<string, string>) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ lastBySection }));
  } catch {
    /* ignore quota */
  }
}

/** Paths that live under another section's sidebar entry. */
const SECTION_ALIASES: Record<string, string> = {
  '/supplier-orders': '/suppliers',
};

export function getNavSection(pathname: string): string | null {
  const hit = NAV_SECTIONS.find(
    (s) => pathname === s || pathname.startsWith(`${s}/`)
  );
  if (!hit) return null;
  return SECTION_ALIASES[hit] || hit;
}

type NavMemoryValue = {
  lastBySection: Record<string, string>;
  /** Bumps when the user re-clicks the active section — clears keep-alive for that section. */
  generationBySection: Record<string, number>;
  /** Resolve sidebar/tab click: same section → reset to root; else → last path or root. */
  resolveSectionNav: (sectionKey: string) => {
    to: string;
    reset: boolean;
  };
  clearSectionCache: (sectionKey: string) => void;
  /**
   * Called during render by a route guard that redirects away. A denied path is
   * never remembered or kept alive: its cached <Navigate> only fires on mount,
   * so restoring it would leave an empty page.
   */
  markDenied: (fullPath: string) => void;
  isDenied: (fullPath: string) => boolean;
};

const NavMemoryContext = createContext<NavMemoryValue | null>(null);

export function NavMemoryProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const [lastBySection, setLastBySection] = useState<Record<string, string>>(
    () => loadStored().lastBySection
  );
  const [generationBySection, setGenerationBySection] = useState<
    Record<string, number>
  >({});
  const deniedRef = useRef<Set<string>>(new Set());
  const { user } = useAuth();

  useEffect(() => {
    deniedRef.current = new Set();
  }, [user?.id]);

  const markDenied = useCallback((fullPath: string) => {
    deniedRef.current.add(fullPath);
  }, []);
  const isDenied = useCallback((fullPath: string) => deniedRef.current.has(fullPath), []);

  // Track wherever the user goes so sidebar return restores that URL.
  useEffect(() => {
    const section = getNavSection(location.pathname);
    if (!section) return;
    // Don't remember login or bare redirects.
    if (location.pathname === '/' || location.pathname === '/login') return;
    const full = `${location.pathname}${location.search}`;
    const denied = deniedRef.current.has(full);
    setLastBySection((prev) => {
      if (denied) {
        if (prev[section] !== full) return prev;
        const next = { ...prev, [section]: section };
        saveStored(next);
        return next;
      }
      if (prev[section] === full) return prev;
      const next = { ...prev, [section]: full };
      saveStored(next);
      return next;
    });
  }, [location.pathname, location.search]);

  const clearSectionCache = useCallback((sectionKey: string) => {
    setGenerationBySection((prev) => ({
      ...prev,
      [sectionKey]: (prev[sectionKey] || 0) + 1,
    }));
    setLastBySection((prev) => {
      const next = { ...prev, [sectionKey]: sectionKey };
      saveStored(next);
      return next;
    });
  }, []);

  const resolveSectionNav = useCallback(
    (sectionKey: string) => {
      const current = getNavSection(location.pathname);
      if (current === sectionKey) {
        clearSectionCache(sectionKey);
        return { to: sectionKey, reset: true };
      }
      const remembered = lastBySection[sectionKey];
      const usable =
        remembered &&
        getNavSection(remembered.split('?')[0]) === sectionKey &&
        !deniedRef.current.has(remembered);
      return {
        to: usable ? remembered : sectionKey,
        reset: false,
      };
    },
    [location.pathname, lastBySection, clearSectionCache]
  );

  const value = useMemo(
    () => ({
      lastBySection,
      generationBySection,
      resolveSectionNav,
      clearSectionCache,
      markDenied,
      isDenied,
    }),
    [lastBySection, generationBySection, resolveSectionNav, clearSectionCache, markDenied, isDenied]
  );

  return (
    <NavMemoryContext.Provider value={value}>{children}</NavMemoryContext.Provider>
  );
}

export function useNavMemory() {
  const ctx = useContext(NavMemoryContext);
  if (!ctx) {
    throw new Error('useNavMemory must be used within NavMemoryProvider');
  }
  return ctx;
}

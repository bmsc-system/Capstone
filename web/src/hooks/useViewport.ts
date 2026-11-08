import { useSyncExternalStore } from 'react';

/** Phones: up to 767px. Tablets: 768–1024px (portrait and landscape). Desktop: wider. */
export const PHONE_QUERY = '(max-width: 767px)';
export const TABLET_QUERY = '(min-width: 768px) and (max-width: 1024px)';
/** Admin/Office touch control sizing (officeTouchTheme). Worker theme is separate. */
export const TOUCH_QUERY = '(max-width: 768px)';
/** Below Ant `lg`: the sidebar becomes a drawer and wide tables become cards. */
export const NARROW_QUERY = '(max-width: 991px)';
/** Admin / Office mobile shell (navy bar, compact controls): phones and tablets. */
export const MOBILE_SHELL_QUERY = '(max-width: 1023.98px)';
/** Tablet landscape and small laptops (1024–1279px): the sidebar starts as an icon rail. */
export const RAIL_QUERY = '(min-width: 1024px) and (max-width: 1279.98px)';

const subscribers = new Map<string, (onChange: () => void) => () => void>();

function subscribe(query: string) {
  let fn = subscribers.get(query);
  if (!fn) {
    fn = (onChange) => {
      const mql = window.matchMedia(query);
      mql.addEventListener('change', onChange);
      return () => mql.removeEventListener('change', onChange);
    };
    subscribers.set(query, fn);
  }
  return fn;
}

export function useMediaQuery(query: string) {
  return useSyncExternalStore(
    subscribe(query),
    () => window.matchMedia(query).matches,
    () => false
  );
}

export function useViewport() {
  const isPhone = useMediaQuery(PHONE_QUERY);
  const isTablet = useMediaQuery(TABLET_QUERY);
  const isNarrow = useMediaQuery(NARROW_QUERY);
  const isTouch = useMediaQuery(TOUCH_QUERY);
  return { isPhone, isTablet, isNarrow, isTouch };
}

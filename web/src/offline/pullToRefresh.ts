import { useEffect, useRef, useState, type RefObject } from 'react';

/** How far the indicator must be pulled down before letting go refreshes. */
export const PULL_TRIGGER = 64;
/** The indicator never grows past this. */
export const PULL_MAX = 96;
/** Finger travel before a downward drag counts as a pull (a tap or a small wobble is not). */
export const PULL_SLOP = 8;
/** Where the indicator rests while refreshing: the spot where letting go started it. */
export const PULL_REFRESHING = PULL_TRIGGER;

/** Finger travel to indicator height: half the distance, so it feels resisted, up to PULL_MAX. */
export function pullOffset(fingerTravel: number): number {
  if (fingerTravel <= 0) return 0;
  return Math.min(PULL_MAX, fingerTravel / 2);
}

export function pullLabel(offset: number, refreshing: boolean): string {
  if (refreshing) return 'Refreshing…';
  return offset >= PULL_TRIGGER ? 'Release to refresh' : 'Pull down to refresh';
}

type Options = {
  enabled: boolean;
  onRefresh: () => Promise<void>;
};

/**
 * Pull to refresh on a scroll area: a downward drag that starts while the area
 * is at its top grows an indicator; letting go past PULL_TRIGGER runs onRefresh.
 * A drag that starts lower down, goes sideways, or uses two fingers scrolls as usual.
 */
export function usePullToRefresh(ref: RefObject<HTMLElement | null>, { enabled, onRefresh }: Options) {
  const [offset, setOffset] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [dragging, setDragging] = useState(false);
  const latest = useRef({ enabled, onRefresh, refreshing });
  latest.current = { enabled, onRefresh, refreshing };

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let startX = 0;
    let startY = 0;
    let tracking = false;
    let pulling = false;
    // The height as of the last move, read on release (state may not have rendered yet).
    let current = 0;

    const reset = () => {
      tracking = false;
      pulling = false;
      setDragging(false);
    };

    const onStart = (e: TouchEvent) => {
      const { enabled: on, refreshing: busy } = latest.current;
      if (!on || busy || e.touches.length !== 1 || el.scrollTop > 0) return;
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
      tracking = true;
      pulling = false;
    };

    const onMove = (e: TouchEvent) => {
      if (!tracking) return;
      if (e.touches.length !== 1) {
        current = 0;
        setOffset(0);
        reset();
        return;
      }
      const dx = e.touches[0].clientX - startX;
      const dy = e.touches[0].clientY - startY;
      if (!pulling) {
        if (dy > PULL_SLOP && dy > Math.abs(dx) && el.scrollTop <= 0) {
          // A fast flick may send only a move or two, so this one counts too.
          pulling = true;
          setDragging(true);
        } else {
          if (dy < -PULL_SLOP || Math.abs(dx) > PULL_SLOP) tracking = false;
          return;
        }
      }
      // Hold the page still while the indicator follows the finger.
      if (e.cancelable) e.preventDefault();
      current = pullOffset(dy - PULL_SLOP);
      setOffset(current);
    };

    const onEnd = () => {
      if (!tracking) return;
      const wasPulling = pulling;
      const reached = current;
      current = 0;
      reset();
      if (!wasPulling) return;
      if (reached < PULL_TRIGGER) {
        setOffset(0);
        return;
      }
      setRefreshing(true);
      setOffset(PULL_REFRESHING);
      void latest.current
        .onRefresh()
        .catch(() => undefined)
        .finally(() => {
          setRefreshing(false);
          setOffset(0);
        });
    };

    el.addEventListener('touchstart', onStart, { passive: true });
    el.addEventListener('touchmove', onMove, { passive: false });
    el.addEventListener('touchend', onEnd);
    el.addEventListener('touchcancel', onEnd);
    return () => {
      el.removeEventListener('touchstart', onStart);
      el.removeEventListener('touchmove', onMove);
      el.removeEventListener('touchend', onEnd);
      el.removeEventListener('touchcancel', onEnd);
    };
  }, [ref]);

  return { offset, refreshing, dragging };
}

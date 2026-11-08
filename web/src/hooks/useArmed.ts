import { useEffect, useState } from 'react';

/** Taps that arrive within this long after a sheet or dialog opens are the
 *  rest of a quick double-tap on the button that opened it. */
export const ARM_DELAY_MS = 450;

/** False for a moment after `open` turns true, then true. A sheet uses it to
 *  ignore taps that were meant for the button underneath. */
export function useArmed(open: boolean, delayMs = ARM_DELAY_MS): boolean {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    setArmed(false);
    if (!open) return;
    const t = window.setTimeout(() => setArmed(true), delayMs);
    return () => window.clearTimeout(t);
  }, [open, delayMs]);
  return armed;
}

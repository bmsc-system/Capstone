import dayjs from 'dayjs';
import type { Operation } from '../../types';

/** Time worked on an operation so far, in milliseconds, from its start/pause/resume log. */
export function workedMsSoFar(op: Pick<Operation, 'timeLogs' | 'isPaused'>, nowMs: number): number {
  const logs = [...(op.timeLogs || [])].sort((a, b) => dayjs(a.eventAt).valueOf() - dayjs(b.eventAt).valueOf());
  let total = 0;
  let open: number | null = null;
  for (const log of logs) {
    const t = dayjs(log.eventAt).valueOf();
    if (log.event === 'START' || log.event === 'RESUME') {
      if (open == null) open = t;
    } else if (log.event === 'PAUSE' || log.event === 'COMPLETE') {
      if (open != null && t > open) total += t - open;
      open = null;
    }
  }
  if (open != null && !op.isPaused) total += Math.max(0, nowMs - open);
  return total;
}

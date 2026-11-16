import type { ScheduleBoardOperation } from '../../api/schedule.api';

/** Looks the board can show, for a legend that lists only what is on it. */
export type BoardState = 'job' | 'completed' | 'waiting' | 'late' | 'breakdown' | 'clash' | 'down' | 'stalled';

/** Which looks are on the laid-out rows. */
export function boardStates(
  prepared: {
    opById: Map<string, ScheduleBoardOperation>;
    bars: { opId: string; left: number; width: number; clash: boolean }[];
    downRects: { left: number; width: number }[];
  }[]
): Set<BoardState> {
  const found = new Set<BoardState>();
  for (const { opById, bars, downRects } of prepared) {
    if (downRects.length) found.add('breakdown');
    for (const op of opById.values()) {
      found.add('job');
      if (op.status === 'COMPLETED') found.add('completed');
      if (op.waitingForMaterials) found.add('waiting');
      if (op.isLate) found.add('late');
      if (op.stalled) found.add('stalled');
      if (op.onDownUnit) found.add('down');
    }
    for (const bar of bars) {
      if (bar.clash) found.add('clash');
      if (downRects.some((d) => d.left < bar.left + bar.width && bar.left < d.left + d.width)) found.add('down');
    }
  }
  return found;
}

import { describe, expect, it } from 'vitest';
import { boardStates } from './boardStates';
import type { ScheduleBoardOperation } from '../../api/schedule.api';

const op = (id: string, extra: Partial<ScheduleBoardOperation> = {}) =>
  ({ id, status: 'SCHEDULED', ...extra }) as ScheduleBoardOperation;

const row = (
  ops: ScheduleBoardOperation[],
  bars: { opId: string; left: number; width: number; clash: boolean }[] = [],
  downRects: { left: number; width: number }[] = []
) => ({ opById: new Map(ops.map((o) => [o.id, o])), bars, downRects });

describe('boardStates', () => {
  it('lists nothing for an empty board', () => {
    expect([...boardStates([row([])])]).toEqual([]);
  });

  it('lists only the plain job color for ordinary work', () => {
    const states = boardStates([row([op('a')], [{ opId: 'a', left: 0, width: 40, clash: false }])]);
    expect([...states]).toEqual(['job']);
  });

  it('finds clashes, down machines and the other warnings', () => {
    const states = boardStates([
      row(
        [op('a', { status: 'COMPLETED' }), op('b', { waitingForMaterials: true, isLate: true, stalled: true })],
        [
          { opId: 'a', left: 0, width: 40, clash: true },
          { opId: 'b', left: 100, width: 40, clash: false },
        ],
        [{ left: 120, width: 30 }]
      ),
    ]);
    expect(states).toEqual(
      new Set(['job', 'completed', 'waiting', 'late', 'stalled', 'clash', 'breakdown', 'down'])
    );
  });

  it('shows a breakdown without "booked on a down machine" when no work overlaps it', () => {
    const states = boardStates([
      row([op('a')], [{ opId: 'a', left: 0, width: 40, clash: false }], [{ left: 60, width: 20 }]),
    ]);
    expect(states.has('breakdown')).toBe(true);
    expect(states.has('down')).toBe(false);
  });
});

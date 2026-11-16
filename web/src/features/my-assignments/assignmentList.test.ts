import { describe, expect, it } from 'vitest';
import { filterChips, filterOps, sortOps, whenLine } from './assignmentList';

const op = (id: string, extra: Record<string, unknown>) => ({ id, status: 'SCHEDULED', ...extra });

describe('worker assignment list', () => {
  const active = [
    op('a', { jobCreatedAt: '2026-11-01T00:00:00Z', scheduledStart: '2026-11-20T00:00:00Z' }),
    op('b', { jobCreatedAt: '2026-11-10T00:00:00Z', scheduledStart: '2026-11-18T00:00:00Z' }),
    op('c', { jobCreatedAt: '2026-11-05T00:00:00Z', scheduledStart: '2026-11-25T00:00:00Z', status: 'IN_PROGRESS', actualStart: '2026-11-16T00:00:00Z' }),
  ];

  it('shows the latest job first by default and can sort the other ways', () => {
    expect(sortOps(active, 'active', 'newest').map((o) => o.id)).toEqual(['b', 'c', 'a']);
    expect(sortOps(active, 'active', 'oldest').map((o) => o.id)).toEqual(['a', 'c', 'b']);
    expect(sortOps(active, 'active', 'soonest').map((o) => o.id)).toEqual(['c', 'b', 'a']);
  });

  it('sorts finished work by when it was done', () => {
    const done = [
      op('x', { status: 'COMPLETED', actualEnd: '2026-02-09T00:00:00Z' }),
      op('y', { status: 'COMPLETED', actualEnd: '2026-03-14T00:00:00Z' }),
    ];
    expect(sortOps(done, 'completed', 'newest').map((o) => o.id)).toEqual(['y', 'x']);
  });

  it('offers a chip per status present and filters by it', () => {
    const chips = filterChips(active, 'active');
    expect(chips.map((c) => `${c.label} ${c.count}`)).toEqual(['All 3', 'Scheduled 2', 'In progress 1']);
    expect(filterOps(active, 'active', 'In progress').map((o) => o.id)).toEqual(['c']);
  });

  it('says when and where in one line', () => {
    expect(whenLine({ status: 'COMPLETED', actualEnd: '2026-02-09T05:00:00Z', actualWorkedHours: 5.5, machineUnitLabel: 'Shaper #1' })).toBe(
      'Done Feb 9 · 5h 30m · Shaper #1'
    );
    expect(whenLine({ status: 'SCHEDULED', scheduledStart: '2026-03-02T00:00:00Z', machineUnitLabel: 'Lathe #1' })).toBe(
      'Starts Mar 2, 8:00 AM · Lathe #1'
    );
  });
});

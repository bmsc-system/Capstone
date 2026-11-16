import { describe, expect, it } from 'vitest';
import { newestFirst } from './newestFirst';

describe('newestFirst', () => {
  it('puts the latest entry on top and undated ones last', () => {
    const rows = [
      { id: 'a', createdAt: '2026-11-01T08:00:00+00:00' },
      { id: 'b', createdAt: null },
      { id: 'c', createdAt: '2026-11-15T08:00:00+00:00' },
      { id: 'd', createdAt: '2026-11-10T08:00:00+08:00' },
    ];
    expect(newestFirst(rows).map((r) => r.id)).toEqual(['c', 'd', 'a', 'b']);
  });

  it('can sort by another time and leaves the input alone', () => {
    const rows = [{ id: 'x', at: '2026-01-01' }, { id: 'y', at: '2026-02-01' }];
    expect(newestFirst(rows, (r) => r.at).map((r) => r.id)).toEqual(['y', 'x']);
    expect(rows[0].id).toBe('x');
  });
});

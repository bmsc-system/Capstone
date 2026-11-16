import { describe, expect, it } from 'vitest';
import {
  fmtCompact,
  fmtMonth,
  fmtSignedPct,
  onTimePct,
  pctChange,
  previousPeriod,
  ptsChange,
  symmetricBound,
} from './format';

describe('analytics format', () => {
  it('uses one month format', () => {
    expect(fmtMonth('2026-10')).toBe('Oct 2026');
    expect(fmtMonth(null)).toBe('—');
  });

  it('compacts large numbers', () => {
    expect(fmtCompact(218345)).toBe('218k');
    expect(fmtCompact(1250000)).toBe('1.3M');
    expect(fmtCompact(42)).toBe('42');
  });

  it('moves to millions once rounding reaches 1,000k', () => {
    expect(fmtCompact(999_800)).toBe('1.0M');
    expect(fmtCompact(999_400)).toBe('999k');
    expect(fmtCompact(9_960_000)).toBe('10M');
    expect(fmtCompact(999.6)).toBe('1k');
    expect(fmtCompact(-999_800)).toBe('-1.0M');
  });

  it('signs differences from target', () => {
    expect(fmtSignedPct(12.4)).toBe('+12%');
    expect(fmtSignedPct(-8)).toBe('−8%');
    expect(fmtSignedPct(0.2)).toBe('0%');
  });

  it('compares with the previous period', () => {
    expect(pctChange(120, 100)).toBe(20);
    expect(pctChange(80, 100)).toBe(-20);
    expect(pctChange(5, 0)).toBeNull();
    expect(pctChange(5, null)).toBeNull();
    expect(ptsChange(71, 84)).toBe(-13);
    expect(ptsChange(71, null)).toBeNull();
  });

  it('takes the same number of days just before the period', () => {
    expect(previousPeriod({ from: '2026-08-17', to: '2026-10-12' })).toEqual({
      from: '2026-06-21',
      to: '2026-08-16',
    });
  });

  it('counts on time over delivered jobs', () => {
    expect(onTimePct(3, 1)).toBe(75);
    expect(onTimePct(0, 0)).toBeNull();
  });

  it('rounds a symmetric axis to a clean step', () => {
    expect(symmetricBound([-14, 6])).toBe(20);
    expect(symmetricBound([-100, 3])).toBe(150);
    expect(symmetricBound([2])).toBe(20);
  });
});

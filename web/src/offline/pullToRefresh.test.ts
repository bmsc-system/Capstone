import { describe, expect, it } from 'vitest';
import { PULL_MAX, PULL_TRIGGER, pullLabel, pullOffset } from './pullToRefresh';

describe('pullOffset', () => {
  it('moves the indicator half as far as the finger, never past the maximum', () => {
    expect(pullOffset(-20)).toBe(0);
    expect(pullOffset(0)).toBe(0);
    expect(pullOffset(80)).toBe(40);
    expect(pullOffset(1000)).toBe(PULL_MAX);
  });

  it('needs a real pull to reach the trigger', () => {
    expect(pullOffset(PULL_TRIGGER * 2 - 2)).toBeLessThan(PULL_TRIGGER);
    expect(pullOffset(PULL_TRIGGER * 2)).toBe(PULL_TRIGGER);
  });
});

describe('pullLabel', () => {
  it('tells the worker what letting go will do', () => {
    expect(pullLabel(10, false)).toBe('Pull down to refresh');
    expect(pullLabel(PULL_TRIGGER, false)).toBe('Release to refresh');
    expect(pullLabel(0, true)).toBe('Refreshing…');
  });
});

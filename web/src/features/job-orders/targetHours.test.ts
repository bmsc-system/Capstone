import { describe, expect, it } from 'vitest';
import type { TargetHoursHint } from '../../types';
import { autoHours, hintDetail, hintLabel } from './targetHours';

const hints: Record<string, TargetHoursHint> = {
  turn: { hours: 4, basis: 'SAME_JOB', samples: 1, jobNumber: 'JO-2026-0001', pastHours: 2, pastQuantity: 5, quantity: 10 },
  mill: { hours: 3, basis: 'PAST_JOBS', samples: 6 },
};

describe('autoHours', () => {
  it('fills an empty box from the suggestion', () => {
    expect(autoHours({ operationTypeId: 'mill', estimatedHours: null }, hints)).toEqual({
      operationTypeId: 'mill',
      estimatedHours: 3,
      hoursAuto: true,
    });
  });

  it('never replaces hours the Admin typed', () => {
    const row = { operationTypeId: 'mill', estimatedHours: 5, hoursAuto: false };
    expect(autoHours(row, hints)).toBe(row);
  });

  it('follows a change of operation while the hours are still the suggested ones', () => {
    expect(autoHours({ operationTypeId: 'turn', estimatedHours: 3, hoursAuto: true }, hints).estimatedHours).toBe(4);
  });

  it('clears suggested hours when the new operation has no history', () => {
    expect(autoHours({ operationTypeId: 'weld', estimatedHours: 3, hoursAuto: true }, hints)).toMatchObject({
      estimatedHours: null,
      hoursAuto: false,
    });
  });

  it('leaves an empty box empty when there is no history', () => {
    const row = { operationTypeId: 'weld', estimatedHours: null };
    expect(autoHours(row, hints)).toBe(row);
  });
});

describe('labels', () => {
  it('names the earlier job or the number of past jobs', () => {
    expect(hintLabel(hints.turn)).toBe('As on JO-2026-0001');
    expect(hintLabel(hints.mill)).toBe('From 6 past jobs');
  });

  it('explains the quantity scaling', () => {
    expect(hintDetail(hints.turn)).toContain('2 h on JO-2026-0001 for 5 pcs, so 4 h for 10 pcs');
  });
});

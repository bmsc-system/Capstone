import { describe, expect, it } from 'vitest';
import type { MachineUnitInfo } from '../../types';
import { fasterPairText, fasterUnitText, pairKey, pairedUnitId, parsePairKey, scoreBreakdown, unitPairText } from './unitPairing';

const day = (iso: string) => iso.slice(5, 10);

const LATHE = 'lathe';
const units: MachineUnitInfo[] = [
  { id: 'l4', machineTypeId: LATHE, label: 'Lathe #4', defaultOperatorId: 'ben', defaultOperatorName: 'Ben Santos' },
  { id: 'l5', machineTypeId: LATHE, label: 'Lathe #5', defaultOperatorId: 'juan', defaultOperatorName: 'Juan Cruz' },
  { id: 'l10', machineTypeId: LATHE, label: 'Lathe #10' },
  { id: 'l7', machineTypeId: LATHE, label: 'Lathe #7' },
  { id: 'm1', machineTypeId: 'mill', label: 'Milling #1' },
];

describe('worker and unit pairing', () => {
  it('gives a lead their own unit', () => {
    expect(pairedUnitId('juan', LATHE, units)).toBe('l5');
  });

  it('gives a lead with no unit the first open unit of the type', () => {
    expect(pairedUnitId('carlo', LATHE, units)).toBe('l7');
  });

  it('keeps a suggested unit that is theirs or open', () => {
    expect(pairedUnitId('juan', LATHE, units, 'l10')).toBe('l10');
  });

  it('prefers their own unit over a suggested unit of another operator', () => {
    expect(pairedUnitId('juan', LATHE, units, 'l4')).toBe('l5');
  });

  it('falls back to the suggested unit of another operator when nothing else exists', () => {
    const onlyOwned = units.filter((u) => u.defaultOperatorId === 'ben');
    expect(pairedUnitId('juan', LATHE, onlyOwned, 'l4')).toBe('l4');
  });

  it('labels the pair, naming the owner of another operator’s unit', () => {
    expect(unitPairText(units[1], 'juan')).toBe('Lathe #5');
    expect(unitPairText(units[2], 'juan')).toBe('Lathe #10');
    expect(unitPairText(units[0], 'juan')).toBe("Lathe #4 · Ben's unit");
  });
});

describe('faster unit suggestion text', () => {
  const base = {
    machineUnitId: 'l4',
    machineUnitLabel: 'Lathe #4',
    scheduledStart: '2026-10-13T00:00:00Z',
    scheduledEnd: '2026-10-13T04:00:00Z',
    projectedCompletion: '2026-10-14T08:00:00Z',
  };

  it('says how much earlier and both finish dates, naming another operator', () => {
    expect(
      fasterUnitText(
        {
          ...base,
          ownership: 'OTHER',
          operatorName: 'Ben Santos',
          hoursEarlier: 7,
          currentProjectedCompletion: '2026-10-21T08:00:00Z',
        },
        day,
      ),
    ).toBe("Lathe #4 (Ben's unit) is free 7 h earlier · job finishes 10-14 instead of 10-21");
  });

  it('names the faster worker and unit and the earlier finish', () => {
    expect(
      fasterPairText(
        {
          assignedWorkerId: 'emerson',
          workerName: 'Worker A',
          workerNickname: 'Emerson',
          machineUnitId: 's2',
          machineUnitLabel: 'Shaper #2',
          scheduledStart: '2026-10-27T00:00:00Z',
          scheduledEnd: '2026-10-27T08:00:00Z',
          projectedCompletion: '2026-10-28T00:00:00Z',
        },
        (iso) => (iso.startsWith('2026-10-28') ? 'Oct 28' : iso),
      ),
    ).toBe('Use Emerson + Shaper #2 → finishes Oct 28');
  });

  it('round-trips a worker and unit as a select value', () => {
    expect(parsePairKey(pairKey('w1', 'u2'))).toEqual({ workerId: 'w1', machineUnitId: 'u2' });
  });

  it('shows each score as points of its weight', () => {
    expect(
      scoreBreakdown(
        {
          workerId: 'w',
          fullName: 'A',
          email: null,
          skills: [],
          score: 0.7,
          matchedSkills: [],
          components: { operator: 1, skill: 0.6, efficiency: 0.5, workload: 1 },
        },
        { operator: 0.4, skill: 0.3, efficiency: 0.2, workload: 0.1 },
      ),
    ).toBe('Assigned operator 40 of 40 · Skill 18 of 30 · Performance 10 of 20 · Workload 10 of 10');
  });

  it('explains when the saved unit has no time at all', () => {
    expect(
      fasterUnitText({ ...base, ownership: 'OPEN', hoursEarlier: null, currentProjectedCompletion: null }, day),
    ).toBe('Lathe #4 is free · job can finish 10-14; the saved unit has no time for it');
  });
});

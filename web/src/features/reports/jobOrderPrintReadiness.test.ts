import { describe, expect, it } from 'vitest';
import type { JobOrder, Operation } from '../../types';
import { jobOrderPrintBlockers } from './jobOrderPrintReadiness';

const op = (o: Partial<Operation>): Operation =>
  ({
    id: 'op',
    sequenceNo: 1,
    operationName: 'Facing',
    status: 'PENDING',
    machineTypeId: 'mt',
    machineUnitId: 'mu',
    assignedWorkerId: 'w',
    scheduledStart: '2026-03-18T01:00:00Z',
    scheduledEnd: '2026-03-18T03:00:00Z',
    ...o,
  }) as Operation;

const job = (status: JobOrder['status'], operations: Operation[]) => ({ status, operations });

describe('jobOrderPrintBlockers', () => {
  it('blocks drafts and cancelled jobs', () => {
    expect(jobOrderPrintBlockers(job('DRAFT', [op({})]))).toHaveLength(1);
    expect(jobOrderPrintBlockers(job('CANCELLED', [op({})]))).toEqual(['It was cancelled.']);
  });

  it('allows a released job with every operation filled in', () => {
    expect(jobOrderPrintBlockers(job('SCHEDULED', [op({})]))).toEqual([]);
  });

  it('names what each operation is missing', () => {
    const out = jobOrderPrintBlockers(
      job('SCHEDULED', [op({ machineUnitId: null, scheduledStart: null }), op({ sequenceNo: 2, assignedWorkerId: null })]),
    );
    expect(out).toEqual([
      'Operation 1 (Facing) has no machine unit and schedule.',
      'Operation 2 (Facing) has no workers.',
    ]);
  });

  it('does not ask for a machine on an operation that needs none', () => {
    expect(jobOrderPrintBlockers(job('SCHEDULED', [op({ machineTypeId: null, machineUnitId: null })]))).toEqual([]);
  });
});

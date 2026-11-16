import type { JobOrder } from '../../types';

/**
 * Why a job order can't be printed yet. A printed JO goes to the floor, so it
 * must be released and every operation must have its machine unit, crew and
 * schedule. An empty list means it is ready to print.
 */
export function jobOrderPrintBlockers(job: Pick<JobOrder, 'status' | 'operations'>): string[] {
  if (job.status === 'DRAFT') return ['It has not been planned and released yet.'];
  if (job.status === 'CANCELLED') return ['It was cancelled.'];
  const ops = [...(job.operations || [])].sort((a, b) => a.sequenceNo - b.sequenceNo);
  if (ops.length === 0) return ['It has no operations.'];
  const out: string[] = [];
  for (const op of ops) {
    const missing: string[] = [];
    const needsMachine = Boolean(op.machineTypeId || op.machineTypeCode);
    if (needsMachine && !op.machineUnitId && !op.machineUnitLabel) missing.push('machine unit');
    if (!op.assignedWorkerId && !op.assignedWorkerName) missing.push('workers');
    if (!op.scheduledStart || !op.scheduledEnd) missing.push('schedule');
    if (missing.length) {
      const list =
        missing.length > 1 ? `${missing.slice(0, -1).join(', ')} and ${missing[missing.length - 1]}` : missing[0];
      out.push(`Operation ${op.sequenceNo} (${op.operationName}) has no ${list}.`);
    }
  }
  return out;
}

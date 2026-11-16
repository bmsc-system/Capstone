import type { TargetHoursHint } from '../../types';

type Row = {
  operationTypeId?: string;
  estimatedHours?: number | null;
  hoursAuto?: boolean;
};

const fmt = (h?: number | null) =>
  h == null ? '' : `${Number(h).toLocaleString(undefined, { maximumFractionDigits: 2 })} h`;

const pcs = (q?: number | null) =>
  q == null ? '' : `${Number(q).toLocaleString(undefined, { maximumFractionDigits: 2 })} pcs`;

/** Short note under the Target box. */
export function hintLabel(hint: TargetHoursHint): string {
  return hint.basis === 'SAME_JOB'
    ? `As on ${hint.jobNumber}`
    : `From ${hint.samples} past jobs`;
}

/** Longer explanation for the note's tooltip. */
export function hintDetail(hint: TargetHoursHint): string {
  if (hint.basis === 'SAME_JOB') {
    const scaled =
      hint.pastQuantity && hint.quantity && hint.pastQuantity !== hint.quantity
        ? ` for ${pcs(hint.pastQuantity)}, so ${fmt(hint.hours)} for ${pcs(hint.quantity)}`
        : '';
    return `The same job for this client took ${fmt(hint.pastHours)} on ${hint.jobNumber}${scaled}. You can change it.`;
  }
  return `The usual hours worked on the last ${hint.samples} finished jobs with this operation. You can change it.`;
}

/**
 * Hours for a row whose operation is set: the suggestion fills an empty box,
 * or replaces a number the system filled before (never one the Admin typed).
 */
export function autoHours<T extends Row>(row: T, hints: Record<string, TargetHoursHint>): T {
  const hint = row.operationTypeId ? hints[row.operationTypeId] : undefined;
  const empty = row.estimatedHours == null;
  if (!empty && !row.hoursAuto) return row;
  if (hint) return { ...row, estimatedHours: hint.hours, hoursAuto: true };
  return row.hoursAuto ? { ...row, estimatedHours: null, hoursAuto: false } : row;
}

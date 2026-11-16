import type { Dayjs } from 'dayjs';
import type { Operation } from '../../types';
import { formatHoursMinutes } from '../../utils/duration';
import { shopToday, toShopDayjs } from '../../utils/shopTime';
import { machineLabel, opStatusPill } from './opStatus';

export type AssignmentTab = 'active' | 'completed';
export type AssignmentSort = 'newest' | 'oldest' | 'soonest';

type Op = Pick<
  Operation,
  | 'status'
  | 'dueDate'
  | 'isPaused'
  | 'waitingForMaterials'
  | 'jobCreatedAt'
  | 'scheduledStart'
  | 'actualStart'
  | 'actualEnd'
  | 'actualWorkedHours'
  | 'machineUnitLabel'
  | 'machineTypeName'
  | 'machineNames'
>;

export const SORT_OPTIONS: Record<AssignmentTab, { value: AssignmentSort; label: string }[]> = {
  active: [
    { value: 'newest', label: 'Newest first' },
    { value: 'oldest', label: 'Oldest first' },
    { value: 'soonest', label: 'Starts soonest' },
  ],
  completed: [
    { value: 'newest', label: 'Newest first' },
    { value: 'oldest', label: 'Oldest first' },
  ],
};

const t = (iso?: string | null) => {
  const v = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(v) ? null : v;
};

/** The time "newest" goes by: when the job came in, or when the work was finished. */
function addedAt(op: Op, tab: AssignmentTab): number | null {
  return tab === 'completed' ? t(op.actualEnd) : t(op.jobCreatedAt) ?? t(op.scheduledStart);
}

export function sortOps<T extends Op>(ops: readonly T[], tab: AssignmentTab, sort: AssignmentSort): T[] {
  const key = (op: T) => (sort === 'soonest' ? t(op.actualStart) ?? t(op.scheduledStart) : addedAt(op, tab));
  const dir = sort === 'newest' ? -1 : 1;
  return [...ops].sort((a, b) => {
    const ka = key(a);
    const kb = key(b);
    if (ka === kb) return 0;
    if (ka == null) return 1;
    if (kb == null) return -1;
    return (ka - kb) * dir;
  });
}

/** Filter chips for a tab: the status labels present (active) or a period (completed). */
export function filterChips<T extends Op>(ops: readonly T[], tab: AssignmentTab): { key: string; label: string; count: number }[] {
  if (tab === 'completed') {
    return [
      { key: 'all', label: 'All', count: ops.length },
      { key: 'week', label: 'This week', count: ops.filter((o) => inPeriod(o, 'week')).length },
      { key: 'month', label: 'This month', count: ops.filter((o) => inPeriod(o, 'month')).length },
    ];
  }
  const counts = new Map<string, number>();
  for (const op of ops) {
    const label = opStatusPill(op).label;
    counts.set(label, (counts.get(label) || 0) + 1);
  }
  return [
    { key: 'all', label: 'All', count: ops.length },
    ...[...counts.entries()].map(([label, count]) => ({ key: label, label, count })),
  ];
}

function inPeriod(op: Op, period: 'week' | 'month', today: Dayjs = shopToday()): boolean {
  if (!op.actualEnd) return false;
  const done = toShopDayjs(op.actualEnd);
  return period === 'week'
    ? !done.isBefore(today.subtract(6, 'day'), 'day')
    : done.isSame(today, 'month');
}

export function filterOps<T extends Op>(ops: readonly T[], tab: AssignmentTab, key: string): T[] {
  if (key === 'all') return [...ops];
  if (tab === 'completed') return ops.filter((o) => inPeriod(o, key as 'week' | 'month'));
  return ops.filter((o) => opStatusPill(o).label === key);
}

function when(iso: string, today: Dayjs = shopToday()): string {
  const d = toShopDayjs(iso);
  if (d.isSame(today, 'day')) return `today, ${d.format('h:mm A')}`;
  if (d.isSame(today.add(1, 'day'), 'day')) return `tomorrow, ${d.format('h:mm A')}`;
  return d.format('MMM D, h:mm A');
}

/** One short line under the job: when it starts (or started, or was done) and where. */
export function whenLine(op: Op): string {
  const machine = machineLabel(op);
  let lead = '';
  if (op.status === 'COMPLETED') {
    lead = op.actualEnd ? `Done ${toShopDayjs(op.actualEnd).format('MMM D')}` : 'Done';
    if (op.actualWorkedHours != null) lead += ` · ${formatHoursMinutes(op.actualWorkedHours)}`;
  } else if (op.actualStart) {
    lead = `Started ${when(op.actualStart)}`;
  } else if (op.scheduledStart) {
    lead = `Starts ${when(op.scheduledStart)}`;
  }
  return [lead, machine].filter(Boolean).join(' · ');
}

/** The day shown in the tile on the left of a card: when it was done, started or starts. */
export function dateTile(op: Op, today: Dayjs = shopToday()): { day: string; label: string } | null {
  const iso = op.status === 'COMPLETED' ? op.actualEnd : op.actualStart ?? op.scheduledStart;
  if (!iso) return null;
  const d = toShopDayjs(iso);
  return { day: d.format('D'), label: d.isSame(today, 'day') ? 'TODAY' : d.format('MMM').toUpperCase() };
}

/** Active work splits into what is under way (Now) and what is still to start
 *  (Coming up); finished work groups by the month it was done, with the hours. */
export function groupOps<T extends Op>(
  ops: readonly T[],
  tab: AssignmentTab
): { key: string; label: string; note?: string; ops: T[] }[] {
  if (tab === 'active') {
    const now = ops.filter((o) => o.status === 'IN_PROGRESS');
    const next = ops.filter((o) => o.status !== 'IN_PROGRESS');
    return [
      { key: 'now', label: 'Now', ops: now },
      { key: 'next', label: 'Coming up', ops: next },
    ].filter((g) => g.ops.length);
  }
  const groups: { key: string; label: string; note?: string; ops: T[] }[] = [];
  for (const op of ops) {
    const key = op.actualEnd ? toShopDayjs(op.actualEnd).format('YYYY-MM') : 'none';
    let g = groups.find((x) => x.key === key);
    if (!g) {
      g = { key, label: op.actualEnd ? toShopDayjs(op.actualEnd).format('MMMM YYYY') : 'No date', ops: [] };
      groups.push(g);
    }
    g.ops.push(op);
  }
  for (const g of groups) {
    const hours = g.ops.reduce((sum, o) => sum + (Number(o.actualWorkedHours) || 0), 0);
    g.note = `${g.ops.length} ${g.ops.length === 1 ? 'job' : 'jobs'}${hours ? ` · ${formatHoursMinutes(hours)}` : ''}`;
  }
  return groups;
}

/** The short "when" on an active card: today's start time, or the day and time. */
export function activeWhen(op: Op, today: Dayjs = shopToday()): string | null {
  if (op.actualStart) {
    const d = toShopDayjs(op.actualStart);
    return `Started ${d.isSame(today, 'day') ? d.format('h:mm A') : d.format('MMM D, h:mm A')}`;
  }
  if (op.scheduledStart) {
    const d = toShopDayjs(op.scheduledStart);
    if (d.isSame(today, 'day')) return `Today, ${d.format('h:mm A')}`;
    if (d.isSame(today.add(1, 'day'), 'day')) return `Tomorrow, ${d.format('h:mm A')}`;
    return d.diff(today, 'day') < 7 ? d.format('ddd, h:mm A') : d.format('MMM D, h:mm A');
  }
  return null;
}

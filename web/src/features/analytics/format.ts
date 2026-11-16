import dayjs from 'dayjs';
import { formatShop } from '../../utils/shopTime';

/** "2026-10" -> "Oct 2026". */
export function fmtMonth(ym: string | null | undefined): string {
  if (!ym) return '—';
  return dayjs(`${ym.slice(0, 7)}-01`).format('MMM YYYY');
}

/** "2026-10-12" or an ISO time -> "Oct 12" (shop time). */
export function fmtDay(value: string | null | undefined): string {
  if (!value) return '—';
  return value.length <= 10 ? dayjs(value).format('MMM D') : formatShop(value, 'MMM D');
}

/** "Oct 12" plus the year when it is not this year. */
export function fmtDate(value: string | null | undefined): string {
  if (!value) return '—';
  const d = value.length <= 10 ? dayjs(value) : dayjs(formatShop(value, 'YYYY-MM-DD'));
  return d.year() === dayjs().year() ? d.format('MMM D') : d.format('MMM D, YYYY');
}

export function fmtNum(value: number | null | undefined, digits = 0): string {
  if (value == null || Number.isNaN(value)) return '—';
  return value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** 218345 -> "218k", 1250000 -> "1.3M". For axes and tight spots. */
export function fmtCompact(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—';
  const abs = Math.abs(value);
  const sign = value < 0 ? '-' : '';
  // Decide the unit after rounding, so 999,800 reads "1.0M", never "1000k".
  if (abs >= 999_500) {
    const m = abs / 1e6;
    return `${sign}${m.toFixed(m >= 9.95 ? 0 : 1)}M`;
  }
  if (abs >= 999.5) return `${sign}${Math.round(abs / 1e3)}k`;
  return String(Math.round(value * 10) / 10);
}

export function fmtPeso(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—';
  return `₱${fmtNum(value)}`;
}

export function fmtPesoCompact(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—';
  return `₱${fmtCompact(value)}`;
}

export function fmtPct(value: number | null | undefined, digits = 0): string {
  if (value == null || Number.isNaN(value)) return '—';
  return `${value.toFixed(digits)}%`;
}

/** Signed percent vs target: "+12%" over, "−8%" under. */
export function fmtSignedPct(value: number | null | undefined, digits = 0): string {
  if (value == null || Number.isNaN(value)) return '—';
  const r = Number(value.toFixed(digits));
  if (r === 0) return '0%';
  return `${r > 0 ? '+' : '−'}${Math.abs(r).toFixed(digits)}%`;
}

/** % change from prev to curr; null when there is no base to compare with. */
export function pctChange(curr: number | null | undefined, prev: number | null | undefined): number | null {
  if (curr == null || prev == null || !Number.isFinite(curr) || !Number.isFinite(prev) || prev === 0) return null;
  return ((curr - prev) / Math.abs(prev)) * 100;
}

/** Percentage-point change; null when either side is missing. */
export function ptsChange(curr: number | null | undefined, prev: number | null | undefined): number | null {
  return curr == null || prev == null ? null : curr - prev;
}

/** On-time share of delivered jobs; null when none were delivered. */
export function onTimePct(onTime: number, late: number): number | null {
  return onTime + late > 0 ? (onTime / (onTime + late)) * 100 : null;
}

/** The same number of days immediately before the period. */
export function previousPeriod(params: { from: string; to: string }): { from: string; to: string } {
  const from = dayjs(params.from);
  const to = dayjs(params.to);
  const days = to.diff(from, 'day') + 1;
  return {
    from: from.subtract(days, 'day').format('YYYY-MM-DD'),
    to: from.subtract(1, 'day').format('YYYY-MM-DD'),
  };
}

/** Symmetric axis bound around 0 rounded up to a clean step (5, 10, 20, 25, 50 ...). */
export function symmetricBound(values: (number | null | undefined)[], minimum = 10): number {
  const max = Math.max(minimum, ...values.filter((v): v is number => v != null).map((v) => Math.abs(v)));
  const steps = [5, 10, 20, 25, 50, 75, 100, 150, 200, 250, 500, 1000];
  return steps.find((s) => s >= max * 1.05) ?? Math.ceil(max / 100) * 100;
}

export const JOB_TYPE_LABEL: Record<string, string> = {
  FABRICATION: 'Fabrication',
  MACHINING: 'Machining',
  REPAIR: 'Repair',
  OTHER: 'Other',
  UNKNOWN: 'Other',
  UNSPECIFIED: 'Other',
};

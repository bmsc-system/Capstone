/**
 * The schedule board grid shared by the Schedule page and the schedule proposals
 * (Plan job order, Reschedule, Re-propose schedule): day/hour header, the "Now"
 * line, machine or worker rows and the operation bars.
 */
import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { Tooltip } from 'antd';
import { ToolOutlined, WarningOutlined } from '@ant-design/icons';
import dayjs, { type Dayjs } from 'dayjs';
import {
  crewIdsOf,
  type ScheduleBoardDowntime,
  type ScheduleBoardOperation,
  type ShopDayWindow,
} from '../../api/schedule.api';
import { TabBar } from '../../components/ui';
import { PersonAvatar, crewNames } from '../../components/PersonAvatar';
import { adminPx } from '../../theme/adminTheme';
import { OPERATION_BAR_COLOR } from '../../theme/statuses';
import { formatShop, formatShopDateTime, SHOP_TZ } from '../../utils/shopTime';
import { personLabel } from '../../utils/people';
import MonthDayChip from './MonthDayChip';
import {
  HOUR_END,
  HOUR_START,
  TIMELINE_BORDER as BORDER,
  TIMELINE_NAVY as NAVY,
  WORKING_HOURS_NOTE,
  buildMonthTimelineLayout,
  buildWeekTimelineLayout,
  clipSegmentToPeriod,
  dayColumnsForView,
  defaultShopDayWindows,
  fitDayPxPerHour,
  layoutRowBars,
  leftPx,
  MATERIAL_WAIT_BAR_IMAGE,
  pxPerHour,
  scaleWeekTimelineLayout,
  scheduleOpTitle,
  shopDayKey,
  timelineWidth,
  widthPx,
  type TimelineViewMode,
  type WeekTimelineLayout,
} from './scheduleTimelineUtils';

/** "Nov 3, 8:00 AM – 10:00 AM", or both dates when it runs past the day. */
function shopRange(start?: string | null, end?: string | null) {
  if (!start) return '—';
  const s = formatShop(start, 'MMM D, h:mm A');
  if (!end) return s;
  const sameDay = formatShop(start, 'YYYY-MM-DD') === formatShop(end, 'YYYY-MM-DD');
  return `${s} – ${formatShop(end, sameDay ? 'h:mm A' : 'MMM D, h:mm A')}`;
}


type ViewMode = TimelineViewMode;
export type RowMode = 'machine' | 'worker';

const STATUS_COLOR = OPERATION_BAR_COLOR;
export const OTHER_JOB_COLOR = '#e2e8f0';
export const THIS_JOB_COLOR = '#2563eb';
export const CLASH_COLOR = '#7A1528';
export const DOWN_COLOR = '#C45A6A';
export const STALLED_COLOR = '#D97706';
const DOWN_OVERLAY_IMAGE =
  'repeating-linear-gradient(-45deg, rgba(196,90,106,0.75) 0 3px, transparent 3px 9px)';
const BAR_GAP = 2;
const MIN_BAR_W = adminPx(16);

function statusLabel(s: string) {
  if (s === 'REWORK') return 'Redo';
  if (s === 'IN_PROGRESS') return 'In progress';
  return s.charAt(0) + s.slice(1).toLowerCase().replace(/_/g, ' ');
}

export type RowDef = {
  key: string;
  label: string;
  group?: string;
  machineUnitId?: string | null;
  workerId?: string | null;
  personName?: string;
  photoVersion?: number | null;
  noMachine?: boolean;
};

export const NOW_COLOR = '#2563eb';

const BAR_TEXT_STYLE = {
  display: 'flex',
  flexDirection: 'column',
  justifyContent: 'center',
  flex: 1,
  minWidth: 0,
  gap: 1,
  lineHeight: 1.2,
} as const;

const BAR_LINE_STYLE = {
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
} as const;

const BAR_PILL_STYLE = {
  flexShrink: 0,
  border: '1px solid rgba(255,255,255,0.85)',
  background: 'rgba(255,255,255,0.12)',
  borderRadius: 999,
  padding: '0 7px',
  fontSize: 10,
  fontWeight: 600,
  lineHeight: '16px',
  whiteSpace: 'nowrap',
} as const;

export function PressGroup<T extends string>({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: T;
  onChange: (next: T) => void;
  options: { label: string; value: T }[];
}) {
  // The shared pill tabs, so the highlight slides between options like everywhere else.
  return (
    <span role="group" aria-label={label}>
      <TabBar<T> className="sched-seg" size="small" value={value} onChange={onChange} items={options} />
    </span>
  );
}

/** Shop time, refreshed every minute for the board's "now" line. */
function useShopNow(): Dayjs {
  const [now, setNow] = useState(() => dayjs().tz(SHOP_TZ));
  useEffect(() => {
    const id = window.setInterval(() => setNow(dayjs().tz(SHOP_TZ)), 60_000);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

export const NO_MACHINE_KEY = '__none__';
export const NO_OPS: ScheduleBoardOperation[] = [];
export const NO_DOWNTIMES: ScheduleBoardDowntime[] = [];

function opSegments(op: ScheduleBoardOperation) {
  if (op.segments.length > 0) return op.segments;
  return op.scheduledStart && op.scheduledEnd ? [{ start: op.scheduledStart, end: op.scheduledEnd }] : [];
}

export function opGroupKeys(op: ScheduleBoardOperation, rowMode: RowMode) {
  return rowMode === 'worker' ? crewIdsOf(op) : [op.machineUnitId || NO_MACHINE_KEY];
}

function rowGroupKey(row: RowDef, rowMode: RowMode) {
  if (rowMode === 'worker') return row.workerId;
  return row.noMachine ? NO_MACHINE_KEY : row.machineUnitId;
}

export function groupBy<T>(items: T[], keyOf: (item: T) => string | null | undefined) {
  const map = new Map<string | null | undefined, T[]>();
  for (const item of items) {
    const key = keyOf(item);
    const list = map.get(key);
    if (list) list.push(item);
    else map.set(key, [item]);
  }
  return map;
}

export function groupByMany<T>(items: T[], keysOf: (item: T) => (string | null | undefined)[]) {
  const map = new Map<string | null | undefined, T[]>();
  for (const item of items) {
    for (const key of keysOf(item)) {
      const list = map.get(key);
      if (list) list.push(item);
      else map.set(key, [item]);
    }
  }
  return map;
}

type BoardRowsProps = {
  rows: RowDef[];
  rowMode: RowMode;
  opsByRowKey: Map<string | null | undefined, ScheduleBoardOperation[]>;
  downtimesByUnit: Map<string, ScheduleBoardDowntime[]>;
  rowH: number;
  labelW: number;
  boardW: number;
  pph: number;
  dayColumns: ReturnType<typeof dayColumnsForView>;
  from: Dayjs;
  to: Dayjs;
  viewMode: ViewMode;
  isMobile: boolean;
  weekLayout: WeekTimelineLayout | null;
  dayHourPx: number | undefined;
  nowLeft: number | null;
  /** Without it the bars are not links (proposal views). */
  onOpenJob?: (jobOrderId: string) => void;
  /** Proposal views: this job stands out, other work is greyed and unlabelled. */
  highlightJobId?: string;
  highlightColor?: string | null;
};

const BoardRows = memo(function BoardRows({
  rows,
  rowMode,
  opsByRowKey,
  downtimesByUnit,
  rowH,
  labelW,
  boardW,
  pph,
  dayColumns,
  from,
  to,
  viewMode,
  isMobile,
  weekLayout,
  dayHourPx,
  nowLeft,
  onOpenJob,
  highlightJobId,
  highlightColor,
}: BoardRowsProps) {
  const columnFill = false;
  // Layout every row once per board change — not inline during JSX map.
  const prepared = useMemo(() => {
    const pos = { from, to, mode: viewMode, mobile: isMobile, weekLayout, hourPx: dayHourPx };
    const args = [from, viewMode, isMobile, weekLayout, dayHourPx] as const;
    // Only operations that touch the visible period need laying out.
    const fromMs = from.startOf('day').valueOf();
    const toMs = to.endOf('day').valueOf();
    const inPeriod = (op: ScheduleBoardOperation) =>
      opSegments(op).some(
        (seg) => Date.parse(seg.start) <= toMs && Date.parse(seg.end) >= fromMs,
      );
    return rows.map((row) => {
      const ops = (opsByRowKey.get(rowGroupKey(row, rowMode)) ?? NO_OPS).filter(inPeriod);
      const dts =
        (rowMode === 'machine' && row.machineUnitId
          ? downtimesByUnit.get(row.machineUnitId)
          : undefined) ?? NO_DOWNTIMES;
      const opById = new Map(ops.map((op) => [op.id, op]));
      // Month shows a count per day, not bars, so skip the bar layout there.
      const { bars, laneCount } =
        viewMode === 'month'
          ? { bars: [] as ReturnType<typeof layoutRowBars>['bars'], laneCount: 1 }
          : layoutRowBars(
              ops.map((op) => ({ id: op.id, segments: opSegments(op) })),
              pos,
              { markClashes: !row.noMachine, gap: BAR_GAP, minWidth: MIN_BAR_W },
            );
      const lanes = viewMode === 'month' ? 1 : laneCount;
      const trackH = rowH * lanes;
      const downRects = dts.flatMap((d) => {
        const clipped = clipSegmentToPeriod(d.segmentStart, d.segmentEnd, from, to);
        if (!clipped) return [];
        const left = leftPx(clipped.start, ...args);
        const width = widthPx(clipped.start, clipped.end, ...args);
        return left == null || width == null || width <= 0 ? [] : [{ d, left, width }];
      });
      // Month: which operations touch each day, worked out once per row
      // (not once per day column per operation).
      const monthHits = new Map<string, ScheduleBoardOperation[]>();
      if (viewMode === 'month') {
        for (const op of ops) {
          const days = new Set<string>();
          for (const seg of opSegments(op)) {
            const start = shopDayKey(seg.start);
            const end = shopDayKey(seg.end);
            for (const col of dayColumns) {
              if (start <= col.key && col.key <= end) days.add(col.key);
            }
          }
          for (const key of days) {
            const list = monthHits.get(key);
            if (list) list.push(op);
            else monthHits.set(key, [op]);
          }
        }
      }
      return { row, opById, bars, laneCount, trackH, downRects, monthHits };
    });
  }, [
    rows,
    rowMode,
    opsByRowKey,
    downtimesByUnit,
    rowH,
    from,
    to,
    viewMode,
    isMobile,
    weekLayout,
    dayHourPx,
    dayColumns,
  ]);

  return (
    <div style={{ position: 'relative' }}>
    {nowLeft != null ? (
      <div
        style={{
          position: 'absolute',
          top: 0,
          bottom: 0,
          left: labelW + nowLeft - 1,
          width: 2,
          background: NOW_COLOR,
          zIndex: 3,
          pointerEvents: 'none',
        }}
      />
    ) : null}
    {prepared.map(({ row, opById, bars, laneCount, trackH, downRects, monthHits }) => {
      return (
        <div key={row.key}>
          {row.group ? (
            <div
              style={{
                position: 'sticky',
                left: 0,
                zIndex: 2,
                background: '#f1f5f9',
                padding: '4px 8px',
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: 0.5,
                textTransform: 'uppercase',
                color: '#94a3b8',
                borderBottom: `1px solid ${BORDER}`,
              }}
            >
              {row.group}
            </div>
          ) : null}
          <div
            style={{
              display: 'flex',
              minHeight: trackH,
              borderBottom: `1px solid #f1f5f9`,
            }}
          >
            <div
              style={{
                width: labelW,
                flexShrink: 0,
                position: 'sticky',
                left: 0,
                zIndex: 4,
                background: '#fff',
                borderRight: `1px solid ${BORDER}`,
                padding: '6px 8px',
                fontSize: isMobile ? 11 : 12,
                fontWeight: 600,
                color: row.noMachine ? '#64748b' : NAVY,
                display: 'flex',
                alignItems: viewMode === 'month' ? 'flex-start' : 'center',
                overflow: viewMode === 'month' ? 'visible' : 'hidden',
                whiteSpace: viewMode === 'month' ? 'normal' : 'nowrap',
                minHeight: trackH,
                gap: 6,
                lineHeight: 1.25,
              }}
            >
              {row.workerId && row.personName ? (
                <PersonAvatar
                  userId={row.workerId}
                  fullName={row.personName}
                  photoVersion={row.photoVersion}
                  size={isMobile ? 20 : 24}
                />
              ) : null}
              <span style={viewMode === 'month' ? { whiteSpace: 'normal' } : { overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {row.label}
              </span>
            </div>
            <div
              style={{
                position: 'relative',
                width: boardW,
                minHeight: trackH,
                height: trackH,
                backgroundImage:
                  viewMode === 'day'
                    ? `repeating-linear-gradient(90deg, transparent, transparent ${pph - 1}px, #f1f5f9 ${pph - 1}px, #f1f5f9 ${pph}px)`
                    : undefined,
              }}
            >
              {dayColumns.map((col, i) => (
                <div
                  key={col.key}
                  style={{
                    position: 'absolute',
                    left: col.left,
                    top: 0,
                    bottom: 0,
                    width: 1,
                    background: i === 0 ? 'transparent' : '#f1f5f9',
                  }}
                />
              ))}

              {laneCount > 1
                ? Array.from({ length: laneCount - 1 }, (_, i) => (
                    <div
                      key={`lane-rule-${i}`}
                      style={{
                        position: 'absolute',
                        left: 0,
                        right: 0,
                        top: rowH * (i + 1),
                        height: 1,
                        background: '#e2e8f0',
                        zIndex: 0,
                        pointerEvents: 'none',
                      }}
                    />
                  ))
                : null}

              {downRects.map(({ d, left: barLeft, width: barW }) => (
                  <Tooltip
                    key={d.id}
                    title={
                      <div>
                        <div style={{ fontWeight: 600 }}>Machine breakdown</div>
                        <div>{d.reason}</div>
                        <div>
                          {formatShopDateTime(d.startedAt)} →{' '}
                          {d.open ? 'still down' : formatShopDateTime(d.endedAt)}
                        </div>
                      </div>
                    }
                  >
                    <div
                      style={{
                        position: 'absolute',
                        top: columnFill ? 0 : 4,
                        height: columnFill ? trackH : trackH - 8,
                        left: barLeft,
                        width: barW,
                        background:
                          'repeating-linear-gradient(-45deg, #E8C5CB, #E8C5CB 4px, #F5E6E9 4px, #F5E6E9 8px)',
                        border: '1px solid #C45A6A',
                        borderRadius: columnFill ? 0 : 4,
                        opacity: 0.9,
                        zIndex: 1,
                      }}
                    />
                  </Tooltip>
              ))}

              {viewMode === 'month'
                ? dayColumns.map((col) => {
                    const hits = monthHits.get(col.key);
                    if (!hits?.length) return null;
                    return (
                      <MonthDayChip
                        key={col.key}
                        dayKey={col.key}
                        ops={hits}
                        left={col.left}
                        width={col.width}
                        rowMode={rowMode}
                        onOpenJob={highlightJobId ? undefined : onOpenJob}
                      />
                    );
                  })
                : bars.flatMap((bar) => {
                const op = opById.get(bar.opId);
                if (!op) return [];
                const barTop = columnFill ? bar.lane * rowH : bar.lane * rowH + 4;
                const barHeight = columnFill ? rowH : rowH - 8;
                const barLeft = bar.left;
                const barW = bar.width;
                // Breakdown hatching drawn over the bar, so a down machine is never hidden.
                const downOverlays = downRects.flatMap(({ d, left, width }) => {
                  const l = Math.max(left, barLeft);
                  const r = Math.min(left + width, barLeft + barW);
                  return r > l ? [{ key: `${bar.key}-down-${d.id}`, left: l, width: r - l }] : [];
                });
                const onDown = !!op.onDownUnit || downOverlays.length > 0;
                const isThisJob = !!highlightJobId && op.jobOrderId === highlightJobId;
                const muted = !!highlightJobId && !isThisJob;
                const color = isThisJob
                  ? highlightColor || op.scheduleColor || THIS_JOB_COLOR
                  : muted
                    ? OTHER_JOB_COLOR
                    : op.scheduleColor || STATUS_COLOR[op.status] || '#2563eb';
                const late = !!op.isLate && !muted;
                const stalled = !!op.stalled && !muted;
                const waiting = !!op.waitingForMaterials && !muted;
                const showText = barW >= 22 && !muted;
                const showPill = barW >= adminPx(150) && !isMobile && !muted;
                const pill = isThisJob
                  ? onDown
                    ? 'Machine down'
                    : 'Proposed'
                  : onDown
                    ? 'Machine down'
                    : op.stalled
                      ? 'Stalled'
                      : statusLabel(op.status);
                const clickable = !!onOpenJob && !highlightJobId;
                const rings = [
                  late ? 'inset 0 0 0 2px #7A1528' : null,
                  bar.clash ? `0 0 0 2px ${CLASH_COLOR}` : '0 1px 2px rgba(15,23,42,0.18)',
                ].filter(Boolean).join(', ');
                  const opTitle = scheduleOpTitle(op.sequenceNo, op.operationName);
                  const title = op.jobNumber ? `Job ${op.jobNumber}` : opTitle;
                  const meta = [op.clientName, op.jobNumber ? opTitle : null]
                    .filter(Boolean)
                    .join(' · ');
                  return [
                    <Tooltip
                      key={bar.key}
                      title={
                        <div style={{ maxWidth: 260 }}>
                          {bar.clash ? (
                            <div style={{ color: '#FCA5A5', fontWeight: 700 }}>
                              <WarningOutlined /> Clash: overlaps other work on this{' '}
                              {rowMode === 'machine' ? 'machine' : 'worker'}
                            </div>
                          ) : null}
                          {onDown ? (
                            <div style={{ color: '#FCA5A5', fontWeight: 700 }}>
                              <ToolOutlined /> Machine down
                              {op.downtimeReason ? `: ${op.downtimeReason}` : ''}
                            </div>
                          ) : null}
                          {op.stalled ? (
                            <div style={{ color: '#FCD34D', fontWeight: 700 }}>
                              Stalled: paused since {formatShopDateTime(op.pausedAt)}. Resume it,
                              re-plan or reassign it.
                            </div>
                          ) : null}
                          <div style={{ fontWeight: 700 }}>
                            {opTitle}
                            {op.jobNumber ? ` · ${op.jobNumber}` : ''}
                          </div>
                          <div>{[op.jobTitle, op.clientName].filter(Boolean).join(' · ')}</div>
                          <div>
                            {crewNames(
                              op.crew,
                              op.assignedWorkerName
                                ? personLabel(op.assignedWorkerName, op.assignedWorkerNickname)
                                : null,
                            ) || 'No worker'}
                          </div>
                          <div>
                            {shopRange(op.scheduledStart, op.scheduledEnd)}
                            {op.estimatedHours != null ? ` · ${op.estimatedHours} h target` : ''}
                          </div>
                          <div>{statusLabel(op.status)}</div>
                          {op.waitingForMaterials ? (
                            <div style={{ color: '#FCD34D' }}>
                              Waiting for materials: {op.materialWaitReason}
                            </div>
                          ) : null}
                          {late ? (
                            <div style={{ color: '#E8C5CB' }}>
                              At risk of missing date required ({op.dueDate || '—'})
                            </div>
                          ) : null}
                        </div>
                      }
                    >
                      <button
                        type="button"
                        onClick={clickable ? () => onOpenJob?.(op.jobOrderId) : undefined}
                        tabIndex={clickable ? undefined : -1}
                        aria-label={bar.short ? `${opTitle}${op.jobNumber ? ` · ${op.jobNumber}` : ''}` : undefined}
                        style={{
                          position: 'absolute',
                          top: barTop,
                          height: barHeight,
                          left: barLeft,
                          width: barW,
                          background: color,
                          backgroundImage: waiting ? MATERIAL_WAIT_BAR_IMAGE : undefined,
                          // Bars take their job's color, so finished work is faded instead.
                          opacity: op.status === 'COMPLETED' && !muted ? 0.55 : undefined,
                          border: muted ? '1px solid #cbd5e1' : '1px solid rgba(15,23,42,0.28)',
                          outline: onDown
                            ? `2px dashed ${DOWN_COLOR}`
                            : stalled
                              ? `2px dotted ${STALLED_COLOR}`
                              : undefined,
                          outlineOffset: onDown || stalled ? 1 : undefined,
                          borderRadius: adminPx(6),
                          color: '#fff',
                          display: 'flex',
                          alignItems: 'center',
                          gap: adminPx(6),
                          boxSizing: 'border-box',
                          margin: 0,
                          padding: barW < adminPx(64) ? `0 ${adminPx(4)}px` : `0 ${adminPx(8)}px`,
                          overflow: 'hidden',
                          textAlign: 'left',
                          cursor: clickable ? 'pointer' : 'default',
                          zIndex: 2,
                          boxShadow: rings,
                        }}
                      >
                        {bar.clash && barW >= 14 ? (
                          <WarningOutlined
                            aria-label="Clash"
                            style={{ color: '#fff', background: CLASH_COLOR, borderRadius: 3, padding: 1, flexShrink: 0 }}
                          />
                        ) : null}
                        {showText ? (
                          <span style={BAR_TEXT_STYLE}>
                            <span style={{ ...BAR_LINE_STYLE, fontSize: adminPx(isMobile ? 11 : 12), fontWeight: 700 }}>
                              {title}
                            </span>
                            {meta ? (
                              <span
                                style={{
                                  ...BAR_LINE_STYLE,
                                  fontSize: adminPx(isMobile ? 10 : 11),
                                  fontWeight: 500,
                                  opacity: 0.9,
                                }}
                              >
                                {meta}
                              </span>
                            ) : null}
                          </span>
                        ) : null}
                        {showPill ? (
                          <span
                            style={
                              onDown
                                ? { ...BAR_PILL_STYLE, background: DOWN_COLOR }
                                : stalled
                                  ? { ...BAR_PILL_STYLE, background: STALLED_COLOR }
                                  : BAR_PILL_STYLE
                            }
                          >
                            {pill}
                          </span>
                        ) : null}
                      </button>
                    </Tooltip>,
                    ...downOverlays.map((o) => (
                      <div
                        key={o.key}
                        aria-hidden
                        style={{
                          position: 'absolute',
                          top: barTop,
                          height: barHeight,
                          left: o.left,
                          width: o.width,
                          backgroundImage: DOWN_OVERLAY_IMAGE,
                          borderRadius: adminPx(6),
                          zIndex: 3,
                          pointerEvents: 'none',
                        }}
                      />
                    )),
                  ];
              })}
            </div>
          </div>
        </div>
      );
    })}
    </div>
  );
});


type BoardGridProps = {
  rows: RowDef[];
  rowMode: RowMode;
  opsByRowKey: Map<string | null | undefined, ScheduleBoardOperation[]>;
  downtimesByUnit: Map<string, ScheduleBoardDowntime[]>;
  from: Dayjs;
  to: Dayjs;
  viewMode: ViewMode;
  isMobile: boolean;
  rowH: number;
  labelW: number;
  shopDayWindows?: ShopDayWindow[] | null;
  /** True while a Day/Week/Month or row switch is still rebuilding the bars. */
  pending?: boolean;
  onOpenJob?: (jobOrderId: string) => void;
  highlightJobId?: string;
  highlightColor?: string | null;
};

/** Header, "Now" line and rows, sized to the space it is given. */
export function BoardGrid({
  rows,
  rowMode,
  opsByRowKey,
  downtimesByUnit,
  from,
  to,
  viewMode,
  isMobile,
  rowH,
  labelW,
  shopDayWindows,
  pending = false,
  onOpenJob,
  highlightJobId,
  highlightColor,
}: BoardGridProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [availW, setAvailW] = useState(0);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    // A kept-alive page hidden with display:none reports 0; keep the last real width.
    const measure = () => {
      if (el.clientWidth > 0) setAvailW(el.clientWidth);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const naturalWeekLayout = useMemo(() => {
    if (viewMode === 'day') return null;
    const windows =
      shopDayWindows && shopDayWindows.length > 0 ? shopDayWindows : defaultShopDayWindows(from, to);
    return viewMode === 'week'
      ? buildWeekTimelineLayout(from, to, windows, isMobile)
      : buildMonthTimelineLayout(from, to, windows, isMobile);
  }, [viewMode, shopDayWindows, from, to, isMobile]);

  const weekLayout = useMemo(() => {
    if (!naturalWeekLayout) return null;
    const target = Math.max(naturalWeekLayout.totalWidth, Math.max(0, availW - labelW));
    return scaleWeekTimelineLayout(naturalWeekLayout, target);
  }, [naturalWeekLayout, availW, labelW]);

  const dayHourPx = useMemo(() => {
    if (viewMode !== 'day') return undefined;
    return fitDayPxPerHour(isMobile, Math.max(0, availW - labelW));
  }, [viewMode, isMobile, availW, labelW]);

  const boardW = timelineWidth(from, to, viewMode, isMobile, weekLayout, dayHourPx);
  const dayColumns = useMemo(
    () => dayColumnsForView(from, to, viewMode, isMobile, weekLayout, dayHourPx),
    [from, to, viewMode, isMobile, weekLayout, dayHourPx]
  );
  const pph = dayHourPx ?? pxPerHour(viewMode, isMobile);
  const now = useShopNow();
  const nowLeft = useMemo(() => {
    const hour = now.hour() + now.minute() / 60;
    if (hour < HOUR_START || hour > HOUR_END) return null;
    if (now.isBefore(from.startOf('day')) || now.isAfter(to.endOf('day'))) return null;
    const left = leftPx(now.toISOString(), from, viewMode, isMobile, weekLayout, dayHourPx);
    return left != null && left >= 0 && left <= boardW ? left : null;
  }, [now, from, to, viewMode, isMobile, weekLayout, dayHourPx, boardW]);

  return (
    <div
      ref={scrollRef}
      className={`sched-board-view${pending ? ' is-pending' : ''}`}
      style={{
        border: `1px solid ${BORDER}`,
        borderRadius: 10,
        background: '#fff',
        overflow: 'auto',
        WebkitOverflowScrolling: 'touch',
      }}
    >
      <div style={{ minWidth: labelW + boardW, width: '100%' }}>
        <div
          style={{
            display: 'flex',
            position: 'sticky',
            top: 0,
            zIndex: 5,
            background: '#f8fafc',
          }}
        >
          <div
            style={{
              width: labelW,
              flexShrink: 0,
              position: 'sticky',
              left: 0,
              zIndex: 4,
              background: '#f8fafc',
              borderBottom: `1px solid ${BORDER}`,
              borderRight: `1px solid ${BORDER}`,
              padding: '8px 8px',
              fontSize: 11,
              fontWeight: 700,
              color: '#64748b',
            }}
          >
            {rowMode === 'machine' ? 'Machine' : 'Worker'}
          </div>
          <div
            style={{
              position: 'relative',
              width: boardW,
              height: 36,
              borderBottom: `1px solid ${BORDER}`,
            }}
          >
            {dayColumns.map((col, i) => (
              <div
                key={col.key}
                style={{
                  position: 'absolute',
                  left: col.left,
                  width: col.width,
                  top: 0,
                  bottom: 0,
                  borderLeft: i === 0 ? 'none' : `1px solid ${BORDER}`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: isMobile ? 10 : 11,
                  fontWeight: 600,
                  color: '#475569',
                }}
              >
                {col.label}
              </div>
            ))}
            {nowLeft != null ? (
              <>
                <div
                  style={{
                    position: 'absolute',
                    left: nowLeft - 1,
                    bottom: 0,
                    width: 2,
                    height: 8,
                    background: NOW_COLOR,
                    pointerEvents: 'none',
                  }}
                />
                <div
                  aria-label="Now"
                  style={{
                    position: 'absolute',
                    left: nowLeft,
                    bottom: -8,
                    transform: 'translateX(-50%)',
                    background: NOW_COLOR,
                    color: '#fff',
                    fontSize: 10,
                    fontWeight: 600,
                    lineHeight: '16px',
                    padding: '0 7px',
                    borderRadius: 4,
                    whiteSpace: 'nowrap',
                    boxShadow: '0 1px 2px rgba(15,23,42,0.2)',
                    pointerEvents: 'none',
                  }}
                >
                  Now
                </div>
              </>
            ) : null}
          </div>
        </div>

        <BoardRows
          rows={rows}
          rowMode={rowMode}
          opsByRowKey={opsByRowKey}
          downtimesByUnit={downtimesByUnit}
          rowH={rowH}
          labelW={labelW}
          boardW={boardW}
          pph={pph}
          dayColumns={dayColumns}
          from={from}
          to={to}
          viewMode={viewMode}
          isMobile={isMobile}
          weekLayout={weekLayout}
          dayHourPx={dayHourPx}
          nowLeft={nowLeft}
          onOpenJob={onOpenJob}
          highlightJobId={highlightJobId}
          highlightColor={highlightColor}
        />
      </div>
      <div className="sched-page__note">
        {weekLayout
          ? `${WORKING_HOURS_NOTE} Scroll sideways for more days.`
          : `${HOUR_START}:00–${HOUR_END}:00. Scroll sideways for more days.`}
      </div>
    </div>
  );
}

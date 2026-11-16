import { memo, useMemo, useState, type CSSProperties } from 'react';
import { Popover } from 'antd';
import { DownOutlined, RightOutlined } from '@ant-design/icons';
import type { ScheduleBoardOperation } from '../../api/schedule.api';
import { OPERATION_BAR_COLOR } from '../../theme/statuses';
import { formatShop } from '../../utils/shopTime';
import { personLabel } from '../../utils/people';
import { scheduleOpTitle, shopDayKey } from './scheduleTimelineUtils';

type Props = {
  dayKey: string;
  ops: ScheduleBoardOperation[];
  left: number;
  width: number;
  /** Worker rows show the machine; machine rows show the worker. */
  rowMode: 'machine' | 'worker';
  onOpenJob?: (jobOrderId: string) => void;
  /** Planning a job: its days are filled in its colour, other work is muted. */
  highlightJobId?: string;
  highlightColor?: string;
};

function statusLabel(s: string) {
  if (s === 'REWORK') return 'Redo';
  if (s === 'IN_PROGRESS') return 'In progress';
  return s.charAt(0) + s.slice(1).toLowerCase().replace(/_/g, ' ');
}

function segmentsOf(op: ScheduleBoardOperation) {
  if (op.segments.length > 0) return op.segments;
  return op.scheduledStart && op.scheduledEnd ? [{ start: op.scheduledStart, end: op.scheduledEnd }] : [];
}

/** Working time this operation has on the day: "8:00 AM – 12:00 PM", with … when it runs on from or past the day. */
function dayTimes(op: ScheduleBoardOperation, dayKey: string) {
  const parts: string[] = [];
  for (const seg of segmentsOf(op)) {
    const s = shopDayKey(seg.start);
    const e = shopDayKey(seg.end);
    if (s > dayKey || e < dayKey) continue;
    const from = s === dayKey ? formatShop(seg.start, 'h:mm A') : '…';
    const to = e === dayKey ? formatShop(seg.end, 'h:mm A') : '…';
    parts.push(`${from} – ${to}`);
  }
  return parts.join(', ');
}

type JobGroup = {
  jobOrderId: string;
  jobNumber: string;
  title: string;
  clientName: string;
  late: boolean;
  ops: ScheduleBoardOperation[];
};

function groupByJob(ops: ScheduleBoardOperation[]): JobGroup[] {
  const map = new Map<string, JobGroup>();
  for (const op of ops) {
    let g = map.get(op.jobOrderId);
    if (!g) {
      g = {
        jobOrderId: op.jobOrderId,
        jobNumber: op.jobNumber || 'Job',
        title: op.jobTitle || '',
        clientName: op.clientName || '',
        late: false,
        ops: [],
      };
      map.set(op.jobOrderId, g);
    }
    g.ops.push(op);
    if (op.isLate) g.late = true;
  }
  for (const g of map.values()) g.ops.sort((a, b) => a.sequenceNo - b.sequenceNo);
  return [...map.values()];
}

function MonthDayChip({ dayKey, ops, left, width, rowMode, onOpenJob, highlightJobId, highlightColor }: Props) {
  const [open, setOpen] = useState(false);
  const n = ops.length;
  const mine = highlightJobId ? ops.filter((op) => op.jobOrderId === highlightJobId).length : 0;
  const tone = !highlightJobId ? '' : mine ? ' is-this-job' : ' is-muted';
  const jobs = useMemo(() => (open ? groupByJob(ops) : []), [open, ops]);
  const day = formatShop(`${dayKey}T12:00:00+08:00`, 'ddd, MMM D');

  const renderContent = () => (
    <div className="sched-day-list" role="menu">
      <div className="sched-day-list__head">
        <strong>{day}</strong>
        <span>
          {n} operation{n === 1 ? '' : 's'} · {jobs.length} job{jobs.length === 1 ? '' : 's'}
        </span>
      </div>
      <div className="sched-day-list__body">
        {jobs.map((g, i) => (
          <button
            key={g.jobOrderId}
            type="button"
            role="menuitem"
            className={`sched-day-job${g.late ? ' is-late' : ''}`}
            style={{ animationDelay: `${Math.min(i, 8) * 30}ms` }}
            onClick={() => {
              setOpen(false);
              onOpenJob?.(g.jobOrderId);
            }}
          >
            <span className="sched-day-job__top">
              <span className="sched-day-job__no">{g.jobNumber}</span>
              <span className="sched-day-job__title">{g.title || g.clientName}</span>
              {g.late ? <span className="sched-day-job__late">Late</span> : null}
              <RightOutlined className="sched-day-job__go" />
            </span>
            {g.title && g.clientName ? <span className="sched-day-job__client">{g.clientName}</span> : null}
            <span className="sched-day-job__ops">
              {g.ops.map((op) => {
                const who =
                  rowMode === 'worker'
                    ? op.machineUnitLabel || 'No machine'
                    : op.assignedWorkerName
                      ? personLabel(op.assignedWorkerName, op.assignedWorkerNickname)
                      : 'No worker yet';
                return (
                  <span key={op.id} className="sched-day-op">
                    <span
                      className="sched-day-op__dot"
                      style={{ background: op.scheduleColor || OPERATION_BAR_COLOR[op.status] || '#2563eb' }}
                      aria-hidden
                    />
                    <span className="sched-day-op__name">{scheduleOpTitle(op.sequenceNo, op.operationName)}</span>
                    <span className="sched-day-op__meta">
                      {[dayTimes(op, dayKey), who, statusLabel(op.status)].filter(Boolean).join(' · ')}
                    </span>
                  </span>
                );
              })}
            </span>
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <Popover
      trigger="click"
      placement="bottomLeft"
      arrow={false}
      open={open}
      onOpenChange={setOpen}
      content={renderContent}
      rootClassName="sched-day-pop"
      destroyOnHidden
    >
      <button
        type="button"
        className={`sched-month-chip${tone}${open ? ' is-open' : ''}`}
        style={{
          left: left + 2,
          width: Math.max(24, width - 4),
          ...(mine && highlightColor ? ({ '--chip-color': highlightColor } as CSSProperties) : null),
        }}
        aria-haspopup="menu"
        aria-expanded={open}
        title={`${n} operation${n === 1 ? '' : 's'} on ${day}${mine ? ` (${mine} of this job)` : ''}`}
      >
        <span className="sched-month-chip__text">
          {width >= 112 ? `${n} operation${n === 1 ? '' : 's'}` : `${n} op${n === 1 ? '' : 's'}`}
        </span>
        <DownOutlined className="sched-month-chip__caret" />
      </button>
    </Popover>
  );
}

export default memo(MonthDayChip);

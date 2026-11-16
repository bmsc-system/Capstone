import { useMemo, useRef } from 'react';
import dayjs, { type Dayjs } from 'dayjs';
import { Table, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  ClockCircleOutlined,
  InboxOutlined,
  ToolOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import type { ScheduleBoardDowntime, ScheduleBoardOperation } from '../../api/schedule.api';
import { OPERATION_BAR_COLOR, OPERATION_STATUS } from '../../theme/statuses';
import StatusPill from '../../components/StatusPill';
import { PersonAvatar, crewNames } from '../../components/PersonAvatar';
import { personLabel } from '../../utils/people';
import { SHOP_TZ } from '../../utils/shopTime';
import { scheduleOpTitle } from './scheduleTimelineUtils';
import { opSegments, pieceOnDay, timeRange } from './ScheduleAgenda';

type OpRow = {
  kind: 'op';
  key: string;
  dayKey: string;
  date: Dayjs;
  start: Dayjs;
  end: Dayjs;
  op: ScheduleBoardOperation;
  daySpan: number;
  dayCount: number;
};

type DownRow = {
  kind: 'down';
  key: string;
  dayKey: string;
  date: Dayjs;
  start: Dayjs;
  end: Dayjs;
  dt: ScheduleBoardDowntime;
  daySpan: number;
  dayCount: number;
};

type ListRow = OpRow | DownRow;

type Props = {
  operations: ScheduleBoardOperation[];
  downtimes: ScheduleBoardDowntime[];
  from: Dayjs;
  to: Dayjs;
  onOpenJob: (jobOrderId: string) => void;
};

/**
 * Desktop list view of the schedule: every operation in the period, one row
 * per day it runs on, in time order, with the day shown once per group.
 */
export default function ScheduleList({ operations, downtimes, from, to, onOpenJob }: Props) {
  const rows = useMemo<ListRow[]>(() => {
    const out: ListRow[] = [];
    const last = to.tz(SHOP_TZ).startOf('day');
    for (let d = from.tz(SHOP_TZ).startOf('day'); !d.isAfter(last); d = d.add(1, 'day')) {
      const dayKey = d.format('YYYY-MM-DD');
      const day: ListRow[] = [];
      for (const dt of downtimes) {
        const piece = pieceOnDay([{ start: dt.segmentStart, end: dt.segmentEnd }], d);
        if (piece)
          day.push({
            kind: 'down',
            key: `dt-${dt.id}-${dayKey}`,
            dayKey,
            date: d,
            dt,
            ...piece,
            daySpan: 0,
            dayCount: 0,
          });
      }
      for (const op of operations) {
        const piece = pieceOnDay(opSegments(op), d);
        if (piece)
          day.push({
            kind: 'op',
            key: `${op.id}-${dayKey}`,
            dayKey,
            date: d,
            op,
            ...piece,
            daySpan: 0,
            dayCount: 0,
          });
      }
      day.sort(
        (a, b) =>
          a.start.valueOf() - b.start.valueOf() ||
          (a.kind === 'down'
            ? -1
            : b.kind === 'down'
              ? 1
              : a.op.sequenceNo - (b as OpRow).op.sequenceNo)
      );
      if (!day.length) continue;
      const opCount = day.filter((r) => r.kind === 'op').length;
      day[0].daySpan = day.length;
      for (const r of day) r.dayCount = opCount;
      out.push(...day);
    }
    return out;
  }, [operations, downtimes, from, to]);

  const today = dayjs().tz(SHOP_TZ).startOf('day');
  const scrollRef = useRef<HTMLDivElement>(null);

  const columns: ColumnsType<ListRow> = [
    {
      title: 'Date',
      key: 'date',
      width: 100,
      onCell: (r) => ({ rowSpan: r.daySpan, className: 'sched-list__date-cell' }),
      render: (_, r) => {
        const isToday = r.date.isSame(today, 'day');
        return (
          <div className={`sched-list__date${isToday ? ' is-today' : ''}`}>
            <div className="sched-list__dow">{r.date.format('ddd')}</div>
            <div className="sched-list__day">{r.date.format('MMM D')}</div>
            <div className="sched-list__count">
              {[
                isToday ? 'Today' : null,
                r.dayCount ? `${r.dayCount} op${r.dayCount === 1 ? '' : 's'}` : null,
              ]
                .filter(Boolean)
                .join(' · ')}
            </div>
          </div>
        );
      },
    },
    {
      title: 'Time',
      key: 'time',
      width: 156,
      render: (_, r) => {
        const color =
          r.kind === 'down'
            ? '#c45a6a'
            : r.op.scheduleColor || OPERATION_BAR_COLOR[r.op.status] || '#2563eb';
        return (
          <span className="sched-list__time">
            <span className="sched-list__dot" style={{ background: color }} aria-hidden />
            {r.kind === 'down' && r.dt.open ? 'Still down' : timeRange(r.start, r.end)}
          </span>
        );
      },
    },
    {
      title: 'Job order',
      key: 'job',
      width: 230,
      ellipsis: true,
      render: (_, r) =>
        r.kind === 'down' ? (
          <span className="sched-list__down">
            <ToolOutlined aria-hidden /> Machine breakdown
          </span>
        ) : (
          <div className="sched-list__job">
            <div className="sched-list__job-no">{r.op.jobNumber || 'Job order'}</div>
            <div
              className="sched-list__sub"
              title={[r.op.jobTitle, r.op.clientName].filter(Boolean).join(' · ')}
            >
              {[r.op.jobTitle, r.op.clientName].filter(Boolean).join(' · ')}
            </div>
          </div>
        ),
    },
    {
      title: 'Operation',
      key: 'operation',
      width: 160,
      ellipsis: true,
      render: (_, r) =>
        r.kind === 'down' ? (
          <span className="sched-list__sub">{r.dt.reason || '—'}</span>
        ) : (
          <div>
            <div>{scheduleOpTitle(r.op.sequenceNo, r.op.operationName)}</div>
            {r.op.estimatedHours != null ? (
              <div className="sched-list__sub">
                Target {Number(r.op.estimatedHours).toFixed(1)} h
              </div>
            ) : null}
          </div>
        ),
    },
    {
      title: 'Machine',
      key: 'machine',
      width: 110,
      ellipsis: true,
      render: (_, r) =>
        r.kind === 'down'
          ? r.dt.machineUnitLabel || 'Machine'
          : r.op.machineUnitLabel ||
            r.op.machineTypeName || <span className="sched-list__muted">No machine</span>,
    },
    {
      title: 'Workers',
      key: 'crew',
      width: 180,
      ellipsis: true,
      render: (_, r) => {
        if (r.kind === 'down') return null;
        const op = r.op;
        const names = crewNames(
          op.crew,
          op.assignedWorkerName
            ? personLabel(op.assignedWorkerName, op.assignedWorkerNickname)
            : null
        );
        if (!names) return <span className="sched-list__muted">No worker yet</span>;
        return (
          <span className="sched-list__crew" title={names}>
            {op.assignedWorkerId ? (
              <PersonAvatar
                userId={op.assignedWorkerId}
                fullName={op.assignedWorkerName || ''}
                photoVersion={op.assignedWorkerPhotoVersion}
                size={22}
              />
            ) : null}
            <span className="sched-list__crew-names">{names}</span>
          </span>
        );
      },
    },
    {
      title: 'Status',
      key: 'status',
      width: 150,
      render: (_, r) => {
        if (r.kind === 'down') {
          return <span className="sched-list__flag is-red">{r.dt.open ? 'Down now' : 'Down'}</span>;
        }
        const op = r.op;
        return (
          <div className="sched-list__status">
            <StatusPill status={op.status} map={OPERATION_STATUS} compact />
            {op.isLate ? (
              <Tooltip title="Expected to finish after the date required">
                <span className="sched-list__flag is-red">
                  <WarningOutlined aria-hidden /> At risk
                  {op.dueDate ? ` · due ${dayjs(op.dueDate).format('MMM D')}` : ''}
                </span>
              </Tooltip>
            ) : null}
            {op.waitingForMaterials ? (
              <span className="sched-list__flag is-amber">
                <InboxOutlined aria-hidden /> Waiting for materials
              </span>
            ) : null}
            {op.stalled ? (
              <span className="sched-list__flag is-amber">
                <ClockCircleOutlined aria-hidden /> Paused over a day
              </span>
            ) : null}
            {op.onDownUnit ? (
              <span className="sched-list__flag is-red">
                <ToolOutlined aria-hidden /> Machine is down
              </span>
            ) : null}
          </div>
        );
      },
    },
  ];

  return (
    <div className="sched-list" ref={scrollRef}>
      <Table<ListRow>
        className="sched-list__table"
        size="small"
        rowKey="key"
        columns={columns}
        dataSource={rows}
        pagination={false}
        sticky={{ getContainer: () => scrollRef.current || window }}
        scroll={{ x: 1080 }}
        locale={{ emptyText: 'Nothing scheduled in this period.' }}
        rowClassName={(r) =>
          [
            r.daySpan ? 'sched-list__first' : '',
            r.kind === 'down' ? 'sched-list__row--down' : 'sched-list__row--op',
            r.kind === 'op' && r.op.isLate ? 'is-late' : '',
          ]
            .filter(Boolean)
            .join(' ')
        }
        onRow={(r) =>
          r.kind === 'op'
            ? {
                onClick: () => onOpenJob(r.op.jobOrderId),
                onKeyDown: (e) => {
                  if (e.key === 'Enter') onOpenJob(r.op.jobOrderId);
                },
                tabIndex: 0,
                role: 'link',
                'aria-label': `Open ${r.op.jobNumber || 'job order'}`,
              }
            : {}
        }
      />
    </div>
  );
}

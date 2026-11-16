import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Grid, Skeleton } from 'antd';
import { AimOutlined } from '@ant-design/icons';
import dayjs, { type Dayjs } from 'dayjs';
import {
  scheduleApi,
  type ScheduleBoardDowntime,
  type ScheduleBoardOperation,
  type ShopDayWindow,
} from '../../api/schedule.api';
import { getErrorMessage } from '../../api/client';
import InfoTip from '../../components/InfoTip';
import PeriodNavigator from '../../components/PeriodNavigator';
import { Legend, type LegendItem } from '../../components/ui';
import { TOUCH_QUERY, useMediaQuery } from '../../hooks/useViewport';
import { adminPx } from '../../theme/adminTheme';
import type { MachineUnitInfo, ProposedOperation } from '../../types';
import { personLabel } from '../../utils/people';
import { SHOP_TZ } from '../../utils/shopTime';
import {
  BoardGrid,
  CLASH_COLOR,
  DOWN_COLOR,
  NO_DOWNTIMES,
  OTHER_JOB_COLOR,
  PressGroup,
  THIS_JOB_COLOR,
  groupBy,
  groupByMany,
  opGroupKeys,
  type RowDef,
  type RowMode,
} from '../schedule/BoardGrid';
import type { BoardState } from '../schedule/boardStates';
import ScheduleExpandShell from '../schedule/ScheduleExpandShell';
import {
  periodBounds,
  weekStartFromIsoDates,
  type TimelineViewMode,
} from '../schedule/scheduleTimelineUtils';
import JobScheduleColorPicker from './JobScheduleColorPicker';
import '../schedule/scheduleBoard.css';

type BoardWorker = {
  id: string;
  fullName: string;
  nickname?: string | null;
  photoVersion?: number | null;
};

type BoardData = {
  operations: ScheduleBoardOperation[];
  workers: BoardWorker[];
  downtimes: ScheduleBoardDowntime[];
  shopDayWindows: ShopDayWindow[];
};

function buildMachineRows(units: MachineUnitInfo[]): RowDef[] {
  const rows: RowDef[] = [];
  let lastType = '';
  const sorted = [...units].sort((a, b) => {
    const ta = a.machineTypeName || '';
    const tb = b.machineTypeName || '';
    if (ta !== tb) return ta.localeCompare(tb);
    return a.label.localeCompare(b.label);
  });
  for (const u of sorted) {
    const group = u.machineTypeName || u.machineTypeCode || 'Machines';
    rows.push({
      key: u.id,
      label: u.label,
      group: group !== lastType ? group : undefined,
      machineUnitId: u.id,
    });
    lastType = group;
  }
  rows.push({ key: '__none__', label: 'No machine', noMachine: true });
  return rows;
}

function buildWorkerRows(workers: BoardWorker[], ops: ScheduleBoardOperation[]): RowDef[] {
  const byId = new Map<string, BoardWorker>();
  for (const w of workers) byId.set(w.id, w);
  for (const op of ops) {
    if (op.assignedWorkerId && !byId.has(op.assignedWorkerId)) {
      byId.set(op.assignedWorkerId, {
        id: op.assignedWorkerId,
        fullName: op.assignedWorkerName || 'Worker',
        nickname: op.assignedWorkerNickname,
        photoVersion: op.assignedWorkerPhotoVersion,
      });
    }
    for (const m of op.crew || []) {
      if (!byId.has(m.id)) {
        byId.set(m.id, { id: m.id, fullName: m.fullName, nickname: m.nickname, photoVersion: m.photoVersion });
      }
    }
  }
  return [...byId.values()]
    .sort((a, b) => a.fullName.localeCompare(b.fullName))
    .map((w) => ({
      key: w.id,
      label: personLabel(w.fullName, w.nickname),
      workerId: w.id,
      personName: w.fullName,
      photoVersion: w.photoVersion,
    }));
}

function segmentsForOp(op: ProposedOperation) {
  // Prefer the scheduler's working segments (already split across days).
  if (op.segments && op.segments.length > 0) return op.segments;
  if (op.scheduledStart && op.scheduledEnd) {
    return [{ start: op.scheduledStart, end: op.scheduledEnd }];
  }
  return [];
}

function proposedToBoardOp(
  op: ProposedOperation,
  jobId: string,
  jobNumber?: string | null,
  jobTitle?: string,
  scheduleColor?: string | null,
  workerName?: string | null
): ScheduleBoardOperation {
  return {
    id: op.id || `proposed-${op.sequenceNo}`,
    jobOrderId: jobId,
    jobNumber: jobNumber || undefined,
    jobTitle,
    sequenceNo: op.sequenceNo,
    operationName: op.operationName || `Operation ${op.sequenceNo}`,
    status: 'SCHEDULED',
    estimatedHours: op.estimatedHours ?? undefined,
    scheduledStart: op.scheduledStart || null,
    scheduledEnd: op.scheduledEnd || null,
    segments: segmentsForOp(op),
    machineTypeId: op.machineTypeId,
    machineUnitId: op.machineUnitId,
    machineUnitLabel: op.machineUnitLabel,
    assignedWorkerId: op.assignedWorkerId,
    assignedWorkerName: workerName || undefined,
    helperIds: op.helperIds || [],
    scheduleColor: scheduleColor || null,
  };
}

const PERIODS: { label: string; value: TimelineViewMode }[] = [
  { label: 'Day', value: 'day' },
  { label: 'Week', value: 'week' },
  { label: 'Month', value: 'month' },
];

const ROW_MODES: { label: string; value: RowMode }[] = [
  { label: 'By machine', value: 'machine' },
  { label: 'By worker', value: 'worker' },
];

type Props = {
  jobId: string;
  jobNumber?: string | null;
  jobTitle: string;
  operations: ProposedOperation[];
  machineUnits: MachineUnitInfo[];
  scheduleColor?: string | null;
  onScheduleColorChange?: (hex: string) => void;
  colorPickerDisabled?: boolean;
};

/**
 * The proposed schedule drawn on the same board as the Schedule page: the same
 * Day / Week / Month and machine / worker switches, period navigator, Today,
 * "Now" line and bars. This job stands out; other booked work is greyed.
 */
export default function ScheduleWeekView({
  jobId,
  jobNumber,
  jobTitle,
  operations,
  machineUnits,
  scheduleColor,
  onScheduleColorChange,
  colorPickerDisabled,
}: Props) {
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.md;
  const isTouch = useMediaQuery(TOUCH_QUERY);
  const rowH = isTouch ? 52 : isMobile ? adminPx(40) : adminPx(44);
  const [viewMode, setViewMode] = useState<TimelineViewMode>('week');
  const [rowMode, setRowMode] = useState<RowMode>('machine');
  // Keep the pills snappy; rebuild the board at lower priority, as on the Schedule page.
  const boardViewMode = useDeferredValue(viewMode);
  const boardRowMode = useDeferredValue(rowMode);
  const boardPending = boardViewMode !== viewMode || boardRowMode !== rowMode;
  const labelW = isMobile ? adminPx(96) : boardViewMode === 'month' ? adminPx(220) : adminPx(168);

  const proposed = useMemo(
    () => operations.filter((o) => o.scheduled && o.scheduledStart && o.scheduledEnd),
    [operations]
  );

  // Open on the week the proposal starts in.
  const defaultWeekKey = useMemo(() => {
    const dates = proposed.flatMap((o) => segmentsForOp(o).map((s) => s.start));
    return weekStartFromIsoDates(dates).format('YYYY-MM-DD');
  }, [proposed]);
  const [anchor, setAnchor] = useState<Dayjs>(() => dayjs.tz(defaultWeekKey, SHOP_TZ).startOf('day'));
  useEffect(() => {
    setAnchor(dayjs.tz(defaultWeekKey, SHOP_TZ).startOf('day'));
  }, [defaultWeekKey]);

  const { from, to } = useMemo(() => periodBounds(anchor, boardViewMode), [anchor, boardViewMode]);
  const fromKey = from.format('YYYY-MM-DD');
  const toKey = to.format('YYYY-MM-DD');

  const [data, setData] = useState<BoardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState('');

  // Periods already seen show at once while they refresh.
  const cache = useRef(new Map<string, BoardData>());
  useEffect(() => {
    let cancelled = false;
    const key = `${jobId}|${fromKey}|${toKey}`;
    const cached = cache.current.get(key);
    if (cached) setData(cached);
    setLoading(!cached);
    setFetchError('');
    (async () => {
      try {
        const { data: res } = await scheduleApi.board({ from: fromKey, to: toKey, includeCompleted: false });
        const next: BoardData = {
          // This job's saved operations are replaced by the proposal.
          operations: res.operations.filter((op) => op.jobOrderId !== jobId),
          workers: res.workers || [],
          downtimes: res.downtimes || [],
          shopDayWindows: res.shopDayWindows || [],
        };
        if (cache.current.size > 30) cache.current.clear();
        cache.current.set(key, next);
        if (!cancelled) setData(next);
      } catch (err) {
        if (!cancelled) setFetchError(getErrorMessage(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [jobId, fromKey, toKey]);

  const workerNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const w of data?.workers || []) map.set(w.id, personLabel(w.fullName, w.nickname));
    return map;
  }, [data]);

  const mergedOps = useMemo(() => {
    const thisJob = proposed.map((op) =>
      proposedToBoardOp(
        op,
        jobId,
        jobNumber,
        jobTitle,
        scheduleColor,
        op.assignedWorkerId ? workerNameById.get(op.assignedWorkerId) : null
      )
    );
    return [...(data?.operations || []), ...thisJob];
  }, [proposed, data, jobId, jobNumber, jobTitle, scheduleColor, workerNameById]);

  const rows = useMemo(
    () =>
      boardRowMode === 'worker'
        ? buildWorkerRows(data?.workers || [], mergedOps)
        : buildMachineRows(machineUnits),
    [boardRowMode, data, mergedOps, machineUnits]
  );
  const opsByRowKey = useMemo(
    () => groupByMany(mergedOps, (o) => opGroupKeys(o, boardRowMode)),
    [mergedOps, boardRowMode]
  );
  const downtimesByUnit = useMemo(
    () => groupBy(data?.downtimes ?? NO_DOWNTIMES, (d) => d.machineUnitId),
    [data]
  ) as Map<string, ScheduleBoardDowntime[]>;

  // Warnings show in the key only when they are on the board.
  const [boardStates, setBoardStates] = useState<ReadonlySet<BoardState>>(new Set());
  const legend: LegendItem[] = useMemo(
    () => [
      { label: 'This job (proposed)', color: scheduleColor || THIS_JOB_COLOR },
      { label: 'Other booked work', color: OTHER_JOB_COLOR, border: '1px solid #cbd5e1' },
      ...(boardStates.has('breakdown')
        ? [
            {
              label: 'Machine breakdown',
              pattern: 'repeating-linear-gradient(-45deg, #E8C5CB, #E8C5CB 3px, #F5E6E9 3px, #F5E6E9 6px)',
              border: '1px solid #C45A6A',
            },
          ]
        : []),
      ...(boardStates.has('clash')
        ? [{ label: 'Clash (double-booked)', color: '#fff', border: `2px solid ${CLASH_COLOR}` }]
        : []),
      ...(boardStates.has('down')
        ? [{ label: 'Booked on a down machine', color: '#fff', border: `2px dashed ${DOWN_COLOR}` }]
        : []),
    ],
    [scheduleColor, boardStates]
  );

  if (!proposed.length) return null;

  const shiftPeriod = (dir: -1 | 1) => {
    if (viewMode === 'day') setAnchor((a) => a.add(dir, 'day'));
    else if (viewMode === 'week') setAnchor((a) => a.add(dir * 7, 'day'));
    else setAnchor((a) => a.add(dir, 'month'));
  };

  const goToToday = () => {
    setViewMode('day');
    setAnchor(dayjs().tz(SHOP_TZ));
  };

  return (
    <div className="jo-proposal-board">
      <ScheduleExpandShell
        title={
          <span className="jo-proposal-board__switches">
            <PressGroup label="Rows" value={rowMode} onChange={setRowMode} options={ROW_MODES} />
            <PressGroup label="Period" value={viewMode} onChange={setViewMode} options={PERIODS} />
          </span>
        }
        center={
          <PeriodNavigator
            mode={viewMode}
            value={anchor}
            onChange={(d) => setAnchor(d.tz(SHOP_TZ))}
            onStep={shiftPeriod}
          />
        }
        trailing={
          <span className="jo-proposal-board__trailing">
            {onScheduleColorChange ? (
              <JobScheduleColorPicker
                value={scheduleColor}
                onChange={onScheduleColorChange}
                disabled={colorPickerDisabled}
              />
            ) : null}
            <Button icon={<AimOutlined />} onClick={goToToday}>
              Today
            </Button>
          </span>
        }
      >
        {fetchError ? (
          <div className="jo-proposal-board__error">Could not load the shop schedule: {fetchError}</div>
        ) : null}
        {loading && !data ? (
          <div className="sched-page__loading" aria-busy>
            <Skeleton active paragraph={{ rows: 6 }} title={false} />
          </div>
        ) : (
          <BoardGrid
            rows={rows}
            rowMode={boardRowMode}
            opsByRowKey={opsByRowKey}
            downtimesByUnit={downtimesByUnit}
            from={from}
            to={to}
            viewMode={boardViewMode}
            isMobile={isMobile}
            rowH={rowH}
            labelW={labelW}
            shopDayWindows={data?.shopDayWindows}
            pending={boardPending}
            highlightJobId={jobId}
            highlightColor={scheduleColor}
            onStates={setBoardStates}
          />
        )}
      </ScheduleExpandShell>
      <div className="sched-page__legend jo-proposal-board__legend">
        <Legend items={legend} />
        <InfoTip
          label="About this view"
          content="Bars for this job show the proposed times; nothing is saved until you confirm. Other booked work is greyed so this job stands out."
        />
      </div>
    </div>
  );
}

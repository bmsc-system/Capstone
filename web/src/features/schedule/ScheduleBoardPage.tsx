import { OPERATION_BAR_COLOR, TONE_BAR_FILL } from '../../theme/statuses';
import { Legend, StatCard, StatGrid, TabBar, type LegendItem } from '../../components/ui';
import InfoTip from '../../components/InfoTip';
import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react';
import {
  Badge,
  Button,
  Drawer,
  Grid,
  Select,
  Segmented,
  Skeleton,
  Switch,
  message,
} from 'antd';
import {
  AimOutlined,
  FilterOutlined,
  CloseOutlined,
  CalendarOutlined,
  DashboardOutlined,
  WarningOutlined,
  InboxOutlined,
} from '@ant-design/icons';
import PeriodNavigator from '../../components/PeriodNavigator';
import dayjs, { type Dayjs } from 'dayjs';
import { useNavigate } from 'react-router-dom';
import {
  scheduleApi,
  type ScheduleBoardDowntime,
  type ScheduleBoardResponse,
} from '../../api/schedule.api';
import { getErrorMessage } from '../../api/client';
import { useAuth } from '../../hooks/useAuth';
import { useOverdueCheck } from '../../hooks/useOverdueCheck';
import { adminPx } from '../../theme/adminTheme';
import {
  MATERIAL_WAIT_BAR_IMAGE,
  periodBounds,
  type TimelineViewMode,
} from './scheduleTimelineUtils';
import { SHOP_TZ } from '../../utils/shopTime';
import ScheduleExpandShell from './ScheduleExpandShell';
import WorkerPersonalSchedule from './WorkerPersonalSchedule';
import ScheduleAgenda from './ScheduleAgenda';
import ScheduleList from './ScheduleList';
import { capacityLine } from './capacityText';
import { MOBILE_SHELL_QUERY, TOUCH_QUERY, useMediaQuery } from '../../hooks/useViewport';
import { personLabel } from '../../utils/people';
import {
  BoardGrid,
  CLASH_COLOR,
  DOWN_COLOR,
  NO_DOWNTIMES,
  NO_OPS,
  PressGroup,
  STALLED_COLOR,
  groupBy,
  groupByMany,
  opGroupKeys,
  type RowDef,
  type RowMode,
} from './BoardGrid';
import './scheduleBoard.css';

type ViewMode = TimelineViewMode;


const SCHEDULE_LEGEND: LegendItem[] = [
  // Bars take their job order's color; the status is written on the bar.
  {
    label: 'Each job order has its own color',
    pattern: 'linear-gradient(90deg, #2563EB 0 33%, #DB2777 33% 66%, #4D7C0F 66%)',
  },
  { label: 'Completed (faded)', color: 'rgba(37, 99, 235, 0.55)' },
  { label: 'Waiting for materials', pattern: `${MATERIAL_WAIT_BAR_IMAGE}, ${OPERATION_BAR_COLOR.SCHEDULED}` },
  { label: 'At risk of missing date', color: '#fff', border: `2px solid ${TONE_BAR_FILL.red}` },
  {
    label: 'Machine breakdown',
    pattern: 'repeating-linear-gradient(-45deg, #E8C5CB, #E8C5CB 3px, #F5E6E9 3px, #F5E6E9 6px)',
    border: '1px solid #C45A6A',
  },
  { label: 'Clash (double-booked)', color: '#fff', border: `2px solid ${CLASH_COLOR}` },
  { label: 'Booked on a down machine', color: '#fff', border: `2px dashed ${DOWN_COLOR}` },
  { label: 'Stalled (paused over a day)', color: '#fff', border: `2px dotted ${STALLED_COLOR}` },
];

type PhoneView = 'list' | 'board';
const PHONE_VIEW_KEY = 'schedule.phoneView';
type DeskView = 'board' | 'list';
const DESK_VIEW_KEY = 'schedule.deskView';

function readDeskView(): DeskView {
  try {
    return localStorage.getItem(DESK_VIEW_KEY) === 'list' ? 'list' : 'board';
  } catch {
    return 'board';
  }
}

function readPhoneView(): PhoneView {
  try {
    return localStorage.getItem(PHONE_VIEW_KEY) === 'board' ? 'board' : 'list';
  } catch {
    return 'list';
  }
}

export default function ScheduleBoardPage() {
  const { isWorker } = useAuth();
  if (isWorker) return <WorkerPersonalSchedule />;
  return <AdminOfficeScheduleBoard />;
}

function AdminOfficeScheduleBoard() {
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.md;
  const isTouch = useMediaQuery(TOUCH_QUERY);
  const plainStats = useMediaQuery(MOBILE_SHELL_QUERY);
  // Bars are rowH - 8 tall; touch screens get 44px bars.
  const rowH = isTouch ? 52 : isMobile ? adminPx(40) : adminPx(44);
  const [viewMode, setViewMode] = useState<ViewMode>('week');
  const labelW = isMobile ? adminPx(96) : viewMode === 'month' ? adminPx(220) : adminPx(168);
  const [rowMode, setRowMode] = useState<RowMode>('machine');
  // Keep the pills snappy; rebuild the heavy board at lower priority.
  const boardViewMode = useDeferredValue(viewMode);
  const boardRowMode = useDeferredValue(rowMode);
  const boardPending = boardViewMode !== viewMode || boardRowMode !== rowMode;
  const [anchor, setAnchor] = useState(() => dayjs().tz(SHOP_TZ));
  const [includeCompleted, setIncludeCompleted] = useState(true);
  const [machineTypeId, setMachineTypeId] = useState<string | undefined>();
  const [workerId, setWorkerId] = useState<string | undefined>();
  const [clientId, setClientId] = useState<string | undefined>();
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [phoneView, setPhoneView] = useState<PhoneView>(readPhoneView);
  const changePhoneView = (next: PhoneView) => {
    setPhoneView(next);
    try {
      localStorage.setItem(PHONE_VIEW_KEY, next);
    } catch {
      /* storage unavailable */
    }
  };
  const [deskView, setDeskView] = useState<DeskView>(readDeskView);
  const changeDeskView = (next: DeskView) => {
    setDeskView(next);
    try {
      localStorage.setItem(DESK_VIEW_KEY, next);
    } catch {
      /* storage unavailable */
    }
  };
  const [data, setData] = useState<ScheduleBoardResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const { from, to } = useMemo(
    () => periodBounds(anchor, boardViewMode),
    [anchor, boardViewMode],
  );
  useOverdueCheck();

  // Periods already seen show at once while they refresh, so going back and
  // forth between Day / Week / Month or weeks does not wait on the server.
  const boardCache = useRef(new Map<string, typeof data>());
  useEffect(() => {
    let cancelled = false;
    const params = {
      from: from.format('YYYY-MM-DD'),
      to: to.format('YYYY-MM-DD'),
      machineTypeId,
      workerId,
      clientId,
      includeCompleted,
    };
    const cacheKey = JSON.stringify(params);
    const cached = boardCache.current.get(cacheKey);
    if (cached) setData(cached);
    (async () => {
      setLoading(!cached);
      try {
        const res = await scheduleApi.board(params);
        if (boardCache.current.size > 30) boardCache.current.clear();
        boardCache.current.set(cacheKey, res.data);
        if (!cancelled) setData(res.data);
      } catch (err) {
        if (!cancelled) message.error(getErrorMessage(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [from, to, machineTypeId, workerId, clientId, includeCompleted]);

  const machineTypes = useMemo(() => {
    const map = new Map<string, string>();
    for (const u of data?.machineUnits || []) {
      if (u.machineTypeId) {
        map.set(u.machineTypeId, u.machineTypeName || u.machineTypeCode || u.machineTypeId);
      }
    }
    return [...map.entries()].map(([id, name]) => ({ id, name }));
  }, [data]);

  const rows: RowDef[] = useMemo(() => {
    if (!data) return [];
    if (boardRowMode === 'worker') {
      return data.workers.map((w) => ({
        key: w.id,
        label: personLabel(w.fullName, w.nickname),
        workerId: w.id,
        personName: w.fullName,
        photoVersion: w.photoVersion,
      }));
    }
    const out: RowDef[] = [];
    let lastType = '';
    const sorted = [...data.machineUnits].sort((a, b) => {
      const ta = a.machineTypeName || a.machineTypeCode || '';
      const tb = b.machineTypeName || b.machineTypeCode || '';
      if (ta !== tb) return ta.localeCompare(tb);
      return a.label.localeCompare(b.label);
    });
    for (const u of sorted) {
      const group = u.machineTypeName || u.machineTypeCode || 'Machines';
      out.push({
        key: u.id,
        label: u.label,
        group: group !== lastType ? group : undefined,
        machineUnitId: u.id,
      });
      lastType = group;
    }
    out.push({ key: '__none__', label: 'No machine', noMachine: true });
    return out;
  }, [data, boardRowMode]);

  const shiftPeriod = (dir: -1 | 1) => {
    if (viewMode === 'day') setAnchor((a) => a.add(dir, 'day'));
    else if (viewMode === 'week') setAnchor((a) => a.add(dir * 7, 'day'));
    else setAnchor((a) => a.add(dir, 'month'));
  };

  const goToToday = () => {
    setViewMode('day');
    setAnchor(dayjs().tz(SHOP_TZ));
  };

  const jumpToDate = (d: Dayjs) => {
    setAnchor(d.tz(SHOP_TZ));
  };

  const clearBoardFilters = () => {
    setMachineTypeId(undefined);
    setClientId(undefined);
    setWorkerId(undefined);
    setIncludeCompleted(true);
  };

  const opsByRowKey = useMemo(
    () => groupByMany(data?.operations ?? NO_OPS, (o) => opGroupKeys(o, boardRowMode)),
    [data, boardRowMode]
  );
  const downtimesByUnit = useMemo(
    () => groupBy(data?.downtimes ?? NO_DOWNTIMES, (d) => d.machineUnitId),
    [data]
  ) as Map<string, ScheduleBoardDowntime[]>;

  // navigate changes identity on every route change; keep the bars' click handler stable.
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;
  const openJob = useCallback(
    (jobOrderId: string) => navigateRef.current(`/job-orders/${jobOrderId}`),
    []
  );

  const summary = data?.summary;
  const isPhoneBoard = plainStats;
  const boardCollapsedMaxHeight = isPhoneBoard
      ? 'calc(100dvh - 340px)'
      : isMobile
        ? 'calc(100dvh - 260px)'
        : 'calc(100vh - 280px)';
  const today = dayjs().tz(SHOP_TZ).startOf('day');
  const showingToday = !today.isBefore(from, 'day') && !today.isAfter(to, 'day');
  const nearFull = summary?.machinesNearFullCapacity || [];
  const capacityRows = summary?.machineCapacity || [];
  const atRiskCount = summary?.jobsAtRisk?.length ?? 0;
  const waitingJobs = useMemo(() => {
    const seen = new Map<string, string>();
    for (const op of data?.operations || []) {
      if (op.waitingForMaterials && !seen.has(op.jobOrderId)) {
        seen.set(op.jobOrderId, op.jobNumber || op.jobTitle || op.jobOrderId.slice(0, 8));
      }
    }
    return [...seen.values()];
  }, [data]);
  const activeFilterCount =
    [machineTypeId, workerId, clientId].filter(Boolean).length + (includeCompleted ? 0 : 1);

  const filterSelects = (
    <>
      {!isPhoneBoard && deskView === 'board' && (
        <PressGroup
          label="Rows"
          value={rowMode}
          onChange={setRowMode}
          options={[
            { label: 'By machine', value: 'machine' },
            { label: 'By worker', value: 'worker' },
          ]}
        />
      )}
      <Select
        allowClear
        placeholder="Machine type"
        style={{ width: isMobile ? '100%' : 160 }}
        value={machineTypeId}
        onChange={setMachineTypeId}
        options={machineTypes.map((t) => ({ value: t.id, label: t.name }))}
      />
      <Select
        allowClear
        showSearch
        optionFilterProp="label"
        placeholder="Worker"
        style={{ width: isMobile ? '100%' : 160 }}
        value={workerId}
        onChange={setWorkerId}
        options={(data?.workers || []).map((w) => ({
          value: w.id,
          label: personLabel(w.fullName, w.nickname),
        }))}
      />
      <Select
        allowClear
        showSearch
        optionFilterProp="label"
        placeholder="Client"
        style={{ width: isMobile ? '100%' : 160 }}
        value={clientId}
        onChange={setClientId}
        options={(data?.clients || []).map((c) => ({ value: c.id, label: c.name }))}
      />
    </>
  );

  const filterToggles = (
    <>
      <label
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 13,
          whiteSpace: 'nowrap',
        }}
      >
        <Switch size="small" checked={includeCompleted} onChange={setIncludeCompleted} />
        Show completed
      </label>
      {!isPhoneBoard && (
        <span style={{ marginLeft: 8 }}>
          <PressGroup
            label="View"
            value={deskView}
            onChange={changeDeskView}
            options={[
              { label: 'Board', value: 'board' },
              { label: 'List', value: 'list' },
            ]}
          />
        </span>
      )}
    </>
  );

  const board = (
    <div
      className={isPhoneBoard ? undefined : 'ui-page sched-page'}
      style={
        isPhoneBoard
          ? { display: 'flex', flexDirection: 'column', gap: 10, minHeight: 0, minWidth: 0 }
          : undefined
      }
    >
      {isPhoneBoard ? (
        <div className="sched-m">
          <div className="sched-m__top">
            <div className="sched-m__nav">
              <PeriodNavigator
                className="sched-m__period"
                short
                mode={viewMode}
                value={anchor}
                onChange={jumpToDate}
                onStep={shiftPeriod}
              />
              {!showingToday ? (
                <Button className="sched-m__today" onClick={goToToday}>
                  Today
                </Button>
              ) : null}
              <Badge count={activeFilterCount} size="small" offset={[-4, 4]} className="sched-m__filter">
                <button type="button" className="sched-m__icon" onClick={() => setFiltersOpen(true)} aria-label="Filters">
                  <FilterOutlined />
                </button>
              </Badge>
            </div>
            <div className="sched-m__groups">
              <Segmented
                className="sched-seg sched-m__seg"
                block
                aria-label="View"
                value={phoneView}
                onChange={(v) => changePhoneView(v as 'list' | 'board')}
                options={[
                  { label: 'List', value: 'list' },
                  { label: 'Board', value: 'board' },
                ]}
              />
            </div>
          </div>
          <div className="sched-m__stats">
            <div className="sched-m__stat">
              <div className="sched-m__stat-n">{summary?.jobsScheduled ?? '—'}</div>
              <div className="sched-m__stat-l">JO scheduled</div>
            </div>
            <div className="sched-m__stat">
              <div className="sched-m__stat-n">{nearFull.length}</div>
              <div className="sched-m__stat-l">Near full</div>
            </div>
            <div className={`sched-m__stat${atRiskCount ? ' is-danger' : ''}`}>
              <div className="sched-m__stat-n">{atRiskCount}</div>
              <div className="sched-m__stat-l">At risk</div>
            </div>
            <div className="sched-m__stat">
              <div className="sched-m__stat-n">{waitingJobs.length}</div>
              <div className="sched-m__stat-l">No material</div>
            </div>
          </div>
        </div>
      ) : (
        <StatGrid min={200}>
          <StatCard
            icon={<CalendarOutlined />}
            tone="blue"
            label="JO scheduled"
            value={summary?.jobsScheduled ?? '—'}
            loading={loading && !data}
            hint={
              plainStats
                ? undefined
                : summary?.jobsScheduled
                  ? `${summary.operationsScheduled} operation${
                      summary.operationsScheduled === 1 ? '' : 's'
                    } on the board`
                  : 'No job orders in this period'
            }
          />
          <StatCard
            icon={<DashboardOutlined />}
            tone="amber"
            label={
              <>
                Near full capacity
                {capacityRows.length && !plainStats ? (
                  <InfoTip
                    label="How capacity is worked out"
                    title="Load this period"
                    content={
                      <>
                        <div>
                          Hours booked inside the period, less hours already worked, over the shop
                          hours (with overtime days) of units that are not down.
                        </div>
                        {capacityRows.map((c) => (
                          <div key={c.machineTypeId}>{capacityLine(c)}</div>
                        ))}
                      </>
                    }
                  />
                ) : null}
              </>
            }
            value={
              plainStats ? nearFull.length : nearFull.length ? nearFull.map((m) => m.machineTypeCode).join(', ') : 'None'
            }
            loading={loading && !data}
            hint={
              plainStats
                ? undefined
                : nearFull.length
                ? nearFull.map((m) => `${m.machineTypeCode} ${m.projectedLoadPct ?? '—'}%`).join(' · ')
                : 'None at 80% load or more'
            }
          />
          <StatCard
            icon={<WarningOutlined />}
            tone="red"
            label="Jobs at risk"
            value={atRiskCount}
            valueTone={atRiskCount > 0 ? 'red' : undefined}
            loading={loading && !data}
            hint={
              plainStats
                ? undefined
                : atRiskCount
                ? (summary?.jobsAtRisk || [])
                    .slice(0, 4)
                    .map((j) => j.jobNumber || j.jobTitle)
                    .join(', ') + (atRiskCount > 4 ? '…' : '')
                : 'None past date required'
            }
          />
          <StatCard
            icon={<InboxOutlined />}
            tone="amber"
            label="Waiting for materials"
            value={waitingJobs.length}
            loading={loading && !data}
            hint={
              plainStats
                ? undefined
                : waitingJobs.length
                ? waitingJobs.slice(0, 4).join(', ') + (waitingJobs.length > 4 ? '…' : '')
                : 'All jobs have materials'
            }
          />
        </StatGrid>
      )}

      <div className={isPhoneBoard ? 'sched-page__phone-board' : 'ui-card sched-page__card'}>
      {!isPhoneBoard ? (
        <div className="sched-page__filters">
          <div className="sched-page__filters-left">{filterSelects}</div>
          <div className="sched-page__filters-right">{filterToggles}</div>
        </div>
      ) : null}

      {loading && !data ? (
        <div className="sched-page__loading" aria-busy>
          <Skeleton active paragraph={{ rows: 8 }} title={false} />
        </div>
      ) : isPhoneBoard && phoneView === 'list' ? (
        <ScheduleAgenda
          operations={data?.operations ?? NO_OPS}
          downtimes={data?.downtimes ?? NO_DOWNTIMES}
          from={from}
          to={to}
          groupBy={rowMode}
          onOpenJob={openJob}
        />
      ) : (
        <ScheduleExpandShell
          collapsedMaxHeight={boardCollapsedMaxHeight}
          title={
            !isPhoneBoard ? (
              <PressGroup
                label="Period"
                value={viewMode}
                onChange={setViewMode}
                options={[
                  { label: 'Day', value: 'day' },
                  { label: 'Week', value: 'week' },
                  { label: 'Month', value: 'month' },
                ]}
              />
            ) : undefined
          }
          center={
            !isPhoneBoard ? (
              <PeriodNavigator
                mode={viewMode}
                value={anchor}
                onChange={jumpToDate}
                onStep={shiftPeriod}
              />
            ) : undefined
          }
          trailing={
            !isPhoneBoard ? (
              <Button
                icon={<AimOutlined />}
                onClick={goToToday}
              >
                Today
              </Button>
            ) : undefined
          }
        >
          {deskView === 'list' && !isPhoneBoard ? (
            <ScheduleList
              operations={data?.operations ?? NO_OPS}
              downtimes={data?.downtimes ?? NO_DOWNTIMES}
              from={from}
              to={to}
              onOpenJob={openJob}
            />
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
              onOpenJob={openJob}
            />
          )}
        </ScheduleExpandShell>
      )}
      {!isPhoneBoard && deskView === 'board' ? (
        <div className="sched-page__legend">
          <Legend items={SCHEDULE_LEGEND} />
          <InfoTip
            label="About bar colours"
            content="Bars take the job's own colour when one is set on the job; otherwise they show the operation status. Click a bar to open its job order."
          />
        </div>
      ) : null}
      </div>

      <Drawer
        className="sched-f-drawer"
        rootClassName="sched-f-drawer"
        placement="bottom"
        size="auto"
        open={filtersOpen}
        onClose={() => setFiltersOpen(false)}
        closable={false}
        title={null}
        styles={{
          section: {
            padding: 0,
            borderRadius: '16px 16px 0 0',
            overflow: 'hidden',
            background: '#f1f5f9',
          },
          header: { display: 'none', padding: 0 },
          body: { padding: 0, background: '#f1f5f9' },
        }}
      >
        <div className="sched-f">
          <div className="sched-f__handle" />
          <div className="sched-f__head">
            <div>
              <div className="sched-f__title">Filters</div>
              <div className="sched-f__sub">
                {activeFilterCount ? `${activeFilterCount} on` : 'None on'}
              </div>
            </div>
            {activeFilterCount ? (
              <button type="button" className="sched-f__text" onClick={clearBoardFilters}>
                Clear
              </button>
            ) : null}
            <button
              type="button"
              className="sched-m__icon"
              onClick={() => setFiltersOpen(false)}
              aria-label="Close"
            >
              <CloseOutlined />
            </button>
          </div>

          {isPhoneBoard ? (
            <>
              <div className="sched-f__card">
                <div className="sched-f__label">Period</div>
                <div className="sched-m__pills" role="group" aria-label="Period">
                  <TabBar className="sched-seg" block value={viewMode} onChange={setViewMode} items={[{ value: 'day' as const, label: 'Day' }, { value: 'week' as const, label: 'Week' }, { value: 'month' as const, label: 'Month' }]} />
                </div>
              </div>
              <div className="sched-f__card">
                <div className="sched-f__label">Group by</div>
                <div className="sched-m__pills" role="group" aria-label="Rows">
                  <TabBar className="sched-seg" block value={rowMode} onChange={setRowMode} items={[{ value: 'machine' as const, label: 'Machine' }, { value: 'worker' as const, label: 'Worker' }]} />
                </div>
              </div>
            </>
          ) : (
            <div className="sched-f__card">
              <div className="sched-f__label">Rows</div>
              <div className="sched-m__pills" role="group" aria-label="Rows">
                <TabBar
                  className="sched-seg"
                  block
                  value={rowMode}
                  onChange={setRowMode}
                  items={[
                    { value: 'machine' as const, label: 'Machine' },
                    { value: 'worker' as const, label: 'Worker' },
                  ]}
                />
              </div>
            </div>
          )}

          <div className="sched-f__card">
            <div className="sched-f__label">Narrow by</div>
            <div className="sched-f__rows">
              <div className="sched-f__row">
                <span className="sched-f__row-k">Machine type</span>
                <Select
                  allowClear
                  variant="borderless"
                  placeholder="All"
                  className="sched-f__select"
                  value={machineTypeId}
                  onChange={setMachineTypeId}
                  options={machineTypes.map((t) => ({ value: t.id, label: t.name }))}
                />
              </div>
              <div className="sched-f__row">
                  <span className="sched-f__row-k">Worker</span>
                  <Select
                    allowClear
                    showSearch
                    variant="borderless"
                    optionFilterProp="label"
                    placeholder="All"
                    className="sched-f__select"
                    value={workerId}
                    onChange={setWorkerId}
                    options={(data?.workers || []).map((w) => ({
                      value: w.id,
                      label: personLabel(w.fullName, w.nickname),
                    }))}
                  />
                </div>
              <div className="sched-f__row">
                <span className="sched-f__row-k">Client</span>
                <Select
                  allowClear
                  showSearch
                  variant="borderless"
                  optionFilterProp="label"
                  placeholder="All"
                  className="sched-f__select"
                  value={clientId}
                  onChange={setClientId}
                  options={(data?.clients || []).map((c) => ({ value: c.id, label: c.name }))}
                />
              </div>
            </div>
          </div>

          <div className="sched-f__card">
            <div className="sched-f__label">Operations</div>
            <div className="sched-m__pills" role="group" aria-label="Completed">
              <TabBar
                className="sched-seg"
                block
                value={includeCompleted ? 'all' : 'hide'}
                onChange={(v) => setIncludeCompleted(v === 'all')}
                items={[
                  { value: 'all', label: 'All' },
                  { value: 'hide', label: 'Hide done' },
                ]}
              />
            </div>
          </div>

          <button type="button" className="sched-f__done" onClick={() => setFiltersOpen(false)}>
            Done
          </button>
        </div>
      </Drawer>
    </div>
  );

  return board;
}

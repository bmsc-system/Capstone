import { useEffect, useMemo, useState } from 'react';
import { Button, Skeleton } from 'antd';
import {
  CheckCircleOutlined,
  ClusterOutlined,
  FileTextOutlined,
  PlayCircleOutlined,
  RightOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend as ChartLegend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { jobOrdersApi } from '../../api/jobOrders.api';
import { operationsApi } from '../../api/operations.api';
import { scheduleApi, type ScheduleBoardResponse } from '../../api/schedule.api';
import type { JobOrder, MachineUnitStatus } from '../../types';
import {
  DataTable,
  EmptyState,
  IdChip,
  Page,
  SectionCard,
  StatCard,
  StatGrid,
  StatusPill,
  rowOpenProps,
} from '../../components/ui';
import {
  JOB_FLAG,
  JOB_STATUS,
  MACHINE_STATUS,
  OPERATION_BAR_COLOR,
  OPERATION_STATUS,
  TONE_BAR_FILL,
} from '../../theme/statuses';
import { chart } from '../../theme/tokens';
import { formatShop, shopNow, shopToday, SHOP_TZ } from '../../utils/shopTime';
import { isJobFinished } from '../job-orders/jobStage';
import { periodBounds } from '../schedule/scheduleTimelineUtils';
import { useAuth } from '../../hooks/useAuth';
import OfficeDashboard from './OfficeDashboard';

type Load<T> = { data: T | null; failed: boolean };

function isJobOverdue(job: JobOrder) {
  return (
    !isJobFinished(job.status) &&
    job.status !== 'CANCELLED' &&
    dayjs(job.dueDate).isBefore(shopToday(), 'day')
  );
}

const OP_SERIES = ['COMPLETED', 'IN_PROGRESS', 'SCHEDULED', 'PENDING'] as const;

type MachineState = 'running' | 'idle' | 'breakdown';
function machineState(u: MachineUnitStatus): MachineState | null {
  if (u.active === false) return null;
  if (u.down) return 'breakdown';
  if (u.currentOperation) return 'running';
  return 'idle';
}

function CardError({ what }: { what: string }) {
  return (
    <EmptyState
      compact
      icon={<WarningOutlined />}
      title={`Couldn't load ${what}`}
      description="Refresh the page to try again."
    />
  );
}

/** Each role gets its own dashboard (objective 4.1). */
export default function DashboardPage() {
  const { isOfficeStaff } = useAuth();
  return isOfficeStaff ? <OfficeDashboard /> : <AdminDashboard />;
}

function AdminDashboard() {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<Load<JobOrder[]>>({ data: null, failed: false });
  const [toPlan, setToPlan] = useState<number | null>(null);
  const [units, setUnits] = useState<Load<MachineUnitStatus[]>>({ data: null, failed: false });
  const [board, setBoard] = useState<Load<ScheduleBoardResponse>>({ data: null, failed: false });
  const week = useMemo(() => periodBounds(shopNow(), 'week'), []);

  useEffect(() => {
    let alive = true;
    jobOrdersApi
      .list({ scope: 'production', summary: true })
      .then(({ data }) => alive && setJobs({ data, failed: false }))
      .catch(() => alive && setJobs({ data: null, failed: true }));
    jobOrdersApi
      .list({ scope: 'drafts', summary: true })
      .then(({ data }) => alive && setToPlan(data.length))
      .catch(() => undefined);
    operationsApi
      .machineUnitStatus(false)
      .then(({ data }) => alive && setUnits({ data, failed: false }))
      .catch(() => alive && setUnits({ data: null, failed: true }));
    scheduleApi
      .board({
        from: week.from.format('YYYY-MM-DD'),
        to: week.to.format('YYYY-MM-DD'),
        includeCompleted: true,
      })
      .then(({ data }) => alive && setBoard({ data, failed: false }))
      .catch(() => alive && setBoard({ data: null, failed: true }));
    return () => {
      alive = false;
    };
  }, [week]);

  const jobStats = useMemo(() => {
    const list = jobs.data || [];
    return {
      active: list.filter((j) => j.status === 'SCHEDULED' || j.status === 'IN_PROGRESS').length,
      inProgress: list.filter((j) => j.status === 'IN_PROGRESS').length,
      waiting: list.filter((j) => j.status === 'IN_PROGRESS' && j.waitingForMaterials).length,
      completed: list.filter((j) => j.status === 'COMPLETED').length,
      forDelivery: list.filter((j) => j.status === 'FOR_DELIVERY').length,
      overdue: list.filter(isJobOverdue).length,
    };
  }, [jobs.data]);

  const recent = useMemo(
    () =>
      [...(jobs.data || [])]
        .sort((a, b) =>
          String(b.updatedAt || b.createdAt || '').localeCompare(String(a.updatedAt || a.createdAt || '')),
        )
        .slice(0, 6),
    [jobs.data],
  );

  const weekSeries = useMemo(() => {
    const days = Array.from({ length: 7 }, (_, i) => week.from.add(i, 'day'));
    const rows = days.map((d) => {
      const row: Record<string, number | string> = { day: d.format('ddd'), date: d.format('YYYY-MM-DD') };
      for (const s of OP_SERIES) row[s] = 0;
      return row;
    });
    for (const op of board.data?.operations || []) {
      const start = op.segments[0]?.start || op.scheduledStart;
      if (!start) continue;
      const key = dayjs(start).tz(SHOP_TZ).format('YYYY-MM-DD');
      const row = rows.find((r) => r.date === key);
      const s = (OP_SERIES as readonly string[]).includes(op.status) ? op.status : 'PENDING';
      if (row) row[s] = Number(row[s]) + 1;
    }
    return rows;
  }, [board.data, week]);
  const weekTotal = weekSeries.reduce(
    (n, r) => n + OP_SERIES.reduce((m, s) => m + Number(r[s] || 0), 0),
    0,
  );

  const machineCounts = useMemo(() => {
    const c: Record<MachineState, number> = { running: 0, idle: 0, breakdown: 0 };
    for (const u of units.data || []) {
      const s = machineState(u);
      if (s) c[s] += 1;
    }
    return c;
  }, [units.data]);
  const machineTotal = machineCounts.running + machineCounts.idle + machineCounts.breakdown;
  const machinePie = (['running', 'idle', 'breakdown'] as const).map((k) => ({
    key: k,
    name: MACHINE_STATUS[k].label,
    value: machineCounts[k],
    color: TONE_BAR_FILL[MACHINE_STATUS[k].tone],
  }));

  const jobsLoading = !jobs.data && !jobs.failed;

  return (
    <Page className="dashboard-page">
      <StatGrid min={200}>
        <StatCard
          icon={<FileTextOutlined />}
          tone="blue"
          label="Active job orders"
          value={jobStats.active}
          loading={jobsLoading}
          hint={toPlan != null ? `${toPlan} to plan` : undefined}
          onClick={() => navigate('/job-orders')}
        />
        <StatCard
          icon={<PlayCircleOutlined />}
          tone="indigo"
          label="In progress"
          value={jobStats.inProgress}
          loading={jobsLoading}
          hint={jobStats.waiting ? `${jobStats.waiting} waiting for materials` : 'On the shop floor now'}
          onClick={() => navigate('/job-orders')}
        />
        <StatCard
          icon={<CheckCircleOutlined />}
          tone="green"
          label="Not yet delivered"
          value={jobStats.completed + jobStats.forDelivery}
          loading={jobsLoading}
          hint={`${jobStats.forDelivery} For Delivery · ${jobStats.completed} Completed`}
          onClick={() => navigate('/job-orders')}
        />
        <StatCard
          icon={<WarningOutlined />}
          tone="red"
          label="Overdue"
          value={jobStats.overdue}
          valueTone={jobStats.overdue > 0 ? 'red' : undefined}
          loading={jobsLoading}
          hint="Past date required"
          onClick={() => navigate('/job-orders')}
        />
      </StatGrid>

      <div className="dashboard-page__charts">
        <SectionCard
          title="Operations this week"
          subtitle={`${week.from.format('MMM D')} – ${week.to.format('MMM D')} · by scheduled day`}
          info="Each operation on the schedule this week, counted on the day it is scheduled to start, by its current status."
          extra={
            <Button type="link" size="small" onClick={() => navigate('/schedule')}>
              Open schedule <RightOutlined />
            </Button>
          }
        >
          {board.failed ? (
            <CardError what="the schedule" />
          ) : !board.data ? (
            <Skeleton active paragraph={{ rows: 6 }} title={false} />
          ) : weekTotal === 0 ? (
            <EmptyState
              compact
              icon={<FileTextOutlined />}
              title="Nothing scheduled this week"
              description="Released job orders appear here once their operations are scheduled."
            />
          ) : (
            <div className="dashboard-page__chart" role="img" aria-label={`${weekTotal} operations scheduled this week`}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={weekSeries} margin={{ top: 4, right: 8, bottom: 0, left: -16 }} barCategoryGap="28%">
                  <CartesianGrid vertical={false} stroke={chart.grid} />
                  <XAxis dataKey="day" tickLine={false} axisLine={false} tick={{ fill: chart.axis, fontSize: chart.axisFontSize }} />
                  <YAxis allowDecimals={false} tickLine={false} axisLine={false} tick={{ fill: chart.axis, fontSize: chart.axisFontSize }} />
                  <ChartTooltip cursor={{ fill: 'rgba(15,23,42,0.04)' }} />
                  <ChartLegend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />
                  {OP_SERIES.map((s, i) => (
                    <Bar
                      key={s}
                      dataKey={s}
                      name={OPERATION_STATUS[s].label}
                      stackId="ops"
                      fill={OPERATION_BAR_COLOR[s]}
                      radius={i === OP_SERIES.length - 1 ? [4, 4, 0, 0] : undefined}
                      isAnimationActive={false}
                    />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </SectionCard>

        <SectionCard
          title="Machine status"
          subtitle="Right now"
          extra={
            <Button type="link" size="small" onClick={() => navigate('/machines')}>
              Open machines <RightOutlined />
            </Button>
          }
        >
          {units.failed ? (
            <CardError what="machines" />
          ) : !units.data ? (
            <Skeleton active avatar={{ size: 120 }} paragraph={{ rows: 3 }} title={false} />
          ) : machineTotal === 0 ? (
            <EmptyState compact icon={<ClusterOutlined />} title="No machines on the floor" />
          ) : (
            <div className="dashboard-page__donut">
              <div className="dashboard-page__donut-chart" role="img" aria-label={`${machineTotal} machines`}>
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={machinePie}
                      dataKey="value"
                      nameKey="name"
                      innerRadius="68%"
                      outerRadius="100%"
                      paddingAngle={machinePie.filter((p) => p.value > 0).length > 1 ? 2 : 0}
                      stroke="none"
                      isAnimationActive={false}
                    >
                      {machinePie.map((p) => (
                        <Cell key={p.key} fill={p.color} />
                      ))}
                    </Pie>
                    <ChartTooltip />
                  </PieChart>
                </ResponsiveContainer>
                <div className="dashboard-page__donut-center">
                  <span className="dashboard-page__donut-total ui-tabular">{machineTotal}</span>
                  <span className="dashboard-page__donut-label">machines</span>
                </div>
              </div>
              <ul className="dashboard-page__donut-legend">
                {machinePie.map((p) => (
                  <li key={p.key}>
                    <span className="dashboard-page__swatch" style={{ background: p.color }} aria-hidden />
                    <span className="dashboard-page__legend-name">{p.name}</span>
                    <span className="dashboard-page__legend-value ui-tabular">{p.value}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </SectionCard>
      </div>

      <SectionCard
        className="dashboard-recent"
        title="Recent job orders"
        subtitle="Last changed first"
        flush
        extra={
          <Button type="link" size="small" onClick={() => navigate('/job-orders')}>
            View all <RightOutlined />
          </Button>
        }
      >
        {jobs.failed ? (
          <CardError what="job orders" />
        ) : (
          <DataTable<JobOrder>
            card={{ title: 'title', subtitle: ['jobNumber', 'clientName'], badge: 'status' }}
            rowKey="id"
            loading={jobsLoading}
            dataSource={recent}
            pagination={false}
            scroll={{ x: 760 }}
            locale={{
              emptyText: (
                <EmptyState compact icon={<FileTextOutlined />} title="No released job orders yet" />
              ),
            }}
            onRow={(job) => rowOpenProps(() => navigate(`/job-orders/${job.id}`))}
            columns={[
              {
                title: 'Job #',
                key: 'jobNumber',
                width: 136,
                render: (_: unknown, job) => <IdChip>{job.jobNumber || job.id.slice(0, 8)}</IdChip>,
              },
              { title: 'Client', dataIndex: 'clientName', width: 180, ellipsis: true },
              { title: 'Title', dataIndex: 'title', ellipsis: true },
              {
                title: 'Status',
                key: 'status',
                width: 140,
                render: (_: unknown, job) =>
                  isJobOverdue(job) ? (
                    <StatusPill tone={JOB_FLAG.overdue.tone} compact>
                      {JOB_FLAG.overdue.label}
                    </StatusPill>
                  ) : (
                    <StatusPill status={job.status} map={JOB_STATUS} compact />
                  ),
              },
              {
                title: 'Date required',
                key: 'dueDate',
                width: 130,
                render: (_: unknown, job) => (
                  <span className={`ui-tabular${isJobOverdue(job) ? ' tone-text--red' : ''}`}>
                    {formatShop(job.dueDate, 'MMM D, YYYY')}
                  </span>
                ),
              },
            ]}
          />
        )}
      </SectionCard>
    </Page>
  );
}

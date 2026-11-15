import { useState } from 'react';
import { Button, Segmented } from 'antd';
import { Link } from 'react-router-dom';
import StatusPill from '../../components/StatusPill';
import { PageSkeleton } from '../../components/ui';
import { exportCsv } from '../../utils/csvExport';
import { fmtDay, fmtNum, fmtSignedPct, onTimePct, pctChange, ptsChange, symmetricBound } from './format';
import { Card, ChartEmpty, Grid, Heatmap, Kpi, Kpis, LineTrend, PALETTE, SortedBars } from './kit';
import { CardLoading, FLAG, finishOf, slackText } from './cards';
import { useCapacity, useCompletion, useOverview, usePeriods, useTrend, useUtilization } from './sources';
import PerformanceVsTargetCard from './PerformanceVsTargetCard';

const BASIS_LABEL: Record<string, string> = {
  WORKER: 'worker history',
  MACHINE: 'machine history',
  TYPE: 'operation type history',
  DEFAULT: 'target time',
  OUTSOURCED: 'supplier date',
};

export default function AnalyticsProductionPage() {
  const { period, previous } = usePeriods();
  const overview = useOverview(period).data;
  const prev = useOverview(previous).data;
  const trend = useTrend(period).data;
  const util = useUtilization(period).data;
  const completion = useCompletion().data;
  const capacity = useCapacity().data;
  const [output, setOutput] = useState<'jobs' | 'ops'>('jobs');
  const [allJobs, setAllJobs] = useState(false);

  if (!overview) return <PageSkeleton variant="cards" />;

  const onTime = onTimePct(overview.jobs.onTime, overview.jobs.late);
  const onTimePrev = prev ? onTimePct(prev.jobs.onTime, prev.jobs.late) : null;

  const weeks = (trend?.weeks ?? []).map((w) => ({
    label: fmtDay(w.weekStart),
    tipLabel: `Week of ${fmtDay(w.weekStart)}`,
    jobs: w.jobsFinished,
    ops: w.operationCount,
    variance: w.averageVariancePct,
  }));
  const outputTotal = weeks.reduce((s, w) => s + (output === 'jobs' ? w.jobs : w.ops), 0);
  const varianceWeeks = weeks.filter((w) => w.variance != null);
  const lastVariance = varianceWeeks[varianceWeeks.length - 1]?.variance ?? null;
  const bound = symmetricBound(varianceWeeks.map((w) => w.variance));

  const machines = (util?.machines ?? []).filter((m) => (m.busyHours ?? 0) > 0);
  const idle = (util?.machines ?? []).filter((m) => !((m.busyHours ?? 0) > 0));
  const busiest = [...machines].sort((a, b) => (b.utilizationPct ?? 0) - (a.utilizationPct ?? 0))[0];

  const jobs = completion?.jobs ?? [];
  const late = jobs.filter((j) => j.flag === 'RED' || j.flag === 'AMBER').length;
  const shownJobs = allJobs ? jobs : jobs.slice(0, 8);

  const types = (capacity?.machineTypes ?? []).filter((t) => (t.availableHours ?? 0) > 0);
  const over80 = types.filter((t) => t.above80Pct).length;

  return (
    <div>
      <Kpis>
        <Kpi
          label="Jobs finished"
          value={fmtNum(overview.jobs.completed)}
          delta={prev ? pctChange(overview.jobs.completed, prev.jobs.completed) : undefined}
        />
        <Kpi
          label="On time"
          value={onTime == null ? '—' : fmtNum(onTime)}
          unit={onTime == null ? undefined : '%'}
          delta={ptsChange(onTime, onTimePrev)}
          deltaUnit="pts"
        />
        <Kpi
          label="Avg days late"
          value={fmtNum(overview.jobs.averageDaysLate, 1)}
          delta={prev ? pctChange(overview.jobs.averageDaysLate, prev.jobs.averageDaysLate) : undefined}
          better="down"
          info="Average over late deliveries only."
        />
        <Kpi
          label="Time vs target"
          value={fmtSignedPct(overview.efficiency.averageVariancePct)}
          delta={ptsChange(overview.efficiency.averageVariancePct, prev?.efficiency.averageVariancePct ?? null)}
          deltaUnit="pts"
          better="down"
          info={`Average of (hours worked − target) ÷ target over ${overview.efficiency.completedOperationsWithVariance} finished operations. Below 0 is faster than target.${
            overview.excludedOperationCount ? ` ${overview.excludedOperationCount} without a target are not counted.` : ''
          }`}
        />
        <Kpi
          label="Machines down"
          value={fmtNum(overview.downtime.openCount)}
          valueTone={overview.downtime.openCount ? 'red' : undefined}
          sub="now"
        />
      </Kpis>

      <Grid>
        <Card
          title="Weekly output"
          span={6}
          takeaway={
            trend ? (
              <>
                <strong>{outputTotal}</strong> {output === 'jobs' ? 'jobs' : 'operations'} finished
              </>
            ) : undefined
          }
          controls={
            <Segmented
              size="small"
              value={output}
              onChange={(v) => setOutput(v as 'jobs' | 'ops')}
              options={[
                { label: 'Jobs', value: 'jobs' },
                { label: 'Operations', value: 'ops' },
              ]}
            />
          }
          onExport={
            trend
              ? () =>
                  exportCsv('weekly-output', trend.weeks, [
                    { key: 'w', header: 'Week of', value: (r) => r.weekStart },
                    { key: 'j', header: 'Jobs finished', value: (r) => r.jobsFinished },
                    { key: 'o', header: 'Operations finished', value: (r) => r.operationCount },
                    { key: 'v', header: 'Time vs target %', value: (r) => r.averageVariancePct ?? '' },
                  ])
              : undefined
          }
        >
          {!trend ? (
            <CardLoading />
          ) : weeks.length < 2 ? (
            <ChartEmpty title="Not enough weeks yet" text="Pick a longer period." height={220} />
          ) : (
            <LineTrend
              rows={weeks}
              height={220}
              series={[
                {
                  key: output,
                  label: output === 'jobs' ? 'Jobs finished' : 'Operations finished',
                  color: PALETTE.primary,
                },
              ]}
            />
          )}
        </Card>

        <Card
          title="Time vs target"
          span={6}
          takeaway={
            lastVariance == null ? (
              trend ? 'No finished operations with a target yet' : undefined
            ) : (
              <>
                Last week <strong>{fmtSignedPct(lastVariance)}</strong> {lastVariance > 0 ? 'over' : 'vs'} target
              </>
            )
          }
          takeawayTone={lastVariance == null ? undefined : lastVariance > 0 ? 'bad' : 'good'}
          info="Average difference between hours worked and target hours of operations finished each week. 0 = on target; below 0 = faster."
        >
          {!trend ? (
            <CardLoading />
          ) : varianceWeeks.length < 2 ? (
            <ChartEmpty title="Not enough weeks yet" height={220} />
          ) : (
            <LineTrend
              rows={weeks}
              height={220}
              domain={[-bound, bound]}
              ticks={[-bound, -bound / 2, 0, bound / 2, bound]}
              zeroLine
              yFormat={(v) => `${v > 0 ? '+' : ''}${v}%`}
              series={[{ key: 'variance', label: 'Time vs target', color: PALETTE.third }]}
              tipFormat={(_, v) => fmtSignedPct(v, 1)}
            />
          )}
        </Card>

        <Card
          title="Machine use by week"
          span={12}
          takeaway={
            busiest && (busiest.utilizationPct ?? 0) > 0 ? (
              <>
                Busiest: <strong>{busiest.machineUnitLabel}</strong> at {fmtNum(busiest.utilizationPct)}%
              </>
            ) : util ? (
              'No machine running time in this period'
            ) : undefined
          }
          info="Hours an operation was running on the unit inside working hours ÷ the shop's available hours that week."
          foot={idle.length && machines.length ? `Idle all period: ${idle.map((m) => m.machineUnitLabel).join(', ')}` : undefined}
          onExport={
            util
              ? () =>
                  exportCsv(
                    'machine-use-by-week',
                    util.machines.flatMap((m) =>
                      m.cells.map((c) => ({ unit: m.machineUnitLabel, type: m.machineTypeCode, ...c }))
                    ),
                    [
                      { key: 'u', header: 'Unit', value: (r) => r.unit },
                      { key: 't', header: 'Type', value: (r) => r.type ?? '' },
                      { key: 'w', header: 'Week of', value: (r) => r.weekStart },
                      { key: 'b', header: 'Busy hours', value: (r) => r.busyHours ?? '' },
                      { key: 'p', header: 'Use %', value: (r) => r.utilizationPct ?? '' },
                    ]
                  )
              : undefined
          }
        >
          {!util ? (
            <CardLoading rows={6} />
          ) : !machines.length ? (
            <ChartEmpty title="No machine running time in this period" />
          ) : (
            <Heatmap
              columns={util.weeks.map((w) => ({ key: w.weekStart, label: fmtDay(w.weekStart) }))}
              rows={machines.map((m) => ({
                key: m.machineUnitId,
                label: m.machineUnitLabel,
                cells: m.cells.map((c) => c.utilizationPct),
                total: m.utilizationPct,
                tips: m.cells.map(
                  (c, i) =>
                    `${m.machineUnitLabel}, week of ${fmtDay(c.weekStart)}: ${fmtNum(c.busyHours, 1)} of ${fmtNum(
                      util.weeks[i]?.availableHours,
                      0
                    )} h`
                ),
              }))}
            />
          )}
        </Card>

        <PerformanceVsTargetCard span={7} />

        <Card
          title="Workload, next 4 weeks"
          span={5}
          takeaway={
            capacity ? (
              over80 ? (
                <>
                  <strong>{over80}</strong> machine type{over80 === 1 ? '' : 's'} above 80% booked
                </>
              ) : (
                'All machine types below 80% booked'
              )
            ) : undefined
          }
          takeawayTone={over80 ? 'bad' : undefined}
          info={`Scheduled operation hours ÷ available hours of all active units of the type, ${
            capacity ? `${fmtDay(capacity.horizon.from)} – ${fmtDay(capacity.horizon.to)}` : 'next 4 weeks'
          }. The dashed line is 80%.${capacity?.thinSample ? ` ${capacity.thinSampleNote ?? ''}` : ''}`}
        >
          {!capacity ? (
            <CardLoading />
          ) : !types.length ? (
            <ChartEmpty title="Nothing scheduled yet" />
          ) : (
            <SortedBars
              max={100}
              reference={80}
              rows={types.map((t) => ({
                key: t.machineTypeId,
                label: t.machineTypeName || t.machineTypeCode,
                value: t.projectedLoadPct ?? 0,
                display: `${fmtNum(t.projectedLoadPct)}%`,
                sub: `${fmtNum(t.scheduledLoadHours)} h`,
                color: t.above80Pct ? PALETTE.warn : PALETTE.primary,
              }))}
            />
          )}
        </Card>
        <Card
          title="Open jobs: predicted finish"
          span={12}
          chip="Now"
          takeaway={
            completion ? (
              late ? (
                <>
                  <strong>{late}</strong> of {jobs.length} may finish after the date required
                </>
              ) : jobs.length ? (
                `All ${jobs.length} on track`
              ) : (
                'No open jobs'
              )
            ) : undefined
          }
          takeawayTone={late ? 'bad' : undefined}
          info={
            completion ? (
              <div style={{ maxWidth: 340, display: 'grid', gap: 6 }}>
                <div>
                  Remaining target hours × the usual actual ÷ target ratio, laid on working hours. The ratio comes
                  from the assigned worker, else the machine, else the operation type (each needs{' '}
                  {completion.minSamples} finished operations). The later of predicted and scheduled finish is used.
                </div>
                <table className="ana-table">
                  <thead>
                    <tr>
                      <th>Operation type</th>
                      <th className="num">Ratio</th>
                      <th className="num">Samples</th>
                    </tr>
                  </thead>
                  <tbody>
                    {completion.operationTypes.slice(0, 8).map((t) => (
                      <tr key={t.operationTypeId}>
                        <td>{t.operationTypeName}</td>
                        <td className="num">{t.enoughHistory ? `× ${fmtNum(t.ratio, 2)}` : '—'}</td>
                        <td className="num">{t.samples}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : undefined
          }
          onExport={
            completion
              ? () =>
                  exportCsv('open-jobs-predicted-finish', jobs, [
                    { key: 'j', header: 'Job', value: (r) => r.jobNumber },
                    { key: 'c', header: 'Client', value: (r) => r.clientName ?? '' },
                    { key: 'd', header: 'Date required', value: (r) => r.dueDate ?? '' },
                    { key: 'p', header: 'Predicted finish', value: (r) => r.predictedFinish ?? '' },
                    { key: 's', header: 'Scheduled finish', value: (r) => r.scheduledFinish ?? '' },
                    { key: 'k', header: 'Slack days', value: (r) => r.slackDays ?? '' },
                    { key: 'f', header: 'Status', value: (r) => (r.flag ? FLAG[r.flag].label : '') },
                  ])
              : undefined
          }
        >
          {!completion ? (
            <CardLoading rows={6} />
          ) : !jobs.length ? (
            <ChartEmpty title="No released jobs" />
          ) : (
            <div className="ana-table__wrap">
              <table className="ana-table">
                <thead>
                  <tr>
                    <th>Job</th>
                    <th>Required</th>
                    <th>Finish</th>
                    <th className="num">Slack</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {shownJobs.map((j) => (
                    <tr key={j.jobOrderId}>
                      <td className="strong">
                        <Link to={`/job-orders/${j.jobOrderId}`}>{j.jobNumber}</Link>
                        <div className="ana-muted" style={{ fontWeight: 400, fontSize: 12 }}>
                          {j.clientName ?? '—'}
                        </div>
                      </td>
                      <td>{fmtDay(j.dueDate)}</td>
                      <td
                        title={
                          j.basis === 'ESTIMATE'
                            ? `Predicted from ${j.ratioBases.map((b) => BASIS_LABEL[b] ?? b).join(', ') || 'history'}`
                            : 'Scheduled finish'
                        }
                      >
                        {fmtDay(finishOf(j))}
                        <span className="ana-muted" style={{ fontSize: 11.5 }}>
                          {j.basis === 'ESTIMATE' ? ' · predicted' : ' · scheduled'}
                        </span>
                      </td>
                      <td className="num">{slackText(j.slackDays)}</td>
                      <td style={{ textAlign: 'right' }}>
                        {j.flag ? (
                          <StatusPill tone={FLAG[j.flag].tone} compact>
                            {FLAG[j.flag].label}
                          </StatusPill>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {jobs.length > 8 ? (
                <Button type="link" size="small" onClick={() => setAllJobs((v) => !v)} style={{ paddingLeft: 8 }}>
                  {allJobs ? 'Show fewer' : `Show all ${jobs.length}`}
                </Button>
              ) : null}
            </div>
          )}
        </Card>
      </Grid>
    </div>
  );
}

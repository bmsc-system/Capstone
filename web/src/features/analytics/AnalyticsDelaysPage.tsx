import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Segmented } from 'antd';
import { PageSkeleton } from '../../components/ui';
import type { AnalyticsDelayCauseDetail, AnalyticsDelayCauseRow, AnalyticsLateJobCause } from '../../types';
import { exportCsv } from '../../utils/csvExport';
import DelayCauseDrawer from './DelayCauseDrawer';
import { fmtDay, fmtNum, pctChange } from './format';
import { Card, ChartEmpty, Grid, Kpi, Kpis, LineTrend, PALETTE, ParetoChart, SortedBars } from './kit';
import { useDelays, useIsAdmin, usePeriods } from './sources';

const REASON_LABEL: Record<string, string> = {
  END_OF_SHIFT: 'End of shift',
  BREAK: 'Break',
  MACHINE_DOWN: 'Machine breakdown',
  WAITING_MATERIAL: 'Waiting for material',
  WAITING_PRIOR_OPERATION: 'Waiting on prior operation',
  EMERGENCY: 'Emergency (had to leave)',
  WAITING_TOOL: 'Waiting for a tool',
  WAITING_INSTRUCTIONS: 'Waiting for drawing or instructions',
  POWER_INTERRUPTION: 'Power interruption',
  MOVED_TO_OTHER_JOB: 'Moved to another job',
  WORKER_AWAY: 'Worker away',
  OTHER: 'Other',
  MACHINE_DOWNTIME: 'Machine downtime',
};

function reasonLabel(reason: string) {
  return (
    REASON_LABEL[reason] ??
    reason
      .split('_')
      .map((w) => w.charAt(0) + w.slice(1).toLowerCase())
      .join(' ')
  );
}

function causeLabel(c: AnalyticsLateJobCause) {
  return c.cause === c.label ? reasonLabel(c.cause) : c.label;
}

type CauseRow = {
  label: string;
  hours: number;
  shareOfTotalPct: number;
  details: AnalyticsDelayCauseDetail[];
  moreDetails: number;
  detailBy: string | null;
  action: string | null;
  incidentCount: number;
};

const SOURCE_WORD: Record<string, string> = {
  machine: 'machine',
  supplier: 'supplier',
  job: 'job',
  'outside shop': 'outside shop',
  operation: 'operation',
};

/** Under the Pareto chart: each cause with its biggest source, one line each.
 *  The cards below break breakdowns and material delays down further. */
function CauseDetails({
  causes,
  vitalFew,
  onOpen,
}: {
  causes: CauseRow[];
  vitalFew: number;
  onOpen: (index: number) => void;
}) {
  return (
    <div className="ana-causes">
      <div className="ana-causes__title">Biggest source of each cause · click a cause to see its records</div>
      <ol className="ana-causes__list">
        {causes.map((c, i) => {
          const [top, ...rest] = c.details;
          const others = rest.length + (c.moreDetails || 0);
          const word = c.detailBy ? SOURCE_WORD[c.detailBy] ?? 'source' : 'source';
          return (
            <li key={c.label} className={`ana-causes__row${i < vitalFew ? ' is-vital' : ''}`}>
              <span className="ana-causes__rank">{i + 1}</span>
              <span className="ana-causes__cause">
                <button type="button" className="ana-causes__name ana-causes__open" onClick={() => onOpen(i)}>
                  {c.label}
                </button>
                <span className="ana-causes__hours">
                  {fmtNum(c.hours, 1)} h · {fmtNum(c.shareOfTotalPct)}%
                </span>
              </span>
              <span className="ana-causes__from">
                {top ? (
                  <>
                    <span
                      className="ana-chip ana-causes__chip"
                      title={top.jobs.length ? `${top.jobs.join(', ')}${top.moreJobs ? ` and ${top.moreJobs} more` : ''}` : undefined}
                    >
                      <strong>{top.label}</strong>
                      {top.hours != null ? <>&nbsp;· {fmtNum(top.hours, 1)} h</> : null}
                    </span>
                    {others ? (
                      <span
                        className="ana-muted"
                        title={rest.map((d) => `${d.label}${d.hours != null ? ` · ${fmtNum(d.hours, 1)} h` : ''}`).join('\n') || undefined}
                      >
                        and {others} other {word}
                        {others === 1 ? '' : 's'}
                      </span>
                    ) : null}
                  </>
                ) : (
                  <span className="ana-muted">No detail recorded</span>
                )}
              </span>
              {c.action ? <span className="ana-causes__action">{c.action}</span> : null}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

export default function AnalyticsDelaysPage() {
  const { period, previous } = usePeriods();
  const [rev, setRev] = useState(0);
  const data = useDelays(period, rev).data;
  const prev = useDelays(previous).data;
  const isAdmin = useIsAdmin();
  const [view, setView] = useState<'hours' | 'jobs'>('hours');
  const [openCause, setOpenCause] = useState<string | null>(null);

  if (!data) return <PageSkeleton variant="cards" />;

  const threshold = data.paretoThresholdPct ?? 80;
  const causes = data.causes
    .filter((c) => (c.hours ?? 0) > 0)
    .map((c) => ({
      label: c.label || reasonLabel(c.cause),
      hours: c.hours ?? 0,
      cumulativePct: c.cumulativePct ?? 0,
      shareOfTotalPct: c.shareOfTotalPct ?? 0,
      count: c.occurrenceCount,
      details: c.details ?? [],
      moreDetails: c.moreDetails ?? 0,
      detailBy: c.detailBy ?? null,
      action: c.action ?? null,
      incidentCount: c.incidentCount ?? 0,
      cause: c.cause,
    }));
  const rawCauses = data.causes.filter((c) => (c.hours ?? 0) > 0);
  const opened: AnalyticsDelayCauseRow | null = rawCauses.find((c) => c.cause === openCause) ?? null;
  const lateCauses = (data.lateJobCauses ?? []).map((c) => ({
    label: c.label || reasonLabel(c.cause),
    hours: c.jobs,
    cumulativePct: c.cumulativePct,
    shareOfTotalPct: c.shareOfTotalPct,
    jobNumbers: c.jobNumbers,
  }));
  const lateVital = Math.max(
    1,
    Math.min(lateCauses.findIndex((c) => c.cumulativePct >= threshold) + 1 || lateCauses.length, lateCauses.length)
  );
  const other = data.otherShare;
  const trend = data.weeklyTrend;
  const trendColors = [PALETTE.primary, PALETTE.warn, PALETTE.good];
  const trendRows = (trend?.weeks ?? []).map((w, i) => {
    const row: Record<string, number | string | null> = { label: fmtDay(w), tipLabel: `Week of ${fmtDay(w)}` };
    trend?.series.forEach((sr, k) => {
      row[`s${k}`] = sr.hours[i] ?? 0;
    });
    return row;
  });
  const vitalFew = Math.min(data.vitalFewCount ?? 1, causes.length);
  const top = causes[0];
  const downtime = data.machineDowntime.filter((d) => (d.totalDowntimeHours ?? 0) > 0 || d.openCount > 0);
  const downNow = data.machineDowntime.filter((d) => d.openCount > 0).length;
  const material = data.materialDelays ?? [];
  const lateJobs = data.lateJobs ?? [];
  const excluded = data.excludedNonWorkingPauses;

  return (
    <div>
      <Kpis>
        <Kpi
          label="Delay hours"
          value={fmtNum(data.totalDelayHours, 1)}
          delta={prev ? pctChange(data.totalDelayHours, prev.totalDelayHours) : undefined}
          better="down"
        />
        <Kpi
          label="Top cause"
          value={
            <span className="ana-kpi__text" title={top?.label}>
              {top?.label ?? '—'}
            </span>
          }
          sub={top ? `${fmtNum(top.shareOfTotalPct)}% of delay hours` : 'No delays'}
        />
        <Kpi
          label="Late jobs"
          value={fmtNum(lateJobs.length)}
          delta={prev ? pctChange(lateJobs.length, prev.lateJobs?.length ?? 0) : undefined}
          better="down"
          info="Jobs delivered after the date required in this period."
        />
        <Kpi
          label="Machines down"
          value={fmtNum(downNow)}
          valueTone={downNow ? 'red' : undefined}
          sub="now"
        />
      </Kpis>

      <Grid>
        <Card
          title="Delay causes (Pareto)"
          span={12}
          controls={
            <Segmented
              size="small"
              value={view}
              onChange={(v) => setView(v as 'hours' | 'jobs')}
              options={[
                { label: 'Hours lost', value: 'hours' },
                { label: 'Late jobs', value: 'jobs' },
              ]}
            />
          }
          takeaway={
            view === 'jobs' ? (
              lateCauses[0] ? (
                <>
                  <strong>{lateCauses[0].label}</strong> cost the most hours on {lateCauses[0].hours} of{' '}
                  {lateJobs.length} late job{lateJobs.length === 1 ? '' : 's'}
                </>
              ) : undefined
            ) : !top ? undefined : vitalFew === 1 ? (
              <>
                <strong>{top.label}</strong> alone is {fmtNum(top.shareOfTotalPct)}% of delay hours
              </>
            ) : (
              <>
                <strong>{vitalFew} causes</strong> make up {fmtNum(causes[vitalFew - 1]?.cumulativePct)}% of delay hours
              </>
            )
          }
          info={
            <div style={{ maxWidth: 340, display: 'grid', gap: 6 }}>
              <div>
                Working hours lost to late materials, pause reasons, machine breakdowns and outside work, largest first.
                The line is the running share; causes up to the {threshold}% line are the few to fix first.
              </div>
              <div>
                Breaks and end-of-shift pauses are normal non-working time, not delays
                {excluded?.totalHours ? ` (${fmtNum(excluded.totalHours, 1)} h left out)` : ''}.
              </div>
              <div>
                A "Machine down" pause that overlaps a breakdown on the same machine is counted once
                {data.breakdownOverlapHours ? ` (${fmtNum(data.breakdownOverlapHours, 1)} h overlap removed)` : ''}.
              </div>
            </div>
          }
          onExport={() =>
            exportCsv('delay-causes-pareto', causes, [
              { key: 'c', header: 'Cause', value: (r) => r.label },
              { key: 'h', header: 'Hours', value: (r) => r.hours },
              { key: 'n', header: 'Times', value: (r) => r.count },
              { key: 's', header: 'Share %', value: (r) => r.shareOfTotalPct },
              { key: 'k', header: 'Cumulative %', value: (r) => r.cumulativePct },
              {
                key: 'd',
                header: 'Where it came from',
                value: (r) => r.details.map((d) => `${d.label} ${fmtNum(d.hours, 1)}h`).join('; '),
              },
            ])
          }
        >
          {view === 'jobs' ? (
            lateCauses.length ? (
              <>
                <ParetoChart
                  rows={lateCauses}
                  threshold={threshold}
                  vitalFew={lateVital}
                  height={300}
                  valueLabel="Late jobs"
                  valueFormat={(v) => fmtNum(v)}
                  integerAxis
                />
                <div className="ana-causes">
                  <div className="ana-causes__title">Each late job counted once, under the cause that cost it the most hours</div>
                  <ol className="ana-causes__list">
                    {lateCauses.map((c, i) => (
                      <li key={c.label} className={`ana-causes__row${i < lateVital ? ' is-vital' : ''}`}>
                        <span className="ana-causes__rank">{i + 1}</span>
                        <span className="ana-causes__cause">
                          <span className="ana-causes__name">{c.label}</span>
                          <span className="ana-causes__hours">
                            {c.hours} job{c.hours === 1 ? '' : 's'} · {fmtNum(c.shareOfTotalPct)}%
                          </span>
                        </span>
                        <span className="ana-causes__from ana-muted">
                          {c.jobNumbers.slice(0, 4).join(', ')}
                          {c.jobNumbers.length > 4 ? ` and ${c.jobNumbers.length - 4} more` : ''}
                        </span>
                      </li>
                    ))}
                  </ol>
                </div>
              </>
            ) : (
              <ChartEmpty title="No jobs delivered late in this period" height={220} />
            )
          ) : causes.length ? (
            <>
              <ParetoChart
                rows={causes}
                threshold={threshold}
                vitalFew={vitalFew}
                height={300}
                onSelect={(i) => setOpenCause(causes[i]?.cause ?? null)}
              />
              {other && other.hours > 0 ? (
                <div className={`ana-note${other.pct >= 10 ? ' ana-note--warn' : ''}`}>
                  "Other" is {fmtNum(other.pct)}% of lost hours
                  {other.withoutNote ? ` · ${other.withoutNote} pause${other.withoutNote === 1 ? '' : 's'} without a note` : ''}
                  {isAdmin ? ' · open it to give each pause its real reason' : ''}
                </div>
              ) : null}
              <CauseDetails causes={causes} vitalFew={vitalFew} onOpen={(i) => setOpenCause(causes[i]?.cause ?? null)} />
            </>
          ) : (
            <ChartEmpty title="No delay hours in this period" height={220} />
          )}
        </Card>

        <Card
          title="Top causes by week"
          span={12}
          takeaway={
            trend?.series.length ? (
              <>
                Weekly hours lost to the <strong>{trend.series.length}</strong> biggest cause{trend.series.length === 1 ? '' : 's'}
              </>
            ) : undefined
          }
          info="Hours lost each week (Monday to Sunday) to the top causes of the period, to see whether a fix is working."
        >
          {trend && trend.series.length && trendRows.length > 1 ? (
            <LineTrend
              rows={trendRows}
              series={trend.series.map((sr, k) => ({ key: `s${k}`, label: sr.label, color: trendColors[k] }))}
              height={220}
              yFormat={(v) => fmtNum(v)}
              tipFormat={(_, v) => `${fmtNum(v, 1)} h`}
            />
          ) : (
            <ChartEmpty title="Choose a period of two weeks or more to see a trend" height={160} />
          )}
        </Card>

        <Card
          title="Breakdowns by machine"
          span={6}
          takeaway={
            downtime[0] ? (
              <>
                <strong>{downtime[0].machineUnitLabel}</strong> · {fmtNum(downtime[0].totalDowntimeHours, 1)} h
              </>
            ) : (
              'No breakdowns'
            )
          }
          info="Machine breakdown hours, in shop working hours. Red bars are still down."
        >
          {downtime.length ? (
            <SortedBars
              rows={downtime.map((d) => ({
                key: d.machineUnitId,
                label: d.machineUnitLabel || d.machineUnitId.slice(0, 8),
                value: d.totalDowntimeHours ?? 0,
                display: `${fmtNum(d.totalDowntimeHours, 1)} h`,
                sub: d.openCount ? 'down now' : `${d.occurrenceCount}×`,
                color: d.openCount ? PALETTE.bad : PALETTE.primary,
              }))}
              limit={8}
            />
          ) : (
            <ChartEmpty title="No breakdowns in this period" />
          )}
        </Card>

        <Card
          title="Material delays"
          span={6}
          takeaway={
            material.length ? (
              <>
                <strong>{material.length}</strong> job{material.length === 1 ? '' : 's'} started late for material
              </>
            ) : (
              'None'
            )
          }
          info="Jobs moved later because a supplier delivered late or materials were not ordered in time. Working hours from the planned start before the move to the first actual start (or now). Moves made only because a start date passed are not counted."
        >
          {material.length ? (
            <div className="ana-list">
              {material.slice(0, 6).map((m) => (
                <Link key={m.jobOrderId} to={`/job-orders/${m.jobOrderId}`} className="ana-list__row">
                  <div className="ana-list__main">
                    <div className="ana-list__title">{m.jobNumber}</div>
                    <div className="ana-list__sub">
                      {m.causeLabel}
                      {m.supplierNames ? ` · ${m.supplierNames}` : ''}
                    </div>
                  </div>
                  <div className="ana-list__end">
                    <strong>{fmtNum(m.hours, 1)} h</strong>
                  </div>
                </Link>
              ))}
            </div>
          ) : (
            <ChartEmpty title="No material delays in this period" />
          )}
        </Card>

        <Card
          title="Late jobs"
          span={12}
          takeaway={
            lateJobs.length ? (
              <>
                <strong>{lateJobs.length}</strong> delivered late · up to {Math.max(...lateJobs.map((j) => j.daysLate))}{' '}
                days
              </>
            ) : (
              'No late deliveries'
            )
          }
          takeawayTone={lateJobs.length ? 'bad' : undefined}
          info="Jobs delivered after the date required, with the delay hours recorded on their operations."
          onExport={
            lateJobs.length
              ? () =>
                  exportCsv('late-jobs', lateJobs, [
                    { key: 'j', header: 'Job', value: (r) => r.jobNumber },
                    { key: 'c', header: 'Client', value: (r) => r.clientName ?? '' },
                    { key: 'd', header: 'Date required', value: (r) => r.dueDate },
                    { key: 'v', header: 'Delivered', value: (r) => r.deliveredDate },
                    { key: 'l', header: 'Days late', value: (r) => r.daysLate },
                    {
                      key: 'x',
                      header: 'Causes',
                      value: (r) => r.causes.map((c) => `${causeLabel(c)} ${c.hours.toFixed(1)}h`).join('; '),
                    },
                  ])
              : undefined
          }
        >
          {lateJobs.length ? (
            <div className="ana-table__wrap">
              <table className="ana-table">
                <thead>
                  <tr>
                    <th>Job</th>
                    <th>Client</th>
                    <th>Required</th>
                    <th>Delivered</th>
                    <th className="num">Days late</th>
                    <th>Causes</th>
                  </tr>
                </thead>
                <tbody>
                  {[...lateJobs]
                    .sort((a, b) => b.daysLate - a.daysLate)
                    .map((j) => (
                      <tr key={j.jobOrderId}>
                        <td className="strong">
                          <Link to={`/job-orders/${j.jobOrderId}`}>{j.jobNumber}</Link>
                        </td>
                        <td>{j.clientName ?? '—'}</td>
                        <td>{fmtDay(j.dueDate)}</td>
                        <td>{fmtDay(j.deliveredDate)}</td>
                        <td className="num strong ana-bad">{j.daysLate}</td>
                        <td>
                          <span style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                            {j.causes.length ? (
                              j.causes.slice(0, 3).map((c) => (
                                <span key={c.cause} className="ana-chip" title={c.detail ?? undefined}>
                                  {causeLabel(c)} · {fmtNum(c.hours, 1)} h
                                </span>
                              ))
                            ) : (
                              <span className="ana-muted">No delay recorded</span>
                            )}
                          </span>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          ) : (
            <ChartEmpty title="No jobs delivered late in this period" height={100} />
          )}
        </Card>
      </Grid>
      <DelayCauseDrawer
        cause={opened}
        onClose={() => setOpenCause(null)}
        isAdmin={isAdmin}
        onRelabeled={() => setRev((r) => r + 1)}
      />
    </div>
  );
}

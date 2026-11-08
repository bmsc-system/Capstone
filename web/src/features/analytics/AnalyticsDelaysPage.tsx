import { Link } from 'react-router-dom';
import { PageSkeleton } from '../../components/ui';
import type { AnalyticsDelayCauseDetail, AnalyticsLateJobCause } from '../../types';
import { exportCsv } from '../../utils/csvExport';
import { fmtDay, fmtNum, pctChange } from './format';
import { Card, ChartEmpty, Grid, Kpi, Kpis, PALETTE, ParetoChart, SortedBars } from './kit';
import { useDelays, usePeriods } from './sources';

const REASON_LABEL: Record<string, string> = {
  END_OF_SHIFT: 'End of shift',
  BREAK: 'Break',
  MACHINE_DOWN: 'Machine breakdown',
  WAITING_MATERIAL: 'Waiting for material',
  WAITING_PRIOR_OPERATION: 'Waiting on prior operation',
  OTHER: 'Other',
  MACHINE_DOWNTIME: 'Machine downtime',
  REWORK: 'Rework',
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

const DETAIL_BY: Record<string, string> = {
  machine: 'By machine',
  supplier: 'By supplier',
  job: 'By job',
  'outside shop': 'By outside shop',
  operation: 'By operation',
};

type CauseRow = {
  label: string;
  hours: number;
  shareOfTotalPct: number;
  details: AnalyticsDelayCauseDetail[];
  moreDetails: number;
  detailBy: string | null;
};

/** Under the Pareto chart: which machine, supplier, outside shop or operation
 *  each cause's hours came from, so the top causes can be acted on. */
function CauseDetails({ causes, vitalFew }: { causes: CauseRow[]; vitalFew: number }) {
  return (
    <div className="ana-causes">
      <div className="ana-causes__title">Where the hours came from</div>
      <ol className="ana-causes__list">
        {causes.map((c, i) => (
          <li key={c.label} className={`ana-causes__row${i < vitalFew ? ' is-vital' : ''}`}>
            <span className="ana-causes__rank">{i + 1}</span>
            <span className="ana-causes__cause">
              <span className="ana-causes__name">{c.label}</span>
              <span className="ana-causes__hours">
                {fmtNum(c.hours, 1)} h · {fmtNum(c.shareOfTotalPct)}%
              </span>
            </span>
            <span className="ana-causes__from">
              {c.detailBy ? <span className="ana-causes__by">{DETAIL_BY[c.detailBy] ?? ''}</span> : null}
              {c.details.length ? (
                c.details.map((d) => {
                  const jobs = d.jobs.length
                    ? `${d.jobs.join(', ')}${d.moreJobs ? ` and ${d.moreJobs} more` : ''}`
                    : null;
                  return (
                    <span key={d.label} className="ana-chip ana-causes__chip" title={jobs ?? undefined}>
                      <strong>{d.label}</strong>&nbsp;· {fmtNum(d.hours, 1)} h
                      {d.count > 1 ? ` · ${d.count}×` : ''}
                      {d.note ? ` · ${d.note}` : ''}
                      {jobs && c.detailBy !== 'job' ? <span className="ana-causes__jobs">&nbsp;({jobs})</span> : null}
                    </span>
                  );
                })
              ) : (
                <span className="ana-muted">No detail recorded</span>
              )}
              {c.moreDetails ? <span className="ana-muted">+{c.moreDetails} more</span> : null}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export default function AnalyticsDelaysPage() {
  const { period, previous } = usePeriods();
  const data = useDelays(period).data;
  const prev = useDelays(previous).data;

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
    }));
  const vitalFew = Math.min(data.vitalFewCount ?? 1, causes.length);
  const top = causes[0];
  const downtime = data.machineDowntime.filter((d) => (d.totalDowntimeHours ?? 0) > 0 || d.openCount > 0);
  const downNow = data.machineDowntime.filter((d) => d.openCount > 0).length;
  const rework = (data.reworkByReason ?? []).filter((r) => r.count > 0);
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
          value={<span style={{ fontSize: 17 }}>{top?.label ?? '—'}</span>}
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
          takeaway={
            !top ? undefined : vitalFew === 1 ? (
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
                Working hours lost to late materials, pause reasons, machine breakdowns and redo work, largest first.
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
          {causes.length ? (
            <>
              <ParetoChart rows={causes} threshold={threshold} vitalFew={vitalFew} height={300} />
              <CauseDetails causes={causes} vitalFew={vitalFew} />
            </>
          ) : (
            <ChartEmpty title="No delay hours in this period" height={220} />
          )}
        </Card>

        <Card
          title="Breakdowns by machine"
          span={4}
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
          title="Redo by reason"
          span={4}
          takeaway={
            rework[0] ? (
              <>
                <strong>{rework[0].label}</strong> · {rework[0].count} redo{rework[0].count === 1 ? '' : 's'}
              </>
            ) : (
              'No redo work'
            )
          }
          info="Redo operations finished in this period, by the reason recorded when the work was sent back, with the hours spent redoing it."
        >
          {rework.length ? (
            <SortedBars
              rows={rework.map((r) => ({
                key: r.reason,
                label: r.label,
                value: r.hours ?? 0,
                display: `${fmtNum(r.hours, 1)} h`,
                sub: `${r.count}×`,
              }))}
              limit={8}
            />
          ) : (
            <ChartEmpty title="No redo work in this period" />
          )}
        </Card>

        <Card
          title="Material delays"
          span={4}
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
    </div>
  );
}

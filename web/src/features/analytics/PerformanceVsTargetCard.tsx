import { useState } from 'react';
import { Button, Segmented } from 'antd';
import { exportCsv } from '../../utils/csvExport';
import { fmtNum, fmtSignedPct } from './format';
import { Card, ChartEmpty, DivergingBars, type Span } from './kit';
import { CardLoading } from './cards';
import { useByMachine, useByOperationType, useByWorker, usePeriods } from './sources';

type View = 'worker' | 'type' | 'machine';

type PerfRow = {
  key: string;
  label: string;
  ops: number;
  target: number | null;
  worked: number | null;
  variance: number;
  onTarget: number | null;
};

/** Time against target by worker, operation type or machine (Production tab, Admin). */
export default function PerformanceVsTargetCard({ span = 7 }: { span?: Span }) {
  const { period } = usePeriods();
  const byWorker = useByWorker(period).data;
  const byType = useByOperationType(period).data;
  const byMachine = useByMachine(period).data;
  const [view, setView] = useState<View>('worker');
  const [table, setTable] = useState(false);

  const minOps = byWorker?.minimumOperationCount ?? 3;
  const rowsFor = (v: View): PerfRow[] | null => {
    if (v === 'worker') {
      if (!byWorker) return null;
      return byWorker.workers.map((w) => ({
        key: w.workerId,
        label: w.workerName,
        ops: w.operationCount,
        target: w.totalEstimatedHours,
        worked: w.totalActualWorkedHours,
        variance: w.averageVariancePct ?? 0,
        onTarget: w.onEstimateRatePct,
      }));
    }
    if (v === 'type') {
      if (!byType) return null;
      return byType.operationTypes
        .filter((t) => t.averageVariancePct != null && t.operationCount >= byType.minimumOperationCount)
        .map((t) => ({
          key: t.operationTypeId,
          label: t.operationTypeName,
          ops: t.operationCount,
          target: t.totalEstimatedHours,
          worked: t.totalActualWorkedHours,
          variance: t.averageVariancePct ?? 0,
          onTarget: t.onEstimateRatePct,
        }));
    }
    if (!byMachine) return null;
    return byMachine.machineUnits
      .filter((u) => u.averageVariancePct != null && !u.belowMinimumSample)
      .map((u) => ({
        key: u.machineUnitId,
        label: u.machineUnitLabel,
        ops: u.operationCount,
        target: u.totalEstimatedHours,
        worked: u.totalActualWorkedHours,
        variance: u.averageVariancePct ?? 0,
        onTarget: u.onEstimateRatePct,
      }));
  };
  const rows = rowsFor(view)?.sort((a, b) => a.variance - b.variance) ?? null;
  const faster = rows?.filter((r) => r.variance < 0).length ?? 0;
  const noun = view === 'worker' ? 'workers' : view === 'type' ? 'operation types' : 'machines';

  return (
    <Card
      title="Performance vs target"
      span={span}
      foot={
        rows && rows.length > 0 && rows.length < 3
          ? `Others have fewer than ${minOps} finished operations in this period.`
          : undefined
      }
      takeaway={
        rows?.length ? (
          <>
            <strong>
              {faster} of {rows.length}
            </strong>{' '}
            {noun} faster than target
          </>
        ) : undefined
      }
      info={`Average of (hours worked − target hours) ÷ target over finished operations in the period. Needs at least ${minOps} finished operations each. This is the "past performance" input to worker recommendations.`}
      controls={
        <Segmented
          size="small"
          value={view}
          onChange={(v) => setView(v as View)}
          options={[
            { label: 'Worker', value: 'worker' },
            { label: 'Operation', value: 'type' },
            { label: 'Machine', value: 'machine' },
          ]}
        />
      }
      onExport={
        rows?.length
          ? () =>
              exportCsv(`performance-by-${view}`, rows, [
                { key: 'n', header: 'Name', value: (r) => r.label },
                { key: 'o', header: 'Finished operations', value: (r) => r.ops },
                { key: 't', header: 'Target hours', value: (r) => r.target ?? '' },
                { key: 'w', header: 'Hours worked', value: (r) => r.worked ?? '' },
                { key: 'v', header: 'Time vs target %', value: (r) => r.variance },
                { key: 'e', header: 'On target %', value: (r) => r.onTarget ?? '' },
              ])
          : undefined
      }
    >
      {!rows ? (
        <CardLoading rows={6} />
      ) : !rows.length ? (
        <ChartEmpty
          title="Not enough finished operations"
          text={`Each needs at least ${minOps} finished operations with a target in the period.`}
          height={200}
        />
      ) : (
        <>
          <DivergingBars
            rows={rows.map((r) => ({
              key: r.key,
              label: r.label,
              value: r.variance,
              title: `${r.label}: ${r.ops} operations · ${fmtNum(r.worked, 1)} h worked vs ${fmtNum(r.target, 1)} h target`,
            }))}
            format={(v) => fmtSignedPct(v)}
            leftLabel="faster"
            rightLabel="slower"
          />
          <Button type="link" size="small" style={{ padding: 0, marginTop: 8 }} onClick={() => setTable((t) => !t)}>
            {table ? 'Hide table' : 'Show table'}
          </Button>
          {table ? (
            <div className="ana-table__wrap" style={{ marginTop: 4 }}>
              <table className="ana-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th className="num">Operations</th>
                    <th className="num">Target h</th>
                    <th className="num">Worked h</th>
                    <th className="num">vs target</th>
                    <th className="num">On target</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.key}>
                      <td className="strong">{r.label}</td>
                      <td className="num">{r.ops}</td>
                      <td className="num">{fmtNum(r.target, 1)}</td>
                      <td className="num">{fmtNum(r.worked, 1)}</td>
                      <td className={`num ${r.variance > 0 ? 'ana-bad' : 'ana-good'}`}>
                        {fmtSignedPct(r.variance, 1)}
                      </td>
                      <td className="num">{r.onTarget == null ? '—' : `${fmtNum(r.onTarget)}%`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </>
      )}
    </Card>
  );
}

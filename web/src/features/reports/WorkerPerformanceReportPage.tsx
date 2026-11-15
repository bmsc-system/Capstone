import { shopToday } from '../../utils/shopTime';
import { useEffect, useMemo, useState } from 'react';
import { DatePicker, message } from 'antd';
import { analyticsApi } from '../../api/analytics.api';
import { workersApi } from '../../api/jobOrders.api';
import { getErrorMessage } from '../../api/client';
import { useShopDetails } from '../../hooks/useShopDetails';
import { ANALYTICS_DEFAULT_MIN_OPS } from '../../constants/shopLetterhead';
import type { AnalyticsByWorker, AnalyticsDelays, AnalyticsWorkerRow, User } from '../../types';
import {
  defaultAnalyticsRange,
  formatInt,
  rangeToParams,
  type AnalyticsRange,
} from '../analytics/analyticsPeriod';
import { ReportToolbar } from './ReportChrome';
import {
  FormalTable,
  ReportHeading,
  ReportNotes,
  ReportSignatures,
  ReportTitleBlock,
  SummaryTable,
  num,
  signedPct,
  sum,
} from './FormalReport';
import { PrintDocument } from './PrintTemplate';
import { PageSkeleton } from '../../components/ui';
import './reportsPrint.css';

const PAUSE_LABEL: Record<string, string> = {
  END_OF_SHIFT: 'End of shift',
  BREAK: 'Break',
  MACHINE_DOWN: 'Machine breakdown',
  WAITING_MATERIAL: 'Waiting for material',
  WAITING_PRIOR_OPERATION: 'Waiting on prior operation',
  EMERGENCY: 'Emergency (had to leave)',
  OTHER: 'Other',
};

export default function WorkerPerformanceReportPage() {
  const shop = useShopDetails();
  const [range, setRange] = useState<AnalyticsRange>(defaultAnalyticsRange);
  const params = rangeToParams(range);
  const [byWorker, setByWorker] = useState<AnalyticsByWorker | null>(null);
  const [delays, setDelays] = useState<AnalyticsDelays | null>(null);
  const [workers, setWorkers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const [perf, list, d] = await Promise.all([
          analyticsApi.byWorker({ ...params, minOps: 1 }),
          workersApi.list(),
          analyticsApi.delays(params),
        ]);
        if (!cancelled) {
          setByWorker(perf.data);
          setWorkers(list.data);
          setDelays(d.data);
        }
      } catch (err) {
        if (!cancelled) message.error(getErrorMessage(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [params.from, params.to]);

  const minOps = ANALYTICS_DEFAULT_MIN_OPS;

  const rows = useMemo(() => {
    const byId = new Map((byWorker?.workers || []).map((w) => [w.workerId, w]));
    const merged: (AnalyticsWorkerRow & { belowMin: boolean })[] = [];

    for (const u of workers) {
      const perf = byId.get(u.id);
      const operationCount = perf?.operationCount ?? 0;
      const belowMin = operationCount < minOps;
      merged.push({
        workerId: u.id,
        workerName: u.fullName,
        operationCount,
        totalEstimatedHours: perf?.totalEstimatedHours ?? null,
        totalActualWorkedHours: perf?.totalActualWorkedHours ?? null,
        averageVariancePct: belowMin ? null : (perf?.averageVariancePct ?? null),
        laborEfficiencyPct: belowMin ? null : (perf?.laborEfficiencyPct ?? null),
        onEstimateRatePct: belowMin ? null : (perf?.onEstimateRatePct ?? null),
        reworkWorkedHours: perf?.reworkWorkedHours ?? null,
        belowMin,
      });
      byId.delete(u.id);
    }
    for (const leftover of byId.values()) {
      const belowMin = leftover.operationCount < minOps;
      merged.push({
        ...leftover,
        averageVariancePct: belowMin ? null : leftover.averageVariancePct,
        laborEfficiencyPct: belowMin ? null : leftover.laborEfficiencyPct,
        onEstimateRatePct: belowMin ? null : leftover.onEstimateRatePct,
        belowMin,
      });
    }
    return merged.sort((a, b) => a.workerName.localeCompare(b.workerName));
  }, [byWorker, workers, minOps]);

  const pauseRows = delays?.pauseReasons || [];
  const totalOps = sum(rows, (r) => r.operationCount) ?? 0;
  const activeWorkers = rows.filter((r) => r.operationCount > 0).length;
  const totalTarget = sum(rows, (r) => r.totalEstimatedHours);
  const totalActual = sum(rows, (r) => r.totalActualWorkedHours);
  const overallEff = totalTarget != null && totalActual ? (totalTarget / totalActual) * 100 : null;
  const totalPause = sum(pauseRows, (r) => r.totalPausedHours);

  return (
    <PrintDocument
      shop={shop}
      pageSize="a4"
      wide
      toolbar={
        <ReportToolbar
          title="Worker performance report"
          periodFrom={params.from}
          periodTo={params.to}
          extra={
            <DatePicker.RangePicker
              value={range}
              allowClear={false}
              format="MMM D, YYYY"
              onChange={(vals) => {
                if (vals?.[0] && vals?.[1]) {
                  setRange([vals[0].startOf('day'), vals[1].endOf('day')]);
                }
              }}
              disabledDate={(d) => d.isAfter(shopToday(), 'day')}
            />
          }
        />
      }
    >
      <div className="formal-report">
        <ReportTitleBlock title="Worker Performance Report" from={params.from} to={params.to} />

        {loading && !byWorker ? (
          <PageSkeleton variant="block" />
        ) : (
          <>
            <ReportHeading n={1}>Summary</ReportHeading>
            <SummaryTable
              caption="Table 1. Summary of worker performance"
              items={[
                { label: 'Workers with finished operations', value: formatInt(activeWorkers) },
                { label: 'Operations completed', value: formatInt(totalOps) },
                { label: 'Target hours', value: num(totalTarget) },
                { label: 'Actual hours worked', value: num(totalActual) },
                {
                  label: 'Overall labor efficiency',
                  value: overallEff == null ? '—' : `${num(overallEff)}%`,
                },
                { label: 'Pause hours (all reasons)', value: num(totalPause) },
              ]}
            />

            <ReportHeading n={2}>Performance by Worker</ReportHeading>
            <FormalTable
              caption="Table 2. Operations completed and labor efficiency of each worker"
              numbered
              rows={rows}
              rowKey={(r) => r.workerId}
              empty="No workers to show for this period."
              columns={[
                { title: 'Worker', render: (r) => r.workerName, width: '21%' },
                {
                  title: 'Operations completed',
                  render: (r) => formatInt(r.operationCount),
                  align: 'right',
                  width: '14%',
                },
                {
                  title: 'Target hours',
                  render: (r) => num(r.totalEstimatedHours),
                  align: 'right',
                  width: '10%',
                },
                {
                  title: 'Actual hours',
                  render: (r) => num(r.totalActualWorkedHours),
                  align: 'right',
                  width: '10%',
                },
                {
                  title: 'Labor efficiency (%)',
                  render: (r) => num(r.laborEfficiencyPct),
                  align: 'right',
                  width: '13%',
                },
                {
                  title: 'On-time rate (%)',
                  render: (r) => num(r.onEstimateRatePct),
                  align: 'right',
                  width: '12%',
                },
                {
                  title: 'Variance vs. target',
                  render: (r) => signedPct(r.averageVariancePct),
                  align: 'right',
                  width: '14%',
                  nowrap: true,
                },
              ]}
              total={[
                'Total',
                formatInt(totalOps),
                num(totalTarget),
                num(totalActual),
                num(overallEff),
                '',
                '',
              ]}
            />
            <ReportNotes
              items={[
                'Labor efficiency = target hours ÷ actual hours × 100. Above 100% means the work took less time than its target.',
                'On-time rate is the share of operations finished close to their target hours.',
                'Variance vs. target is the average difference from target hours; a negative value means faster than target.',
                `A dash (—) means the worker has fewer than ${minOps} finished operations, too few for a fair average.`,
              ]}
            />

            <ReportHeading n={3}>Pause Hours by Reason</ReportHeading>
            <FormalTable
              caption="Table 3. Shop-wide pause hours by reason"
              rows={pauseRows}
              rowKey={(r) => r.reason}
              empty="No pause hours were recorded in this period."
              columns={[
                { title: 'Reason', render: (r) => PAUSE_LABEL[r.reason] || r.reason, width: '46%' },
                {
                  title: 'Occurrences',
                  render: (r) => formatInt(r.occurrenceCount),
                  align: 'right',
                  width: '18%',
                },
                {
                  title: 'Hours',
                  render: (r) => num(r.totalPausedHours),
                  align: 'right',
                  width: '18%',
                },
                {
                  title: 'Share (%)',
                  render: (r) =>
                    totalPause && r.totalPausedHours != null
                      ? num((r.totalPausedHours / totalPause) * 100)
                      : '—',
                  align: 'right',
                  width: '18%',
                },
              ]}
              total={[
                'Total',
                formatInt(sum(pauseRows, (r) => r.occurrenceCount)),
                num(totalPause),
                totalPause ? '100.0' : '—',
              ]}
            />
            <ReportNotes items={['Same figures as Analytics → Delays for this period.']} />

            <ReportSignatures shop={shop} />
          </>
        )}
      </div>
    </PrintDocument>
  );
}

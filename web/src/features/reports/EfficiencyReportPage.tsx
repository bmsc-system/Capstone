import { shopToday } from '../../utils/shopTime';
import { useEffect, useState } from 'react';
import { DatePicker, message } from 'antd';
import { analyticsApi } from '../../api/analytics.api';
import { getErrorMessage } from '../../api/client';
import { useShopDetails } from '../../hooks/useShopDetails';
import type {
  AnalyticsByMachine,
  AnalyticsByOperationType,
  AnalyticsDelayCauseRow,
  AnalyticsDelays,
  AnalyticsMachineTypeRow,
  AnalyticsOperationTypeRow,
  AnalyticsOverview,
} from '../../types';
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
  type FormalColumn,
} from './FormalReport';
import { PrintDocument } from './PrintTemplate';
import { PageSkeleton } from '../../components/ui';
import './reportsPrint.css';

const CAUSE_TYPE: Record<string, string> = {
  PAUSE: 'Pause',
  DOWNTIME: 'Downtime',
  MATERIAL: 'Material',
  OUTSOURCED: 'Outside work',
};

type EffRow = {
  operationCount: number;
  totalEstimatedHours: number | null;
  totalActualWorkedHours: number | null;
  laborEfficiencyPct: number | null;
  averageVariancePct: number | null;
};

function effColumns<T extends EffRow>(
  firstTitle: string,
  name: (r: T) => string,
  tooFew?: (r: T) => boolean
): FormalColumn<T>[] {
  return [
    { title: firstTitle, render: name, width: '30%' },
    {
      title: 'Operations finished',
      render: (r) => formatInt(r.operationCount),
      align: 'right',
      width: '13%',
    },
    {
      title: 'Planned hours',
      render: (r) => num(r.totalEstimatedHours),
      align: 'right',
      width: '13%',
    },
    {
      title: 'Actual hours',
      render: (r) => num(r.totalActualWorkedHours),
      align: 'right',
      width: '13%',
    },
    {
      title: 'Labor efficiency (%)',
      render: (r) => (tooFew?.(r) ? '—' : num(r.laborEfficiencyPct)),
      align: 'right',
      width: '15%',
    },
    {
      title: 'Variance vs. target',
      render: (r) => (tooFew?.(r) ? '—' : signedPct(r.averageVariancePct)),
      align: 'right',
      width: '16%',
      nowrap: true,
    },
  ];
}

function effTotal(rows: EffRow[]) {
  const planned = sum(rows, (r) => r.totalEstimatedHours);
  const actual = sum(rows, (r) => r.totalActualWorkedHours);
  const eff = planned != null && actual ? (planned / actual) * 100 : null;
  return [
    'Total',
    formatInt(sum(rows, (r) => r.operationCount)),
    num(planned),
    num(actual),
    num(eff),
    '',
  ];
}

export default function EfficiencyReportPage() {
  const shop = useShopDetails();
  const [range, setRange] = useState<AnalyticsRange>(defaultAnalyticsRange);
  const params = rangeToParams(range);
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [opTypes, setOpTypes] = useState<AnalyticsByOperationType | null>(null);
  const [machines, setMachines] = useState<AnalyticsByMachine | null>(null);
  const [delays, setDelays] = useState<AnalyticsDelays | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const [o, ot, m, d] = await Promise.all([
          analyticsApi.overview(params),
          analyticsApi.byOperationType(params),
          analyticsApi.byMachine(params),
          analyticsApi.delays(params),
        ]);
        if (!cancelled) {
          setOverview(o.data);
          setOpTypes(ot.data);
          setMachines(m.data);
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

  const minOps = opTypes?.minimumOperationCount ?? machines?.minimumOperationCount;
  const pareto = (delays?.causes || []).filter((c) => (c.hours ?? 0) > 0);
  const opRows = opTypes?.operationTypes || [];
  const machineRows = machines?.machineTypes || [];
  const threshold = delays?.paretoThresholdPct ?? null;
  // A cause is in the vital few while the hours before it are still under the threshold.
  const isVital = (r: AnalyticsDelayCauseRow) =>
    threshold != null && r.cumulativePct != null && r.shareOfTotalPct != null
      ? r.cumulativePct - r.shareOfTotalPct < threshold
      : false;

  return (
    <PrintDocument
      shop={shop}
      pageSize="a4"
      wide
      toolbar={
        <ReportToolbar
          title="Efficiency report"
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
        <ReportTitleBlock title="Efficiency Report" from={params.from} to={params.to} />

        {loading && !overview ? (
          <PageSkeleton variant="block" />
        ) : (
          <>
            <ReportHeading n={1}>Summary</ReportHeading>
            <SummaryTable
              caption="Table 1. Summary of efficiency"
              items={[
                { label: 'Job orders finished', value: formatInt(overview?.jobs.completed) },
                {
                  label: 'Average variance vs. target hours',
                  value: signedPct(overview?.efficiency.averageVariancePct),
                  remark: 'A negative value means faster than target.',
                },
                {
                  label: 'Hours lost to delays',
                  value: num(delays?.totalDelayHours),
                  remark: pareto[0] ? `Largest cause: ${pareto[0].label}` : undefined,
                },
              ]}
            />

            <ReportHeading n={2}>Labor Efficiency by Operation Type</ReportHeading>
            <FormalTable
              caption="Table 2. Planned and actual hours of finished operations by operation type"
              rows={opRows}
              rowKey={(r) => r.operationTypeId}
              empty="No finished operations in this period."
              columns={effColumns<AnalyticsOperationTypeRow>(
                'Operation type',
                (r) => r.operationTypeName
              )}
              total={effTotal(opRows)}
            />

            <ReportHeading n={3}>Labor Efficiency by Machine Type</ReportHeading>
            <FormalTable
              caption="Table 3. Planned and actual hours of finished operations by machine type"
              rows={machineRows}
              rowKey={(r) => r.machineTypeId}
              empty="No finished operations in this period."
              columns={effColumns<AnalyticsMachineTypeRow>(
                'Machine type',
                (r) => r.machineTypeName,
                (r) => r.belowMinimumSample
              )}
              total={effTotal(machineRows)}
            />
            <ReportNotes
              items={[
                'Labor efficiency = planned (target) hours ÷ actual hours worked × 100, counting working time only. Above 100% means the work took less time than planned.',
                'Variance vs. target is the average difference from planned hours; a negative value means faster than planned.',
                minOps != null
                  ? `A dash (—) means fewer than ${minOps} finished operations, too few for a fair average.`
                  : null,
              ]}
            />

            <ReportHeading n={4}>Causes of Delay (Pareto Analysis)</ReportHeading>
            <FormalTable
              caption="Table 4. Hours lost by cause of delay, largest first"
              numbered
              rows={pareto}
              rowKey={(r) => `${r.causeType}-${r.cause}`}
              rowClassName={(r) => (isVital(r) ? 'fr-row-flag' : undefined)}
              empty="No delay hours were recorded in this period."
              columns={[
                { title: 'Cause', render: (r) => r.label, width: '34%' },
                {
                  title: 'Type',
                  render: (r) => CAUSE_TYPE[r.causeType] || r.causeType,
                  width: '13%',
                },
                { title: 'Hours lost', render: (r) => num(r.hours), align: 'right', width: '11%' },
                {
                  title: 'Occurrences',
                  render: (r) => formatInt(r.occurrenceCount),
                  align: 'right',
                  width: '12%',
                },
                {
                  title: 'Share (%)',
                  render: (r) => num(r.shareOfTotalPct),
                  align: 'right',
                  width: '11%',
                },
                {
                  title: 'Cumulative (%)',
                  render: (r) => num(r.cumulativePct),
                  align: 'right',
                  width: '13%',
                },
              ]}
              total={[
                'Total',
                '',
                num(sum(pareto, (r) => r.hours)),
                formatInt(sum(pareto, (r) => r.occurrenceCount)),
                '100.0',
                '',
              ]}
            />
            <ReportNotes
              items={[
                threshold != null
                  ? `Causes in bold make up the first ${threshold}% of hours lost (the "vital few"), where corrective action has the most effect.`
                  : null,
              ]}
            />

            <ReportSignatures shop={shop} />
          </>
        )}
      </div>
    </PrintDocument>
  );
}

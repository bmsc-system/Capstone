import { useState } from 'react';
import { PageSkeleton } from '../../components/ui';
import { exportCsv } from '../../utils/csvExport';
import { fmtMonth, fmtNum, fmtPeso, fmtPesoCompact, JOB_TYPE_LABEL, pctChange } from './format';
import { Card, ChartEmpty, Grid, Kpi, Kpis, PALETTE, SortedBars } from './kit';
import { CardLoading, DemandForecastCard, SalesForecastCard } from './cards';
import { useDemandForecast, useJobOrders, usePeriods, useSalesForecast, useSalesSummary } from './sources';

export default function AnalyticsSalesPage() {
  const { period, previous } = usePeriods();
  const sales = useSalesSummary(period).data;
  const salesPrev = useSalesSummary(previous).data;
  const jobs = useJobOrders(period).data;
  const jobsPrev = useJobOrders(previous).data;
  const [salesMonths, setSalesMonths] = useState(3);
  const [demandMonths, setDemandMonths] = useState(3);
  const forecast = useSalesForecast(salesMonths).data;
  const demand = useDemandForecast(demandMonths).data;

  if (!sales) return <PageSkeleton variant="cards" />;

  const avg = sales.completedJobCount ? (sales.totalAmount ?? 0) / sales.completedJobCount : null;
  const avgPrev =
    salesPrev && salesPrev.completedJobCount ? (salesPrev.totalAmount ?? 0) / salesPrev.completedJobCount : null;
  const pipeline = forecast?.committedPipeline;
  const pipelineMonths = (pipeline?.byExpectedCompletionMonth ?? []).filter((m) => m.jobCount > 0);
  const clients = sales.byClient.filter((c) => (c.amount ?? 0) > 0);
  const types = sales.byJobType.filter((t) => (t.amount ?? 0) > 0);
  const topClient = [...clients].sort((a, b) => (b.amount ?? 0) - (a.amount ?? 0))[0];
  const topType = [...types].sort((a, b) => (b.amount ?? 0) - (a.amount ?? 0))[0];
  const total = sales.totalAmount ?? 0;

  return (
    <div>
      <Kpis>
        <Kpi
          label="Sales"
          value={fmtPesoCompact(sales.totalAmount)}
          delta={salesPrev ? pctChange(sales.totalAmount, salesPrev.totalAmount) : undefined}
          info="Amount of jobs finished in the period (Completed, For Delivery or Delivered), by the day the last operation ended."
        />
        <Kpi
          label="Jobs finished"
          value={fmtNum(sales.completedJobCount)}
          delta={salesPrev ? pctChange(sales.completedJobCount, salesPrev.completedJobCount) : undefined}
        />
        <Kpi
          label="Job orders in"
          value={fmtNum(jobs?.received.count)}
          delta={jobs && jobsPrev ? pctChange(jobs.received.count, jobsPrev.received.count) : undefined}
          info="By the date the job order was entered (the client PO date on older records)."
        />
        <Kpi label="Avg job value" value={fmtPesoCompact(avg)} delta={avgPrev != null ? pctChange(avg, avgPrev) : undefined} />
        <Kpi
          label="Pipeline"
          value={fmtPesoCompact(pipeline?.totalAmount)}
          sub={pipeline ? `${pipeline.jobCount} released, not finished` : undefined}
        />
      </Kpis>

      <Grid>
        <SalesForecastCard data={forecast} months={salesMonths} onMonthsChange={setSalesMonths} />
        <DemandForecastCard data={demand} months={demandMonths} onMonthsChange={setDemandMonths} />

        <Card
          title="Pipeline by month"
          span={4}
          chip="Now"
          takeaway={
            pipelineMonths[0] ? (
              <>
                <strong>{fmtPesoCompact(pipelineMonths[0].amount)}</strong> due to finish in{' '}
                {fmtMonth(pipelineMonths[0].month)}
              </>
            ) : forecast ? (
              'No released jobs waiting'
            ) : undefined
          }
          info="Released jobs not yet finished, by the month they are expected to finish (scheduled end, else date required)."
          onExport={
            pipelineMonths.length
              ? () =>
                  exportCsv('pipeline-by-month', pipelineMonths, [
                    { key: 'm', header: 'Month', value: (r) => r.month },
                    { key: 'n', header: 'Jobs', value: (r) => r.jobCount },
                    { key: 'a', header: 'Amount', value: (r) => r.amount ?? '' },
                  ])
              : undefined
          }
        >
          {!forecast ? (
            <CardLoading />
          ) : pipelineMonths.length ? (
            <SortedBars
              sort={false}
              rows={pipelineMonths.map((m) => ({
                key: m.month,
                label: fmtMonth(m.month),
                value: m.amount ?? 0,
                display: fmtPesoCompact(m.amount),
                sub: `${m.jobCount} job${m.jobCount === 1 ? '' : 's'}`,
                color: PALETTE.second,
              }))}
            />
          ) : (
            <ChartEmpty title="No released jobs waiting" />
          )}
        </Card>

        <Card
          title="Sales by client"
          span={4}
          takeaway={
            topClient ? (
              <>
                <strong>{topClient.clientName}</strong> · {fmtNum(((topClient.amount ?? 0) / (total || 1)) * 100)}% of sales
              </>
            ) : (
              'No sales in this period'
            )
          }
          onExport={
            clients.length
              ? () =>
                  exportCsv('sales-by-client', clients, [
                    { key: 'c', header: 'Client', value: (r) => r.clientName ?? '' },
                    { key: 'n', header: 'Jobs', value: (r) => r.jobCount },
                    { key: 'a', header: 'Amount', value: (r) => r.amount ?? '' },
                    { key: 'v', header: 'Average job value', value: (r) => r.averageJobValue ?? '' },
                  ])
              : undefined
          }
        >
          {clients.length ? (
            <SortedBars
              limit={8}
              rows={clients.map((c) => ({
                key: c.clientId,
                label: c.clientName ?? '—',
                value: c.amount ?? 0,
                display: fmtPesoCompact(c.amount),
                sub: `${c.jobCount}`,
                title: `${c.clientName}: ${fmtPeso(c.amount)} from ${c.jobCount} job${c.jobCount === 1 ? '' : 's'}`,
              }))}
            />
          ) : (
            <ChartEmpty title="No finished jobs in this period" />
          )}
        </Card>

        <Card
          title="Sales by job type"
          span={4}
          takeaway={
            topType ? (
              <>
                <strong>{JOB_TYPE_LABEL[topType.jobType] ?? topType.jobType}</strong> ·{' '}
                {fmtNum(((topType.amount ?? 0) / (total || 1)) * 100)}% of sales
              </>
            ) : (
              'No sales in this period'
            )
          }
        >
          {types.length ? (
            <SortedBars
              rows={types.map((t) => ({
                key: t.jobType,
                label: JOB_TYPE_LABEL[t.jobType] ?? t.jobType,
                value: t.amount ?? 0,
                display: fmtPesoCompact(t.amount),
                sub: `${t.jobCount} job${t.jobCount === 1 ? '' : 's'}`,
                color: PALETTE.third,
              }))}
            />
          ) : (
            <ChartEmpty title="No finished jobs in this period" />
          )}
        </Card>
      </Grid>
    </div>
  );
}

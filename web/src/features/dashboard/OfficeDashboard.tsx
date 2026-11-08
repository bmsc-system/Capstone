import { useEffect, useMemo, useState } from 'react';
import { Button, Skeleton } from 'antd';
import {
  AlertOutlined,
  FileDoneOutlined,
  FileTextOutlined,
  RightOutlined,
  ShoppingCartOutlined,
  TruckOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { analyticsApi } from '../../api/analytics.api';
import type { OfficeSummary } from '../../types';
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
import { formatShop } from '../../utils/shopTime';
import './officeDashboard.css';

type TaskKind = 'materials' | 'invoice';
type Task = {
  key: string;
  kind: TaskKind;
  jobId: string;
  jobNumber: string | null;
  clientName: string | null;
  detail: string;
  when: string | null;
};

const TASK_LABEL: Record<TaskKind, { label: string; tone: 'amber' | 'blue' }> = {
  materials: { label: 'Order materials', tone: 'amber' },
  invoice: { label: 'Record sales invoice', tone: 'blue' },
};

function day(iso: string | null) {
  return iso ? formatShop(iso, 'MMM D, YYYY') : '—';
}

function qty(v: number) {
  return Number.isInteger(v) ? String(v) : v.toFixed(2).replace(/\.?0+$/, '');
}

/** Dashboard for the Office Staff: their own work waiting to be done. */
export default function OfficeDashboard() {
  const navigate = useNavigate();
  const [data, setData] = useState<OfficeSummary | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    analyticsApi
      .officeSummary()
      .then(({ data: d }) => alive && setData(d))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, []);

  const reported = data?.reportedLow ?? [];
  const reportedIds = new Set(reported.map((r) => r.toolId));
  const forecastDue = (data?.restockDue ?? []).filter((it) => !reportedIds.has(it.id));
  const toBuyCount = reported.length + forecastDue.length;

  const tasks = useMemo<Task[]>(() => {
    if (!data) return [];
    return [
      ...data.materialsToOrder.map((j) => ({
        key: `m-${j.id}`,
        kind: 'materials' as const,
        jobId: j.id,
        jobNumber: j.jobNumber,
        clientName: j.clientName,
        detail: j.title,
        when: j.dueDate,
      })),
      ...data.invoicesToRecord.map((j) => ({
        key: `i-${j.id}`,
        kind: 'invoice' as const,
        jobId: j.id,
        jobNumber: j.jobNumber,
        clientName: j.clientName,
        detail: j.title,
        when: j.dueDate,
      })),
    ];
  }, [data]);

  const loading = !data && !failed;
  const deliveries = data ? [...data.lateDeliveries, ...data.deliveriesDueSoon] : [];

  if (failed) {
    return (
      <Page className="dashboard-page">
        <EmptyState
          icon={<WarningOutlined />}
          title="Couldn't load the dashboard"
          description="Refresh the page to try again."
        />
      </Page>
    );
  }

  return (
    <Page className="dashboard-page office-dash">
      <StatGrid min={180}>
        <StatCard
          icon={<FileTextOutlined />}
          tone="blue"
          label="Pending job orders"
          value={data?.pendingJobs ?? 0}
          loading={loading}
          hint="Waiting for the Admin to plan"
          onClick={() => navigate('/job-orders?tab=drafts')}
        />
        <StatCard
          icon={<ShoppingCartOutlined />}
          tone="amber"
          label="Materials to order"
          value={data?.materialsToOrder.length ?? 0}
          valueTone={data?.materialsToOrder.length ? 'amber' : undefined}
          loading={loading}
          hint="Jobs with nothing ordered yet"
          onClick={() => navigate('/supplier-orders')}
        />
        <StatCard
          icon={<TruckOutlined />}
          tone="red"
          label="Late deliveries"
          value={data?.lateDeliveries.length ?? 0}
          valueTone={data?.lateDeliveries.length ? 'red' : undefined}
          loading={loading}
          hint={data ? `${data.deliveriesDueSoon.length} due in the next 7 days` : undefined}
          onClick={() => navigate('/supplier-orders')}
        />
        <StatCard
          icon={<FileDoneOutlined />}
          tone="indigo"
          label="Invoices to record"
          value={data?.invoicesToRecord.length ?? 0}
          loading={loading}
          hint="Completed jobs without an invoice"
          onClick={() => navigate('/job-orders')}
        />
        <StatCard
          icon={<AlertOutlined />}
          tone="amber"
          label="Consumables to buy"
          value={toBuyCount}
          valueTone={reported.length ? 'red' : toBuyCount ? 'amber' : undefined}
          loading={loading}
          hint={reported.length ? `${reported.length} reported by workers` : 'Due by the purchase forecast'}
          onClick={() => navigate('/tools?tab=consumables')}
        />
      </StatGrid>

      <div className="dashboard-page__charts">
        <SectionCard
          title="Needs your action"
          subtitle="Job orders waiting on the office"
          flush
          extra={
            <Button type="link" size="small" onClick={() => navigate('/job-orders')}>
              Job orders <RightOutlined />
            </Button>
          }
        >
          <DataTable<Task>
            card={{ title: 'detail', subtitle: ['jobNumber', 'clientName'] }}
            rowKey="key"
            loading={loading}
            dataSource={tasks}
            pagination={false}
            scroll={{ x: 640 }}
            locale={{
              emptyText: (
                <EmptyState compact icon={<FileDoneOutlined />} title="Nothing waiting on the office" />
              ),
            }}
            onRow={(t) => rowOpenProps(() => navigate(`/job-orders/${t.jobId}`))}
            columns={[
              {
                title: 'To do',
                key: 'kind',
                width: 190,
                render: (_: unknown, t) => (
                  <StatusPill tone={TASK_LABEL[t.kind].tone} compact>
                    {TASK_LABEL[t.kind].label}
                  </StatusPill>
                ),
              },
              {
                title: 'Job #',
                key: 'jobNumber',
                width: 130,
                render: (_: unknown, t) => <IdChip>{t.jobNumber || '—'}</IdChip>,
              },
              { title: 'Client', dataIndex: 'clientName', width: 160, ellipsis: true },
              { title: 'Details', dataIndex: 'detail', ellipsis: true },
              {
                title: 'Date',
                key: 'when',
                width: 120,
                render: (_: unknown, t) => <span className="ui-tabular">{day(t.when)}</span>,
              },
            ]}
          />
        </SectionCard>

        <div className="office-dash__side">
          <SectionCard
            title="Supplier deliveries"
            subtitle="Late, and due in the next 7 days"
            extra={
              <Button type="link" size="small" onClick={() => navigate('/supplier-orders')}>
                Orders <RightOutlined />
              </Button>
            }
          >
            {loading ? (
              <Skeleton active paragraph={{ rows: 3 }} title={false} />
            ) : deliveries.length === 0 ? (
              <EmptyState compact icon={<TruckOutlined />} title="No deliveries due" />
            ) : (
              <ul className="office-dash__list">
                {deliveries.slice(0, 6).map((d) => (
                  <li key={d.id}>
                    <button type="button" onClick={() => navigate(`/supplier-orders/${d.id}`)}>
                      <span className="office-dash__main">
                        <strong>{d.poNumber || 'Supplier order'}</strong>
                        <span>{d.supplierName || '—'}</span>
                      </span>
                      <span className={`office-dash__when ui-tabular${d.daysOverdue > 0 ? ' tone-text--red' : ''}`}>
                        {d.daysOverdue > 0
                          ? `${d.daysOverdue} day${d.daysOverdue === 1 ? '' : 's'} late`
                          : day(d.expectedDeliveryDate)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>

          <SectionCard
            title="Consumables to buy"
            subtitle="Reported by workers, then due by how often they are bought"
            extra={
              <Button type="link" size="small" onClick={() => navigate('/tools?tab=consumables')}>
                Inventory <RightOutlined />
              </Button>
            }
          >
            {loading ? (
              <Skeleton active paragraph={{ rows: 3 }} title={false} />
            ) : !data || toBuyCount === 0 ? (
              <EmptyState compact icon={<AlertOutlined />} title="Nothing due to buy" />
            ) : (
              <ul className="office-dash__list">
                {reported.slice(0, 6).map((r) => (
                  <li key={`r-${r.id}`}>
                    <button type="button" onClick={() => navigate('/tools?tab=consumables')}>
                      <span className="office-dash__main">
                        <strong>{r.name}</strong>
                        <span>
                          Reported by {r.reportedByName ?? 'a worker'}
                          {r.reportedAt ? ` · ${formatShop(r.reportedAt, 'MMM D, h:mm A')}` : ''}
                        </span>
                      </span>
                      <StatusPill tone={r.level === 'OUT' ? 'red' : 'amber'} compact>
                        {r.level === 'OUT' ? 'Out' : 'Running low'}
                      </StatusPill>
                    </button>
                  </li>
                ))}
                {forecastDue.slice(0, Math.max(6 - reported.length, 0)).map((it) => (
                  <li key={it.id}>
                    <button type="button" onClick={() => navigate('/tools?tab=consumables')}>
                      <span className="office-dash__main">
                        <strong>{it.name}</strong>
                        <span>
                          {it.lastBoughtOn ? `Bought ${formatShop(it.lastBoughtOn, 'MMM D')}` : 'Not bought yet'}
                          {it.usualQuantity
                            ? ` · usually ${qty(it.usualQuantity >= 10 ? Math.round(it.usualQuantity) : Math.round(it.usualQuantity * 10) / 10)} ${it.unit}`
                            : ''}
                        </span>
                      </span>
                      <span
                        className="office-dash__when ui-tabular"
                        style={it.status === 'OVERDUE' ? { color: 'var(--c-danger, #b91c1c)' } : undefined}
                      >
                        {it.status === 'OVERDUE' ? 'Was due ' : 'Due '}
                        {formatShop(it.nextPurchaseOn, 'MMM D')}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>
        </div>
      </div>
    </Page>
  );
}

import { formatShop, isFutureShopDay, shopToday } from '../../utils/shopTime';
import dayjs from 'dayjs';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, DatePicker, Descriptions, Drawer, Segmented, message } from 'antd';
import type { TableColumnsType } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { Clock, Package, ShoppingCart, Truck } from 'lucide-react';
import { inventoryApi } from '../../api/tools.api';
import { jobOrdersApi } from '../../api/jobOrders.api';
import { getErrorMessage } from '../../api/client';
import OverdueTag from '../../components/OverdueTag';
import StatusPill from '../../components/StatusPill';
import type {
  MaterialPurchase,
  MaterialPurchaseList,
  MaterialStockBucket,
} from '../../types';
import { exportCsv } from '../../utils/csvExport';
import { useAuth } from '../../hooks/useAuth';
import ResponsiveTable from '../../components/ui/ResponsiveTable';
import { FilterBar, rowOpenProps } from '../../components/ui';
import { MOBILE_SHELL_QUERY, NARROW_QUERY, useMediaQuery } from '../../hooks/useViewport';
import {
  CategoryTile,
  InventorySummaryCards,
  type InventorySummaryCard,
} from './inventoryLook';
import { formatPoMaterialLabel } from '../../components/MaterialInputs';
import { confirmAction } from '../../utils/confirmAction';

function money(v: number | null | undefined) {
  if (v == null) return '—';
  return `₱${Number(v).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

const EMPTY_BUCKET: MaterialStockBucket = { count: 0, value: 0, quantityByUnit: [] };

type StockView = 'ON_HAND' | 'INCOMING' | 'CONSUMED';

function isCancelled(r: MaterialPurchase) {
  return r.status === 'CANCELLED';
}

function isConsumed(r: MaterialPurchase) {
  return Boolean(r.consumedAt) || r.status === 'CONSUMED';
}

function isOnHand(r: MaterialPurchase) {
  return Boolean(r.dateReceived) && !isConsumed(r) && !isCancelled(r);
}

function isIncoming(r: MaterialPurchase) {
  return !r.dateReceived && !isConsumed(r) && !isCancelled(r) && r.status !== 'DRAFT';
}

function daysInStock(received?: string | null) {
  if (!received) return null;
  const days = shopToday().startOf('day').diff(dayjs(received).startOf('day'), 'day');
  return Number.isFinite(days) ? Math.max(0, days) : null;
}

function jobLabel(r: MaterialPurchase) {
  return r.jobNumber || (r.jobOrderId ? r.jobOrderId.slice(0, 8) : null);
}

export default function RawMaterialsPanel() {
  const { canOfficeWrite: canEdit } = useAuth();
  const [rows, setRows] = useState<MaterialPurchase[]>([]);
  const [summary, setSummary] = useState<MaterialPurchaseList['summary']>({
    purchaseCount: 0,
    totalSpend: 0,
    awaitingDeliveryCount: 0,
    onOrder: EMPTY_BUCKET,
    onHand: EMPTY_BUCKET,
    consumed: EMPTY_BUCKET,
  });
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<StockView>('ON_HAND');
  const [lateOnly, setLateOnly] = useState(false);
  const [search, setSearch] = useState('');
  const [detail, setDetail] = useState<MaterialPurchase | null>(null);
  const compact = useMediaQuery(MOBILE_SHELL_QUERY);
  const narrow = useMediaQuery(NARROW_QUERY);

  const fetchRows = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await inventoryApi.materialPurchases({});
      setRows(data.items);
      setSummary(data.summary);
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchRows();
  }, [fetchRows]);

  const markReceived = (row: MaterialPurchase) => {
    let receivedDate = shopToday();
    confirmAction({
      wide: true,
      title: `Mark “${row.materialName}” received?`,
      content: (
        <div style={{ marginTop: 8 }}>
          <div style={{ marginBottom: 8, fontSize: 13, color: '#475569' }}>Date received</div>
          <DatePicker
            style={{ width: '100%' }}
            defaultValue={shopToday()}
            format="MMM D, YYYY"
            allowClear={false}
            disabledDate={isFutureShopDay}
            onChange={(d) => {
              if (d) receivedDate = d;
            }}
          />
        </div>
      ),
      okText: 'Mark received',
      onOk: async () => {
        try {
          await jobOrdersApi.updateMaterialPurchase(row.jobOrderId!, row.id, {
            dateReceived: receivedDate.format('YYYY-MM-DD'),
          });
          message.success('Marked received');
          setDetail(null);
          await fetchRows();
        } catch (err) {
          message.error(getErrorMessage(err));
          throw err;
        }
      },
    });
  };

  const onHand = useMemo(() => rows.filter(isOnHand), [rows]);
  const incoming = useMemo(() => rows.filter(isIncoming), [rows]);
  const consumed = useMemo(() => rows.filter(isConsumed), [rows]);
  const late = useMemo(() => incoming.filter((r) => (r.daysOverdue || 0) > 0), [incoming]);

  const rawCards: InventorySummaryCard[] = useMemo(
    () => [
      { key: 'ON_HAND', label: 'On hand', shortLabel: 'On hand', value: onHand.length, icon: Package, tone: 'green' },
      { key: 'INCOMING', label: 'Incoming', shortLabel: 'Incoming', value: incoming.length, icon: Truck, tone: 'amber' },
      { key: 'late', label: 'Late deliveries', shortLabel: 'Late', value: late.length, icon: Clock, tone: 'red' },
      {
        key: 'jobs_waiting',
        label: 'Jobs waiting on materials',
        shortLabel: 'Jobs waiting',
        value: summary.jobsWaitingOnMaterialsCount || 0,
        icon: ShoppingCart,
        tone: 'blue',
      },
    ],
    [onHand.length, incoming.length, late.length, summary.jobsWaitingOnMaterialsCount]
  );

  const visibleRows = useMemo(() => {
    const pool =
      lateOnly && view === 'INCOMING'
        ? late
        : view === 'ON_HAND'
          ? onHand
          : view === 'INCOMING'
            ? incoming
            : consumed;
    const q = search.trim().toLowerCase();
    if (!q) return pool;
    return pool.filter((r) =>
      [r.materialName, r.gradeOrSpec, r.sizeSpec, r.jobNumber].some(
        (v) => v && String(v).toLowerCase().includes(q)
      )
    );
  }, [view, lateOnly, late, onHand, incoming, consumed, search]);

  const exportRows = () =>
    exportCsv(
      `raw-materials-${view.toLowerCase()}.csv`,
      visibleRows,
      [
        { key: 'material', header: 'Material', value: (r) => r.materialName },
        { key: 'size', header: 'Size', value: (r) => [r.gradeOrSpec, r.sizeSpec].filter(Boolean).join(' ') },
        { key: 'qty', header: 'Quantity', value: (r) => r.quantity },
        { key: 'unit', header: 'Unit', value: (r) => r.unit },
        { key: 'job', header: 'ReservedFor', value: (r) => r.jobNumber },
        { key: 'received', header: 'DateReceived', value: (r) => r.dateReceived },
        { key: 'expected', header: 'Expected', value: (r) => r.currentExpectedDate },
        { key: 'consumed', header: 'ConsumedAt', value: (r) => r.consumedAt },
        { key: 'days', header: 'DaysWaiting', value: (r) => daysInStock(r.dateReceived) ?? '' },
      ]
    );

  const reservedCell = (r: MaterialPurchase) => {
    const label = jobLabel(r);
    if (!label) return '—';
    if (!r.jobOrderId) return label;
    return (
      <Link to={`/job-orders/${r.jobOrderId}`} onClick={(e) => e.stopPropagation()}>
        {label}
      </Link>
    );
  };

  const cardColumns: TableColumnsType<MaterialPurchase> = [
    {
      title: 'Material',
      key: 'material',
      sorter: (a, b) => a.materialName.localeCompare(b.materialName),
      render: (_: unknown, r) => formatPoMaterialLabel(r.materialName, r.gradeOrSpec, r.sizeSpec),
    },
    { title: 'Reserved for', key: 'job', render: (_: unknown, r) => jobLabel(r) || '—' },
    { title: 'Qty', key: 'qty', render: (_: unknown, r) => `${r.quantity} ${r.unit}` },
    {
      title: view === 'CONSUMED' ? 'Consumed' : view === 'INCOMING' ? 'Expected' : 'Received',
      key: 'when',
      render: (_: unknown, r) => {
        if (view === 'CONSUMED') {
          return r.consumedAt ? formatShop(r.consumedAt, 'MMM D, YYYY') : '—';
        }
        if (view === 'INCOMING') {
          return (
            <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
              {r.currentExpectedDate ? formatShop(r.currentExpectedDate, 'MMM D, YYYY') : 'No date'}
              <OverdueTag
                days={r.daysOverdue}
                tooltip={
                  r.currentExpectedDate
                    ? `Expected ${formatShop(r.currentExpectedDate, 'MMM D, YYYY')} and not received yet.`
                    : undefined
                }
              />
            </span>
          );
        }
        const days = daysInStock(r.dateReceived);
        return r.dateReceived
          ? `${formatShop(r.dateReceived, 'MMM D, YYYY')}${days != null ? ` · ${days}d` : ''}`
          : '—';
      },
    },
  ];

  const columns: TableColumnsType<MaterialPurchase> = [
    {
      title: 'Material',
      dataIndex: 'materialName',
      width: 280,
      ellipsis: true,
      sorter: (a, b) => a.materialName.localeCompare(b.materialName),
      render: (_: string, r) => (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <CategoryTile category={r.materialName} />
          <span style={{ fontWeight: 600, color: '#0f172a' }}>
            {formatPoMaterialLabel(r.materialName, r.gradeOrSpec, r.sizeSpec)}
          </span>
        </div>
      ),
    },
    {
      title: 'Qty',
      key: 'qty',
      width: 110,
      render: (_: unknown, r) => `${r.quantity} ${r.unit}`,
    },
    {
      title: 'Reserved for',
      key: 'job',
      width: 140,
      render: (_: unknown, r) => reservedCell(r),
    },
    ...(view === 'ON_HAND'
      ? [
          {
            title: 'Received',
            dataIndex: 'dateReceived',
            width: 130,
            render: (v: string | null) => (v ? formatShop(v, 'MMM D, YYYY') : '—'),
          },
          {
            title: 'Days waiting',
            key: 'days',
            width: 120,
            sorter: (a: MaterialPurchase, b: MaterialPurchase) =>
              (daysInStock(a.dateReceived) ?? -1) - (daysInStock(b.dateReceived) ?? -1),
            render: (_: unknown, r: MaterialPurchase) => {
              const days = daysInStock(r.dateReceived);
              return days == null ? '—' : `${days} day${days === 1 ? '' : 's'}`;
            },
          },
        ]
      : view === 'INCOMING'
        ? [
            {
              title: 'Expected',
              key: 'expected',
              width: 200,
              render: (_: unknown, r: MaterialPurchase) => (
                <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                  {r.currentExpectedDate ? formatShop(r.currentExpectedDate, 'MMM D, YYYY') : '—'}
                  <OverdueTag
                    days={r.daysOverdue}
                    tooltip={
                      r.currentExpectedDate
                        ? `Expected ${formatShop(r.currentExpectedDate, 'MMM D, YYYY')} and not received yet.`
                        : undefined
                    }
                  />
                </span>
              ),
            },
          ]
        : [
            {
              title: 'Consumed',
              dataIndex: 'consumedAt',
              width: 140,
              render: (v: string | null) => (v ? formatShop(v, 'MMM D, YYYY') : '—'),
            },
          ]),
  ];

  return (
    <div>
      <InventorySummaryCards
        cards={rawCards}
        activeKey={lateOnly ? 'late' : view}
        onSelect={(key) => {
          if (!key || key === 'ON_HAND') {
            setView('ON_HAND');
            setLateOnly(false);
          } else if (key === 'late') {
            setView('INCOMING');
            setLateOnly(true);
          } else if (key === 'INCOMING' || key === 'CONSUMED') {
            setView(key);
            setLateOnly(false);
          } else if (key === 'jobs_waiting') {
            setView('INCOMING');
            setLateOnly(false);
          }
        }}
      />

      <FilterBar
        search={{ value: search, onChange: setSearch, placeholder: 'Search material or job #…' }}
        tabs={
          <Segmented<StockView>
            value={view}
            onChange={(v) => {
              setView(v);
              setLateOnly(false);
            }}
            options={[
              { label: `On hand (${onHand.length})`, value: 'ON_HAND' },
              { label: `Incoming (${incoming.length})`, value: 'INCOMING' },
              { label: `Consumed (${consumed.length})`, value: 'CONSUMED' },
            ]}
          />
        }
        actions={
          <Button icon={<DownloadOutlined />} disabled={!visibleRows.length} onClick={exportRows} aria-label="Export CSV">
            {compact ? null : 'Export CSV'}
          </Button>
        }
      />

      <ResponsiveTable
        className="std-list-table raw-materials-table"
        rowKey="id"
        loading={loading}
        dataSource={visibleRows}
        columns={narrow ? cardColumns : columns}
        cardsBelow="narrow"
        card={{ title: 'material', subtitle: ['job'], actions: 'none' }}
        onRow={(r) => rowOpenProps(() => setDetail(r))}
        pagination={{ pageSize: 25, showSizeChanger: true }}
        scroll={narrow ? undefined : { x: 880 }}
        sticky={
          narrow
            ? undefined
            : {
                offsetHeader: 0,
                getContainer: () =>
                  (document.querySelector('.app-shell__scroll') as HTMLElement | null) ?? window,
              }
        }
        locale={{
          emptyText:
            view === 'ON_HAND'
              ? 'No material on hand for a job.'
              : view === 'INCOMING'
                ? 'Nothing incoming.'
                : 'No consumed material in this list.',
        }}
      />

      <Drawer
        title={detail ? formatPoMaterialLabel(detail.materialName, detail.gradeOrSpec, detail.sizeSpec) : 'Material'}
        open={Boolean(detail)}
        onClose={() => setDetail(null)}
        size={400}
        destroyOnHidden
      >
        {detail ? (
          <div className="raw-detail">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Quantity">
                {detail.quantity} {detail.unit}
              </Descriptions.Item>
              <Descriptions.Item label="Reserved for">
                {detail.jobOrderId ? (
                  <Link to={`/job-orders/${detail.jobOrderId}`}>{jobLabel(detail)}</Link>
                ) : (
                  '—'
                )}
              </Descriptions.Item>
              <Descriptions.Item label="Supplier">{detail.supplierName || '—'}</Descriptions.Item>
              <Descriptions.Item label="PO">
                {detail.supplierOrderId ? (
                  <Link to={`/supplier-orders/${detail.supplierOrderId}`}>{detail.poNumber || 'Open PO'}</Link>
                ) : (
                  detail.poNumber || 'No PO'
                )}
              </Descriptions.Item>
              <Descriptions.Item label="Unit cost">{money(detail.unitCost)}</Descriptions.Item>
              <Descriptions.Item label="Total">
                {money(detail.lineTotal ?? detail.quantity * detail.unitCost)}
              </Descriptions.Item>
              <Descriptions.Item label="Ordered">
                {detail.dateOrdered ? formatShop(detail.dateOrdered, 'MMM D, YYYY') : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="Expected">
                {detail.currentExpectedDate ? formatShop(detail.currentExpectedDate, 'MMM D, YYYY') : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="Received">
                {detail.dateReceived ? formatShop(detail.dateReceived, 'MMM D, YYYY') : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="Days waiting">
                {daysInStock(detail.dateReceived) ?? '—'}
              </Descriptions.Item>
              <Descriptions.Item label="Consumed">
                {detail.consumedAt ? formatShop(detail.consumedAt, 'MMM D, YYYY') : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="Status">
                <StatusPill
                  color={
                    isConsumed(detail) ? 'gray' : isOnHand(detail) ? 'green' : (detail.daysOverdue || 0) > 0 ? 'red' : 'amber'
                  }
                  compact
                >
                  {isConsumed(detail) ? 'Consumed' : isOnHand(detail) ? 'On hand' : 'Incoming'}
                </StatusPill>
              </Descriptions.Item>
            </Descriptions>
            {canEdit && isIncoming(detail) && detail.jobOrderId && !detail.supplierOrderId ? (
              <Button style={{ marginTop: 16 }} onClick={() => markReceived(detail)}>
                Mark received
              </Button>
            ) : null}
          </div>
        ) : null}
      </Drawer>
    </div>
  );
}

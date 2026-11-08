import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Dayjs } from 'dayjs';
import { Button, DatePicker, Drawer, Form, Input, InputNumber, Select, message } from 'antd';
import type { TableColumnsType } from 'antd';
import { DownloadOutlined, PlusOutlined, ShoppingCartOutlined } from '@ant-design/icons';
import { BellRing, CalendarClock, CircleAlert, Package, Wallet } from 'lucide-react';
import { analyticsApi } from '../../api/analytics.api';
import { consumablesApi } from '../../api/consumables.api';
import { getErrorMessage } from '../../api/client';
import StatusPill from '../../components/StatusPill';
import { FilterBar, FormModal, TwoLineCell, rowOpenProps } from '../../components/ui';
import ResponsiveTable from '../../components/ui/ResponsiveTable';
import { MOBILE_SHELL_QUERY, NARROW_QUERY, useMediaQuery } from '../../hooks/useViewport';
import type { ConsumablePurchase, ConsumableReplenishment, ConsumableReport, ReplenishmentItem } from '../../types';
import { confirmAction } from '../../utils/confirmAction';
import { exportCsv } from '../../utils/csvExport';
import { formatShop, isFutureShopDay, shopToday } from '../../utils/shopTime';
import { CategoryTile, InventorySummaryCards, type InventorySummaryCard } from './inventoryLook';

const STATUS: Record<ReplenishmentItem['status'], { label: string; color: 'red' | 'amber' | 'green' | 'gray' }> = {
  OVERDUE: { label: 'Overdue', color: 'red' },
  DUE_SOON: { label: 'Due soon', color: 'amber' },
  OK: { label: 'OK', color: 'green' },
  LEARNING: { label: 'Too few purchases', color: 'gray' },
  NO_HISTORY: { label: 'Not bought yet', color: 'gray' },
};

const day = (iso?: string | null) => (iso ? formatShop(iso, 'MMM D, YYYY') : '—');
const num = (v?: number | null) =>
  v == null ? '—' : Number(v).toLocaleString('en-PH', { maximumFractionDigits: Math.abs(v) >= 10 ? 0 : 1 });
const peso = (v?: number | null) =>
  v == null ? '—' : `₱${Number(v).toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function whenNext(i: ReplenishmentItem) {
  if (i.daysUntilNext == null) return null;
  if (i.daysUntilNext < 0) return `${-i.daysUntilNext} day${i.daysUntilNext === -1 ? '' : 's'} late`;
  if (i.daysUntilNext === 0) return 'today';
  return `in ${i.daysUntilNext} day${i.daysUntilNext === 1 ? '' : 's'}`;
}

type RecordValues = {
  toolId: string;
  purchasedOn: Dayjs;
  quantity: number;
  unitCost?: number | null;
  boughtFrom?: string;
  note?: string;
};

/**
 * Consumables are not counted or scanned. The Office Staff record what the shop
 * buys (most of it without a purchase order), and the replenishment forecast
 * says when each item is usually bought next.
 */
export default function ConsumablesPanel({ canEdit }: { canEdit: boolean }) {
  const [data, setData] = useState<ConsumableReplenishment | null>(null);
  // Running-low reports from the shop floor, by item.
  const [reports, setReports] = useState<Record<string, ConsumableReport>>({});
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [summaryKey, setSummaryKey] = useState<string | null>(null);
  const [recordFor, setRecordFor] = useState<ReplenishmentItem | 'any' | null>(null);
  const [saving, setSaving] = useState(false);
  const [historyFor, setHistoryFor] = useState<ReplenishmentItem | null>(null);
  const [history, setHistory] = useState<ConsumablePurchase[] | null>(null);
  const [form] = Form.useForm<RecordValues>();
  const compact = useMediaQuery(MOBILE_SHELL_QUERY);
  const narrow = useMediaQuery(NARROW_QUERY);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [{ data: d }, { data: open }] = await Promise.all([
        analyticsApi.consumableReplenishment(),
        consumablesApi.reports(),
      ]);
      setData(d);
      setReports(Object.fromEntries(open.map((r) => [r.toolId, r])));
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const loadHistory = useCallback(async (item: ReplenishmentItem) => {
    setHistory(null);
    try {
      const { data: rows } = await consumablesApi.purchases(item.toolId);
      setHistory(rows);
    } catch (err) {
      message.error(getErrorMessage(err));
      setHistory([]);
    }
  }, []);

  useEffect(() => {
    if (historyFor) void loadHistory(historyFor);
  }, [historyFor, loadHistory]);

  const items = useMemo(() => data?.items ?? [], [data]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((i) => {
      if (q && !`${i.name} ${i.shopTerm || ''} ${i.sizeSpec || ''}`.toLowerCase().includes(q)) return false;
      if (summaryKey === 'reported' && !reports[i.toolId]) return false;
      if (summaryKey === 'overdue' && i.status !== 'OVERDUE') return false;
      if (summaryKey === 'due_soon' && i.status !== 'DUE_SOON') return false;
      if (summaryKey === 'ok' && i.status !== 'OK') return false;
      return true;
    }).sort((a, b) => Number(!!reports[b.toolId]) - Number(!!reports[a.toolId]));
  }, [items, query, summaryKey, reports]);

  const cards: InventorySummaryCard[] = useMemo(() => {
    const s = data?.summary;
    const reported = Object.keys(reports).length;
    return [
      { key: 'reported', label: 'Reported low', shortLabel: 'Reported', value: reported, icon: BellRing, tone: reported ? 'red' : 'slate', caption: 'By workers on the shop floor' },
      { key: 'overdue', label: 'Overdue to buy', shortLabel: 'Overdue', value: s?.overdue ?? 0, icon: CircleAlert, tone: 'red', caption: 'Past the usual purchase date' },
      { key: 'due_soon', label: 'Due soon', value: s?.dueSoon ?? 0, icon: CalendarClock, tone: 'amber', caption: `Within ${data?.method.dueSoonDays ?? 7} days` },
      { key: 'ok', label: 'OK', value: items.filter((i) => i.status === 'OK').length, icon: Package, tone: 'green', caption: `${s?.forecast ?? 0} items with a forecast` },
      { key: 'spent', label: 'Bought · last 30 days', shortLabel: 'Bought (30 d)', value: s?.purchasesLast30Days ?? 0, icon: Wallet, tone: 'slate', caption: `Purchases worth ${peso(s?.spentLast30Days ?? 0)}` },
    ];
  }, [data, items, reports]);

  const openRecord = (target: ReplenishmentItem | 'any') => {
    form.resetFields();
    form.setFieldsValue({
      toolId: target === 'any' ? undefined : target.toolId,
      purchasedOn: shopToday(),
      quantity: target === 'any' ? undefined : (target.usualQuantity ?? target.lastQuantity ?? undefined),
      unitCost: target === 'any' ? undefined : (target.lastUnitCost ?? undefined),
    } as Partial<RecordValues>);
    setRecordFor(target);
  };

  const onRecord = async (values: RecordValues) => {
    setSaving(true);
    try {
      await consumablesApi.record({
        toolId: values.toolId,
        purchasedOn: values.purchasedOn.format('YYYY-MM-DD'),
        quantity: values.quantity,
        unitCost: values.unitCost ?? null,
        boughtFrom: values.boughtFrom?.trim() || null,
        note: values.note?.trim() || null,
      });
      message.success('Purchase recorded');
      setRecordFor(null);
      await load();
      if (historyFor && historyFor.toolId === values.toolId) void loadHistory(historyFor);
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const dismissReport = (r: ConsumableReport) =>
    confirmAction({
      title: `Dismiss the report for ${r.name ?? 'this item'}?`,
      content: 'Use this when there is still enough on the shelf. Recording a purchase closes the report by itself.',
      okText: 'Dismiss',
      onOk: async () => {
        try {
          await consumablesApi.dismissReport(r.id);
          message.success('Report dismissed');
          await load();
        } catch (err) {
          message.error(getErrorMessage(err));
        }
      },
    });

  const removePurchase = (p: ConsumablePurchase) =>
    confirmAction({
      title: 'Remove this purchase?',
      content: `${num(p.quantity)} ${p.unit} bought ${day(p.purchasedOn)}. Use this only for an entry made by mistake.`,
      okText: 'Remove',
      danger: true,
      onOk: async () => {
        try {
          await consumablesApi.remove(p.id);
          message.success('Purchase removed');
          await load();
          if (historyFor) void loadHistory(historyFor);
        } catch (err) {
          message.error(getErrorMessage(err));
        }
      },
    });

  const selected = recordFor && recordFor !== 'any' ? recordFor : null;
  const pickedId = Form.useWatch('toolId', form);
  const picked = items.find((i) => i.toolId === pickedId) ?? selected;

  const itemCell = (i: ReplenishmentItem) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
      <CategoryTile category={i.name} />
      <TwoLineCell
        primary={i.name}
        secondary={[i.sizeSpec, i.shopTerm ? `“${i.shopTerm}”` : null].filter(Boolean).join(' · ') || null}
      />
    </div>
  );
  const reportPill = (r: ConsumableReport) => (
    <span
      title={`Reported by ${r.reportedByName ?? 'a worker'}${r.reportedAt ? `, ${formatShop(r.reportedAt, 'MMM D, h:mm A')}` : ''}`}
    >
      <StatusPill color={r.level === 'OUT' ? 'red' : 'amber'} compact>
        {r.level === 'OUT' ? 'Reported out' : 'Reported low'}
      </StatusPill>
    </span>
  );
  const statusCell = (i: ReplenishmentItem) => {
    const r = reports[i.toolId];
    return (
      <span style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'flex-start', gap: 4 }}>
        {r ? reportPill(r) : null}
        <StatusPill color={STATUS[i.status].color} compact>
          {STATUS[i.status].label}
        </StatusPill>
        {r ? (
          <span style={{ fontSize: 11, color: '#64748b' }}>
            by {r.reportedByName ?? 'a worker'}
            {r.reportedAt ? ` · ${formatShop(r.reportedAt, 'MMM D')}` : ''}
          </span>
        ) : null}
      </span>
    );
  };

  const columns: TableColumnsType<ReplenishmentItem> = [
    { title: 'Consumable', key: 'name', render: (_: unknown, i) => itemCell(i), sorter: (a, b) => a.name.localeCompare(b.name) },
    {
      title: 'Last bought',
      key: 'last',
      width: 160,
      render: (_: unknown, i) =>
        i.lastBoughtOn ? (
          <TwoLineCell primary={day(i.lastBoughtOn)} secondary={`${num(i.lastQuantity)} ${i.unit}`} />
        ) : (
          '—'
        ),
    },
    {
      title: 'Usually',
      key: 'every',
      width: 170,
      render: (_: unknown, i) =>
        i.everyDays != null ? (
          <TwoLineCell primary={`every ~${Math.round(i.everyDays)} days`} secondary={`about ${num(i.usualQuantity)} ${i.unit}`} />
        ) : (
          <span style={{ color: '#94a3b8' }}>
            {i.purchases ? `${i.purchases} of ${data?.method.minPurchases ?? 3} purchases` : 'No purchases yet'}
          </span>
        ),
    },
    {
      title: 'Next purchase',
      key: 'next',
      width: 170,
      sorter: (a, b) => (a.daysUntilNext ?? 1e6) - (b.daysUntilNext ?? 1e6),
      render: (_: unknown, i) =>
        i.nextPurchaseOn ? <TwoLineCell primary={day(i.nextPurchaseOn)} secondary={whenNext(i)} /> : '—',
    },
    { title: 'Status', key: 'status', width: 150, render: (_: unknown, i) => statusCell(i) },
    ...(canEdit
      ? [
          {
            title: '',
            key: 'actions',
            width: 120,
            render: (_: unknown, i: ReplenishmentItem) => (
              <span style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'flex-start', gap: 2 }}>
                <Button
                  size="small"
                  icon={<ShoppingCartOutlined />}
                  onClick={(e) => {
                    e.stopPropagation();
                    openRecord(i);
                  }}
                >
                  Bought
                </Button>
                {reports[i.toolId] ? (
                  <Button
                    size="small"
                    type="link"
                    style={{ paddingInline: 0 }}
                    onClick={(e) => {
                      e.stopPropagation();
                      dismissReport(reports[i.toolId]);
                    }}
                  >
                    Dismiss report
                  </Button>
                ) : null}
              </span>
            ),
          },
        ]
      : []),
  ];

  const cardColumns: TableColumnsType<ReplenishmentItem> = [
    { title: 'Consumable', key: 'name', render: (_: unknown, i) => itemCell(i) },
    { title: 'Next purchase', key: 'next', render: (_: unknown, i) => (i.nextPurchaseOn ? `${day(i.nextPurchaseOn)} (${whenNext(i)})` : '—') },
    { title: 'Last bought', key: 'last', render: (_: unknown, i) => (i.lastBoughtOn ? `${day(i.lastBoughtOn)} · ${num(i.lastQuantity)} ${i.unit}` : '—') },
    { title: 'Status', key: 'status', render: (_: unknown, i) => statusCell(i) },
  ];

  const exportRows = () =>
    exportCsv(`consumables-${data?.asOf ?? 'today'}`, filtered, [
      { key: 'n', header: 'Consumable', value: (r) => r.name },
      { key: 's', header: 'Size', value: (r) => r.sizeSpec ?? '' },
      { key: 'u', header: 'Unit', value: (r) => r.unit },
      { key: 'p', header: 'Purchases', value: (r) => r.purchases },
      { key: 'l', header: 'Last bought', value: (r) => r.lastBoughtOn ?? '' },
      { key: 'e', header: 'Every (days)', value: (r) => r.everyDays ?? '' },
      { key: 'q', header: 'Usual quantity', value: (r) => r.usualQuantity ?? '' },
      { key: 'x', header: 'Next purchase', value: (r) => r.nextPurchaseOn ?? '' },
      { key: 'st', header: 'Status', value: (r) => STATUS[r.status].label },
      {
        key: 'rp',
        header: 'Reported by workers',
        value: (r) => (reports[r.toolId] ? `${reports[r.toolId].level === 'OUT' ? 'Out' : 'Running low'} (${reports[r.toolId].reportedByName ?? ''})` : ''),
      },
    ]);

  return (
    <div className="std-list-page">
      <InventorySummaryCards
        cards={cards}
        activeKey={summaryKey}
        onSelect={(key) => setSummaryKey(key === 'spent' ? null : key)}
      />
      <FilterBar
        search={{ value: query, onChange: setQuery, placeholder: 'Search consumable…' }}
        actions={
          <>
            <Button icon={<DownloadOutlined />} disabled={!filtered.length} onClick={exportRows} aria-label="Export CSV">
              {compact ? null : 'Export CSV'}
            </Button>
            {canEdit ? (
              <Button type="primary" icon={<PlusOutlined />} onClick={() => openRecord('any')} style={{ fontWeight: 700 }}>
                {compact ? 'Record' : 'Record purchase'}
              </Button>
            ) : null}
          </>
        }
      />
      <ResponsiveTable
        className="std-list-table"
        rowKey="toolId"
        size="small"
        loading={loading}
        dataSource={filtered}
        columns={narrow ? cardColumns : columns}
        cardsBelow="narrow"
        card={{ title: 'name', badge: 'status', actions: 'none' }}
        onRow={(i) => rowOpenProps(() => setHistoryFor(i))}
        pagination={{ pageSize: 25, hideOnSinglePage: true }}
        scroll={narrow ? undefined : { x: 880 }}
        locale={{
          emptyText: items.length
            ? 'No consumable matches.'
            : 'No consumables yet. Add them on the Catalog tab.',
        }}
      />

      <FormModal
        open={recordFor !== null}
        icon={<ShoppingCartOutlined />}
        title="Record a purchase"
        subtitle={selected ? selected.name : 'A consumable the shop bought'}
        size="sm"
        onCancel={() => setRecordFor(null)}
        onOk={() => form.submit()}
        okText="Save"
        confirmLoading={saving}
        forceRender
      >
        <Form form={form} layout="vertical" onFinish={onRecord} requiredMark="optional" scrollToFirstError={{ block: 'center' }}>
          {recordFor === 'any' ? (
            <Form.Item name="toolId" label="Consumable" rules={[{ required: true, message: 'Pick the consumable' }]}>
              <Select
                showSearch
                optionFilterProp="label"
                placeholder="Pick a consumable"
                options={items.map((i) => ({
                  value: i.toolId,
                  label: [i.name, i.sizeSpec, i.shopTerm ? `“${i.shopTerm}”` : null].filter(Boolean).join(' · '),
                }))}
                onChange={(id: string) => {
                  const it = items.find((x) => x.toolId === id);
                  if (it) {
                    form.setFieldsValue({
                      quantity: it.usualQuantity ?? it.lastQuantity ?? undefined,
                      unitCost: it.lastUnitCost ?? undefined,
                    } as Partial<RecordValues>);
                  }
                }}
              />
            </Form.Item>
          ) : (
            <Form.Item name="toolId" hidden>
              <Input />
            </Form.Item>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '0 12px' }}>
            <Form.Item name="purchasedOn" label="Date bought" rules={[{ required: true, message: 'Pick the date' }]}>
              <DatePicker style={{ width: '100%' }} format="MMM D, YYYY" disabledDate={(d) => isFutureShopDay(d)} />
            </Form.Item>
            <Form.Item name="quantity" label="Quantity" rules={[{ required: true, message: 'Enter the quantity' }]}>
              <InputNumber min={0.01} style={{ width: '100%' }} addonAfter={picked?.unit ?? 'pcs'} />
            </Form.Item>
            <Form.Item name="unitCost" label="Unit cost (₱)">
              <InputNumber min={0} step={0.01} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="boughtFrom" label="Bought from">
              <Input maxLength={120} placeholder="e.g. hardware store" />
            </Form.Item>
          </div>
          <Form.Item name="note" label="Note" style={{ marginBottom: 4 }}>
            <Input maxLength={255} />
          </Form.Item>
        </Form>
      </FormModal>

      <Drawer
        open={historyFor !== null}
        onClose={() => setHistoryFor(null)}
        title={historyFor ? historyFor.name : 'Purchases'}
        width={compact ? '100%' : 480}
        extra={
          canEdit && historyFor ? (
            <Button type="primary" size="small" icon={<ShoppingCartOutlined />} onClick={() => openRecord(historyFor)}>
              Record purchase
            </Button>
          ) : null
        }
      >
        {historyFor ? (
          <div style={{ display: 'grid', gap: 12 }}>
            <div style={{ fontSize: 13, color: '#475569' }}>
              {historyFor.everyDays != null
                ? `Usually bought every ~${Math.round(historyFor.everyDays)} days, about ${num(historyFor.usualQuantity)} ${historyFor.unit} at a time. Next: ${day(historyFor.nextPurchaseOn)} (${whenNext(historyFor)}).`
                : `A forecast appears after ${data?.method.minPurchases ?? 3} purchases.`}
            </div>
            {history == null ? (
              <div style={{ color: '#94a3b8' }}>Loading…</div>
            ) : history.length === 0 ? (
              <div style={{ color: '#94a3b8' }}>No purchases recorded yet.</div>
            ) : (
              history.map((p) => (
                <div
                  key={`${p.source}-${p.id}`}
                  style={{ border: '1px solid var(--c-border)', borderRadius: 8, padding: '10px 12px', display: 'grid', gap: 2 }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <strong>{day(p.purchasedOn)}</strong>
                    <span>
                      {num(p.quantity)} {p.unit}
                      {p.amount != null ? ` · ${peso(p.amount)}` : ''}
                    </span>
                  </div>
                  <div style={{ fontSize: 12, color: '#64748b' }}>
                    {[
                      p.boughtFrom,
                      p.poNumber ? `PO ${p.poNumber}` : null,
                      p.recordedBy ? `recorded by ${p.recordedBy}` : null,
                      p.note,
                    ]
                      .filter(Boolean)
                      .join(' · ') || '—'}
                  </div>
                  {canEdit && p.source === 'RECORDED' ? (
                    <div>
                      <Button type="link" size="small" danger style={{ padding: 0 }} onClick={() => removePurchase(p)}>
                        Remove
                      </Button>
                    </div>
                  ) : null}
                </div>
              ))
            )}
          </div>
        ) : null}
      </Drawer>
    </div>
  );
}


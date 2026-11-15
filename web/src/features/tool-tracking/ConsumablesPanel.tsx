import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Dayjs } from 'dayjs';
import { Button, DatePicker, Drawer, Form, Input, InputNumber, Select, Space, message } from 'antd';
import type { TableColumnsType } from 'antd';
import { DownloadOutlined, ExportOutlined, NumberOutlined, PlusOutlined, ShoppingCartOutlined } from '@ant-design/icons';
import { BellRing, CalendarClock, CircleAlert, Package, Wallet } from 'lucide-react';
import { analyticsApi } from '../../api/analytics.api';
import { CONSUMABLE_UNITS } from '../../api/catalog.api';
import { consumablesApi } from '../../api/consumables.api';
import { toolsApi } from '../../api/tools.api';
import { getErrorMessage } from '../../api/client';
import StatusPill from '../../components/StatusPill';
import { FilterBar, FormModal, TwoLineCell, rowOpenProps } from '../../components/ui';
import { RowMenu } from '../../components/ui/DataTable';
import ResponsiveTable from '../../components/ui/ResponsiveTable';
import { MOBILE_SHELL_QUERY, NARROW_QUERY, useMediaQuery } from '../../hooks/useViewport';
import type {
  ConsumableIssue,
  ConsumableIssueOptions,
  ConsumableIssueWorker,
  ConsumablePurchase,
  ConsumableReplenishment,
  ConsumableReport,
  ReplenishmentItem,
} from '../../types';
import { confirmAction } from '../../utils/confirmAction';
import { exportCsv } from '../../utils/csvExport';
import { formatShop, isFutureShopDay, shopToday } from '../../utils/shopTime';
import { CategoryTile, InventorySummaryCards, type InventorySummaryCard } from './inventoryLook';

const STATUS: Record<ReplenishmentItem['status'], { label: string; color: 'red' | 'amber' | 'green' | 'gray' }> = {
  OVERDUE: { label: 'Reorder now', color: 'red' },
  DUE_SOON: { label: 'Reorder soon', color: 'amber' },
  OK: { label: 'OK', color: 'green' },
  LEARNING: { label: 'Too little history', color: 'gray' },
  NO_HISTORY: { label: 'No history yet', color: 'gray' },
};

const day = (iso?: string | null) => (iso ? formatShop(iso, 'MMM D, YYYY') : '—');
const num = (v?: number | null) =>
  v == null ? '—' : Number(v).toLocaleString('en-PH', { maximumFractionDigits: Math.abs(v) >= 10 ? 0 : 1 });
const peso = (v?: number | null) =>
  v == null ? '—' : `₱${Number(v).toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/** "uses ~2 pcs a day", or for slow items "uses ~1 can every 30 days". */
function rateText(i: ReplenishmentItem) {
  const d = i.dailyUse ?? 0;
  if (d <= 0) return 'not used lately';
  const every = Math.round(1 / d);
  return d >= 1 || every <= 1 ? `uses ~${num(Math.max(1, Math.round(d)))} ${i.unit} a day` : `uses ~1 ${i.unit} every ${every} days`;
}

function whenNext(i: ReplenishmentItem) {
  if (i.daysUntilNext == null) return null;
  // From stock on hand: at or below the reorder point means reorder now.
  if (i.basis === 'USAGE' && i.daysUntilNext < 0) return 'at the reorder point';
  if (i.daysUntilNext < 0) return `${-i.daysUntilNext} day${i.daysUntilNext === -1 ? '' : 's'} late`;
  if (i.daysUntilNext === 0) return 'today';
  return `in ${i.daysUntilNext} day${i.daysUntilNext === 1 ? '' : 's'}`;
}

type RecordValues = {
  toolId: string;
  purchasedOn: Dayjs;
  quantity: number;
  unit?: string;
  unitCost?: number | null;
  boughtFrom?: string;
  note?: string;
};

type IssueValues = { toolId: string; workerId: string; operationId?: string; quantity: number };

const NO_OPERATION = '__none__';

/**
 * The Office Staff keep the consumables. They record what the shop buys (which
 * adds to the stock on hand) and hand items to workers for the operation they
 * are on (which takes from it). The replenishment forecast reads the stock on
 * hand and how fast each item is handed out.
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
  // Hand a consumable to a worker.
  const [issueOpen, setIssueOpen] = useState(false);
  const [issueForm] = Form.useForm<IssueValues>();
  const [workers, setWorkers] = useState<ConsumableIssueWorker[]>([]);
  const [options, setOptions] = useState<ConsumableIssueOptions | null>(null);
  const [issuing, setIssuing] = useState(false);
  const [handOuts, setHandOuts] = useState<ConsumableIssue[] | null>(null);
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
    setHandOuts(null);
    try {
      const [{ data: rows }, { data: given }] = await Promise.all([
        consumablesApi.purchases(item.toolId),
        consumablesApi.issues({ toolId: item.toolId, limit: 50 }),
      ]);
      setHistory(rows);
      setHandOuts(given);
    } catch (err) {
      message.error(getErrorMessage(err));
      setHistory([]);
      setHandOuts([]);
    }
  }, []);

  const openIssue = (target?: ReplenishmentItem) => {
    issueForm.resetFields();
    issueForm.setFieldsValue({ toolId: target?.toolId, quantity: 1 } as Partial<IssueValues>);
    setOptions(null);
    setIssueOpen(true);
    consumablesApi
      .issueWorkers()
      .then(({ data: rows }) => setWorkers(rows))
      .catch((err) => message.error(getErrorMessage(err)));
  };

  /** The worker's open operations; the one they are on now is picked for them. */
  const pickWorker = async (workerId: string) => {
    setOptions(null);
    issueForm.setFieldsValue({ operationId: undefined });
    try {
      const { data: o } = await consumablesApi.issueOptions(workerId);
      setOptions(o);
      issueForm.setFieldsValue({ operationId: o.currentOperationId ?? o.operations[0]?.operationId ?? NO_OPERATION });
    } catch (err) {
      message.error(getErrorMessage(err));
    }
  };

  const onIssue = async (values: IssueValues) => {
    setIssuing(true);
    try {
      const { data: row } = await consumablesApi.issue({
        toolId: values.toolId,
        workerId: values.workerId,
        quantity: values.quantity,
        operationId: values.operationId && values.operationId !== NO_OPERATION ? values.operationId : null,
      });
      message.success(
        `Handed ${row.quantity} ${row.unit ?? ''} of ${row.name} to ${row.workerName}. ${row.onHandAfter ?? 0} left.`
      );
      setIssueOpen(false);
      await load();
      if (historyFor && historyFor.toolId === values.toolId) void loadHistory(historyFor);
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setIssuing(false);
    }
  };

  /** Set the stock on hand to what is actually on the shelf (first count, or a recount). */
  const setCount = (item: ReplenishmentItem) => {
    let count: number | null = item.onHand ?? 0;
    confirmAction({
      title: `Count of ${item.name}`,
      content: (
        <div style={{ marginTop: 8 }}>
          <div style={{ marginBottom: 6, fontSize: 13, color: '#475569' }}>
            How many are on the shelf now? The stock on hand is set to this number.
          </div>
          <InputNumber
            min={0}
            defaultValue={count ?? 0}
            style={{ width: '100%' }}
            addonAfter={item.unit}
            onChange={(v) => {
              count = typeof v === 'number' ? v : null;
            }}
          />
        </div>
      ),
      okText: 'Save count',
      onOk: async () => {
        if (count == null) {
          message.warning('Enter the count.');
          throw new Error('count required');
        }
        try {
          await toolsApi.correct(item.toolId, { actualCount: count, reason: 'Stock count' });
          message.success('Count saved');
          setHistoryFor((prev) => (prev && prev.toolId === item.toolId ? { ...prev, onHand: count ?? 0 } : prev));
          await load();
        } catch (err) {
          message.error(getErrorMessage(err));
          throw err;
        }
      },
    });
  };

  const undoHandOut = (u: ConsumableIssue) =>
    confirmAction({
      title: 'Undo this hand-out?',
      content: `${num(u.quantity)} ${u.unit ?? ''} to ${u.workerName ?? 'the worker'} goes back to the stock on hand. Use this only for an entry made by mistake.`,
      okText: 'Undo',
      danger: true,
      onOk: async () => {
        try {
          await consumablesApi.undoIssue(u.id);
          message.success('Hand-out undone');
          await load();
          if (historyFor) void loadHistory(historyFor);
        } catch (err) {
          message.error(getErrorMessage(err));
        }
      },
    });

  useEffect(() => {
    if (historyFor) void loadHistory(historyFor);
  }, [historyFor, loadHistory]);

  const items = useMemo(() => data?.items ?? [], [data]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((i) => {
      if (q && !`${i.name} ${i.shopTerm || ''} ${i.sizeSpec || ''}`.toLowerCase().includes(q)) return false;
      if (summaryKey === 'reported' && !reports[i.toolId]) return false;
      if (summaryKey === 'out' && (i.onHand ?? 0) > 0) return false;
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
      // Older reports from the shop floor still open (workers no longer report).
      ...(reported
        ? [{ key: 'reported', label: 'Reported low', shortLabel: 'Reported', value: reported, icon: BellRing, tone: 'red' as const, caption: 'Earlier reports still open' }]
        : [{ key: 'out', label: 'None on hand', shortLabel: 'None left', value: items.filter((i) => (i.onHand ?? 0) <= 0).length, icon: BellRing, tone: 'slate' as const, caption: 'Nothing left to hand out' }]),
      { key: 'overdue', label: 'Reorder now', shortLabel: 'Reorder', value: s?.overdue ?? 0, icon: CircleAlert, tone: 'red', caption: 'At or below the reorder point' },
      { key: 'due_soon', label: 'Reorder soon', shortLabel: 'Soon', value: s?.dueSoon ?? 0, icon: CalendarClock, tone: 'amber', caption: `Within ${data?.method.dueSoonDays ?? 7} days` },
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
      unit: target === 'any' ? undefined : target.unit,
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
        unit: values.unit,
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
  const issueToolId = Form.useWatch('toolId', issueForm);
  const picked = items.find((i) => i.toolId === pickedId) ?? selected;
  const recordUnit = Form.useWatch('unit', form);

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
      <span style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'flex-start', gap: 4, maxWidth: '100%' }}>
        {r ? (
          <>
            {reportPill(r)}
            {/* A worker's report outranks the forecast, so the forecast pill is left out here
                (the Next purchase column still shows the forecast date). */}
            <span style={{ fontSize: 11, lineHeight: 1.35, color: '#64748b', maxWidth: '100%' }}>
              <span style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                by {r.reportedByName ?? 'a worker'}
              </span>
              {r.reportedAt ? (
                <span style={{ display: 'block', whiteSpace: 'nowrap' }}>{formatShop(r.reportedAt, 'MMM D, h:mm A')}</span>
              ) : null}
            </span>
          </>
        ) : (
          <StatusPill color={STATUS[i.status].color} compact>
            {STATUS[i.status].label}
          </StatusPill>
        )}
      </span>
    );
  };

  const columns: TableColumnsType<ReplenishmentItem> = [
    { title: 'Consumable', key: 'name', width: 180, render: (_: unknown, i) => itemCell(i), sorter: (a, b) => a.name.localeCompare(b.name) },
    {
      title: 'On hand',
      key: 'onHand',
      width: 90,
      sorter: (a, b) => (a.onHand ?? 0) - (b.onHand ?? 0),
      render: (_: unknown, i) => (
        <span style={{ fontWeight: 700, color: (i.onHand ?? 0) > 0 ? undefined : '#b91c1c' }}>
          {num(i.onHand ?? 0)} {i.unit}
        </span>
      ),
    },
    {
      title: 'Last bought',
      key: 'last',
      width: 118,
      render: (_: unknown, i) =>
        i.lastBoughtOn ? (
          <TwoLineCell primary={day(i.lastBoughtOn)} secondary={`${num(i.lastQuantity)} ${i.unit}`} />
        ) : (
          '—'
        ),
    },
    {
      title: 'Usage',
      key: 'every',
      width: 196,
      render: (_: unknown, i) =>
        i.basis === 'USAGE' && i.dailyUse != null ? (
          <TwoLineCell
            primary={rateText(i)}
            secondary={
              (i.onHand ?? 0) <= 0 ? 'none left' : i.runOutOn ? `runs out ${day(i.runOutOn)}` : null
            }
          />
        ) : i.everyDays != null ? (
          <TwoLineCell primary={`bought every ~${Math.round(i.everyDays)} days`} secondary={`about ${num(i.usualQuantity)} ${i.unit}`} />
        ) : (
          <span style={{ color: '#94a3b8' }}>
            {i.purchases ? `${i.purchases} of ${data?.method.minPurchases ?? 3} purchases` : 'No purchases yet'}
          </span>
        ),
    },
    {
      title: 'Reorder by',
      key: 'next',
      width: 124,
      sorter: (a, b) => (a.daysUntilNext ?? 1e6) - (b.daysUntilNext ?? 1e6),
      render: (_: unknown, i) =>
        i.nextPurchaseOn ? (
          <TwoLineCell
            primary={i.basis === 'USAGE' && (i.daysUntilNext ?? 0) < 0 ? 'Now' : day(i.nextPurchaseOn)}
            secondary={whenNext(i)}
          />
        ) : (
          '—'
        ),
    },
    { title: 'Status', key: 'status', width: 112, render: (_: unknown, i) => statusCell(i) },
    ...(canEdit
      ? [
          {
            title: '',
            key: 'actions',
            width: 132,
            render: (_: unknown, i: ReplenishmentItem) => (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap' }}>
                <Button
                  size="small"
                  icon={<ExportOutlined />}
                  disabled={(i.onHand ?? 0) <= 0}
                  title={(i.onHand ?? 0) <= 0 ? 'None on hand to hand out' : undefined}
                  onClick={(e) => {
                    e.stopPropagation();
                    openIssue(i);
                  }}
                >
                  Hand out
                </Button>
                <RowMenu
                  items={[
                    {
                      key: 'bought',
                      icon: <ShoppingCartOutlined />,
                      label: 'Record purchase',
                      onClick: ({ domEvent }) => {
                        domEvent.stopPropagation();
                        openRecord(i);
                      },
                    },
                    {
                      key: 'count',
                      icon: <NumberOutlined />,
                      label: 'Set count',
                      onClick: ({ domEvent }) => {
                        domEvent.stopPropagation();
                        setCount(i);
                      },
                    },
                    ...(reports[i.toolId]
                      ? [
                          {
                            key: 'dismiss',
                            label: 'Dismiss report',
                            onClick: ({ domEvent }: { domEvent: { stopPropagation: () => void } }) => {
                              domEvent.stopPropagation();
                              dismissReport(reports[i.toolId]);
                            },
                          },
                        ]
                      : []),
                  ]}
                />
              </span>
            ),
          },
        ]
      : []),
  ];

  const cardColumns: TableColumnsType<ReplenishmentItem> = [
    { title: 'Consumable', key: 'name', render: (_: unknown, i) => itemCell(i) },
    { title: 'On hand', key: 'onHand', render: (_: unknown, i) => `${num(i.onHand ?? 0)} ${i.unit}` },
    { title: 'Reorder by', key: 'next', render: (_: unknown, i) => (i.nextPurchaseOn ? `${day(i.nextPurchaseOn)} (${whenNext(i)})` : '—') },
    { title: 'Last bought', key: 'last', render: (_: unknown, i) => (i.lastBoughtOn ? `${day(i.lastBoughtOn)} · ${num(i.lastQuantity)} ${i.unit}` : '—') },
    { title: 'Status', key: 'status', render: (_: unknown, i) => statusCell(i) },
  ];

  const exportRows = () =>
    exportCsv(`consumables-${data?.asOf ?? 'today'}`, filtered, [
      { key: 'n', header: 'Consumable', value: (r) => r.name },
      { key: 's', header: 'Size', value: (r) => r.sizeSpec ?? '' },
      { key: 'u', header: 'Unit', value: (r) => r.unit },
      { key: 'oh', header: 'On hand', value: (r) => r.onHand ?? 0 },
      { key: 'du', header: 'Use per day', value: (r) => r.dailyUse ?? '' },
      { key: 'ro', header: 'Runs out', value: (r) => r.runOutOn ?? '' },
      { key: 'p', header: 'Purchases', value: (r) => r.purchases },
      { key: 'l', header: 'Last bought', value: (r) => r.lastBoughtOn ?? '' },
      { key: 'e', header: 'Every (days)', value: (r) => r.everyDays ?? '' },
      { key: 'q', header: 'Suggested quantity', value: (r) => r.usualQuantity ?? '' },
      { key: 'x', header: 'Reorder by', value: (r) => r.nextPurchaseOn ?? '' },
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
              <Button icon={<PlusOutlined />} onClick={() => openRecord('any')}>
                {compact ? 'Record' : 'Record purchase'}
              </Button>
            ) : null}
            {canEdit ? (
              <Button type="primary" icon={<ExportOutlined />} onClick={() => openIssue()} style={{ fontWeight: 700 }}>
                {compact ? 'Hand out' : 'Hand out to worker'}
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
        scroll={narrow ? undefined : { x: 960 }}
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
                      unit: it.unit,
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
            <Form.Item
              label="Quantity"
              required
              extra={
                picked && recordUnit && recordUnit !== picked.unit
                  ? `${picked.name} will be counted in ${recordUnit} from now on.`
                  : undefined
              }
            >
              <Space.Compact style={{ width: '100%' }}>
                <Form.Item name="quantity" noStyle rules={[{ required: true, message: 'Enter the quantity' }]}>
                  <InputNumber min={0.01} style={{ width: '100%' }} />
                </Form.Item>
                <Form.Item name="unit" noStyle>
                  <Select
                    aria-label="Unit"
                    style={{ width: 96 }}
                    popupMatchSelectWidth={false}
                    placeholder="unit"
                    options={[...new Set([...(picked?.unit ? [picked.unit] : []), ...CONSUMABLE_UNITS])].map((u) => ({
                      value: u,
                      label: u,
                    }))}
                  />
                </Form.Item>
              </Space.Compact>
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

      <FormModal
        open={issueOpen}
        icon={<ExportOutlined />}
        title="Hand out a consumable"
        subtitle="To a worker, for the operation they are on"
        size="sm"
        onCancel={() => setIssueOpen(false)}
        onOk={() => issueForm.submit()}
        okText="Hand out"
        confirmLoading={issuing}
        forceRender
      >
        <Form form={issueForm} layout="vertical" onFinish={onIssue} requiredMark="optional" scrollToFirstError={{ block: 'center' }}>
          <Form.Item name="toolId" label="Consumable" rules={[{ required: true, message: 'Pick the consumable' }]}>
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="Pick a consumable"
              options={items.map((i) => ({
                value: i.toolId,
                disabled: (i.onHand ?? 0) <= 0,
                label: `${[i.name, i.sizeSpec].filter(Boolean).join(' · ')} — ${num(i.onHand ?? 0)} ${i.unit} on hand`,
              }))}
            />
          </Form.Item>
          <Form.Item name="workerId" label="Worker" rules={[{ required: true, message: 'Pick the worker' }]}>
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="Who is it for?"
              onChange={(id: string) => void pickWorker(id)}
              options={workers.map((w) => ({
                value: w.workerId,
                label: w.currentOperation ? `${w.name} — on ${w.currentOperation}` : w.name,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="operationId"
            label="Operation"
            extra={
              options && !options.currentOperationId
                ? 'This worker has no operation running right now. Pick the one it is for.'
                : undefined
            }
            rules={[{ required: true, message: 'Pick the operation it is for' }]}
          >
            <Select
              placeholder={options ? 'Pick the operation' : 'Pick the worker first'}
              disabled={!options}
              options={[
                ...(options?.operations ?? []).map((o) => ({
                  value: o.operationId,
                  label: `${o.operationName ?? 'Operation'} (${o.jobNumber ?? 'job'})${o.running ? ' — working on it now' : ''}`,
                })),
                { value: NO_OPERATION, label: 'Not for a particular operation' },
              ]}
            />
          </Form.Item>
          <Form.Item name="quantity" label="Quantity" rules={[{ required: true, message: 'Enter the quantity' }]} style={{ marginBottom: 4 }}>
            <InputNumber
              min={0.01}
              style={{ width: '100%' }}
              addonAfter={items.find((i) => i.toolId === issueToolId)?.unit}
            />
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
            <span style={{ display: 'inline-flex', gap: 8 }}>
              <Button size="small" onClick={() => setCount(historyFor)}>
                Set count
              </Button>
              <Button type="primary" size="small" icon={<ShoppingCartOutlined />} onClick={() => openRecord(historyFor)}>
                Record purchase
              </Button>
            </span>
          ) : null
        }
      >
        {historyFor ? (
          <div style={{ display: 'grid', gap: 12 }}>
            <div style={{ fontSize: 13, color: '#475569' }}>
              <strong>
                {num(historyFor.onHand ?? 0)} {historyFor.unit} on hand.
              </strong>{' '}
              {historyFor.basis === 'USAGE' && historyFor.dailyUse != null
                ? `It ${rateText(historyFor)}, so it runs out around ${day(historyFor.runOutOn)}. Reorder point: ${num(historyFor.reorderPoint)} ${historyFor.unit} (${num(historyFor.leadDays)} days for delivery plus ${num(historyFor.safetyDays)} safety days). Reorder ${(historyFor.daysUntilNext ?? 0) < 0 ? 'now' : `by ${day(historyFor.nextPurchaseOn)}`}; suggested order ${num(historyFor.usualQuantity)} ${historyFor.unit}.`
                : historyFor.everyDays != null
                  ? `Not enough hand-outs yet, so this goes by purchases: usually bought every ~${Math.round(historyFor.everyDays)} days, about ${num(historyFor.usualQuantity)} ${historyFor.unit} at a time. Next: ${day(historyFor.nextPurchaseOn)} (${whenNext(historyFor)}).`
                  : `A forecast appears after ${data?.method.minPurchases ?? 3} days with hand-outs, or ${data?.method.minPurchases ?? 3} purchases.`}
            </div>
            <div style={{ fontWeight: 700, fontSize: 13 }}>Handed out</div>
            {handOuts == null ? (
              <div style={{ color: '#94a3b8' }}>Loading…</div>
            ) : handOuts.length === 0 ? (
              <div style={{ color: '#94a3b8' }}>Nothing handed out yet.</div>
            ) : (
              handOuts.map((u) => (
                <div
                  key={u.id}
                  style={{
                    border: '1px solid var(--c-border)',
                    borderRadius: 8,
                    padding: '10px 12px',
                    display: 'grid',
                    gap: 2,
                    opacity: u.undoneAt ? 0.55 : 1,
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <strong>{u.workerName ?? 'Worker'}</strong>
                    <span>
                      {num(u.quantity)} {u.unit}
                      {u.undoneAt ? ' · undone' : ''}
                    </span>
                  </div>
                  <div style={{ fontSize: 12, color: '#64748b' }}>
                    {u.operationName ? `${u.operationName}${u.jobNumber ? ` (${u.jobNumber})` : ''}` : 'Not for a particular operation'}
                    {u.issuedAt ? ` · ${formatShop(u.issuedAt, 'MMM D, h:mm A')}` : ''}
                    {u.issuedByName ? ` · by ${u.issuedByName}` : ''}
                  </div>
                  {canEdit && !u.undoneAt ? (
                    <div>
                      <Button size="small" type="link" style={{ paddingInline: 0 }} onClick={() => undoHandOut(u)}>
                        Undo
                      </Button>
                    </div>
                  ) : null}
                </div>
              ))
            )}
            <div style={{ fontWeight: 700, fontSize: 13 }}>Purchases</div>
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


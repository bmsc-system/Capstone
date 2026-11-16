import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, DatePicker, message } from 'antd';
import type { TableColumnsType } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import dayjs, { type Dayjs } from 'dayjs';
import { supplierOrdersApi } from '../../api/supplierOrders.api';
import { inventoryApi } from '../../api/tools.api';
import { getErrorMessage } from '../../api/client';
import StatusPill from '../../components/StatusPill';
import OverdueTag from '../../components/OverdueTag';
import { FilterBar, FilterSelect, IdChip, TabBar, rowOpenProps } from '../../components/ui';
import { useAuth } from '../../hooks/useAuth';
import { useOverdueCheck } from '../../hooks/useOverdueCheck';
import { NARROW_QUERY, PHONE_QUERY, useMediaQuery } from '../../hooks/useViewport';
import type { SupplierOrder, SupplierOrderStatus } from '../../types';
import OrderMaterialsModal from './OrderMaterialsModal';
import { ORDER_STATUS_PILL, fmtDay, fmtMoney } from './supplierOrderUi';
import ResponsiveTable from '../../components/ui/ResponsiveTable';
import { newestFirst } from '../../utils/newestFirst';

type Filter = 'ALL' | 'OVERDUE' | SupplierOrderStatus;
type PrintedFilter = 'printed' | 'not';

const FILTERS: { label: string; value: Filter }[] = [
  { label: 'All', value: 'ALL' },
  { label: 'Overdue', value: 'OVERDUE' },
  { label: 'Draft', value: 'DRAFT' },
  { label: 'Issued', value: 'ISSUED' },
  { label: 'Received', value: 'RECEIVED' },
  { label: 'Cancelled', value: 'CANCELLED' },
];

const dayValue = (v?: string | null) => (v ? dayjs(v).valueOf() : Number.POSITIVE_INFINITY);

export default function SupplierOrdersPage() {
  const navigate = useNavigate();
  const { canOfficeWrite, isAdmin, user } = useAuth();
  const cards = useMediaQuery(NARROW_QUERY);
  const phone = useMediaQuery(PHONE_QUERY);
  const [rows, setRows] = useState<SupplierOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Filter>('ALL');
  const [newOpen, setNewOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [supplierFilter, setSupplierFilter] = useState<string | undefined>();
  const [printedFilter, setPrintedFilter] = useState<PrintedFilter | undefined>();
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  // Office Staff open their own drafts first; every order stays one click away.
  const officeOnly = canOfficeWrite && !isAdmin;
  const [preparedFilter, setPreparedFilter] = useState<'mine' | undefined>();
  useEffect(() => {
    if (officeOnly) setPreparedFilter(filter === 'DRAFT' ? 'mine' : undefined);
  }, [filter, officeOnly]);
  const [jobsByOrder, setJobsByOrder] = useState<Record<string, string[]>>({});
  useOverdueCheck();

  const [all, setAll] = useState<SupplierOrder[] | null>(null);

  const fetchRows = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await supplierOrdersApi.list(
        filter === 'ALL' ? undefined : { status: filter }
      );
      setRows(newestFirst(data));
      if (filter === 'ALL') setAll(data);
      else supplierOrdersApi.list().then((res) => setAll(res.data)).catch(() => undefined);
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    inventoryApi
      .materialPurchases()
      .then(({ data }) => {
        const map: Record<string, string[]> = {};
        for (const line of data.items) {
          if (!line.supplierOrderId || !line.jobNumber) continue;
          const list = (map[line.supplierOrderId] ||= []);
          if (!list.includes(line.jobNumber)) list.push(line.jobNumber);
        }
        setJobsByOrder(map);
      })
      .catch(() => undefined);
  }, []);

  const counts = useMemo(() => {
    if (!all) return null;
    const c: Record<string, number> = { ALL: all.length, OVERDUE: 0 };
    for (const o of all) {
      c[o.status] = (c[o.status] || 0) + 1;
      if (o.daysOverdue && o.daysOverdue > 0) c.OVERDUE += 1;
    }
    return c;
  }, [all]);

  const supplierOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const o of all || rows) seen.set(o.supplierId, o.supplierName || 'Unknown supplier');
    return [...seen.entries()]
      .map(([value, label]) => ({ value, label }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [all, rows]);

  const activeFilters =
    (supplierFilter ? 1 : 0) + (printedFilter ? 1 : 0) + (range ? 1 : 0) + (preparedFilter ? 1 : 0);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    const from = range?.[0].startOf('day').valueOf();
    const to = range?.[1].endOf('day').valueOf();
    return rows.filter((o) => {
      if (supplierFilter && o.supplierId !== supplierFilter) return false;
      if (preparedFilter === 'mine' && user && o.preparedById !== user.id) return false;
      if (printedFilter === 'printed' && !o.printedAt) return false;
      if (printedFilter === 'not' && o.printedAt) return false;
      if (from != null && to != null) {
        const d = o.dateIssued || o.createdAt;
        const t = d ? dayjs(d).valueOf() : NaN;
        if (!(t >= from && t <= to)) return false;
      }
      if (!q) return true;
      const hay = [o.poNumber, o.supplierName, ...(jobsByOrder[o.id] || [])].filter(Boolean).join(' ').toLowerCase();
      return hay.includes(q);
    });
  }, [rows, search, supplierFilter, printedFilter, range, jobsByOrder, preparedFilter, user]);

  useEffect(() => {
    void fetchRows();
  }, [fetchRows]);

  const columns: TableColumnsType<SupplierOrder> = [
    {
      title: 'PO #',
      key: 'po',
      width: 150,
      render: (_: unknown, r) =>
        r.poNumber ? <IdChip>{r.poNumber}</IdChip> : <span className="tone-text--slate">Not issued</span>,
    },
    { title: 'Supplier', dataIndex: 'supplierName', width: 200, ellipsis: true },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 130,
      render: (v: SupplierOrderStatus, row: SupplierOrder) =>
        row.awaitingApproval ? (
          <StatusPill color="amber" compact>
            Awaiting approval
          </StatusPill>
        ) : (
          <StatusPill color={ORDER_STATUS_PILL[v].color} compact>
            {ORDER_STATUS_PILL[v].label}
          </StatusPill>
        ),
    },
    {
      title: 'Lines',
      key: 'lines',
      width: 150,
      render: (_: unknown, r) => {
        if (cards) return `${r.lineCount} line${r.lineCount === 1 ? '' : 's'}`;
        const parts = [];
        if (r.jobCount) parts.push(`${r.jobCount} job${r.jobCount === 1 ? '' : 's'}`);
        if (r.consumableLineCount) parts.push(`${r.consumableLineCount} consumable`);
        return `${r.lineCount}${parts.length ? ` (${parts.join(', ')})` : ''}`;
      },
    },
    {
      title: 'Issued',
      dataIndex: 'dateIssued',
      width: 120,
      sorter: (a, b) => dayValue(a.dateIssued) - dayValue(b.dateIssued),
      render: (v) => fmtDay(v),
    },
    {
      title: 'Expected',
      dataIndex: 'expectedDeliveryDate',
      width: 150,
      sorter: (a, b) => dayValue(a.expectedDeliveryDate) - dayValue(b.expectedDeliveryDate),
      render: (v, r) => (
        <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          {fmtDay(v)}
          <OverdueTag days={r.daysOverdue} tooltip="Not received by the expected date. Follow up with the supplier." />
        </span>
      ),
    },
    {
      title: 'Subtotal',
      dataIndex: 'subtotal',
      width: 130,
      align: 'right',
      sorter: (a, b) => a.subtotal - b.subtotal,
      render: (v: number) => fmtMoney(v),
    },
    { title: 'Prepared by', dataIndex: 'preparedByName', width: 140, ellipsis: true },
  ];

  const newButton = canOfficeWrite ? (
    <Button type="primary" icon={<PlusOutlined />} onClick={() => setNewOpen(true)}>
      Create supplier order
    </Button>
  ) : null;

  return (
    <div>
      <div
        className="so-list-top"
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 12,
          marginBottom: 12,
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <TabBar<Filter>
          value={filter}
          onChange={setFilter}
          items={FILTERS.map((f) => ({
            ...f,
            count: counts ? counts[f.value] || 0 : null,
            countTone: f.value === 'OVERDUE' && counts?.OVERDUE ? 'red' : undefined,
          }))}
        />
        {phone ? null : newButton}
      </div>

      <FilterBar
        search={{ value: search, onChange: setSearch, placeholder: 'Search PO #, supplier, job #…' }}
        activeFilters={activeFilters}
        onClearFilters={() => {
          setSupplierFilter(undefined);
          setPrintedFilter(undefined);
          setRange(null);
          setPreparedFilter(undefined);
        }}
        filters={
          <>
            <FilterSelect<string>
              label="Supplier"
              allowClear
              showSearch
              optionFilterProp="label"
              value={supplierFilter}
              onChange={(v) => setSupplierFilter(v)}
              options={supplierOptions}
            />
            {officeOnly && (
              <FilterSelect<'mine'>
                label="Prepared by"
                allowClear
                value={preparedFilter}
                onChange={(v) => setPreparedFilter(v)}
                options={[{ value: 'mine', label: 'Me' }]}
              />
            )}
            <FilterSelect<PrintedFilter>
              label="Printed"
              allowClear
              value={printedFilter}
              onChange={(v) => setPrintedFilter(v)}
              options={[
                { value: 'printed', label: 'Printed' },
                { value: 'not', label: 'Not printed' },
              ]}
            />
            {phone ? (
              <div className="so-range">
                <DatePicker
                  value={range?.[0] ?? null}
                  onChange={(d) => setRange(d ? [d, range?.[1] && !range[1].isBefore(d) ? range[1] : d] : null)}
                  placeholder="Issued from"
                  format="MMM D, YYYY"
                  inputReadOnly
                />
                <DatePicker
                  value={range?.[1] ?? null}
                  onChange={(d) => setRange(d ? [range?.[0] && !range[0].isAfter(d) ? range[0] : d, d] : null)}
                  placeholder="Issued to"
                  format="MMM D, YYYY"
                  inputReadOnly
                />
              </div>
            ) : (
              <DatePicker.RangePicker
                className="ui-filterbar__range"
                value={range}
                onChange={(v) => setRange(v && v[0] && v[1] ? [v[0], v[1]] : null)}
                placeholder={['Issued from', 'Issued to']}
                format="MMM D, YYYY"
                allowClear
                inputReadOnly
              />
            )}
          </>
        }
        actions={phone ? newButton : null}
      />

      <ResponsiveTable
        card={{ title: 'po', subtitle: ['supplierName'], badge: 'status', hide: ['dateIssued', 'preparedByName'] }}
        cardsBelow="narrow"
        className="std-list-table"
        rowKey="id"
        loading={loading}
        dataSource={visible}
        columns={columns}
        scroll={{ x: 'max-content' }}
        pagination={{ pageSize: 20 }}
        onRow={(r) => rowOpenProps(() => navigate(`/supplier-orders/${r.id}`))}
        locale={{
          emptyText:
            search || activeFilters
              ? 'No supplier orders match your filters.'
              : filter === 'OVERDUE'
                ? 'No overdue supplier orders.'
                : 'No supplier orders yet.',
        }}
      />

      <OrderMaterialsModal
        open={newOpen}
        onClose={() => setNewOpen(false)}
        onSaved={(order) => {
          setNewOpen(false);
          navigate(`/supplier-orders/${order.id}`);
        }}
      />
    </div>
  );
}

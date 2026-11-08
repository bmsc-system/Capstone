import { useEffect, useMemo, useState } from 'react';
import { Button, DatePicker, message } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import type { Dayjs } from 'dayjs';
import { Link } from 'react-router-dom';
import { auditApi, type AuditLogQuery } from '../../api/audit.api';
import { getErrorMessage } from '../../api/client';
import { FilterBar, FilterSelect, StatusPill, TabBar } from '../../components/ui';
import ResponsiveTable from '../../components/ui/ResponsiveTable';
import { NARROW_QUERY, PHONE_QUERY, useMediaQuery } from '../../hooks/useViewport';
import type { AuditCategory, AuditEntry, AuditLogOptions } from '../../types';
import { downloadCsv, rowsToCsv } from '../../utils/csvExport';
import { formatShop, shopToday } from '../../utils/shopTime';
import AuditDetails from './AuditDetails';
import { actionTone, detailsText, roleLabel } from './auditFormat';
import './audit.css';

type Tab = 'ALL' | AuditCategory;

const TABS: { value: Tab; label: string }[] = [
  { value: 'ALL', label: 'All' },
  { value: 'SIGN_IN', label: 'Sign-in and security' },
  { value: 'CHANGES', label: 'Record changes' },
  { value: 'ACTIONS', label: 'Other actions' },
];

const PAGE_SIZE = 25;
const EXPORT_LIMIT = 2000;

/** Admin only: who signed in, and who created, changed, or deleted what, when, and from where. */
export default function AuditLogPage() {
  const phone = useMediaQuery(PHONE_QUERY);
  const cards = useMediaQuery(NARROW_QUERY);
  const wideAudit = useMediaQuery('(min-width: 1280px)');
  const [tab, setTab] = useState<Tab>('ALL');
  const [search, setSearch] = useState('');
  const [q, setQ] = useState('');
  const [userId, setUserId] = useState<string>();
  const [entityType, setEntityType] = useState<string>();
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<AuditEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [options, setOptions] = useState<AuditLogOptions | null>(null);
  const [exporting, setExporting] = useState(false);

  // Search after the person stops typing.
  useEffect(() => {
    const t = window.setTimeout(() => setQ(search.trim()), 350);
    return () => window.clearTimeout(t);
  }, [search]);

  useEffect(() => {
    auditApi
      .options()
      .then(({ data }) => setOptions(data))
      .catch(() => undefined);
  }, []);

  const query = useMemo<AuditLogQuery>(
    () => ({
      category: tab === 'ALL' ? undefined : tab,
      userId,
      entityType,
      q: q || undefined,
      from: range ? range[0].format('YYYY-MM-DD') : undefined,
      to: range ? range[1].format('YYYY-MM-DD') : undefined,
    }),
    [tab, userId, entityType, q, range],
  );

  useEffect(() => setPage(1), [query]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    auditApi
      .list({ ...query, page, pageSize: PAGE_SIZE })
      .then(({ data }) => {
        if (!alive) return;
        setItems(data.items);
        setTotal(data.total);
      })
      .catch((err) => alive && message.error(getErrorMessage(err)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [query, page]);

  const activeFilters = [userId, entityType, range].filter(Boolean).length;

  const exportCsv = async () => {
    setExporting(true);
    try {
      const rows: AuditEntry[] = [];
      for (let p = 1; rows.length < EXPORT_LIMIT; p += 1) {
        const { data } = await auditApi.list({ ...query, page: p, pageSize: 100 });
        rows.push(...data.items);
        if (rows.length >= data.total || !data.items.length) break;
      }
      const csv = rowsToCsv(rows, [
        { key: 'when', header: 'Date and time', value: (r) => formatShop(r.createdAt, 'YYYY-MM-DD HH:mm:ss') },
        { key: 'user', header: 'User', value: (r) => r.user?.name || (r.category === 'SIGN_IN' ? '' : 'System') },
        { key: 'role', header: 'Role', value: (r) => roleLabel(r.user?.role) },
        { key: 'action', header: 'Action', value: (r) => r.actionLabel },
        { key: 'type', header: 'Record type', value: (r) => r.entityLabel },
        { key: 'record', header: 'Record', value: (r) => r.recordLabel || '' },
        { key: 'summary', header: 'Description', value: (r) => r.summary },
        { key: 'details', header: 'Details', value: (r) => detailsText(r) },
        { key: 'ip', header: 'IP address', value: (r) => r.ipAddress || '' },
      ]);
      downloadCsv(`audit-log-${shopToday().format('YYYY-MM-DD')}`, csv);
      if (total > EXPORT_LIMIT) {
        message.info(`Exported the latest ${EXPORT_LIMIT} entries. Narrow the dates to export older ones.`);
      }
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setExporting(false);
    }
  };

  const exportButton = (
    <Button icon={<DownloadOutlined />} loading={exporting} disabled={!total} onClick={exportCsv}>
      {phone ? null : 'Export CSV'}
    </Button>
  );

  return (
    <div className="audit-page">
      <FilterBar
        tabs={<TabBar<Tab> value={tab} onChange={setTab} items={TABS} />}
        search={{
          value: search,
          onChange: setSearch,
          placeholder: 'Search a name, job #, PO #, or IP address…',
        }}
        activeFilters={activeFilters}
        onClearFilters={() => {
          setUserId(undefined);
          setEntityType(undefined);
          setRange(null);
        }}
        filters={
          <>
            <FilterSelect<string>
              label="User"
              allowClear
              showSearch
              optionFilterProp="label"
              value={userId}
              onChange={(v) => setUserId(v)}
              options={(options?.users || []).map((u) => ({
                value: u.id,
                label: `${u.name} (${roleLabel(u.role)})`,
              }))}
            />
            <FilterSelect<string>
              label="Record"
              allowClear
              showSearch
              optionFilterProp="label"
              value={entityType}
              onChange={(v) => setEntityType(v)}
              options={options?.entityTypes || []}
            />
            <DatePicker.RangePicker
              className="ui-filterbar__range"
              value={range}
              onChange={(v) => setRange(v && v[0] && v[1] ? [v[0], v[1]] : null)}
              placeholder={['From', 'To']}
              format="MMM D, YYYY"
              disabledDate={(d) => d.isAfter(shopToday(), 'day')}
              allowClear
              inputReadOnly
            />
          </>
        }
        actions={exportButton}
      />

      <ResponsiveTable<AuditEntry>
        card={{ title: 'what', subtitle: ['when', 'who'], badge: 'kind', hide: ['ip'] }}
        cardsBelow="narrow"
        className="std-list-table"
        rowKey="id"
        loading={loading}
        dataSource={items}
        scroll={{ x: 'max-content' }}
        pagination={{ current: page, total, pageSize: PAGE_SIZE, onChange: setPage, showTotal: (t) => `${t} entries` }}
        locale={{ emptyText: 'No entries match these filters.' }}
        expandable={{
          expandedRowRender: (r) => <AuditDetails details={r.details} />,
          rowExpandable: (r) => r.details.length > 0,
        }}
        columns={[
          {
            title: 'When',
            key: 'when',
            width: 150,
            render: (_: unknown, r) =>
              cards ? (
                formatShop(r.createdAt, 'MMM D, YYYY h:mm A')
              ) : (
                <div>
                  <div>{formatShop(r.createdAt, 'MMM D, YYYY')}</div>
                  <div className="audit-when__time">{formatShop(r.createdAt, 'h:mm:ss A')}</div>
                </div>
              ),
          },
          {
            title: 'User',
            key: 'who',
            width: 190,
            render: (_: unknown, r) =>
              cards ? (
                r.user ? `${r.user.name} (${roleLabel(r.user.role)})` : r.category === 'SIGN_IN' ? 'Not signed in' : 'System'
              ) : r.user ? (
                <div>
                  <div className="audit-who__name">{r.user.name}</div>
                  <div className="audit-who__role">{roleLabel(r.user.role)}</div>
                </div>
              ) : (
                <span className="audit-who__role">
                  {r.category === 'SIGN_IN' ? 'Not signed in' : 'System'}
                </span>
              ),
          },
          {
            title: 'Action',
            key: 'kind',
            width: 170,
            render: (_: unknown, r) => (
              <StatusPill tone={actionTone(r.action)} compact>
                {r.actionLabel}
              </StatusPill>
            ),
          },
          {
            title: 'What happened',
            key: 'what',
            render: (_: unknown, r) => (
              <div>
                <div className="audit-what__summary">{r.summary}</div>
                {r.link && r.recordLabel ? (
                  <div className="audit-what__record">
                    <Link to={r.link} onClick={(e) => e.stopPropagation()}>
                      Open {r.entityLabel.toLowerCase()}
                    </Link>
                  </div>
                ) : null}
              </div>
            ),
          },
          {
            title: 'IP address',
            key: 'ip',
            width: 130,
            // The list cannot scroll sideways; below 1280px the IP steps aside so
            // "What happened" keeps its width (it showed one letter per line).
            hidden: !wideAudit,
            render: (_: unknown, r) => <span className="audit-ip">{r.ipAddress || '—'}</span>,
          },
        ]}
      />
    </div>
  );
}

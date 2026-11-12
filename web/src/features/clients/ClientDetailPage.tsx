import { formatShop, shopToday, toShopDayjs } from '../../utils/shopTime';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Button, Tooltip, message } from 'antd';
import type { TableColumnsType } from 'antd';
import {
  CarOutlined,
  CopyOutlined,
  EditOutlined,
  BellOutlined,
  FileTextOutlined,
  MailOutlined,
  PhoneOutlined,
  PlusOutlined,
  ToolOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import { clientsApi } from '../../api/jobOrders.api';
import { getErrorMessage } from '../../api/client';
import type { ClientDetail } from '../../types';
import { DataTable, EmptyState, IdChip, Page, PageSkeleton, SectionCard, StatusPill, TabBar, rowOpenProps } from '../../components/ui';
import './clientDetail.css';
import { JOB_STATUS } from '../../theme/statuses';
import { useAuth } from '../../hooks/useAuth';
import ClientFormModal from './ClientFormModal';

type Job = ClientDetail['jobs'][number];
type JobFilter = 'all' | 'draft' | 'active' | 'completed' | 'forDelivery' | 'delivered' | 'cancelled';

const FILTERS: { value: JobFilter; label: string; match: (j: Job) => boolean }[] = [
  { value: 'all', label: 'All', match: () => true },
  { value: 'draft', label: 'To plan', match: (j) => j.status === 'DRAFT' },
  { value: 'active', label: 'Active', match: (j) => j.status === 'SCHEDULED' || j.status === 'IN_PROGRESS' },
  { value: 'completed', label: 'Completed', match: (j) => j.status === 'COMPLETED' },
  { value: 'forDelivery', label: 'For Delivery', match: (j) => j.status === 'FOR_DELIVERY' },
  { value: 'delivered', label: 'Delivered', match: (j) => j.status === 'DELIVERED' },
  { value: 'cancelled', label: 'Cancelled', match: (j) => j.status === 'CANCELLED' },
];

function money(v: number | null | undefined) {
  if (v == null) return '—';
  return `₱${v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}

const AVATAR_COLORS = ['#1d4ed8', '#0f766e', '#7c3aed', '#b45309', '#be123c', '#0e7490', '#4d7c0f', '#334155'];

/** A steady colour per client name for the round avatar. */
function avatarColor(name: string) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return AVATAR_COLORS[h % AVATAR_COLORS.length];
}

function initials(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : name.slice(0, 2)).toUpperCase();
}

/** Shop calendar date (YYYY-MM-DD) the job was ordered: the client's PO date, else when it was entered. */
/** "Oct 23", with the year only when it is not this year. */
function shortDay(v?: string | null) {
  if (!v) return '—';
  const sameYear = formatShop(v, 'YYYY') === shopToday().format('YYYY');
  return formatShop(v, sameYear ? 'MMM D' : 'MMM D, YYYY');
}

function orderDay(j: Job): string | null {
  if (j.poDate) return j.poDate.slice(0, 10);
  return j.createdAt ? toShopDayjs(j.createdAt).format('YYYY-MM-DD') : null;
}

function isOverdue(j: Job) {
  if (
    !j.dueDate ||
    j.status === 'DRAFT' ||
    j.status === 'COMPLETED' ||
    j.status === 'FOR_DELIVERY' ||
    j.status === 'DELIVERED' ||
    j.status === 'CANCELLED'
  )
    return false;
  return j.dueDate.slice(0, 10) < shopToday().format('YYYY-MM-DD');
}

async function copy(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text);
    message.success(`${what} copied`);
  } catch {
    message.error(`Could not copy the ${what.toLowerCase()}`);
  }
}

function None({ children }: { children: ReactNode }) {
  return <span className="client-stats__none">{children}</span>;
}

function ContactRow({
  icon,
  label,
  value,
  href,
  copyAs,
}: {
  icon: ReactNode;
  label: string;
  value: string | null | undefined;
  href?: string;
  copyAs?: string;
}) {
  const body = (
    <>
      <span className="client-contact__icon" aria-hidden>
        {icon}
      </span>
      <span className="client-contact__text">
        <span className="client-contact__label">{label}</span>
        <span className={`client-contact__value${value ? '' : ' is-empty'}`}>{value || 'Not added'}</span>
      </span>
    </>
  );
  return (
    <div className="client-contact">
      {value && href ? (
        <a className="client-contact__main is-link" href={href}>
          {body}
        </a>
      ) : (
        <div className="client-contact__main">{body}</div>
      )}
      {value && copyAs ? (
        <Tooltip title={`Copy ${copyAs.toLowerCase()}`}>
          <Button
            type="text"
            size="small"
            className="client-contact__copy"
            icon={<CopyOutlined />}
            aria-label={`Copy ${copyAs.toLowerCase()}`}
            onClick={() => void copy(value, copyAs)}
          />
        </Tooltip>
      ) : null}
    </div>
  );
}

export default function ClientDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { isAdmin, canManageContacts: canEdit, canOfficeWrite: canEncodeJob } = useAuth();
  const [data, setData] = useState<ClientDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<JobFilter>('all');
  const [editOpen, setEditOpen] = useState(false);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const res = await clientsApi.get(id);
        if (!cancelled) setData(res.data);
      } catch (err) {
        if (!cancelled) message.error(getErrorMessage(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  const jobs = useMemo(
    () => [...(data?.jobs ?? [])].sort((a, b) => (b.createdAt || '').localeCompare(a.createdAt || '')),
    [data]
  );

  const stats = useMemo(() => {
    const active = jobs.filter((j) => j.status === 'SCHEDULED' || j.status === 'IN_PROGRESS').length;
    const draft = jobs.filter((j) => j.status === 'DRAFT').length;
    // Finished in the shop but not yet handed over to the client.
    const awaitingDelivery = jobs.filter((j) => j.status === 'COMPLETED' || j.status === 'FOR_DELIVERY').length;
    const days = jobs.map(orderDay).filter((d): d is string => Boolean(d)).sort();
    return {
      active,
      draft,
      awaitingDelivery,
      firstOrder: days[0] ?? null,
      lastOrder: days[days.length - 1] ?? null,
    };
  }, [jobs]);

  if (loading && !data) {
    return <PageSkeleton variant="detail" />;
  }
  if (!data) {
    return (
      <Page>
        <EmptyState title="Client not found" description="It may have been removed, or the link is wrong." />
      </Page>
    );
  }

  const newJob = () => navigate(`/job-orders/new?client=${data.id}`);
  const since = stats.firstOrder ?? data.createdAt ?? null;
  const counts = Object.fromEntries(FILTERS.map((f) => [f.value, jobs.filter(f.match).length])) as Record<JobFilter, number>;
  const shown = jobs.filter(FILTERS.find((f) => f.value === filter)!.match);
  const draftLabel = isAdmin ? 'To plan' : 'Pending';
  const notify = data.notifyByEmail ? ['Email'] : [];

  const columns: TableColumnsType<Job> = [
    {
      title: 'Job #',
      dataIndex: 'jobNumber',
      width: 122,
      render: (v: string | null, r) => (
        <IdChip to={`/job-orders/${r.id}`} onClick={(e) => e.stopPropagation()}>
          {v || r.id.slice(0, 8).toUpperCase()}
        </IdChip>
      ),
    },
    {
      title: 'Title',
      dataIndex: 'title',
      ellipsis: true,
      render: (v: string) => <span className="client-jobs__title">{v}</span>,
    },
    {
      title: 'Ordered',
      key: 'ordered',
      width: 80,
      sorter: (a, b) => (orderDay(a) || '').localeCompare(orderDay(b) || ''),
      render: (_: unknown, r) => shortDay(orderDay(r)),
    },
    {
      title: 'Required',
      dataIndex: 'dueDate',
      width: 112,
      sorter: (a, b) => (a.dueDate || '').localeCompare(b.dueDate || ''),
      render: (v: string | null, r) =>
        isOverdue(r) ? (
          <span className="tone-text--red" title="Past the date required">
            {shortDay(v)}
          </span>
        ) : (
          shortDay(v)
        ),
    },
    {
      title: 'Value',
      dataIndex: 'amount',
      width: 116,
      align: 'right',
      sorter: (a, b) => (a.amount ?? 0) - (b.amount ?? 0),
      render: (v: number | null) => <span className="ui-tabular client-jobs__value">{money(v)}</span>,
    },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 156,
      render: (st: string, r) => (
        <span className="cd-status">
          <StatusPill status={st} map={JOB_STATUS} compact>
            {st === 'DRAFT' ? draftLabel : undefined}
          </StatusPill>
          {r.deliveredOnTime === false ? (
            <StatusPill tone="red" compact>
              Late
            </StatusPill>
          ) : null}
        </span>
      ),
    },
  ];

  const lastOrderText = stats.lastOrder ? formatShop(stats.lastOrder, 'MMM D, YYYY') : 'No orders yet';

  return (
    <Page className="client-page cd">
      <div className="cd__layout">
        <aside className="cd__side">
          <section className="cd-card cd-profile">
            <div className="cd-profile__avatar" style={{ background: avatarColor(data.name) }} aria-hidden>
              {initials(data.name)}
            </div>
            <h2 className="cd-profile__name">{data.name}</h2>
            <div className="cd-profile__meta">
              {since ? `Client since ${formatShop(since, 'MMMM YYYY')}` : 'New client'}
            </div>
            {canEdit ? (
              <div className="cd-profile__actions">
                <Button icon={<EditOutlined />} onClick={() => setEditOpen(true)} block>
                  Edit client
                </Button>
                {canEncodeJob ? (
                  <Button type="primary" icon={<PlusOutlined />} onClick={newJob} block>
                    New job order
                  </Button>
                ) : null}
              </div>
            ) : null}

            <div className="cd-profile__section">Contact</div>
            <div className="cd-contacts">
              <ContactRow icon={<UserOutlined />} label="Contact person" value={data.contact} />
              <ContactRow
                icon={<MailOutlined />}
                label="Email"
                value={data.email}
                href={data.email ? `mailto:${data.email}` : undefined}
                copyAs="Email"
              />
              <ContactRow
                icon={<PhoneOutlined />}
                label="Mobile"
                value={data.mobileNumber}
                href={data.mobileNumber ? `tel:${data.mobileNumber.replace(/\s+/g, '')}` : undefined}
                copyAs="Mobile number"
              />
              <div className="client-contact">
                <div className="client-contact__main">
                  <span className="client-contact__icon" aria-hidden>
                    <BellOutlined />
                  </span>
                  <span className="client-contact__text">
                    <span className="client-contact__label">Job updates</span>
                    <span className="client-contact__value">
                      {notify.length ? (
                        <StatusPill tone="blue" compact>
                          By email
                        </StatusPill>
                      ) : (
                        <StatusPill tone="grey" compact>
                          Off
                        </StatusPill>
                      )}
                    </span>
                  </span>
                </div>
              </div>
            </div>
          </section>
        </aside>

        <div className="cd__main">
          <section className="cd-card cd-stats" aria-label="Summary">
            <div className="cd-stat">
              <span className="cd-stat__icon cd-stat__icon--blue" aria-hidden>
                <FileTextOutlined />
              </span>
              <span className="cd-stat__label">Job orders</span>
              <span className="cd-stat__value">{jobs.length ? jobs.length : <None>None yet</None>}</span>
              <span className="cd-stat__hint">
                {jobs.length
                  ? [stats.draft ? `${stats.draft} ${draftLabel.toLowerCase()}` : null, `last ${lastOrderText}`]
                      .filter(Boolean)
                      .join(' · ')
                  : 'Their first order shows here'}
              </span>
            </div>
            <div className="cd-stat">
              <span className="cd-stat__icon cd-stat__icon--indigo" aria-hidden>
                <ToolOutlined />
              </span>
              <span className="cd-stat__label">In production</span>
              <span className="cd-stat__value">{stats.active ? stats.active : <None>Nothing running</None>}</span>
              <span className="cd-stat__hint">Scheduled or in progress</span>
            </div>
            <div className="cd-stat">
              <span className="cd-stat__icon cd-stat__icon--green" aria-hidden>
                <CarOutlined />
              </span>
              <span className="cd-stat__label">Awaiting delivery</span>
              <span className="cd-stat__value">
                {stats.awaitingDelivery ? stats.awaitingDelivery : <None>None</None>}
              </span>
              <span className="cd-stat__hint">Finished, not yet with the client</span>
            </div>
            <div className="cd-stat">
              <span className="cd-stat__icon cd-stat__icon--teal" aria-hidden>
                ₱
              </span>
              <span className="cd-stat__label">Total value</span>
              <span className="cd-stat__value">
                {data.totals.totalValue ? money(data.totals.totalValue) : <None>No released jobs</None>}
              </span>
              <span className="cd-stat__hint">Released jobs only</span>
            </div>
          </section>

      <SectionCard
        className="client-jobs"
        title="Job orders"
        subtitle={jobs.length ? `${jobs.length} job${jobs.length === 1 ? '' : 's'}, newest first` : undefined}
      >
        {jobs.length ? (
          <>
            <div className="client-jobs__filters">
              <TabBar<JobFilter>
                size="small"
                value={filter}
                onChange={setFilter}
                items={FILTERS.map((f) => ({
                  value: f.value,
                  label: f.value === 'draft' ? draftLabel : f.label,
                  count: counts[f.value],
                }))}
              />
            </div>
            <DataTable<Job>
              card={{ title: 'jobNumber', subtitle: ['title'], badge: 'status' }}
              cardsBelow="narrow"
              className="client-jobs__table"
              rowKey="id"
              tableLayout="fixed"
              pagination={{ pageSize: 20, hideOnSinglePage: true, showSizeChanger: false }}
              dataSource={shown}
              locale={{
                emptyText: (
                  <EmptyState
                    compact
                    title={`No ${(filter === 'draft' ? draftLabel : FILTERS.find((f) => f.value === filter)!.label).toLowerCase()} job orders`}
                  />
                ),
              }}
              onRow={(r) => rowOpenProps(() => navigate(`/job-orders/${r.id}`))}
              columns={columns}
            />
          </>
        ) : (
          <EmptyState
            icon={<FileTextOutlined />}
            title="No job orders yet"
            description={`Job orders for ${data.name} will be listed here.`}
            action={
              canEncodeJob ? (
                <Button type="primary" icon={<PlusOutlined />} onClick={newJob}>
                  New job order
                </Button>
              ) : undefined
            }
          />
        )}
      </SectionCard>
        </div>
      </div>

      <ClientFormModal
        open={editOpen}
        client={data}
        onClose={() => setEditOpen(false)}
        onSaved={(saved) => {
          setEditOpen(false);
          setData((d) => (d ? { ...d, ...saved } : d));
        }}
      />
    </Page>
  );
}

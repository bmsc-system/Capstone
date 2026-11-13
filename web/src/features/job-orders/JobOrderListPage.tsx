import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Alert, Table, Button, Select, Dropdown, Input, Space, Spin, message, Drawer, Badge, Tooltip } from 'antd';
import type { MenuProps, TableColumnsType } from 'antd';
import {
  PlusOutlined,
  EditOutlined,
  CarOutlined,
  CheckOutlined,
  PrinterOutlined,
  EyeOutlined,
  StopOutlined,
  InboxOutlined,
  MoreOutlined,
  SearchOutlined,
  FilterOutlined,
  CloseOutlined,
  CalendarOutlined,
  AppstoreOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import dayjs from 'dayjs';
import { formatShopDate, scheduleFlagStyle, formatShop, shopToday } from '../../utils/shopTime';
import { jobOrdersApi } from '../../api/jobOrders.api';
import { getErrorMessage } from '../../api/client';
import StatusPill from '../../components/StatusPill';
import { EllipsisText, IdChip, TabBar, rowOpenProps } from '../../components/ui';
import { JOB_STATUS } from '../../theme/statuses';
import { DELAY_LABEL } from '../../components/MaterialDelayTag';
import SelectMultipleIcon from '../../components/SelectMultipleIcon';
import { useAuth } from '../../hooks/useAuth';
import { useOverdueCheck } from '../../hooks/useOverdueCheck';
import { useIsPhone } from '../../hooks/useIsPhone';
import { useMediaQuery } from '../../hooks/useViewport';

/** Below this width the list starts as cards (phones, tablets, small laptops). */
const CARDS_BY_DEFAULT_QUERY = '(max-width: 1279.98px)';
/** The list cannot scroll sideways, so on narrower screens the less important
 *  columns step aside and the job title keeps a readable width. */
const WIDE_LIST_QUERY = '(min-width: 1400px)';
const MID_LIST_QUERY = '(min-width: 1280px)';
import type { JobOrder, JobOrderStatus } from '../../types';
import { compareByDue, compareOpenFirst } from './jobOrderSort';
import { isJobOrderListTab } from './jobOrderListTabs';
import CancelJobModal from './CancelJobModal';
import MarkDeliveredModal from './MarkDeliveredModal';
import { isJobFinished, nextDeliveryStep } from './jobStage';

type ListTab = 'production' | 'drafts' | 'archived';

function tabFromSearch(param: string | null): ListTab {
  // Legacy ?tab=archived → handled by redirect to /job-orders/archived
  if (isJobOrderListTab(param)) return param;
  return 'production';
}

type JobOrderListPageProps = {
  /** Separate archived page; not a day-to-day tab. */
  mode?: 'active' | 'archived';
};

const PRODUCTION_STATUS_OPTIONS: { value: JobOrderStatus; label: string }[] = [
  { value: 'SCHEDULED', label: 'Scheduled' },
  { value: 'IN_PROGRESS', label: 'In Progress' },
  { value: 'COMPLETED', label: 'Completed' },
  { value: 'FOR_DELIVERY', label: 'For Delivery' },
  { value: 'DELIVERED', label: 'Delivered' },
  { value: 'CANCELLED', label: 'Cancelled' },
];

function isJobOverdue(job: JobOrder) {
  return (
    !isJobFinished(job.status) &&
    job.status !== 'CANCELLED' &&
    dayjs(job.dueDate).isBefore(shopToday(), 'day')
  );
}

/** Warnings on an open job, most urgent first. Short labels; the tooltip has the full text. */
function jobFlags(job: JobOrder): { label: string; tone: 'red' | 'amber'; full: string }[] {
  if (job.status === 'CANCELLED' || isJobFinished(job.status)) return [];
  const flags: { label: string; tone: 'red' | 'amber'; full: string }[] = [];
  if (isJobOverdue(job)) flags.push({ label: 'Overdue', tone: 'red', full: 'Past its date required' });
  if (job.needsReplan) flags.push({ label: 'Re-plan', tone: 'red', full: job.needsReplanReason || 'Needs re-plan' });
  if (job.waitingForMaterials)
    flags.push({ label: 'Materials', tone: 'amber', full: job.materialWaitReason || 'Waiting for materials' });
  if (job.materialDelay && job.status === 'SCHEDULED')
    flags.push({ label: 'Moved', tone: 'amber', full: DELAY_LABEL[job.materialDelay.kind] });
  return flags;
}

function JobStatusBadge({ job }: { job: JobOrder }) {
  const pill = <StatusPill status={job.status} map={JOB_STATUS} compact />;
  const flags = jobFlags(job);
  if (!flags.length) return pill;
  const [first, ...rest] = flags;
  return (
    <Tooltip title={flags.map((f) => f.full).join(' · ')}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap' }}>
        {pill}
        <StatusPill color={first.tone} compact>
          {first.label}
        </StatusPill>
        {rest.length ? <span style={{ fontSize: 12, color: '#64748b', fontWeight: 600 }}>+{rest.length}</span> : null}
      </span>
    </Tooltip>
  );
}

function DuePill({ job }: { job: JobOrder }) {
  const st = job.scheduleFlag ? scheduleFlagStyle[job.scheduleFlag] : null;
  const pill = (
    <span
      style={{
        fontSize: 13,
        fontWeight: st ? 700 : 500,
        whiteSpace: 'nowrap',
        color: st ? st.color : '#334155',
      }}
    >
      {formatShop(job.dueDate, 'MMM D, YYYY')}
    </span>
  );
  if (!st || job.scheduleFlag === 'GREEN') return pill;
  const basis = job.scheduleFlagBasis === 'ESTIMATE' ? 'estimated finish (past performance)' : 'scheduled finish';
  return <Tooltip title={`${st.label}: based on the ${basis}`}>{pill}</Tooltip>;
}

const ESTIMATE_HINT = 'From how long past operations of the same kind took.';

function FinishCell({ job }: { job: JobOrder }) {
  if (!job.projectedCompletion && !job.predictedCompletion) {
    return <span style={{ fontSize: 13, color: '#94a3b8' }}>—</span>;
  }
  const flagged = job.scheduleFlag && job.scheduleFlag !== 'GREEN';
  // Finished jobs show when the work actually ended, with no estimate.
  const done = job.status === 'COMPLETED' || job.status === 'FOR_DELIVERY' || job.status === 'DELIVERED';
  const line = (label: string, iso: string | null | undefined, emphasize: boolean) =>
    iso ? (
      <div
        style={{
          fontSize: 12,
          whiteSpace: 'nowrap',
          lineHeight: 1.35,
          color: emphasize ? '#7A1528' : '#0f172a',
          fontWeight: emphasize ? 600 : 400,
        }}
      >
        <span style={{ color: '#64748b' }}>{label}</span> {formatShopDate(iso)}
      </div>
    ) : null;
  return (
    <div>
      {line(done ? 'Done' : 'Sched.', job.projectedCompletion, !!flagged && job.scheduleFlagBasis === 'SCHEDULE')}
      {!done && job.predictedCompletion ? (
        <Tooltip title={ESTIMATE_HINT}>
          <span>{line('Est.', job.predictedCompletion, !!flagged && job.scheduleFlagBasis === 'ESTIMATE')}</span>
        </Tooltip>
      ) : null}
    </div>
  );
}

type ViewMode = 'cards' | 'list';
const VIEW_STORAGE_KEY = 'jobOrders.view';

function readViewMode(): ViewMode {
  const stored = localStorage.getItem(VIEW_STORAGE_KEY);
  if (stored === 'cards' || stored === 'list') return stored;
  return window.matchMedia(CARDS_BY_DEFAULT_QUERY).matches ? 'cards' : 'list';
}

const VIEW_OPTIONS: { key: ViewMode; label: string; icon: ReactNode }[] = [
  { key: 'cards', label: 'Cards', icon: <AppstoreOutlined /> },
  { key: 'list', label: 'List', icon: <UnorderedListOutlined /> },
];

type JobCardProps = {
  job: JobOrder;
  isDraftTab: boolean;
  selected: boolean;
  actions: MenuProps['items'];
  onClick: () => void;
};

function JobCard({ job, isDraftTab, selected, actions, onClick }: JobCardProps) {
  const total = job.opsTotal || 0;
  const done = job.opsCompleted || 0;
  const pct = total ? Math.round((done / total) * 100) : 0;
  const modified = job.updatedAt || job.createdAt;
  const menu = (
    <div onClick={(e) => e.stopPropagation()}>
      <Dropdown menu={{ items: actions }} trigger={['click']} placement="bottomRight">
        <Button
          type="text"
          size="small"
          icon={<MoreOutlined style={{ fontSize: 16 }} />}
          aria-label="More actions"
        />
      </Dropdown>
    </div>
  );

  return (
    <div {...rowOpenProps(onClick)} className={`jo-card ui-row-open${selected ? ' is-selected' : ''}`} role="button">
      <div className="jo-card__top">
        <span className="jo-card__number">{job.jobNumber || job.id.slice(0, 8).toUpperCase()}</span>
        <JobStatusBadge job={job} />
        {menu}
      </div>
      <div className="jo-card__title" title={job.title}>
        {job.title}
      </div>
      <div className="jo-card__client">{job.clientName || 'No client'}</div>
      <div className="jo-card__pills">
        <DuePill job={job} />
        {!isDraftTab ? <FinishCell job={job} /> : null}
      </div>
      {isDraftTab ? (
        <div className="jo-card__stage">{job.draftStage || '—'}</div>
      ) : (
        <div className="jo-card__progress">
          <div className="jo-card__progress-label">
            {done}/{total} ops
          </div>
          <div className="jo-card__bar">
            <div className="jo-card__bar-fill" style={{ width: `${pct}%` }} />
          </div>
        </div>
      )}
      <div className="jo-card__foot">
        {modified && dayjs(modified).isValid()
          ? `Modified ${formatShop(modified, 'MMM D, YYYY')}`
          : '—'}
        {isDraftTab && job.createdByName ? ` · ${job.createdByName}` : ''}
      </div>
    </div>
  );
}

function jobSearchHaystack(job: JobOrder) {
  return [
    job.jobNumber,
    job.id.slice(0, 8),
    job.title,
    job.clientName,
    job.clientPoNumber,
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
}

export default function JobOrderListPage({ mode = 'active' }: JobOrderListPageProps) {
  const archivedOnly = mode === 'archived';
  const [jobs, setJobs] = useState<JobOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [searchParams, setSearchParams] = useSearchParams();
  const [listTab, setListTab] = useState<ListTab>(() =>
    archivedOnly ? 'archived' : tabFromSearch(searchParams.get('tab'))
  );
  const [draftCount, setDraftCount] = useState(0);
  const [archivedCount, setArchivedCount] = useState(0);
  const [releasedCount, setReleasedCount] = useState<number | null>(null);
  const [statusFilter, setStatusFilter] = useState<JobOrderStatus[]>([]);
  const [clientFilter, setClientFilter] = useState<string[]>([]);
  const [awaitingMaterialOnly, setAwaitingMaterialOnly] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [selectMode, setSelectMode] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [error, setError] = useState('');
  const [delivering, setDelivering] = useState(false);
  const [deliverJob, setDeliverJob] = useState<JobOrder | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>(readViewMode);
  const navigate = useNavigate();
  const location = useLocation();

  const changeViewMode = (mode: ViewMode) => {
    setViewMode(mode);
    localStorage.setItem(VIEW_STORAGE_KEY, mode);
  };

  const toggleSelected = (jobId: string) =>
    setSelectedKeys((keys) =>
      keys.includes(jobId) ? keys.filter((k) => k !== jobId) : [...keys, jobId]
    );
  const { isAdmin, canOfficeWrite } = useAuth();
  const isPhone = useIsPhone();
  const wideList = useMediaQuery(WIDE_LIST_QUERY);
  const midList = useMediaQuery(MID_LIST_QUERY);

  useEffect(() => {
    if (archivedOnly) {
      setListTab('archived');
      return;
    }
    // Old bookmark: send people to the dedicated archived page.
    if (searchParams.get('tab') === 'archived') {
      navigate('/job-orders/archived', { replace: true });
      return;
    }
    const next = tabFromSearch(searchParams.get('tab'));
    setListTab((prev) => (prev === next ? prev : next));
  }, [searchParams, archivedOnly, navigate]);

  const selectListTab = (tab: ListTab) => {
    if (archivedOnly) return;
    setListTab(tab);
    setStatusFilter([]);
    setAwaitingMaterialOnly(false);
    setSelectedKeys([]);
    if (tab === 'drafts') setSearchParams({ tab: 'drafts' }, { replace: true });
    else setSearchParams({}, { replace: true });
  };

  const fetchJobs = async (tab: ListTab = listTab) => {
    setLoading(true);
    setError('');
    try {
      const { data } = await jobOrdersApi.list({
        scope: tab === 'archived' ? 'archived' : tab === 'drafts' ? 'drafts' : 'production',
        awaitingMaterial: tab === 'production' && awaitingMaterialOnly ? true : undefined,
      });
      setJobs(data);
      if (tab === 'drafts') setDraftCount(data.length);
      else if (tab === 'archived') setArchivedCount(data.length);
      else if (!awaitingMaterialOnly) setReleasedCount(data.length);
    } catch (err) {
      setJobs([]);
      setError(getErrorMessage(err));
      return;
    } finally {
      setLoading(false);
    }
    if (isAdmin || canOfficeWrite) {
      if (tab !== 'drafts') {
        jobOrdersApi
          .list({ scope: 'drafts' })
          .then((drafts) => setDraftCount(drafts.data.length))
          .catch(() => undefined);
      }
      if (tab !== 'archived') {
        jobOrdersApi
          .list({ scope: 'archived' })
          .then((archived) => setArchivedCount(archived.data.length))
          .catch(() => undefined);
      }
    }
  };

  useOverdueCheck();

  useEffect(() => {
    fetchJobs(listTab);
  }, [listTab, awaitingMaterialOnly]);

  const clientOptions = useMemo(() => {
    const names = new Set<string>();
    jobs.forEach((j) => {
      if (j.clientName) names.add(j.clientName);
    });
    return [...names].sort((a, b) => a.localeCompare(b)).map((name) => ({ value: name, label: name }));
  }, [jobs]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return jobs
      .filter((job) => {
        if (q && !jobSearchHaystack(job).includes(q)) return false;
        if (statusFilter.length && !statusFilter.includes(job.status)) return false;
        if (clientFilter.length && !clientFilter.includes(job.clientName || '')) return false;
        return true;
      })
      .sort(compareOpenFirst);
  }, [jobs, search, statusFilter, clientFilter]);

  const selectedJobs = useMemo(
    () => filtered.filter((j) => selectedKeys.includes(j.id)),
    [filtered, selectedKeys]
  );
  const selectedCompletable = selectedJobs.filter((j) => j.status === 'COMPLETED');
  const activeFilterCount =
    (statusFilter.length ? 1 : 0) +
    (clientFilter.length ? 1 : 0) +
    (awaitingMaterialOnly ? 1 : 0);
  const overdueCount = filtered.filter(isJobOverdue).length;
  const doneCount = filtered.filter((j) => isJobFinished(j.status)).length;

  const clearJobFilters = () => {
    setStatusFilter([]);
    setClientFilter([]);
    setAwaitingMaterialOnly(false);
  };

  const toggleSelectMode = () => {
    if (selectMode) {
      setSelectMode(false);
      setSelectedKeys([]);
    } else {
      setSelectMode(true);
    }
  };

  const handleBulkDeliver = async () => {
    if (!selectedCompletable.length) {
      message.info('Select completed jobs to set For Delivery.');
      return;
    }
    setDelivering(true);
    try {
      for (const job of selectedCompletable) {
        await jobOrdersApi.setForDelivery(job.id);
      }
      message.success(
        selectedCompletable.length === 1
          ? 'Set For Delivery'
          : `${selectedCompletable.length} jobs set For Delivery`
      );
      setSelectedKeys([]);
      await fetchJobs();
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setDelivering(false);
    }
  };

  const handleBulkPrint = () => {
    if (!selectedJobs.length) return;
    selectedJobs.forEach((job) => {
      window.open(`/job-orders/${job.id}/print`, '_blank', 'noopener,noreferrer');
    });
  };

  const [cancelJob, setCancelJob] = useState<JobOrder | null>(null);

  // Office Staff cancel only a job still to be planned; after release only the Admin.
  const canCancelJob = (record: JobOrder) =>
    (isAdmin || (canOfficeWrite && record.status === 'DRAFT')) &&
    !isJobFinished(record.status) &&
    record.status !== 'CANCELLED' &&
    !record.salesInvoice;

  const jobActionItems = (record: JobOrder): MenuProps['items'] => {
    const isDraft = record.status === 'DRAFT';

    if (isDraft) {
      const items: MenuProps['items'] = [];
      if (canOfficeWrite || isAdmin) {
        items.push({
          key: 'view',
          icon: <EyeOutlined />,
          label: 'View',
          onClick: () => navigate(`/job-orders/${record.id}`),
        });
        if (isAdmin) {
          items.push({
            key: 'plan',
            icon: <CalendarOutlined />,
            label: 'Plan',
            onClick: () => navigate(`/job-orders/${record.id}/plan`),
          });
        }
        if (canOfficeWrite) {
          items.push({
            key: 'edit',
            icon: <EditOutlined />,
            label: 'Edit details',
            onClick: () => navigate(`/job-orders/${record.id}/edit`),
          });
        }
        if (canCancelJob(record)) {
          items.push({
            key: 'cancel',
            icon: <StopOutlined />,
            label: 'Cancel job order',
            danger: true,
            onClick: () => setCancelJob(record),
          });
        }
      }
      return items;
    }

    const items: MenuProps['items'] = [
      {
        key: 'view',
        icon: <EyeOutlined />,
        label: 'View',
        onClick: () => navigate(`/job-orders/${record.id}`),
      },
    ];
    if (record.status !== 'CANCELLED') items.push({
      key: 'print',
      icon: <PrinterOutlined />,
      label: 'Print',
      onClick: () =>
        navigate(`/job-orders/${record.id}/print`, { state: { from: location.pathname + location.search } }),
    });
    const step = isAdmin ? nextDeliveryStep(record.status) : null;
    if (step === 'FOR_DELIVERY') {
      items.push({
        key: 'for-delivery',
        icon: <CarOutlined />,
        label: 'Set For Delivery',
        onClick: async () => {
          try {
            await jobOrdersApi.setForDelivery(record.id);
            message.success('Set For Delivery. The client is notified that it is ready for delivery.');
            fetchJobs();
          } catch (err) {
            message.error(getErrorMessage(err));
          }
        },
      });
    } else if (step === 'DELIVERED') {
      items.push({
        key: 'delivered',
        icon: <CheckOutlined />,
        label: 'Mark as Delivered',
        onClick: () => setDeliverJob(record),
      });
    }
    if (canCancelJob(record)) {
      items.push({
        key: 'cancel',
        icon: <StopOutlined />,
        label: 'Cancel job order',
        danger: true,
        onClick: () => setCancelJob(record),
      });
    }
    if (
      (canOfficeWrite || isAdmin) &&
      (isJobFinished(record.status) || record.status === 'CANCELLED')
    ) {
      if (record.isArchived || record.archivedAt) {
        items.push({
          key: 'unarchive',
          icon: <InboxOutlined />,
          label: 'Unarchive',
          onClick: async () => {
            try {
              await jobOrdersApi.unarchive(record.id);
              message.success('Job unarchived');
              await fetchJobs();
            } catch (err) {
              message.error(getErrorMessage(err));
            }
          },
        });
      } else {
        items.push({
          key: 'archive',
          icon: <InboxOutlined />,
          label: 'Archive',
          onClick: async () => {
            try {
              await jobOrdersApi.archive(record.id);
              message.success('Job archived');
              await fetchJobs();
            } catch (err) {
              message.error(getErrorMessage(err));
            }
          },
        });
      }
    }
    return items;
  };

  const productionColumns: TableColumnsType<JobOrder> = [
    {
      title: 'Job #',
      dataIndex: 'jobNumber',
      key: 'jobNumber',
      width: 136,
      sorter: (a, b) =>
        (a.jobNumber || a.id).localeCompare(b.jobNumber || b.id, undefined, { numeric: true }),
      render: (n: string, record) => <IdChip>{n || record.id.slice(0, 8).toUpperCase()}</IdChip>,
    },
    {
      title: 'Title',
      dataIndex: 'title',
      key: 'title',
      ellipsis: true,
      sorter: (a, b) => a.title.localeCompare(b.title),
      render: (t: string) => (
        <EllipsisText title={t}>
          <span style={{ fontWeight: 600, fontSize: 14, color: '#0f172a' }}>{t}</span>
        </EllipsisText>
      ),
    },
    {
      title: 'Client',
      dataIndex: 'clientName',
      key: 'clientName',
      width: 160,
      ellipsis: true,
      sorter: (a, b) => (a.clientName || '').localeCompare(b.clientName || ''),
      render: (v: string | undefined) => <EllipsisText>{v || '—'}</EllipsisText>,
    },
    {
      title: 'Last modified',
      dataIndex: 'updatedAt',
      key: 'updatedAt',
      width: 124,
      hidden: !wideList,
      sorter: (a, b) =>
        dayjs(a.updatedAt || a.createdAt).valueOf() - dayjs(b.updatedAt || b.createdAt).valueOf(),
      render: (_: string | null | undefined, record) => {
        const d = record.updatedAt || record.createdAt;
        return (
          <span style={{ fontSize: 13, whiteSpace: 'nowrap', color: '#0f172a' }}>
            {d && dayjs(d).isValid() ? formatShop(d, 'MMM D, YYYY') : '—'}
          </span>
        );
      },
    },
    {
      title: 'Date required',
      dataIndex: 'dueDate',
      key: 'dueDate',
      width: 112,
      sorter: compareByDue,
      render: (_d: string, record) => <DuePill job={record} />,
    },
    {
      title: (
        <Tooltip title={`Scheduled finish and estimated finish (actual finish once done). ${ESTIMATE_HINT}`}>
          <span>Finish</span>
        </Tooltip>
      ),
      key: 'finish',
      width: 140,
      hidden: !midList,
      render: (_: unknown, record) => <FinishCell job={record} />,
    },
    {
      title: 'Progress',
      key: 'progress',
      width: 82,
      hidden: !wideList,
      sorter: (a, b) => {
        const pa = a.opsTotal ? (a.opsCompleted || 0) / a.opsTotal : 0;
        const pb = b.opsTotal ? (b.opsCompleted || 0) / b.opsTotal : 0;
        return pa - pb;
      },
      render: (_: unknown, record) => {
        const total = record.opsTotal || 0;
        const done = record.opsCompleted || 0;
        const pct = total ? Math.round((done / total) * 100) : 0;
        return (
          <div>
            <div style={{ fontSize: 12, color: '#64748b', lineHeight: 1.2, marginBottom: 4 }}>
              {done}/{total} ops
            </div>
            <div
              style={{
                width: '100%',
                maxWidth: 56,
                height: 3,
                borderRadius: 999,
                background: '#f1f5f9',
                overflow: 'hidden',
              }}
            >
              <div
                style={{
                  width: `${pct}%`,
                  height: '100%',
                  background: '#2563eb',
                  borderRadius: 999,
                }}
              />
            </div>
          </div>
        );
      },
    },
    {
      title: 'Status',
      dataIndex: 'status',
      key: 'status',
      width: 190,
      render: (_s: JobOrderStatus, record) => <JobStatusBadge job={record} />,
    },
    {
      title: '',
      key: 'actions',
      width: 40,
      align: 'center',
      render: (_: unknown, record) => (
        <div onClick={(e) => e.stopPropagation()}>
          <Dropdown menu={{ items: jobActionItems(record) }} trigger={['click']} placement="bottomRight">
            <Button
              type="text"
              size="small"
              icon={<MoreOutlined style={{ fontSize: 18 }} />}
              aria-label="More actions"
            />
          </Dropdown>
        </div>
      ),
    },
  ];

  const draftColumns: TableColumnsType<JobOrder> = [
    productionColumns[0],
    productionColumns[1],
    productionColumns[2],
    {
      title: 'Stage',
      key: 'draftStage',
      width: 200,
      ellipsis: true,
      render: (_: unknown, record) => (
        <span style={{ fontSize: 13, color: '#475569' }}>{record.draftStage || '—'}</span>
      ),
    },
    {
      title: 'Created by',
      key: 'createdByName',
      width: 120,
      ellipsis: true,
      render: (_: unknown, record) => (
        <span style={{ fontSize: 13 }}>{record.createdByName || '—'}</span>
      ),
    },
    {
      title: 'Last modified',
      dataIndex: 'updatedAt',
      key: 'updatedAt',
      width: 124,
      sorter: (a, b) =>
        dayjs(a.updatedAt || a.createdAt).valueOf() - dayjs(b.updatedAt || b.createdAt).valueOf(),
      render: (_: string | null | undefined, record) => {
        const d = record.updatedAt || record.createdAt;
        return (
          <span style={{ fontSize: 13, whiteSpace: 'nowrap', color: '#0f172a' }}>
            {d && dayjs(d).isValid() ? formatShop(d, 'MMM D, YYYY') : '—'}
          </span>
        );
      },
    },
    productionColumns[productionColumns.length - 1],
  ];

  const columns = listTab === 'drafts' ? draftColumns : productionColumns;

  const listTabs =
    !archivedOnly && (isAdmin || canOfficeWrite) ? (
      <div style={{ marginBottom: isPhone ? 10 : 14 }}>
        <TabBar<ListTab>
          block={isPhone}
          value={listTab === 'archived' ? 'production' : listTab}
          onChange={selectListTab}
          items={[
            { label: 'Released', value: 'production', count: releasedCount },
            {
              label: isAdmin ? 'To plan' : 'Pending',
              value: 'drafts',
              count: draftCount,
              countTone: draftCount > 0 ? 'red' : undefined,
            },
          ]}
        />
      </div>
    ) : null;

  const openJob = (job: JobOrder) => {
    if (job.status === 'DRAFT' && isAdmin) navigate(`/job-orders/${job.id}/plan`);
    else navigate(`/job-orders/${job.id}`);
  };

  return (
    <div className="jo-list-page">
      {listTabs}
      {isPhone ? (
        <div className="sched-m jo-m-chrome">
          <div className="sched-m__top">
            <div className="jo-m__nav">
              <Input
                allowClear
                variant="borderless"
                prefix={<SearchOutlined style={{ color: '#94a3b8' }} />}
                placeholder="Search jobs…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="jo-m__search"
              />
              {canOfficeWrite && (
                <button
                  type="button"
                  className="sched-m__icon jo-m__add"
                  onClick={() => navigate('/job-orders/new')}
                  aria-label="New job order"
                >
                  <PlusOutlined />
                </button>
              )}
              <button
                type="button"
                className={`sched-m__icon${selectMode ? ' is-on' : ''}`}
                onClick={toggleSelectMode}
                aria-label={selectMode ? 'Done selecting' : 'Select multiple'}
              >
                <SelectMultipleIcon />
              </button>
              <Badge count={activeFilterCount} size="small" offset={[-4, 4]} className="sched-m__filter">
                <button
                  type="button"
                  className="sched-m__icon"
                  onClick={() => setFiltersOpen(true)}
                  aria-label="Filters"
                >
                  <FilterOutlined />
                </button>
              </Badge>
            </div>
          </div>
          <div className="sched-m__stats">
            <div className="sched-m__stat">
              <div className="sched-m__stat-n">{loading ? '—' : filtered.length}</div>
              <div className="sched-m__stat-l">Jobs</div>
            </div>
            <div className={`sched-m__stat${overdueCount ? ' is-danger' : ''}`}>
              <div className="sched-m__stat-n">{loading ? '—' : overdueCount}</div>
              <div className="sched-m__stat-l">Overdue</div>
            </div>
            <div className="sched-m__stat">
              <div className="sched-m__stat-n">{loading ? '—' : doneCount}</div>
              <div className="sched-m__stat-l">Done</div>
            </div>
          </div>
          {selectMode ? (
            <div className="jo-m__bulk">
              <span className="jo-m__bulk-count">{selectedKeys.length} selected</span>
              {listTab !== 'drafts' ? (
                <>
                  <Button
                    size="small"
                    icon={<PrinterOutlined />}
                    disabled={!selectedJobs.length}
                    onClick={handleBulkPrint}
                  >
                    Print
                  </Button>
                  {isAdmin ? (
                    <Button
                      size="small"
                      icon={<CarOutlined />}
                      loading={delivering}
                      disabled={!selectedCompletable.length}
                      onClick={handleBulkDeliver}
                    >
                      Set For Delivery
                      {selectedCompletable.length ? ` (${selectedCompletable.length})` : ''}
                    </Button>
                  ) : null}
                </>
              ) : (
                <span className="jo-m__bulk-count" style={{ opacity: 0.7 }}>
                  Cancel jobs one at a time from the menu
                </span>
              )}
            </div>
          ) : null}
        </div>
      ) : (
      <div className="jo-list-toolbar">
        <div className="jo-list-filters">
          <Input
            allowClear
            prefix={<SearchOutlined style={{ color: '#94a3b8' }} />}
            placeholder="Search job #, title, client, PO…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="jo-list-search"
          />
          {listTab === 'production' && (
          <Select
            mode="multiple"
            allowClear
            maxTagCount="responsive"
            placeholder="Status"
            className="jo-list-filter"
            value={statusFilter}
            onChange={setStatusFilter}
            options={PRODUCTION_STATUS_OPTIONS}
          />
          )}
          {listTab === 'production' && (
            <Select
              allowClear
              placeholder="Material"
              className="jo-list-filter jo-list-filter--sm"
              value={awaitingMaterialOnly ? 'awaiting' : undefined}
              onChange={(v) => setAwaitingMaterialOnly(v === 'awaiting')}
              options={[{ value: 'awaiting', label: 'Awaiting material' }]}
            />
          )}
          <Select
            mode="multiple"
            allowClear
            showSearch
            optionFilterProp="label"
            maxTagCount="responsive"
            placeholder="Client"
            className="jo-list-filter"
            value={clientFilter}
            onChange={setClientFilter}
            options={clientOptions}
          />
        </div>
        <div className="jo-list-actions">
          <Dropdown
            trigger={['click']}
            placement="bottomRight"
            menu={{
              selectable: true,
              selectedKeys: [viewMode],
              items: VIEW_OPTIONS.map((o) => ({ key: o.key, label: o.label, icon: o.icon })),
              onClick: ({ key }) => changeViewMode(key as ViewMode),
            }}
          >
            <Tooltip title="View">
              <Button
                className="ui-iconbtn"
                icon={VIEW_OPTIONS.find((o) => o.key === viewMode)?.icon}
                aria-label="Change view"
              />
            </Tooltip>
          </Dropdown>
          <Tooltip title={selectMode ? 'Done selecting' : 'Select multiple'}>
            <Button
              type={selectMode ? 'primary' : 'default'}
              ghost={selectMode}
              className="ui-iconbtn"
              icon={<SelectMultipleIcon />}
              onClick={toggleSelectMode}
              aria-pressed={selectMode}
              aria-label={selectMode ? 'Done selecting' : 'Select multiple'}
            />
          </Tooltip>
          {!archivedOnly ? (
            <Tooltip title={`Archived job orders${archivedCount ? ` (${archivedCount})` : ''}`}>
              <Button
                className="ui-iconbtn"
                icon={<InboxOutlined />}
                onClick={() => navigate('/job-orders/archived')}
                aria-label="Archived job orders"
              />
            </Tooltip>
          ) : (
            <Button type="link" onClick={() => navigate('/job-orders')}>
              Back to job orders
            </Button>
          )}
          {canOfficeWrite && !archivedOnly && (
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => navigate('/job-orders/new')}
              style={{ fontWeight: 700 }}
            >
              New Job Order
            </Button>
          )}
        </div>
      </div>
      )}

      {selectMode && !isPhone && (
        <div className="jo-list-bulk">
          <span className="jo-list-bulk__count">
            {selectedKeys.length}
            {' selected'}
            {selectedKeys.length > 0 && selectedJobs.length !== selectedKeys.length
              ? ` (${selectedJobs.length} in view)`
              : ''}
          </span>
          <Space size={8}>
            {listTab !== 'drafts' ? (
              <>
                <Button
                  size="small"
                  icon={<PrinterOutlined />}
                  disabled={!selectedJobs.length}
                  onClick={handleBulkPrint}
                >
                  Print
                </Button>
                {isAdmin ? (
                  <Button
                    size="small"
                    icon={<CarOutlined />}
                    loading={delivering}
                    disabled={!selectedCompletable.length}
                    onClick={handleBulkDeliver}
                  >
                    Set For Delivery
                    {selectedCompletable.length ? ` (${selectedCompletable.length})` : ''}
                  </Button>
                ) : null}
              </>
            ) : null}
            {selectedKeys.length > 0 && (
              <Button size="small" type="text" onClick={() => setSelectedKeys([])}>
                Clear
              </Button>
            )}
          </Space>
        </div>
      )}

      {error ? (
        <Alert
          type="error"
          showIcon
          title="Job orders could not be loaded"
          description={error}
          action={
            <Button type="primary" onClick={() => fetchJobs(listTab)}>
              Try again
            </Button>
          }
        />
      ) : isPhone ? (
        <div className="admin-cards">
          {loading && (
            <div className="page-spinner">
              <Spin />
            </div>
          )}
          {!loading && filtered.length === 0 && (
            <div className="admin-cards__empty">No job orders match your filters yet</div>
          )}
          {!loading &&
            filtered.map((job) => {
              const selected = selectedKeys.includes(job.id);
              return (
                <div
                  key={job.id}
                  {...rowOpenProps(() => (selectMode ? toggleSelected(job.id) : openJob(job)))}
                  className="admin-card jo-mrow ui-row-open"
                  style={selected ? { borderColor: '#2563eb', background: '#eff6ff' } : undefined}
                  role="button"
                >
                  <div className="jo-mrow__top">
                    <span className="jo-mrow__no">{job.jobNumber || job.id.slice(0, 8).toUpperCase()}</span>
                    <div className="jo-mrow__status" onClick={(e) => e.stopPropagation()}>
                      <JobStatusBadge job={job} />
                      <Dropdown menu={{ items: jobActionItems(job) }} trigger={['click']} placement="bottomRight">
                        <Button type="text" size="small" icon={<MoreOutlined style={{ fontSize: 18 }} />} aria-label="More actions" />
                      </Dropdown>
                    </div>
                  </div>
                  <div className="jo-mrow__title">{job.title}</div>
                  <div className="jo-mrow__meta">
                    <span className="jo-mrow__client">{job.clientName || 'No client'}</span>
                    <DuePill job={job} />
                  </div>
                  {listTab === 'drafts' ? (
                    job.draftStage ? <div className="jo-mrow__stage">{job.draftStage}</div> : null
                  ) : job.opsTotal ? (
                    <div className="jo-mrow__progress">
                      <div className="jo-mrow__bar">
                        <span style={{ width: `${Math.round(((job.opsCompleted || 0) / job.opsTotal) * 100)}%` }} />
                      </div>
                      <span className="jo-mrow__ops">
                        {job.opsCompleted || 0}/{job.opsTotal} ops
                      </span>
                    </div>
                  ) : null}
                </div>
              );
            })}
        </div>
      ) : viewMode === 'cards' ? (
        <div className="jo-cards">
          {loading && (
            <div className="page-spinner">
              <Spin />
            </div>
          )}
          {!loading && filtered.length === 0 && (
            <div className="jo-cards__empty">No job orders match your filters yet</div>
          )}
          {!loading &&
            filtered.map((job) => (
              <JobCard
                key={job.id}
                job={job}
                isDraftTab={listTab === 'drafts'}
                selected={selectedKeys.includes(job.id)}
                actions={jobActionItems(job)}
                onClick={() => (selectMode ? toggleSelected(job.id) : openJob(job))}
              />
            ))}
          {!loading && filtered.length > 0 && (
            <div className="jo-cards__total">
              {filtered.length} job{filtered.length === 1 ? '' : 's'}
            </div>
          )}
        </div>
      ) : (
      <Table
        className="jo-list-table"
        rowKey="id"
        size="small"
        columns={columns}
        dataSource={filtered}
        loading={loading}
        pagination={{
          pageSize: 20,
          showSizeChanger: true,
          pageSizeOptions: [10, 20, 50],
          showTotal: (total) => `${total} job${total === 1 ? '' : 's'}`,
        }}
        locale={{ emptyText: 'No job orders match your filters yet' }}
        showSorterTooltip={false}
        tableLayout="fixed"
        rowSelection={
          selectMode
            ? {
                selectedRowKeys: selectedKeys,
                onChange: (keys) => setSelectedKeys(keys.map(String)),
                preserveSelectedRowKeys: true,
              }
            : undefined
        }
        onRow={(record) => rowOpenProps(() => (selectMode ? toggleSelected(record.id) : openJob(record)))}
      />
      )}
      {isPhone ? (
        <Drawer
          className="sched-f-drawer"
          rootClassName="sched-f-drawer"
          placement="bottom"
          size="auto"
          open={filtersOpen}
          onClose={() => setFiltersOpen(false)}
          closable={false}
          title={null}
          styles={{
            section: {
              padding: 0,
              borderRadius: '16px 16px 0 0',
              overflow: 'hidden',
              background: '#f1f5f9',
            },
            header: { display: 'none', padding: 0 },
            body: { padding: 0, background: '#f1f5f9' },
          }}
        >
          <div className="sched-f">
            <div className="sched-f__handle" />
            <div className="sched-f__head">
              <div>
                <div className="sched-f__title">Filters</div>
                <div className="sched-f__sub">{activeFilterCount ? `${activeFilterCount} on` : 'None on'}</div>
              </div>
              {activeFilterCount ? (
                <button type="button" className="sched-f__text" onClick={clearJobFilters}>
                  Clear
                </button>
              ) : null}
              <button
                type="button"
                className="sched-m__icon"
                onClick={() => setFiltersOpen(false)}
                aria-label="Close"
              >
                <CloseOutlined />
              </button>
            </div>
            <div className="sched-f__card">
              <div className="sched-f__label">Narrow by</div>
              <div className="sched-f__rows">
                {listTab === 'production' ? (
                <div className="sched-f__row">
                  <span className="sched-f__row-k">Status</span>
                  <Select
                    mode="multiple"
                    allowClear
                    variant="borderless"
                    maxTagCount={1}
                    placeholder="All"
                    className="sched-f__select"
                    value={statusFilter}
                    onChange={setStatusFilter}
                    options={PRODUCTION_STATUS_OPTIONS}
                  />
                </div>
                ) : null}
                {listTab === 'production' ? (
                <div className="sched-f__row">
                  <span className="sched-f__row-k">Material</span>
                  <Select
                    allowClear
                    variant="borderless"
                    placeholder="All"
                    className="sched-f__select"
                    value={awaitingMaterialOnly ? 'awaiting' : undefined}
                    onChange={(v) => setAwaitingMaterialOnly(v === 'awaiting')}
                    options={[{ value: 'awaiting', label: 'Awaiting material' }]}
                  />
                </div>
                ) : null}
                <div className="sched-f__row">
                  <span className="sched-f__row-k">Client</span>
                  <Select
                    mode="multiple"
                    allowClear
                    showSearch
                    variant="borderless"
                    optionFilterProp="label"
                    maxTagCount={1}
                    placeholder="All"
                    className="sched-f__select"
                    value={clientFilter}
                    onChange={setClientFilter}
                    options={clientOptions}
                  />
                </div>
              </div>
            </div>
            <button type="button" className="sched-f__done" onClick={() => setFiltersOpen(false)}>
              Done
            </button>
          </div>
        </Drawer>
      ) : null}

      <MarkDeliveredModal job={deliverJob} onClose={() => setDeliverJob(null)} onDone={fetchJobs} />

      {cancelJob ? (
        <CancelJobModal
          job={cancelJob}
          open
          onClose={() => setCancelJob(null)}
          onCancelled={async () => {
            setCancelJob(null);
            setSelectedKeys((keys) => keys.filter((k) => k !== cancelJob.id));
            await fetchJobs();
          }}
        />
      ) : null}
    </div>
  );
}

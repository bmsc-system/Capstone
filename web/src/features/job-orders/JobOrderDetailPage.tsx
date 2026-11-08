import type { CSSProperties } from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Button,
  Collapse,
  Col,
  DatePicker,
  Form,
  Input,
  InputNumber,
  Modal,
  Progress,
  Row,
  Select,
  Space,
  Spin,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  CheckCircleFilled,
  CarOutlined,
  CheckOutlined,
  EditOutlined,
  FileTextOutlined,
  InfoCircleOutlined,
  PrinterOutlined,
  ReloadOutlined,
  StopOutlined,
  InboxOutlined,
  RedoOutlined,
} from '@ant-design/icons';
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router-dom';
import dayjs, { type Dayjs } from 'dayjs';
import { formatShop, shopToday } from '../../utils/shopTime';
import './opCard.css';
import { isJobFinished, isJobReleasedForDelivery, nextDeliveryStep } from './jobStage';
import MarkDeliveredModal from './MarkDeliveredModal';
import BookletGuide from './BookletGuide';
import { jobOrdersApi } from '../../api/jobOrders.api';
import { operationsApi } from '../../api/operations.api';
import { notificationsApi } from '../../api/notifications.api';
import { getErrorMessage } from '../../api/client';
import { useAuth } from '../../hooks/useAuth';
import StatusPill from '../../components/StatusPill';
import { IdChip, PageSkeleton, FormModal } from '../../components/ui';
import { JOB_FLAG, JOB_STATUS, OPERATION_STATUS, type StatusDef } from '../../theme/statuses';
import { colors, tones } from '../../theme/tokens';
import MaterialWaitTag from '../../components/MaterialWaitTag';
import MaterialDelayTag from '../../components/MaterialDelayTag';
import NeedsReplanTag from '../../components/NeedsReplanTag';
import { CrewChips } from '../../components/PersonAvatar';
import type {
  JobOrder,
  JobOrderStatus,
  MachineDowntimeRecord,
  MaterialPurchase,
  NotificationLog,
  Operation,
  OperationStatus,
  ReworkReasonCategory,
  ScheduleMove,
} from '../../types';
import { formatDifferenceFromTarget } from '../analytics/analyticsFormat';
import { WorkerPageHeader } from '../../layouts/WorkerLayout';
import { jobOrdersListPath } from './jobOrderListPaths';
import OutsourcedOperationPanel from './OutsourcedOperationPanel';
import OrderMaterialsModal from '../supplier-orders/OrderMaterialsModal';
import {
  JobMaterialsTable,
  rowsFromPlanned,
  rowsFromPurchases,
  rowsFromSummary,
} from './MaterialOrdersSummary';
import RescheduleModal, {
  RESCHEDULE_REASON_OPTIONS,
  rescheduleReasonLabel,
} from './RescheduleModal';
import CancelJobModal, { cancelReasonLabel } from './CancelJobModal';
import { jobOrderPrintBlockers } from '../reports/jobOrderPrintReadiness';
import JobAuditHistory from '../audit/JobAuditHistory';
import { confirmAction } from '../../utils/confirmAction';
import JobNotice from './JobNotice';

const RESCHEDULE_REASON_CODES = new Set(
  RESCHEDULE_REASON_OPTIONS.map((o) => o.value as string)
);

const { Text } = Typography;

const STATUS_PILL = JOB_STATUS as Record<JobOrderStatus, StatusDef>;
const OP_STATUS = OPERATION_STATUS as Record<OperationStatus, StatusDef>;

const REWORK_CATEGORY_OPTIONS: { value: ReworkReasonCategory; label: string }[] = [
  { value: 'DIMENSION_OUT_OF_TOLERANCE', label: 'Dimension out of tolerance' },
  { value: 'SURFACE_FINISH', label: 'Surface finish' },
  { value: 'WRONG_MATERIAL', label: 'Wrong material' },
  { value: 'MACHINE_FAULT', label: 'Machine fault' },
  { value: 'OPERATOR_ERROR', label: 'Operator error' },
  { value: 'OTHER', label: 'Other' },
];

function reworkCategoryLabel(cat: string | null | undefined) {
  if (!cat) return null;
  return REWORK_CATEGORY_OPTIONS.find((o) => o.value === cat)?.label || cat;
}

const GREEN = '#16a34a';
const NAVY = '#0f1c2e';
const BORDER = '#e2e8f0';
const MUTED = '#64748b';

const PART_STAGE_LABEL: Record<string, string> = {
  RAW_MATERIAL: 'Raw material',
  CLIENT_SUPPLIED_ITEM: 'Client supplied item',
  WORK_IN_PROCESS: 'Work in process',
  CUT: 'Cut',
  BLANK: 'Blank',
  FORMED: 'Formed',
  MACHINED: 'Machined',
  ASSEMBLED: 'Assembled',
  HEAT_TREATED: 'Heat treated',
  FINISHED: 'Finished',
};

const JOB_TYPE_LABEL: Record<string, string> = {
  FABRICATION: 'Fabrication',
  MACHINING: 'Machining',
  REPAIR: 'Repair',
};

const NOTIF_UPDATE_LABEL: Record<string, string> = {
  JOB_RECEIVED: 'Job received',
  JOB_STARTED: 'Job started',
  JOB_HALFWAY: 'Halfway update',
  JOB_FINAL_STAGE: 'Final stage',
  JOB_COMPLETED: 'Job finished',
  JOB_DELIVERED: 'Ready for delivery',
  JOB_DELAYED: 'Delayed',
};

const NOTIF_STATUS_LABEL: Record<string, string> = {
  PENDING: 'Pending',
  SENT: 'Sent',
  NOT_SENT: 'Not sent',
  FAILED: 'Failed',
  SKIPPED: 'Skipped',
};

const NOTIF_CHANNEL_LABEL: Record<string, string> = {
  EMAIL: 'Email',
};

const TIME_EVENT_LABEL: Record<string, string> = {
  START: 'Started',
  PAUSE: 'Paused',
  RESUME: 'Resumed',
  COMPLETE: 'Finished',
};

const PAUSE_REASON_LABEL: Record<string, string> = {
  END_OF_SHIFT: 'End of shift',
  BREAK: 'Break',
  MACHINE_DOWN: 'Machine breakdown',
  WAITING_MATERIAL: 'Waiting for material',
  WAITING_PRIOR_OPERATION: 'Waiting on prior operation',
  OTHER: 'Other',
};

function friendlyEnum(value: string | null | undefined, map: Record<string, string>) {
  if (!value) return '—';
  return map[value] || value.replace(/_/g, ' ').toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
}

function dash(v: string | number | null | undefined): string {
  if (v == null || v === '') return '—';
  return String(v);
}

function fmtDate(v?: string | null) {
  return formatShop(v, 'MMM D, YYYY');
}

function fmtDateTime(v?: string | null) {
  return formatShop(v, 'MMM D, YYYY h:mm A');
}

/** "Mar 19 · 11:28 AM – 1:58 PM", or both dates when it runs over days. */
function fmtRange(start?: string | null, end?: string | null) {
  if (!start && !end) return null;
  if (!start || !end) return formatShop(start || end, 'MMM D, h:mm A');
  const sameDay = formatShop(start, 'YYYY-MM-DD') === formatShop(end, 'YYYY-MM-DD');
  return sameDay
    ? `${formatShop(start, 'MMM D')} · ${formatShop(start, 'h:mm A')} – ${formatShop(end, 'h:mm A')}`
    : `${formatShop(start, 'MMM D, h:mm A')} – ${formatShop(end, 'MMM D, h:mm A')}`;
}

const SENT_LATE_MINUTES = 10;

/** "received 3:40 PM (2h 5m later)" when the phone sent the action after it happened. */
function ReceivedAt({ eventAt, receivedAt }: { eventAt: string; receivedAt?: string | null }) {
  if (!receivedAt) return null;
  const minutes = Math.round(dayjs(receivedAt).diff(dayjs(eventAt), 'minute', true));
  const sameDay = formatShop(receivedAt, 'YYYY-MM-DD') === formatShop(eventAt, 'YYYY-MM-DD');
  const when = formatShop(receivedAt, sameDay ? 'h:mm A' : 'MMM D, YYYY h:mm A');
  if (minutes < SENT_LATE_MINUTES) return <> · received {when}</>;
  const gap =
    minutes >= 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m` : `${minutes}m`;
  return (
    <span style={{ color: '#d46b08' }}>
      {' · '}received {when} ({gap} later)
    </span>
  );
}

/** A failed or not-sent client email that no later attempt of the same update has sent. */
function isUnresolvedFailure(n: NotificationLog, all: NotificationLog[]): boolean {
  if (n.status !== 'FAILED' && n.status !== 'NOT_SENT') return false;
  const at = (x: NotificationLog) => new Date(x.createdAt || x.sentAt || 0).getTime();
  return !all.some(
    (o) => o.id !== n.id && o.milestone === n.milestone && o.status === 'SENT' && at(o) >= at(n)
  );
}

function fmtHours(v?: number | null) {
  if (v == null || Number.isNaN(v)) return '—';
  return `${v.toFixed(1)}h`;
}

function fmtVariance(hours?: number | null, pct?: number | null) {
  return formatDifferenceFromTarget(hours, pct);
}

function fmtMoney(n?: number | null) {
  if (n == null) return '—';
  return `₱${Number(n).toLocaleString('en-PH', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function cardStyle(extra?: CSSProperties): CSSProperties {
  return {
    background: '#fff',
    border: `1px solid ${BORDER}`,
    borderRadius: 14,
    padding: 16,
    boxShadow: '0 1px 2px rgba(15,23,42,0.04)',
    ...extra,
  };
}

const SUBHEAD: CSSProperties = {
  fontSize: 11,
  fontWeight: 700,
  color: MUTED,
  textTransform: 'uppercase',
  letterSpacing: 0.4,
  marginBottom: 6,
};

const DETAIL_GRID: CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'auto 1fr',
  gap: '8px 16px',
  fontSize: 13,
};

const ACTIVITY_ROW: CSSProperties = {
  padding: '8px 0',
  borderTop: `1px solid ${BORDER}`,
  fontSize: 12,
  color: MUTED,
};

export default function JobOrderDetailPage() {
  const { id } = useParams();
  const { isWorker } = useAuth();
  if (isWorker && id) {
    return <Navigate to={`/my-assignments/${id}`} replace />;
  }
  return <JobOrderDetailView />;
}

function JobOrderDetailView() {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { isAdmin, canOfficeWrite, isWorker } = useAuth();
  const canManage = isAdmin || canOfficeWrite;

  const [job, setJob] = useState<JobOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [reworkLoading, setReworkLoading] = useState<string | null>(null);
  const [delivering, setDelivering] = useState(false);
  const [deliveredOpen, setDeliveredOpen] = useState(false);
  const [notifications, setNotifications] = useState<NotificationLog[] | null>(null);
  const [purchases, setPurchases] = useState<MaterialPurchase[]>([]);
  const [breakdowns, setBreakdowns] = useState<MachineDowntimeRecord[]>([]);
  const [purchaseOpen, setPurchaseOpen] = useState(false);
  const [draftNotice, setDraftNotice] = useState<{
    id: string;
    supplierName: string;
    lineCount: number;
    jobCount: number;
  } | null>(null);
  const [rescheduleOpen, setRescheduleOpen] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [scheduleMoves, setScheduleMoves] = useState<ScheduleMove[]>([]);
  const [invoiceMode, setInvoiceMode] = useState<'record' | 'view' | 'correct' | null>(null);
  const [invoiceSaving, setInvoiceSaving] = useState(false);
  const [invoiceForm] = Form.useForm();
  const fetchJob = useCallback(async () => {
    if (!id) return;
    const { data } = await jobOrdersApi.get(id);
    setJob(data);
  }, [id]);

  const fetchScheduleMoves = useCallback(async () => {
    if (!id || !canManage) return;
    try {
      const { data } = await jobOrdersApi.listScheduleMoves(id);
      setScheduleMoves(data);
    } catch {
      setScheduleMoves([]);
    }
  }, [id, canManage]);

  const hasJob = Boolean(job);
  useEffect(() => {
    if (!hasJob || !location.hash) return;
    document.getElementById(location.hash.slice(1))?.scrollIntoView({ behavior: 'smooth' });
  }, [hasJob, location.hash]);

  const fetchPurchases = useCallback(async () => {
    if (!id || !canManage) return;
    try {
      const { data } = await jobOrdersApi.listMaterialPurchases(id);
      setPurchases(data);
    } catch {
      setPurchases([]);
    }
  }, [id, canManage]);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        await fetchJob();
        if (canManage) {
          await fetchPurchases();
          await fetchScheduleMoves();
          try {
            const { data } = await jobOrdersApi.listBreakdowns(id);
            if (!cancelled) setBreakdowns(data);
          } catch {
            if (!cancelled) setBreakdowns([]);
          }
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
  }, [id, fetchJob, fetchPurchases, fetchScheduleMoves, canManage]);

  const ops = useMemo(
    () => [...(job?.operations || [])].sort((a, b) => a.sequenceNo - b.sequenceNo),
    [job]
  );
  const jobStarted = ops.some((o) => !!o.actualStart);
  const placedLines = useMemo(
    () => purchases.filter((p) => p.status !== 'DRAFT' && p.status !== 'CANCELLED'),
    [purchases]
  );
  const outstandingLines = useMemo(
    () => placedLines.filter((p) => !p.dateReceived),
    [placedLines]
  );

  useEffect(() => {
    if (!id || !canManage) {
      setNotifications([]);
      return;
    }
    let cancelled = false;
    notificationsApi
      .list({ jobOrderId: id, limit: 200 })
      .then(({ data }) => {
        if (!cancelled) setNotifications(data);
      })
      .catch(() => {
        if (!cancelled) setNotifications([]);
      });
    return () => {
      cancelled = true;
    };
  }, [id, canManage, ops.length, job?.status]);

  const totals = useMemo(() => {
    let est = 0;
    let worked = 0;
    let estN = 0;
    let workedN = 0;
    for (const op of ops) {
      if (op.estimatedHours != null) {
        est += op.estimatedHours;
        estN += 1;
      }
      if (op.actualWorkedHours != null) {
        worked += op.actualWorkedHours;
        workedN += 1;
      }
    }
    return {
      estimated: estN ? est : null,
      worked: workedN ? worked : null,
      varianceHours: estN && workedN ? worked - est : null,
    };
  }, [ops]);

  const backTo = isWorker
    ? '/my-assignments'
    : jobOrdersListPath(job?.status);

  const [reworkOp, setReworkOp] = useState<Operation | null>(null);
  const [reworkCategory, setReworkCategory] = useState<ReworkReasonCategory | undefined>();
  const [reworkNote, setReworkNote] = useState('');
  const [reworkSaving, setReworkSaving] = useState(false);

  const handleRework = (op: Operation) => {
    setReworkCategory(undefined);
    setReworkNote('');
    setReworkOp(op);
  };

  const submitRework = async () => {
    const op = reworkOp;
    if (!op) return;
    if (!reworkCategory) {
      message.error('Category is required');
      return;
    }
    if (reworkCategory === 'OTHER' && !reworkNote.trim()) {
      message.error('Note is required when category is Other');
      return;
    }
    setReworkSaving(true);
    setReworkLoading(op.id);
    try {
      await operationsApi.rework(op.id, {
        category: reworkCategory,
        reason: reworkNote.trim() || undefined,
      });
      message.success('Redo operation created');
      setReworkOp(null);
      await fetchJob();
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setReworkSaving(false);
      setReworkLoading(null);
    }
  };

  const openRecordInvoice = () => {
    if (!job) return;
    invoiceForm.resetFields();
    invoiceForm.setFieldsValue({
      invoiceNumber: '',
      invoiceDate: shopToday(),
      amount: job.amount ?? null,
    });
    setInvoiceMode('record');
  };

  const openCorrectInvoice = () => {
    if (!job?.salesInvoice) return;
    const inv = job.salesInvoice;
    invoiceForm.resetFields();
    invoiceForm.setFieldsValue({
      invoiceNumber: inv.invoiceNumber,
      invoiceDate: dayjs(inv.invoiceDate),
      amount: inv.amount,
      reason: '',
    });
    setInvoiceMode('correct');
  };

  const onSaveInvoice = async (values: {
    invoiceNumber: string;
    invoiceDate: Dayjs;
    amount: number;
    reason?: string;
  }) => {
    if (!job) return;
    setInvoiceSaving(true);
    const payload = {
      invoiceNumber: values.invoiceNumber.trim(),
      invoiceDate: values.invoiceDate.format('YYYY-MM-DD'),
      amount: values.amount,
    };
    try {
      if (invoiceMode === 'correct') {
        await jobOrdersApi.correctInvoice(job.id, { ...payload, reason: (values.reason || '').trim() });
        message.success('Sales invoice corrected');
      } else {
        const { data } = await jobOrdersApi.recordInvoice(job.id, payload);
        message.success(`Sales invoice ${data.invoiceNumber} recorded`);
      }
      setInvoiceMode(null);
      await fetchJob();
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setInvoiceSaving(false);
    }
  };

  const handleSetMaterialNotRequired = () => {
    if (!job) return;
    confirmAction({
      title: 'Set Materials needed to Not required?',
      content: 'For material the client supplies or the shop has in stock.',
      okText: 'Set Not required',
      onOk: async () => {
        try {
          await jobOrdersApi.update(job.id, { materialStatus: 'NOT_REQUIRED' });
          message.success('Materials needed set to Not required');
          await fetchJob();
        } catch (err) {
          message.error(getErrorMessage(err));
          throw err;
        }
      },
    });
  };

  const handleForDelivery = async () => {
    if (!job) return;
    setDelivering(true);
    try {
      await jobOrdersApi.setForDelivery(job.id);
      message.success('Set For Delivery. The client is notified that it is ready for delivery.');
      await fetchJob();
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setDelivering(false);
    }
  };

  if (loading && !job) {
    return (
      <PageSkeleton variant="detail" />
    );
  }

  if (!job) {
    return (
      <div style={{ padding: 24 }}>
        <Text type="secondary">Job order not found.</Text>
        <div style={{ marginTop: 12 }}>
          <Button onClick={() => navigate(backTo)}>Back</Button>
        </div>
      </div>
    );
  }

  const status = STATUS_PILL[job.status] || STATUS_PILL.SCHEDULED;
  const overdue =
    !isJobFinished(job.status) &&
    job.status !== 'DRAFT' &&
    dayjs(job.dueDate).isBefore(shopToday(), 'day');
  const isDraft = job.status === 'DRAFT';
  const printBlockers = jobOrderPrintBlockers(job);
  const isInvoicedOrDelivered = Boolean(job.salesInvoice) || isJobReleasedForDelivery(job);
  const deliveryStep = isAdmin ? nextDeliveryStep(job.status) : null;
  const materialRows = [
    ...rowsFromPlanned(job.plannedMaterials),
    ...(canManage ? rowsFromPurchases(purchases) : rowsFromSummary(job.materialLines || [])),
  ];
  const canCorrectInvoice =
    canOfficeWrite && Boolean(job.salesInvoice) && !isJobReleasedForDelivery(job);
  const isNotStarted = (op: Operation) =>
    op.status !== 'IN_PROGRESS' && op.status !== 'COMPLETED' && !op.actualStart;
  const canReschedule =
    !isDraft &&
    !isJobFinished(job.status) &&
    job.status !== 'CANCELLED' &&
    (job.operations || []).some(isNotStarted);
  // Office Staff cancel only a job still to be planned; after release only the Admin.
  const canCancel =
    (isAdmin || (canOfficeWrite && isDraft)) &&
    !isJobFinished(job.status) &&
    job.status !== 'CANCELLED' &&
    !job.salesInvoice;
  const canArchive =
    canManage &&
    (isJobFinished(job.status) || job.status === 'CANCELLED') &&
    !job.isArchived &&
    !job.archivedAt;
  const canUnarchive = canManage && Boolean(job.isArchived || job.archivedAt);

  const handleArchive = () => {
    confirmAction({
      title: 'Archive this job order?',
      content: 'You can find it later under Archived.',
      okText: 'Archive',
      onOk: async () => {
        try {
          await jobOrdersApi.archive(job.id);
          message.success('Job archived');
          await fetchJob();
        } catch (err) {
          message.error(getErrorMessage(err));
          throw err;
        }
      },
    });
  };

  const handleUnarchive = async () => {
    try {
      await jobOrdersApi.unarchive(job.id);
      message.success('Job unarchived');
      await fetchJob();
    } catch (err) {
      message.error(getErrorMessage(err));
    }
  };
  const opsTotal = (job.operations || []).length;
  const opsDone = (job.operations || []).filter((op) => op.status === 'COMPLETED').length;

  return (
    <div className={`jo-detail-page${isWorker ? '' : ' ui-page ui-page--stack'}`} style={{ maxWidth: 1200, margin: '0 auto', padding: isWorker ? '0 12px 24px' : undefined }}>
      {isWorker ? (
        <div style={{ margin: '0 -12px 12px' }}>
          <WorkerPageHeader
            title="Job Order"
            subtitle={job.jobNumber || job.id.slice(0, 8).toUpperCase()}
            onBack={() => navigate(backTo)}
          />
        </div>
      ) : null}

      {/* Header card: number, status, key facts, actions, progress */}
      <div className="jo-head" style={cardStyle({ marginBottom: 16 })}>
        <div className="jo-head__row">
          <div className="jo-head__main">
            <div className="jo-head__chips">
              <IdChip>{job.jobNumber || job.id.slice(0, 8).toUpperCase()}</IdChip>
              {overdue ? (
                <StatusPill tone={JOB_FLAG.overdue.tone}>{JOB_FLAG.overdue.label}</StatusPill>
              ) : null}
              <StatusPill tone={status.tone}>
                {isDraft && !isAdmin ? 'Pending' : status.label}
                {isDraft && job.draftStage ? ` · ${job.draftStage}` : ''}
              </StatusPill>
              <NeedsReplanTag job={job} compact={false} />
              <MaterialWaitTag wait={job} compact={false} />
              <MaterialDelayTag job={job} compact={false} />
            </div>
            <h2 className="jo-head__title">{job.title}</h2>
            <div className="jo-head__facts">
              <span>{dash(job.clientName)}</span>
              <span className={overdue ? 'tone-text--red' : undefined}>Due {fmtDate(job.dueDate)}</span>
              {job.quantity != null && (
                <span>
                  Qty {job.quantity}
                  {job.unitOfMeasure ? ` ${job.unitOfMeasure}` : ''}
                </span>
              )}
              {!isWorker && job.amount != null && <span className="ui-tabular">{fmtMoney(job.amount)}</span>}
            </div>
          </div>
        {!isWorker && (
        <Space wrap align="center" className="jo-head__actions">
          {isDraft && isAdmin && (
            <Button
              type="primary"
              onClick={() => navigate(`/job-orders/${job.id}/plan`)}
            >
              Plan
            </Button>
          )}
          {canOfficeWrite && isDraft && (
            <Button
              icon={<EditOutlined />}
              onClick={() => navigate(`/job-orders/${job.id}/edit`)}
            >
              Edit details
            </Button>
          )}
          <Tooltip
            title={
              printBlockers.length ? (
                <div>
                  <div style={{ fontWeight: 600 }}>Can't print yet</div>
                  {printBlockers.map((b) => (
                    <div key={b}>{b}</div>
                  ))}
                </div>
              ) : null
            }
          >
            <Button
              icon={<PrinterOutlined />}
              disabled={printBlockers.length > 0}
              onClick={() =>
                navigate(`/job-orders/${job.id}/print`, { state: { from: location.pathname + location.search } })
              }
            >
              Print
            </Button>
          </Tooltip>
          {canManage && job.salesInvoice && (
            <Button icon={<FileTextOutlined />} onClick={() => setInvoiceMode('view')}>
              Sales invoice {job.salesInvoice.invoiceNumber}
            </Button>
          )}
          {canOfficeWrite && job.status === 'COMPLETED' && !job.salesInvoice && (
            <Button type="primary" icon={<FileTextOutlined />} onClick={openRecordInvoice}>
              Record sales invoice
            </Button>
          )}
          {deliveryStep === 'FOR_DELIVERY' && (
            <Tooltip
              title={
                job.salesInvoice
                  ? 'The client is told the job is ready for delivery, and the sales invoice locks.'
                  : 'Office Staff must record the sales invoice first'
              }
            >
              <Button
                type={job.salesInvoice ? 'primary' : 'default'}
                icon={<CarOutlined />}
                loading={delivering}
                disabled={!job.salesInvoice}
                onClick={handleForDelivery}
              >
                Set For Delivery
              </Button>
            </Tooltip>
          )}
          {deliveryStep === 'DELIVERED' && (
            <Tooltip title="Confirm the client received it. On-time is measured on the delivery date.">
              <Button type="primary" icon={<CheckOutlined />} loading={delivering} onClick={() => setDeliveredOpen(true)}>
                Mark as Delivered
              </Button>
            </Tooltip>
          )}
          {isAdmin && canReschedule && (
            <Button icon={<ReloadOutlined />} onClick={() => setRescheduleOpen(true)}>
              Reschedule
            </Button>
          )}
          {canCancel && (
            <Button danger icon={<StopOutlined />} onClick={() => setCancelOpen(true)}>
              Cancel job order
            </Button>
          )}
          {canArchive && (
            <Button icon={<InboxOutlined />} onClick={handleArchive}>
              Archive
            </Button>
          )}
          {canUnarchive && (
            <Button icon={<InboxOutlined />} onClick={handleUnarchive}>
              Unarchive
            </Button>
          )}
        </Space>
        )}
        </div>
        {opsTotal > 0 && (
          <div className="jo-head__progress">
            <span className="jo-head__progress-label">Production progress</span>
            <Progress
              percent={Math.round((opsDone / opsTotal) * 100)}
              showInfo={false}
              strokeColor={tones.blue.solid}
              railColor={colors.surfaceSunken}
              size="small"
              style={{ flex: 1, margin: 0 }}
            />
            <span className="jo-head__progress-count ui-tabular">
              {opsDone}/{opsTotal} operations · {Math.round((opsDone / opsTotal) * 100)}%
            </span>
          </div>
        )}
      </div>

      {job.status === 'CANCELLED' ? (
        <JobNotice
          tone="muted"
          title="Cancelled"
          detail={[
            cancelReasonLabel(job.cancelReason) + (job.cancelNote ? ` — ${job.cancelNote}` : ''),
            job.cancelledByName,
            job.cancelledAt ? fmtDateTime(job.cancelledAt) : null,
            purchases.some((p) => p.reviewAfterJobCancel) ? 'supplier lines to review' : null,
          ]
            .filter(Boolean)
            .join(' · ')}
        />
      ) : null}

      {isAdmin && job.needsReplan ? (
        <JobNotice tone="warn" title="Needs re-plan" detail={job.needsReplanReason || undefined} />
      ) : null}

      {canManage && job.materialDelay ? (
        <JobNotice
          tone="info"
          title={
            {
              MATERIAL: 'Moved later for materials',
              RESCHEDULED: 'Rescheduled',
              OVERRUN: 'Moved later: earlier work ran long',
              BREAKDOWN: 'Moved: machine breakdown',
            }[job.materialDelay.kind]
          }
          detail={
            <>
              {[
                job.materialDelay.reason
                  ? /^[A-Z_]+$/.test(job.materialDelay.reason)
                    ? rescheduleReasonLabel(job.materialDelay.reason)
                    : job.materialDelay.reason
                  : null,
                job.materialDelay.currentStart &&
                job.materialDelay.currentStart !== job.materialDelay.originalStart
                  ? `was ${fmtDateTime(job.materialDelay.originalStart)}, now ${fmtDateTime(job.materialDelay.currentStart)}`
                  : null,
              ]
                .filter(Boolean)
                .join(' · ')}
              {job.materialDelay.supplierOrderId ? (
                <>
                  {' · '}
                  <a onClick={() => navigate(`/supplier-orders/${job.materialDelay!.supplierOrderId}`)}>
                    {job.materialDelay.poNumber || 'Supplier order'}
                  </a>
                </>
              ) : null}
            </>
          }
        />
      ) : null}

      {canManage &&
      job.materialStatus !== 'NOT_REQUIRED' &&
      !jobStarted &&
      (placedLines.length === 0 || outstandingLines.length > 0) ? (
        <JobNotice
          tone="warn"
          title={
            placedLines.length === 0
              ? purchases.some((p) => p.status === 'DRAFT')
                ? 'Can’t start yet: supplier order not issued'
                : 'Can’t start yet: no materials ordered'
              : 'Can’t start yet: waiting for materials'
          }
          detail={
            placedLines.length === 0
              ? undefined
              : outstandingLines.map((p) => p.materialName).join(', ')
          }
          action={
            placedLines.length === 0 && isAdmin ? (
              <Button size="small" onClick={handleSetMaterialNotRequired}>
                Set Not required
              </Button>
            ) : undefined
          }
        />
      ) : null}

      <Row gutter={[16, 16]} align="top" className="jo-detail-grid" style={{ marginBottom: 24 }}>
        <Col xs={24} lg={16}>
          <div id="supplier-orders" className="jo-dcard" style={{ ...cardStyle(), marginBottom: 16, scrollMarginTop: 80 }}>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: 12,
                flexWrap: 'wrap',
                marginBottom: 12,
              }}
            >
              <div style={{ fontWeight: 800, fontSize: 14, color: NAVY }}>Materials</div>
              {canOfficeWrite && job.materialStatus !== 'NOT_REQUIRED' ? (
                <Button type="primary" onClick={() => setPurchaseOpen(true)}>
                  Order materials
                </Button>
              ) : null}
            </div>
            <div style={{ fontSize: 13, color: MUTED, marginBottom: 10 }}>
              Materials needed:{' '}
              <strong style={{ color: NAVY }}>
                {job.materialStatus === 'NOT_REQUIRED'
                  ? 'Not required'
                  : 'To order'}
              </strong>
            </div>
            {materialRows.length || job.materialStatus !== 'NOT_REQUIRED' ? (
              <JobMaterialsTable
                lines={materialRows}
                renderLineAction={(row) =>
                  canOfficeWrite &&
                  row.supplierOrderId &&
                  row.status === 'ORDERED' ? (
                    <Tooltip title="Deliveries arrive complete: receive the whole order on its page.">
                      <Link to={`/supplier-orders/${row.supplierOrderId}`}>Receive order</Link>
                    </Tooltip>
                  ) : null
                }
              />
            ) : null}
          </div>

          <div className="jo-dcard" style={cardStyle({ marginBottom: 16 })}>
            <div style={{ fontWeight: 800, fontSize: 14, color: NAVY, marginBottom: 12 }}>
              Operations
            </div>
            {ops.length === 0 ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                <Text type="secondary" style={{ fontSize: 13 }}>
                  {!isDraft
                    ? 'No operations on this job.'
                    : isAdmin
                      ? 'No operations yet. Add them in the planning step.'
                      : 'No operations yet. The Admin adds them in the planning step.'}
                </Text>
                {isDraft && isAdmin ? (
                  <Button size="small" onClick={() => navigate(`/job-orders/${job.id}/plan`)}>
                    Open planning
                  </Button>
                ) : null}
              </div>
            ) : (
              <div style={{ position: 'relative', paddingLeft: 4 }}>
                {ops.map((op, index) => {
                  const done = op.status === 'COMPLETED';
                  const active = op.status === 'IN_PROGRESS';
                  const isLast = index === ops.length - 1;
                  const opSt = OP_STATUS[op.status] || OP_STATUS.PENDING;
                  const machine =
                    op.machineUnitLabel ||
                    op.machineTypeName ||
                    op.machineTypeCode ||
                    null;
                  const logs = [...(op.timeLogs || [])].sort(
                    (a, b) => dayjs(a.eventAt).valueOf() - dayjs(b.eventAt).valueOf()
                  );

                  return (
                    <div key={op.id} className="jo-op-row" style={{ display: 'flex', gap: 16, position: 'relative' }}>
                      {!isLast && (
                        <div
                          className="jo-op-line"
                          style={{
                            position: 'absolute',
                            left: 15,
                            top: 36,
                            bottom: 0,
                            width: 2,
                            background: done ? GREEN : BORDER,
                          }}
                        />
                      )}
                      <div
                        className="jo-op-dot"
                        style={{
                          width: 32,
                          height: 32,
                          borderRadius: '50%',
                          flexShrink: 0,
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          fontWeight: 800,
                          fontSize: 13,
                          zIndex: 1,
                          background: done ? GREEN : active ? '#2563eb' : 'rgba(217,119,6,0.15)',
                          color: done || active ? '#fff' : '#d97706',
                          border: done || active ? 'none' : '2px solid #d97706',
                        }}
                      >
                        {done ? <CheckCircleFilled /> : op.sequenceNo}
                      </div>

                      <div className={`jo-op-body op-card${active ? ' is-active' : ''}`}>
                        <div className="op-card__head">
                          <div className="op-card__title">
                            <span className="op-card__name">{op.operationName}</span>
                            {op.reworkOfOperationId ? <span className="op-card__redo">Redo</span> : null}
                          </div>
                          <StatusPill tone={opSt.tone} compact>
                            {op.isOutsourced && active ? 'Sent out' : opSt.label}
                            {active && op.isPaused ? ' · Paused' : ''}
                          </StatusPill>
                        </div>
                        {op.isOutsourced ? null : (
                          <div className="op-card__who">
                            <span className="op-card__machine">{machine || 'No machine'}</span>
                            <span className="op-card__sep" aria-hidden />
                            <CrewChips
                              crew={op.crew}
                              lead={{
                                id: op.assignedWorkerId,
                                fullName: op.assignedWorkerName,
                                nickname: op.assignedWorkerNickname,
                                photoVersion: op.assignedWorkerPhotoVersion,
                              }}
                              size={20}
                              emptyText="No worker yet"
                            />
                          </div>
                        )}

                        {op.isOutsourced ? (
                          <OutsourcedOperationPanel
                            op={op}
                            canRecord={isAdmin && !isDraft && !isInvoicedOrDelivered}
                            waitingOnEarlier={!ops.slice(0, index).every((o) => o.status === 'COMPLETED')}
                            onChanged={fetchJob}
                          />
                        ) : (
                        <div className="op-card__facts">
                          <div className="op-card__fact">
                            <span className="op-card__label">Planned</span>
                            <span className="op-card__value">
                              {fmtRange(op.scheduledStart, op.scheduledEnd) || '—'}
                            </span>
                            <span className="op-card__sub">Target {fmtHours(op.estimatedHours)}</span>
                          </div>
                          <div className="op-card__fact">
                            <span className="op-card__label">Actual</span>
                            <span className="op-card__value">
                              {fmtRange(op.actualStart, op.actualEnd) || (active ? 'In progress' : 'Not started')}
                            </span>
                            <span className="op-card__sub">
                              Worked {fmtHours(op.actualWorkedHours)}
                              {(op.crewSize || 1) > 1 && op.laborHours != null
                                ? ` · ${fmtHours(op.laborHours)} labor (${op.crewSize} people)`
                                : ''}
                            </span>
                          </div>
                          <div className="op-card__fact op-card__fact--diff">
                            <span className="op-card__label">Vs target</span>
                            {op.varianceHours != null ? (
                              <>
                                <span
                                  className={`op-card__diff ${op.varianceHours > 0.05 ? 'is-over' : op.varianceHours < -0.05 ? 'is-under' : 'is-even'}`}
                                >
                                  {op.varianceHours > 0 ? '+' : ''}
                                  {op.varianceHours.toFixed(1)} h
                                </span>
                                <span className="op-card__sub">
                                  {op.variancePct != null
                                    ? `${Math.abs(op.variancePct).toFixed(1)}% ${op.varianceHours > 0 ? 'over' : 'under'}`
                                    : op.varianceHours > 0
                                      ? 'over target'
                                      : 'under target'}
                                </span>
                              </>
                            ) : (
                              <span className="op-card__value op-card__muted">—</span>
                            )}
                          </div>
                        </div>
                        )}

                        {op.reworkReasonCategory || op.reworkReason ? (
                          <div className="op-card__note">
                            Redo reason
                            {op.reworkReasonCategory
                              ? `: ${reworkCategoryLabel(op.reworkReasonCategory)}`
                              : ''}
                            {op.reworkReason
                              ? `${op.reworkReasonCategory ? ' — ' : ': '}${op.reworkReason}`
                              : ''}
                          </div>
                        ) : null}

                        {op.isOutsourced ? null : (
                        <Collapse
                          size="small"
                          ghost
                          items={[
                            {
                              key: 'logs',
                              label: (
                                <span style={{ fontSize: 12, fontWeight: 600, color: MUTED }}>
                                  Time log ({logs.length})
                                </span>
                              ),
                              children:
                                logs.length === 0 ? (
                                  <Text type="secondary" style={{ fontSize: 12 }}>
                                    —
                                  </Text>
                                ) : (
                                  <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: MUTED }}>
                                    {logs.map((log) => (
                                      <li key={log.id}>
                                        {TIME_EVENT_LABEL[log.event] || log.event}
                                        {log.reason
                                          ? ` / ${PAUSE_REASON_LABEL[log.reason] || log.reason}`
                                          : ''}
                                        {' · '}
                                        {fmtDateTime(log.eventAt)}
                                        <ReceivedAt eventAt={log.eventAt} receivedAt={log.receivedAt} />
                                        {log.workerName ? ` · ${log.workerName}` : ''}
                                        {log.note ? ` — ${log.note}` : ''}
                                      </li>
                                    ))}
                                  </ul>
                                ),
                            },
                          ]}
                        />
                        )}


                        {isAdmin && !isDraft && !isInvoicedOrDelivered && op.status === 'COMPLETED' && (
                          <Button
                            size="small"
                            style={{ marginTop: 8 }}
                            loading={reworkLoading === op.id}
                            onClick={() => handleRework(op)}
                          >
                            Send for redo
                          </Button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </Col>

        <Col xs={24} lg={8} className="jo-detail-side">
          {job.projectedCompletion || job.predictedCompletion ? <FinishCard job={job} /> : null}
          <div className="jo-dcard" style={cardStyle({ marginBottom: 16 })}>
            <div style={{ fontWeight: 800, fontSize: 14, color: NAVY, marginBottom: 12 }}>
              Details
            </div>
            <div style={DETAIL_GRID}>
              <DetailRow label="Client PO #" value={dash(job.clientPoNumber)} />
              <DetailRow label="Job type" value={friendlyEnum(job.jobType, JOB_TYPE_LABEL)} />
              <DetailRow
                label="Material"
                value={
                  job.materialStatus === 'NOT_REQUIRED'
                    ? 'Not required'
                    : job.materialStatus === 'RECEIVED'
                      ? `Received ${fmtDate(job.materialReceivedDate)}`
                      : job.materialStatus === 'ORDERED'
                        ? `Ordered · expected ${fmtDate(job.materialReadiness?.expectedDate)}`
                        : job.materialStatus === 'TO_ORDER'
                          ? 'To order'
                          : '—'
                }
              />
              <DetailRow
                label="Stage of the part"
                value={friendlyEnum(job.partCondition, PART_STAGE_LABEL)}
              />
              <DetailRow label="Created" value={fmtDate(job.createdAt)} />
            </div>
            {job.description ? (
              <div style={{ marginTop: 14, fontSize: 13, color: MUTED }}>
                <div style={SUBHEAD}>Description</div>
                {job.description}
              </div>
            ) : null}
          </div>

          {jobStarted ? (
            <div className="jo-dcard" style={cardStyle({ marginBottom: 16 })}>
              <div style={{ fontWeight: 800, fontSize: 14, color: NAVY, marginBottom: 12 }}>
                Time taken
              </div>
              <div style={DETAIL_GRID}>
                <DetailRow label="Target hours" value={fmtHours(totals.estimated)} />
                <DetailRow label="Hours worked" value={fmtHours(totals.worked)} />
                <DetailRow
                  label="Difference from target"
                  value={fmtVariance(totals.varianceHours, null)}
                />
              </div>
            </div>
          ) : null}

          {canManage ? (
            <div className="jo-dcard" style={cardStyle({ marginBottom: 16 })}>
              <div style={{ fontWeight: 800, fontSize: 14, color: NAVY, marginBottom: 4 }}>
                Activity
              </div>
              <Collapse
                size="small"
                ghost
                items={[
                  {
                    key: 'schedule-moves',
                    label: (
                      <ActivityLabel title="Reschedule history" count={scheduleMoves.length} />
                    ),
                    children: scheduleMoves.length ? (
                      scheduleMoves.map((m) => {
                        const title = RESCHEDULE_REASON_CODES.has(m.reason || '')
                          ? rescheduleReasonLabel(m.reason)
                          : m.kind === 'MATERIAL'
                            ? 'Material delay'
                            : m.kind === 'BREAKDOWN'
                              ? 'Machine breakdown'
                              : m.kind === 'OVERRUN'
                                ? 'Overrun'
                                : m.kind === 'RESCHEDULED'
                                  ? 'Rescheduled'
                                  : m.kind;
                        const autoText =
                          m.reason && !RESCHEDULE_REASON_CODES.has(m.reason) ? m.reason : null;
                        return (
                          <div key={m.id} style={ACTIVITY_ROW}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                              <span style={{ fontWeight: 700, color: NAVY }}>{title}</span>
                              <span>{fmtDateTime(m.movedAt)}</span>
                            </div>
                            {m.note ? <div>{m.note}</div> : null}
                            {autoText ? <div>{autoText}</div> : null}
                            {m.changes?.operations?.length ? (
                              <div>
                                {m.changes.operations.length} operation
                                {m.changes.operations.length === 1 ? '' : 's'} changed
                              </div>
                            ) : null}
                            <div>
                              {m.previousStart && m.newStart
                                ? `${fmtDateTime(m.previousStart)} → ${fmtDateTime(m.newStart)}`
                                : null}
                              {m.movedByName
                                ? ` · ${m.movedByName}`
                                : ' · Automatic'}
                            </div>
                          </div>
                        );
                      })
                    ) : (
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        No reschedules recorded for this job.
                      </Text>
                    ),
                  },
                  {
                    key: 'breakdowns',
                    label: (
                      <ActivityLabel
                        title="Machine breakdowns"
                        count={breakdowns.length}
                        alert={
                          breakdowns.some((b) => !b.endedAt) ? 'still down' : undefined
                        }
                      />
                    ),
                    children: breakdowns.length ? (
                      breakdowns.map((b) => (
                        <div key={b.id} style={ACTIVITY_ROW}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                            <span style={{ fontWeight: 700, color: NAVY }}>{b.machineUnitLabel}</span>
                            {b.endedAt ? null : (
                              <StatusPill color="red" compact>
                                Still down
                              </StatusPill>
                            )}
                          </div>
                          <div>
                            {b.reason}
                            {b.operationName ? ` · ${b.operationName}` : ''}
                          </div>
                          <div>
                            {fmtDateTime(b.startedAt)} → {b.endedAt ? fmtDateTime(b.endedAt) : 'now'}
                            {b.reportedByName ? ` · ${b.reportedByName}` : ''}
                          </div>
                          {b.note ? <div>{b.note}</div> : null}
                        </div>
                      ))
                    ) : (
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        No breakdowns linked to this job.
                      </Text>
                    ),
                  },
                  {
                    key: 'notifications',
                    label: (
                      <ActivityLabel
                        title="Client notifications"
                        count={notifications?.length ?? 0}
                        alert={
                          notifications?.some((n) => isUnresolvedFailure(n, notifications))
                            ? 'failed'
                            : undefined
                        }
                      />
                    ),
                    children:
                      notifications == null ? (
                        <Spin size="small" />
                      ) : notifications.length ? (
                        notifications.map((n) => (
                          <div key={n.id} style={ACTIVITY_ROW}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                              <span style={{ fontWeight: 700, color: NAVY }}>
                                {friendlyEnum(n.milestone, NOTIF_UPDATE_LABEL)}
                              </span>
                              <span
                                style={{
                                  color:
                                    n.status === 'FAILED'
                                      ? '#b91c1c'
                                      : n.status === 'NOT_SENT'
                                        ? '#b45309'
                                        : MUTED,
                                }}
                              >
                                {friendlyEnum(n.status, NOTIF_STATUS_LABEL)}
                              </span>
                            </div>
                            <div>
                              {!n.recipient || n.recipient === '(none)'
                                ? 'No email address on file for this client'
                                : `${friendlyEnum(n.channel, NOTIF_CHANNEL_LABEL)} to ${n.recipient}`}
                            </div>
                            <div>
                              {n.sentAt
                                ? `Sent ${formatShop(n.sentAt, 'MMM D, h:mm A')}`
                                : n.createdAt
                                  ? `${n.status === 'PENDING' ? 'Queued' : 'Logged'} ${formatShop(n.createdAt, 'MMM D, h:mm A')}`
                                  : '—'}
                            </div>
                            {(n.status === 'FAILED' || n.status === 'NOT_SENT') && n.errorMessage ? (
                              <div style={{ color: MUTED, fontSize: 12 }}>
                                {n.errorMessage}
                                {isUnresolvedFailure(n, notifications) ? '' : ' Sent again later.'}
                              </div>
                            ) : null}
                          </div>
                        ))
                      ) : (
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          No client notifications sent for this job yet.
                        </Text>
                      ),
                  },
                  ...(isAdmin
                    ? [
                        {
                          key: 'audit-history',
                          label: <ActivityLabel title="Change history" />,
                          children: <JobAuditHistory jobId={job.id} />,
                        },
                      ]
                    : []),
                ]}
              />
            </div>
          ) : null}
        </Col>
      </Row>

      <FormModal
        open={!!reworkOp}
        icon={<RedoOutlined />}
        title="Send for redo"
        subtitle={
          reworkOp
            ? `${reworkOp.operationName} failed inspection. A redo operation is added to this job.`
            : undefined
        }
        onCancel={() => setReworkOp(null)}
        onOk={submitRework}
        okText="Create redo operation"
        confirmLoading={reworkSaving}
        destroyOnHidden
      >
        <Form scrollToFirstError={{ block: 'center' }} layout="vertical">
          <Form.Item label="Category" required>
            <Select
              placeholder="Why does it need to be redone?"
              options={REWORK_CATEGORY_OPTIONS}
              value={reworkCategory}
              onChange={(v: ReworkReasonCategory) => setReworkCategory(v)}
            />
          </Form.Item>
          <Form.Item label={reworkCategory === 'OTHER' ? 'Note' : 'Note (optional)'} required={reworkCategory === 'OTHER'}>
            <Input.TextArea
              rows={3}
              placeholder="Extra detail"
              value={reworkNote}
              onChange={(e) => setReworkNote(e.target.value)}
            />
          </Form.Item>
        </Form>
      </FormModal>

      <FormModal
        open={invoiceMode !== null}
        onCancel={() => setInvoiceMode(null)}
        size={invoiceMode === 'record' ? 'lg' : 'md'}
        width={invoiceMode === 'record' ? 680 : undefined}
        icon={<FileTextOutlined />}
        title={
          invoiceMode === 'record'
            ? 'Record sales invoice'
            : invoiceMode === 'correct'
              ? 'Correct sales invoice'
              : 'Sales invoice'
        }
        subtitle={[job.jobNumber, job.clientName].filter(Boolean).join(' · ')}
        footer={
          invoiceMode === 'view' ? (
            <>
              <Button onClick={() => setInvoiceMode(null)} style={{ minWidth: 96 }}>
                Close
              </Button>
              {canCorrectInvoice ? (
                <Button type="primary" onClick={openCorrectInvoice} style={{ fontWeight: 700, minWidth: 120 }}>
                  Correct
                </Button>
              ) : null}
            </>
          ) : (
            <>
              <Button onClick={() => setInvoiceMode(null)} style={{ minWidth: 96 }}>
                Cancel
              </Button>
              <Button
                type="primary"
                loading={invoiceSaving}
                onClick={() => invoiceForm.submit()}
                style={{ fontWeight: 700, minWidth: 120 }}
              >
                {invoiceMode === 'correct' ? 'Save correction' : 'Record invoice'}
              </Button>
            </>
          )
        }
        destroyOnHidden
      >
        {invoiceMode === 'view' && job.salesInvoice ? (
          <>
            <div style={{ ...DETAIL_GRID, marginBottom: 16 }}>
              <div>
                <div style={SUBHEAD}>Sales Invoice No.</div>
                <div style={{ fontWeight: 700, color: NAVY }}>{job.salesInvoice.invoiceNumber}</div>
              </div>
              <div>
                <div style={SUBHEAD}>Invoice date</div>
                <div>{fmtDate(job.salesInvoice.invoiceDate)}</div>
              </div>
              <div>
                <div style={SUBHEAD}>Total amount due</div>
                <div>{fmtMoney(job.salesInvoice.amount)}</div>
              </div>
              <div>
                <div style={SUBHEAD}>Recorded by</div>
                <div>{dash(job.salesInvoice.preparedByName)}</div>
              </div>
            </div>
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 0 }}>
              Reference to the shop&apos;s BIR-registered sales invoice.{' '}
              {canCorrectInvoice
                ? 'It can be corrected until the Admin sets the job For Delivery.'
                : 'It is locked once the job is set For Delivery.'}
            </Text>
          </>
        ) : (
          <Form scrollToFirstError={{ block: 'center' }} form={invoiceForm} layout="vertical" onFinish={onSaveInvoice}>
            {invoiceMode === 'record' ? (
              <>
                <BookletGuide job={job} />
                <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 12 }}>
                  From the shop&apos;s BIR-registered invoice booklet.
                </Text>
              </>
            ) : null}
            <Form.Item
              name="invoiceNumber"
              label="Sales Invoice No."
              extra="The red number printed on the booklet, e.g. 0850"
              rules={[
                { required: true, whitespace: true, message: 'Enter the sales invoice number' },
                { max: 64, message: 'Use at most 64 characters' },
              ]}
            >
              <Input placeholder="e.g. 0850" />
            </Form.Item>
            <Form.Item name="invoiceDate" label="Date" rules={[{ required: true }]}>
              <DatePicker
                style={{ width: '100%' }}
                format="MMM D, YYYY"
                allowClear={false}
                disabledDate={(d) => d.isAfter(shopToday(), 'day')}
              />
            </Form.Item>
            <Form.Item
              name="amount"
              label="Total amount due"
              extra={
                invoiceMode === 'record'
                  ? 'The TOTAL AMOUNT DUE line on the booklet. Starts at the job order amount.'
                  : undefined
              }
              rules={[{ required: true, message: 'Enter the total amount due' }]}
            >
              <InputNumber min={0} precision={2} prefix="₱" style={{ width: '100%' }} />
            </Form.Item>
            {invoiceMode === 'correct' ? (
              <Form.Item
                name="reason"
                label="Reason for the correction"
                rules={[{ required: true, whitespace: true, message: 'Enter why it is being corrected' }]}
              >
                <Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} />
              </Form.Item>
            ) : null}
          </Form>
        )}
      </FormModal>

      <OrderMaterialsModal
        open={purchaseOpen}
        onClose={() => setPurchaseOpen(false)}
        jobId={job.id}
        defaultSupplierId={job.supplierId}
        planned={job.plannedMaterials}
        onSaved={async (order) => {
          setPurchaseOpen(false);
          await fetchPurchases();
          await fetchJob();
          setDraftNotice({
            id: order.id,
            supplierName: order.supplierName || 'the supplier',
            lineCount: order.lineCount,
            jobCount: order.jobCount,
          });
        }}
      />
      <Modal
        open={draftNotice != null}
        title="Added to the draft supplier order"
        okText="Open supplier order"
        onCancel={() => setDraftNotice(null)}
        onOk={() => {
          const id = draftNotice?.id;
          setDraftNotice(null);
          if (id) navigate(`/supplier-orders/${id}`);
        }}
        destroyOnHidden
      >
        {draftNotice
          ? `The lines are on the ${draftNotice.supplierName} draft order (${draftNotice.lineCount} line${
              draftNotice.lineCount === 1 ? '' : 's'
            }, ${draftNotice.jobCount} job${draftNotice.jobCount === 1 ? '' : 's'}). The material counts as ordered once the order is issued.`
          : null}
      </Modal>

      {rescheduleOpen && (
        <RescheduleModal
          jobId={job.id}
          onClose={() => setRescheduleOpen(false)}
          onConfirmed={async () => {
            setRescheduleOpen(false);
            await fetchJob();
            await fetchScheduleMoves();
          }}
        />
      )}

      {cancelOpen && (
        <CancelJobModal
          job={job}
          open={cancelOpen}
          onClose={() => setCancelOpen(false)}
          onCancelled={async () => {
            setCancelOpen(false);
            await fetchJob();
            await fetchPurchases();
          }}
        />
      )}


      <MarkDeliveredModal job={deliveredOpen ? job : null} onClose={() => setDeliveredOpen(false)} onDone={fetchJob} />

    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <div style={{ color: MUTED }}>{label}</div>
      <div style={{ color: NAVY, fontWeight: 600, textAlign: 'right' }}>{value}</div>
    </>
  );
}

function FinishCard({ job }: { job: JobOrder }) {
  const est = job.completionEstimate;
  const late = job.scheduleFlag === 'AMBER' || job.scheduleFlag === 'RED';
  const lateStyle: CSSProperties = { color: '#7A1528' };
  // Once every operation is done, the finish is when the work actually ended.
  const done = job.status === 'COMPLETED' || job.status === 'FOR_DELIVERY' || job.status === 'DELIVERED';
  return (
    <div className="jo-dcard" style={cardStyle({ marginBottom: 16 })}>
      <div style={{ fontWeight: 800, fontSize: 14, color: NAVY, marginBottom: 12 }}>Finish</div>
      <div style={DETAIL_GRID}>
        <div style={{ color: MUTED }}>{done ? 'Finished' : 'Scheduled finish'}</div>
        <div
          style={{
            color: NAVY,
            fontWeight: 600,
            textAlign: 'right',
            ...(late && job.scheduleFlagBasis === 'SCHEDULE' ? lateStyle : {}),
          }}
        >
          {fmtDateTime(job.projectedCompletion)}
        </div>
        {!done ? (
          <>
            <div style={{ color: MUTED }}>
              Estimated finish{' '}
              <Tooltip title="From how long past operations of the same kind actually took.">
                <InfoCircleOutlined style={{ fontSize: 12 }} />
              </Tooltip>
            </div>
            <div
              style={{
                color: NAVY,
                fontWeight: 600,
                textAlign: 'right',
                ...(late && job.scheduleFlagBasis === 'ESTIMATE' ? lateStyle : {}),
              }}
            >
              {fmtDateTime(job.predictedCompletion)}
            </div>
          </>
        ) : null}
        <div style={{ color: MUTED }}>Date required</div>
        <div style={{ color: NAVY, fontWeight: 600, textAlign: 'right' }}>{fmtDate(job.dueDate)}</div>
        {job.forDeliveryAt ? (
          <>
            <div style={{ color: MUTED }}>Set For Delivery</div>
            <div style={{ color: NAVY, fontWeight: 600, textAlign: 'right' }}>{fmtDate(job.forDeliveryAt)}</div>
          </>
        ) : null}
        {job.deliveredAt ? (
          <>
            <div style={{ color: MUTED }}>Delivered</div>
            <div
              style={{
                fontWeight: 700,
                textAlign: 'right',
                color:
                  job.dueDate && dayjs(formatShop(job.deliveredAt, 'YYYY-MM-DD')).isAfter(dayjs(job.dueDate), 'day')
                    ? '#b91c1c'
                    : '#15803d',
              }}
            >
              {fmtDate(job.deliveredAt)}
            </div>
          </>
        ) : null}
      </div>
      {est ? (
        <div style={{ marginTop: 8, fontSize: 12, color: MUTED }}>
          <Collapse
            size="small"
            ghost
            style={{ marginTop: 6 }}
            items={[
              {
                key: 'ops',
                label: <span style={{ fontSize: 12, color: NAVY }}>By operation</span>,
                children: (
                  <div style={{ display: 'grid', gap: 6 }}>
                    {est.operations.map((o) => (
                      <div key={o.operationId} style={{ fontSize: 12 }}>
                        <div style={{ color: NAVY, fontWeight: 600 }}>
                          #{o.sequenceNo} {o.operationName}
                        </div>
                        <div>
                          {fmtHours(o.targetHours)} target ×{' '}
                          {o.enoughHistory
                            ? `${o.ratio.toFixed(2)} (${o.samples} past operations)`
                            : `1.00 (only ${o.samples} past)`}
                          {o.hoursWorked > 0 ? `, ${fmtHours(o.hoursWorked)} worked` : ''}
                          {' → '}
                          {fmtHours(o.predictedHoursLeft)} left, ends {fmtDateTime(o.predictedEnd)}
                        </div>
                      </div>
                    ))}
                  </div>
                ),
              },
            ]}
          />
        </div>
      ) : null}
    </div>
  );
}

function ActivityLabel({ title, count, alert }: { title: string; count?: number; alert?: string }) {
  return (
    <span style={{ fontSize: 13, fontWeight: 600, color: NAVY }}>
      {title}
      {count != null ? <span style={{ color: MUTED, fontWeight: 400 }}> · {count}</span> : null}
      {alert ? <span style={{ color: '#b91c1c', fontWeight: 600 }}> · {alert}</span> : null}
    </span>
  );
}

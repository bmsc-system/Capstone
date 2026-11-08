import { useEffect, useMemo, useRef, useState } from 'react';
import { useArmed } from '../../hooks/useArmed';
import { confirmAction } from '../../utils/confirmAction';
import { formatHoursMinutes } from '../../utils/duration';
import { Button, message } from 'antd';
import { CheckCircleFilled, FileTextOutlined, WarningOutlined } from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import axios from 'axios';
import dayjs from 'dayjs';
import { jobOrdersApi } from '../../api/jobOrders.api';
import { getErrorMessage } from '../../api/client';
import { useAuth } from '../../hooks/useAuth';
import { useWorkerTheme, WorkerPageHeader } from '../../layouts/WorkerLayout';
import { useOffline, type RecordInput } from '../../offline/OfflineProvider';
import { offlineCache } from '../../offline/offlineCache';
import { overlayOperations } from '../../offline/overlay';
import SyncChip from '../../offline/SyncChip';
import { CrewChips } from '../../components/PersonAvatar';
import { DOWNTIME_REASONS, type DowntimeCategory } from '../../constants/downtimeReasons';
import { SHOP_TZ, formatShop, shopToday } from '../../utils/shopTime';
import type { JobOrder, Operation, OperationPauseReason, PartCondition } from '../../types';
import { PageSkeleton } from '../../components/ui';
import { isJobFinished } from '../job-orders/jobStage';
import {
  announceRecorded,
  canStartOperation,
  operationActionLabel,
  showActionError,
} from './operationActions';

const PAUSE_REASONS: { value: OperationPauseReason; label: string }[] = [
  { value: 'END_OF_SHIFT', label: 'End of shift' },
  { value: 'BREAK', label: 'Break' },
  { value: 'MACHINE_DOWN', label: 'Machine breakdown' },
  { value: 'WAITING_MATERIAL', label: 'Waiting for material' },
  { value: 'WAITING_PRIOR_OPERATION', label: 'Waiting on prior operation' },
  { value: 'OTHER', label: 'Other' },
];

const PART_STAGE_LABEL: Record<PartCondition, string> = {
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

function workedSecondsSoFar(op: Operation, nowMs: number): number {
  const logs = [...(op.timeLogs || [])].sort(
    (a, b) => dayjs(a.eventAt).valueOf() - dayjs(b.eventAt).valueOf()
  );
  let total = 0;
  let open: number | null = null;
  for (const log of logs) {
    const t = dayjs(log.eventAt).valueOf();
    if (log.event === 'START' || log.event === 'RESUME') {
      if (open == null) open = t;
    } else if (log.event === 'PAUSE' || log.event === 'COMPLETE') {
      if (open != null && t > open) total += t - open;
      open = null;
    }
  }
  if (open != null && !op.isPaused) total += Math.max(0, nowMs - open);
  return total;
}

function formatElapsed(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

type WorkerMaterial = { key: string; name: string; spec: string; qty: string; received: boolean };

/** One list of the job's materials for the worker: the planned lines (with
 * size), marked received when a received supplier order line matches; the
 * ordered lines alone when nothing was planned. */
function workerMaterialList(job: JobOrder): WorkerMaterial[] {
  const qty = (q: number | null | undefined, u: string | null | undefined) =>
    [q, u].filter((x) => x != null && x !== '').join(' ');
  const norm = (v: string | null | undefined) => (v || '').trim().toLowerCase();
  const lines = job.materialLines || [];
  const planned = (job.rawMaterials || []).filter((m) => m && m.name);
  if (planned.length) {
    return planned.map((m, i) => {
      const matching = lines.filter(
        (l) => norm(l.materialName) === norm(m.name) && norm(l.gradeOrSpec) === norm(m.gradeOrSpec)
      );
      return {
        key: m.id || `${m.name}-${i}`,
        name: m.name,
        spec: [m.gradeOrSpec, m.sizeSpec].filter(Boolean).join(', '),
        qty: qty(m.quantity, m.unit),
        received: matching.length > 0 && matching.every((l) => l.status === 'RECEIVED'),
      };
    });
  }
  return lines.map((l) => ({
    key: l.id,
    name: l.materialName,
    spec: [l.gradeOrSpec, l.sizeSpec].filter(Boolean).join(', '),
    qty: qty(l.quantity, l.unit),
    received: l.status === 'RECEIVED',
  }));
}

export default function AssignmentDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const { colors } = useWorkerTheme();
  const { online, userId, pending, version, record } = useOffline();
  const [loadedJob, setLoadedJob] = useState<JobOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [pauseForOp, setPauseForOp] = useState<Operation | null>(null);
  const [reportForOp, setReportForOp] = useState<Operation | null>(null);
  const [reportNote, setReportNote] = useState('');
  const [nowMs, setNowMs] = useState(() => Date.now());
  const pauseArmed = useArmed(pauseForOp !== null);
  const reportArmed = useArmed(reportForOp !== null);
  // One action per operation at a time. Released a moment after it finishes so
  // a tap that lands before the screen updates is not a second action.
  const busyOps = useRef(new Set<string>());
  const claim = (opId: string) => {
    if (busyOps.current.has(opId)) return false;
    busyOps.current.add(opId);
    return true;
  };
  const release = (opId: string) => {
    window.setTimeout(() => busyOps.current.delete(opId), 1500);
  };

  const fetchJob = async () => {
    if (!id || !userId) return;
    const fromPhone = () => offlineCache.read(userId).jobs[id] || null;
    if (!online) {
      setLoadedJob(fromPhone());
      setLoading(false);
      return;
    }
    try {
      const { data } = await jobOrdersApi.get(id);
      offlineCache.saveJob(userId, data);
      setLoadedJob(data);
    } catch (err) {
      const saved = axios.isAxiosError(err) && !err.response ? fromPhone() : null;
      if (saved) setLoadedJob(saved);
      else message.error(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchJob();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, online, userId, version]);

  const job = useMemo(
    () =>
      loadedJob
        ? { ...loadedJob, operations: overlayOperations(loadedJob.operations || [], pending) }
        : null,
    [loadedJob, pending]
  );

  const labelFor = (op: Operation, verb: string) => operationActionLabel(op, verb, job?.jobNumber);

  /** Records the action; a refusal while online is thrown to the caller. */
  const recordAction = async (
    op: Operation,
    input: Omit<RecordInput, 'operationId' | 'jobOrderId'>,
    sentText: string
  ) => {
    const outcome = await record({ ...input, operationId: op.id, jobOrderId: op.jobOrderId });
    announceRecorded(outcome, online, sentText);
  };

  useEffect(() => {
    const hasActive = (job?.operations || []).some(
      (op) => op.status === 'IN_PROGRESS' && !op.isPaused
    );
    if (!hasActive) return;
    const t = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [job]);

  const runAction = async (
    op: Operation,
    action: 'start' | 'complete' | 'resume',
  ) => {
    if (!claim(op.id)) return;
    setActionLoading(op.id);
    try {
      if (action === 'start') {
        await recordAction(op, { kind: 'start', label: labelFor(op, 'Start') }, 'Operation started');
      } else if (action === 'resume') {
        await recordAction(op, { kind: 'resume', label: labelFor(op, 'Resume') }, 'Operation resumed');
      } else {
        await recordAction(
          op,
          { kind: 'complete', label: labelFor(op, 'Complete') },
          'Operation completed'
        );
      }
    } catch (err) {
      showActionError(err);
    } finally {
      setActionLoading(null);
      release(op.id);
    }
  };

  const confirmPause = async (reason: OperationPauseReason) => {
    if (!pauseForOp || !pauseArmed || !claim(pauseForOp.id)) return;
    const opId = pauseForOp.id;
    setActionLoading(opId);
    try {
      const reasonLabel = PAUSE_REASONS.find((r) => r.value === reason)?.label || reason;
      await recordAction(
        pauseForOp,
        { kind: 'pause', reason, label: `${labelFor(pauseForOp, 'Pause')}: ${reasonLabel}` },
        'Operation paused'
      );
      setPauseForOp(null);
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setActionLoading(null);
      release(opId);
    }
  };

  const confirmBreakdown = async (category: DowntimeCategory) => {
    if (!reportForOp?.machineUnitId || !reportArmed) return;
    if (category === 'OTHER' && !reportNote.trim()) {
      message.warning('Add a note describing the breakdown when choosing Other');
      return;
    }
    if (!claim(reportForOp.id)) return;
    const opId = reportForOp.id;
    setActionLoading(opId);
    try {
      const machine =
        reportForOp.machineUnitLabel || reportForOp.machineTypeName || 'machine';
      await recordAction(
        reportForOp,
        {
          kind: 'breakdown',
          machineUnitId: reportForOp.machineUnitId,
          category,
          note: reportNote.trim() || undefined,
          label: `Report breakdown on ${machine}` + (job?.jobNumber ? ` (${job.jobNumber})` : ''),
        },
        'Breakdown reported'
      );
      setReportForOp(null);
      setReportNote('');
    } catch (err) {
      message.error(getErrorMessage(err));
    } finally {
      setActionLoading(null);
      release(opId);
    }
  };

  if (loading) {
    return (
      <div>
        <WorkerPageHeader title="Job Details" onBack={() => navigate('/my-assignments')} />
        <PageSkeleton variant="detail" />
      </div>
    );
  }

  if (!job) {
    return (
      <div>
        <WorkerPageHeader title="Job Details" onBack={() => navigate('/my-assignments')} />
        <p style={{ color: colors.red, padding: 16 }}>
          {online
            ? 'Job not found'
            : 'This job is not saved on this phone yet. Open it once while connected.'}
        </p>
      </div>
    );
  }

  const finished = isJobFinished(job.status);
  const workerMaterials = workerMaterialList(job);
  const overdue = !finished && dayjs(job.dueDate).isBefore(shopToday(), 'day');
  const statusLabel =
    finished
      ? 'Completed'
      : job.status === 'IN_PROGRESS'
        ? 'In Progress'
        : overdue
          ? 'Overdue'
          : 'Assigned';
  const statusColor =
    finished
      ? colors.green
      : job.status === 'IN_PROGRESS'
        ? colors.accent
        : overdue
          ? colors.red
          : colors.accent;

  const ops = job.operations || [];

  return (
    <div>
      <WorkerPageHeader
        title="Job Details"
        subtitle={job.jobNumber || job.id.slice(0, 8).toUpperCase()}
        onBack={() => navigate('/my-assignments')}
      />

      <div style={{ padding: 16 }}>
        <div
          style={{
            background: colors.card,
            border: `1px solid ${colors.cardBorder}`,
            borderRadius: 14,
            padding: 16,
            marginBottom: 16,
            boxShadow: colors.shadow,
            display: 'flex',
            gap: 12,
            alignItems: 'flex-start',
          }}
        >
          <div
            style={{
              width: 44,
              height: 44,
              borderRadius: 12,
              background: colors.greenSoft,
              color: colors.green,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 20,
              flexShrink: 0,
            }}
          >
            <CheckCircleFilled />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 17, fontWeight: 800, marginBottom: 2 }}>{job.title}</div>
            <div style={{ fontSize: 13, color: colors.textSecondary, marginBottom: 8 }}>
              {job.clientName}
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  padding: '3px 10px',
                  borderRadius: 999,
                  background: `${statusColor}22`,
                  color: statusColor,
                }}
              >
                {statusLabel}
              </span>
              {job.waitingForMaterials && (
                <span
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    padding: '3px 10px',
                    borderRadius: 999,
                    background: 'rgba(217,119,6,0.12)',
                    color: colors.amber,
                  }}
                >
                  Waiting for materials
                </span>
              )}
              <span style={{ fontSize: 12, color: overdue ? colors.red : colors.textSecondary }}>
                Due {formatShop(job.dueDate, 'MMM D, YYYY')}
              </span>
            </div>
            {job.quantity != null && (
              <div style={{ fontSize: 12, color: colors.textSecondary, marginTop: 8 }}>
                Qty {job.quantity}
                {job.unitOfMeasure ? ` ${job.unitOfMeasure}` : ''}
              </div>
            )}
            {job.partCondition && (
              <div style={{ fontSize: 12, color: colors.textSecondary, marginTop: 6 }}>
                <span style={{ fontWeight: 700, color: colors.text }}>Stage of the part</span>
                {': '}
                {PART_STAGE_LABEL[job.partCondition] || job.partCondition}
              </div>
            )}
          </div>
        </div>

        {workerMaterials.length > 0 && (
          <div
            style={{
              background: colors.card,
              border: `1px solid ${colors.cardBorder}`,
              borderRadius: 14,
              padding: 14,
              marginBottom: 16,
              boxShadow: colors.shadow,
            }}
          >
            <div style={{ fontWeight: 800, fontSize: 14, marginBottom: 8 }}>Materials</div>
            {workerMaterials.map((m) => (
              <div key={m.key} style={{ fontSize: 13, color: colors.textSecondary, marginBottom: 4 }}>
                {m.name}
                {m.spec ? ` (${m.spec})` : ''}
                {m.qty ? <> — {m.qty}</> : null}
                {m.received ? ' · received' : ''}
              </div>
            ))}
          </div>
        )}

        <div style={{ fontSize: 14, fontWeight: 800, marginBottom: 12 }}>Operations</div>

        <div style={{ position: 'relative', paddingLeft: 8 }}>
          {ops.map((op, index) => {
            const done = op.status === 'COMPLETED';
            const active = op.status === 'IN_PROGRESS';
            const isMine = Boolean(
              user?.id &&
                (op.assignedWorkerId === user.id || (op.helperIds || []).includes(user.id))
            );
            const canStart = canStartOperation(op, ops, user?.id);
            const isLast = index === ops.length - 1;
            const opName = op.operationName || op.name || 'Operation';
            const seq = op.sequenceNo ?? op.seq ?? index + 1;
            const machineLabel =
              op.machineUnitLabel ||
              op.machineTypeName ||
              (op.machineNames && op.machineNames[0]) ||
              null;
            const started = op.actualStart || op.startedAt;
            const completed = op.actualEnd || op.completedAt;

            return (
              <div key={op.id} style={{ display: 'flex', gap: 14, position: 'relative' }}>
                {!isLast && (
                  <div
                    style={{
                      position: 'absolute',
                      left: 15,
                      top: 32,
                      bottom: 0,
                      width: 2,
                      background: done ? colors.green : colors.cardBorder,
                    }}
                  />
                )}

                <div
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
                    background: done
                      ? colors.green
                      : active
                        ? colors.accent
                        : 'rgba(217,119,6,0.15)',
                    color: done || active ? '#fff' : colors.amber,
                    border: done || active ? 'none' : `2px solid ${colors.amber}`,
                  }}
                >
                  {done ? <CheckCircleFilled /> : seq}
                </div>

                <div
                  style={{
                    flex: 1,
                    background: colors.card,
                    border: `1px solid ${active ? colors.accent : colors.cardBorder}`,
                    borderRadius: 14,
                    padding: 14,
                    marginBottom: 12,
                    boxShadow: colors.shadow,
                    opacity: done ? 0.85 : isMine ? 1 : 0.7,
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10, marginBottom: 10 }}>
                    <span style={{ fontWeight: 800, fontSize: 15, lineHeight: 1.3 }}>{opName}</span>
                    <span style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                    {op.syncState && <SyncChip state={op.syncState} />}
                    <span
                      style={{
                        flexShrink: 0,
                        fontSize: 11,
                        fontWeight: 700,
                        padding: '3px 10px',
                        borderRadius: 999,
                        background: done
                          ? colors.greenSoft
                          : active
                            ? 'rgba(37,99,235,0.12)'
                            : 'rgba(217,119,6,0.12)',
                        color: done ? colors.green : active ? colors.accent : colors.amber,
                      }}
                    >
                      {done
                        ? 'Completed'
                        : active && op.isPaused
                          ? 'Paused'
                          : active
                            ? 'In Progress'
                            : op.status === 'SCHEDULED'
                              ? 'Scheduled'
                              : op.status === 'REWORK'
                                ? 'Redo'
                                : 'Pending'}
                    </span>
                    </span>
                  </div>

                  {op.syncState === 'refused' && (
                    <div
                      style={{
                        marginBottom: 12,
                        padding: '10px 12px',
                        borderRadius: 10,
                        background: '#fef2f2',
                        border: '1px solid #fecaca',
                        color: colors.red,
                        fontSize: 13,
                        lineHeight: 1.45,
                      }}
                    >
                      <div style={{ fontWeight: 800 }}>Not accepted by the server</div>
                      {op.syncError?.message || 'The server refused this action.'} Use the red
                      bar at the top to try again or discard it.
                    </div>
                  )}

                  <div
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 4,
                      marginBottom: 12,
                      fontSize: 12,
                      color: colors.textSecondary,
                      lineHeight: 1.4,
                    }}
                  >
                    {machineLabel ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <span>
                          <span style={{ fontWeight: 600, color: colors.text }}>Machine</span>
                          {': '}
                          {machineLabel}
                        </span>
                        {op.machineDown && (
                          <span
                            style={{
                              fontSize: 11,
                              fontWeight: 700,
                              padding: '2px 8px',
                              borderRadius: 999,
                              background: 'rgba(122,21,40,0.12)',
                              color: colors.red,
                            }}
                          >
                            Down
                          </span>
                        )}
                      </div>
                    ) : (
                      <div>
                        <span style={{ fontWeight: 600, color: colors.text }}>Machine</span>
                        {': No machine'}
                      </div>
                    )}
                    {op.scheduledStart || op.scheduledEnd ? (
                      <div>
                        <span style={{ fontWeight: 600, color: colors.text }}>Scheduled</span>
                        {': '}
                        {op.scheduledStart
                          ? dayjs(op.scheduledStart).tz(SHOP_TZ).format('MMM D, h:mm A')
                          : '—'}
                        {op.scheduledEnd
                          ? ` → ${dayjs(op.scheduledEnd)
                              .tz(SHOP_TZ)
                              .format(
                                op.scheduledStart &&
                                  dayjs(op.scheduledStart)
                                    .tz(SHOP_TZ)
                                    .isSame(dayjs(op.scheduledEnd).tz(SHOP_TZ), 'day')
                                  ? 'h:mm A'
                                  : 'MMM D, h:mm A'
                              )}`
                          : ''}
                      </div>
                    ) : null}
                    {op.estimatedHours != null ? (
                      <div>
                        <span style={{ fontWeight: 600, color: colors.text }}>Target</span>
                        {`: ${formatHoursMinutes(op.estimatedHours)}`}
                      </div>
                    ) : null}
                    {op.assignedWorkerName && (!isMine || (op.crewSize || 1) > 1) ? (
                      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, flexWrap: 'wrap' }}>
                        <span style={{ fontWeight: 600, color: colors.text }}>
                          {(op.crewSize || 1) > 1 ? 'Crew:' : 'Assigned:'}
                        </span>
                        <CrewChips
                          crew={op.crew}
                          lead={{
                            id: op.assignedWorkerId,
                            fullName: op.assignedWorkerName,
                            nickname: op.assignedWorkerNickname,
                            photoVersion: op.assignedWorkerPhotoVersion,
                          }}
                          size={18}
                        />
                      </div>
                    ) : null}
                    {(started || completed) && (
                      <div>
                        {started && `Started ${formatShop(started, 'MMM D, h:mm A')}`}
                        {completed && ` · Done ${formatShop(completed, 'MMM D, h:mm A')}`}
                      </div>
                    )}
                    {isMine && active && (
                      <div style={{ fontSize: 13, fontWeight: 700, color: colors.accent, marginTop: 2 }}>
                        Worked {formatElapsed(workedSecondsSoFar(op, nowMs))}
                        {op.isPaused ? ' (paused)' : ''}
                      </div>
                    )}
                    {op.actualWorkedHours != null && done && (
                      <div>
                        Worked {formatHoursMinutes(op.actualWorkedHours)}
                        {op.estimatedHours != null ? ` / target ${formatHoursMinutes(op.estimatedHours)}` : ''}
                      </div>
                    )}
                  </div>

                  {op.notes?.trim() ? (
                    <div
                      style={{
                        marginBottom: 12,
                        padding: '10px 12px',
                        borderRadius: 10,
                        background: 'rgba(217,119,6,0.08)',
                        border: `1px solid rgba(217,119,6,0.35)`,
                      }}
                    >
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 6,
                          fontSize: 11,
                          fontWeight: 800,
                          letterSpacing: 0.6,
                          textTransform: 'uppercase',
                          color: colors.amber,
                          marginBottom: 4,
                        }}
                      >
                        <FileTextOutlined />
                        Instructions
                      </div>
                      <div
                        style={{
                          fontSize: 13,
                          color: colors.text,
                          lineHeight: 1.5,
                          whiteSpace: 'pre-wrap',
                        }}
                      >
                        {op.notes}
                      </div>
                    </div>
                  ) : null}

                  {canStart && job.waitingForMaterials && (
                    <>
                      <Button block size="large" disabled style={{ height: 48, fontWeight: 700 }}>
                        Start Operation
                      </Button>
                      <div
                        style={{
                          marginTop: 8,
                          fontSize: 12,
                          color: colors.amber,
                          fontWeight: 600,
                          textAlign: 'center',
                          lineHeight: 1.4,
                        }}
                      >
                        Waiting for materials. {job.materialWaitReason}
                      </div>
                    </>
                  )}
                  {canStart && !job.waitingForMaterials && (
                    <Button
                      type="primary"
                      block
                      size="large"
                      loading={actionLoading === op.id}
                      onClick={() => runAction(op, 'start')}
                      style={{
                        height: 48,
                        fontWeight: 700,
                        background: colors.accent,
                        borderColor: colors.accent,
                      }}
                    >
                      Start Operation
                    </Button>
                  )}
                  {isMine && active && !op.isPaused && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                      <Button
                        type="default"
                        block
                        size="large"
                        loading={actionLoading === op.id}
                        onClick={() => {
                          // Taps meant for Start land here once Pause replaces it.
                          if (!busyOps.current.has(op.id)) setPauseForOp(op);
                        }}
                        style={{ height: 48, fontWeight: 700 }}
                      >
                        Pause
                      </Button>
                      <Button
                        type="primary"
                        block
                        size="large"
                        loading={actionLoading === op.id}
                        onClick={() =>
                          !busyOps.current.has(op.id) &&
                          confirmAction({
                            title: `Finish ${op.operationName || 'this step'}?`,
                            content:
                              'Mark it complete only when the work on this step is done. The Administrator checks it next.',
                            okText: 'Mark complete',
                            onOk: () => runAction(op, 'complete'),
                          })
                        }
                        style={{
                          height: 48,
                          fontWeight: 700,
                          background: colors.accent,
                          borderColor: colors.accent,
                        }}
                      >
                        Mark Complete
                      </Button>
                    </div>
                  )}
                  {isMine && active && op.isPaused && (
                    <Button
                      type="primary"
                      block
                      size="large"
                      loading={actionLoading === op.id}
                      onClick={() => runAction(op, 'resume')}
                      style={{
                        height: 48,
                        fontWeight: 700,
                        background: colors.accent,
                        borderColor: colors.accent,
                      }}
                    >
                      Resume
                    </Button>
                  )}

                  {isMine && op.machineUnitId && !op.machineDown && !done && (
                    <div style={{ display: 'flex', justifyContent: 'center', marginTop: 6 }}>
                      <Button
                        type="text"
                        danger
                        className="w-textlink"
                        icon={<WarningOutlined />}
                        disabled={actionLoading === op.id}
                        onClick={() => {
                          if (busyOps.current.has(op.id)) return;
                          setReportNote('');
                          setReportForOp(op);
                        }}
                        style={{ height: 36, fontWeight: 600, fontSize: 13 }}
                      >
                        Report breakdown
                      </Button>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        {pauseForOp && (
          <div
            style={{
              position: 'fixed',
              inset: 0,
              background: 'rgba(15,23,42,0.45)',
              zIndex: 1000,
              display: 'flex',
              alignItems: 'flex-end',
              justifyContent: 'center',
            }}
            onClick={() => pauseArmed && setPauseForOp(null)}
          >
            <div
              style={{
                width: '100%',
                maxWidth: 480,
                background: colors.card,
                borderTopLeftRadius: 16,
                borderTopRightRadius: 16,
                padding: '16px 16px 24px',
                boxShadow: colors.shadow,
              }}
              onClick={(e) => e.stopPropagation()}
            >
              <div style={{ fontWeight: 800, fontSize: 16, marginBottom: 4 }}>Pause reason</div>
              <div style={{ fontSize: 13, color: colors.textSecondary, marginBottom: 12 }}>
                Why are you pausing {pauseForOp.operationName || 'this operation'}?
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {PAUSE_REASONS.map((r) => (
                  <Button
                    key={r.value}
                    block
                    size="large"
                    loading={actionLoading === pauseForOp.id}
                    onClick={() => confirmPause(r.value)}
                    style={{ height: 48, fontWeight: 700, textAlign: 'left' }}
                  >
                    {r.label}
                  </Button>
                ))}
                <Button block size="large" onClick={() => setPauseForOp(null)} style={{ height: 48 }}>
                  Cancel
                </Button>
              </div>
            </div>
          </div>
        )}

        {reportForOp && (
          <div
            style={{
              position: 'fixed',
              inset: 0,
              background: 'rgba(15,23,42,0.45)',
              zIndex: 1000,
              display: 'flex',
              alignItems: 'flex-end',
              justifyContent: 'center',
            }}
            onClick={() => reportArmed && setReportForOp(null)}
          >
            <div
              style={{
                width: '100%',
                maxWidth: 480,
                background: colors.card,
                borderTopLeftRadius: 16,
                borderTopRightRadius: 16,
                padding: '16px 16px 24px',
                boxShadow: colors.shadow,
              }}
              onClick={(e) => e.stopPropagation()}
            >
              <div style={{ fontWeight: 800, fontSize: 16, marginBottom: 4 }}>Report breakdown</div>
              <div style={{ fontSize: 13, color: colors.textSecondary, marginBottom: 12 }}>
                Why is {reportForOp.machineUnitLabel || reportForOp.machineTypeName || 'this machine'} down?
              </div>
              <textarea
                value={reportNote}
                onChange={(e) => setReportNote(e.target.value)}
                placeholder="Note (required for Other)"
                rows={2}
                style={{
                  width: '100%',
                  boxSizing: 'border-box',
                  marginBottom: 12,
                  padding: '10px 12px',
                  borderRadius: 10,
                  border: `1px solid ${colors.cardBorder}`,
                  background: colors.inputBg || colors.card,
                  color: colors.text,
                  fontSize: 14,
                  fontFamily: 'inherit',
                  resize: 'none',
                }}
              />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {DOWNTIME_REASONS.map((r) => (
                  <Button
                    key={r.value}
                    block
                    size="large"
                    loading={actionLoading === reportForOp.id}
                    onClick={() => confirmBreakdown(r.value)}
                    style={{ height: 48, fontWeight: 700, textAlign: 'left' }}
                  >
                    {r.label}
                  </Button>
                ))}
                <Button block size="large" onClick={() => setReportForOp(null)} style={{ height: 48 }}>
                  Cancel
                </Button>
              </div>
            </div>
          </div>
        )}

        {job.description && (
          <div
            style={{
              background: colors.card,
              border: `1px solid ${colors.cardBorder}`,
              borderRadius: 14,
              padding: 14,
              marginTop: 4,
              boxShadow: colors.shadow,
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                fontWeight: 800,
                fontSize: 14,
                marginBottom: 8,
              }}
            >
              <FileTextOutlined style={{ color: colors.accent }} />
              Job Notes
            </div>
            <div style={{ fontSize: 13, color: colors.textSecondary, lineHeight: 1.55 }}>
              {job.description}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

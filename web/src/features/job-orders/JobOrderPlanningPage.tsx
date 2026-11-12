import { useEffect, useMemo, useRef, useState } from 'react';
import { InputNumber, Button, Select, Typography, Alert, Tag, Tooltip, Popover, Badge, message, Input } from 'antd';
import InfoTip from '../../components/InfoTip';
import {
  CloseOutlined,
  DeleteOutlined,
  DownOutlined,
  FileTextOutlined,
  LockOutlined,
  PlusOutlined,
  StarFilled,
  StarOutlined,
  UpOutlined,
  UserAddOutlined,
  WarningOutlined,
  ArrowUpOutlined,
  ArrowDownOutlined,
  CheckCircleFilled,
} from '@ant-design/icons';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import dayjs from 'dayjs';
import { jobOrdersApi, workersApi } from '../../api/jobOrders.api';
import { operationTypesApi } from '../../api/users.api';
import { getErrorMessage } from '../../api/client';
import { MACHINE_OPTIONS } from '../../types';
import { formatShopDateTime, formatShop } from '../../utils/shopTime';
import ScheduleProposalPanel from './ScheduleProposalPanel';
import ScheduleWeekView from './ScheduleWeekView';
import JobOrderFlowSteps, {
  resolveJobFlowStep,
  type JobFlowStepId,
} from './JobOrderFlowSteps';
import { jobOrdersListPath } from './jobOrderListPaths';
import JobInfoCard from './JobInfoCard';
import RecommendationWeightsButton from './RecommendationWeights';
import { PersonAvatar, PersonChip } from '../../components/PersonAvatar';
import { personLabel } from '../../utils/people';
import { pairKey, pairedUnitId, parsePairKey, scoreBreakdown, unitOwnerName, unitPairText } from './unitPairing';
import type {
  FasterPairSuggestion,
  JobOrder,
  MachineInfo,
  MachineUnitInfo,
  OperationType,
  ProposedOperation,
  ScheduleProblem,
  ScheduleProposeResult,
  RecommendationWeightSets,
  ScoringWeights,
  SuggestionDateGate,
  User,
  WorkerSuggestion,
} from '../../types';
import { Page, PageSkeleton } from '../../components/ui';
import './joForm.css';

const { Text } = Typography;

/** Backend reasons carry ISO dates ("received 2026-10-06"); show them like the rest of the page. */
function readableDates(text: string) {
  return text.replace(/\b\d{4}-\d{2}-\d{2}\b/g, (iso) => formatShop(iso, 'MMM D, YYYY'));
}

const SCHEDULE_HELP =
  'The worker and machine unit were chosen in the Operations step and are shown here for reference. ' +
  'Review and edit start times. A worker or unit shown in red is booked elsewhere at that time. ' +
  'Changes show in the week view right away but are not saved until you confirm. Confirming ' +
  'releases the job to the shop floor.';

function isPlanningStatus(status: string) {
  return status === 'DRAFT';
}

type OpFormRow = {
  key: string;
  id?: string;
  operationTypeId?: string;
  operationName?: string;
  machineTypeId?: string;
  machineUnitId?: string;
  assignedWorkerId?: string;
  /** Up to MAX_HELPERS; no helpers on outsourced work. */
  helperIds?: string[];
  estimatedHours?: number | null;
  turnaroundDays?: number | null;
  scheduledStart?: string;
  scheduledEnd?: string;
  status?: string;
  notes?: string;
};

const MAX_HELPERS = 2;

/** Finishing is the final operation: it stays last, and new operations go before it. */
const FINAL_OPERATION_CODE = 'FINISHING';

/** Steps pre-filled when planning a Fabrication job, in shop order. */
const FABRICATION_SEQUENCE = ['LAYOUT', 'CUTTING', 'BENDING', 'FITTING', FINAL_OPERATION_CODE];

function isOutsourcedType(ot?: OperationType | null): boolean {
  return Boolean(ot?.isOutsourced);
}

function isFinalType(ot?: OperationType | null, operationName?: string): boolean {
  if (ot) return ot.code === FINAL_OPERATION_CODE;
  return (operationName || '').trim().toLowerCase().startsWith('finishing');
}

function prefillRows(types: OperationType[], jobType?: string): OpFormRow[] {
  const codes = jobType === 'FABRICATION' ? FABRICATION_SEQUENCE : [FINAL_OPERATION_CODE];
  return codes.map((code) => types.find((t) => t.code === code))
    .filter((t): t is OperationType => Boolean(t))
    .map((t) => ({
      key: newRowKey(),
      operationTypeId: t.id,
      operationName: t.name,
      machineTypeId: t.defaultMachineTypeId || undefined,
      assignedWorkerId: undefined,
    }));
}

/** Units grouped by machine type; the operation's usual machine comes first. */
function machineUnitOptions(
  units: MachineUnitInfo[],
  catalog: MachineInfo[],
  preferredTypeId?: string | null,
  selectedUnitId?: string,
) {
  const byType = new Map<string, MachineUnitInfo[]>();
  for (const u of units) {
    if (u.active === false && u.id !== selectedUnitId) continue;
    const list = byType.get(u.machineTypeId) || [];
    list.push(u);
    byType.set(u.machineTypeId, list);
  }
  const typeName = (typeId: string, sample: MachineUnitInfo) =>
    sample.machineTypeName || catalog.find((m) => m.id === typeId)?.name || 'Machine';
  return [...byType.entries()]
    .sort(([a, ua], [b, ub]) => {
      if (a === preferredTypeId) return -1;
      if (b === preferredTypeId) return 1;
      return typeName(a, ua[0]).localeCompare(typeName(b, ub[0]));
    })
    .map(([typeId, list]) => ({
      label: typeName(typeId, list[0]),
      title: typeName(typeId, list[0]),
      options: list
        .sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }))
        .map((u) => {
          const owner = unitOwnerName(u);
          return {
            value: u.id,
            search: `${u.label} ${typeName(typeId, u)} ${owner || ''}`,
            label: owner ? `${u.label} · ${owner}` : u.label,
          };
        }),
    }));
}

function workerOptions(workers: User[]) {
  return workers.map((w) => {
    const free = w.available !== false;
    const title =
      !free && w.activeJobTitle && w.activeJobTitle !== 'another job'
        ? w.activeJobTitle
        : undefined;
    const name = personLabel(w.fullName, w.nickname);
    return {
      value: w.id,
      disabled: !free,
      search: name,
      label: (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, maxWidth: '100%' }}>
          <PersonChip
            userId={w.id}
            fullName={w.fullName}
            nickname={w.nickname}
            photoVersion={w.photoVersion}
            size={20}
          />
          {free ? null : (
            <span style={{ color: '#8c8c8c' }}>(unavailable{title ? ` · ${title}` : ''})</span>
          )}
        </span>
      ),
    };
  });
}

function pickBestWorker(
  suggestions: WorkerSuggestion[],
  qualifiedWorkers?: User[],
): { workerId?: string; machineUnitId?: string } {
  // Rows that can finish in time for the date required come first; when none
  // can, the closest finish (the server lists it first).
  const usable = suggestions.filter((s) => s.qualified !== false && s.available !== false);
  const fromSuggestion = usable.find((s) => s.meetsDate !== false) ?? usable[0];
  if (fromSuggestion) {
    return {
      workerId: fromSuggestion.workerId,
      machineUnitId: fromSuggestion.machineUnitId || undefined,
    };
  }
  const fromList = qualifiedWorkers?.find((w) => w.available !== false);
  return { workerId: fromList?.id };
}

/** "Juan Cruz · Lathe #2" for a machine lead ("· Ben's unit" when it is another operator's), else the name. */
function suggestionLabel(s: WorkerSuggestion, units: MachineUnitInfo[]) {
  const name = personLabel(s.fullName, s.nickname);
  const unit = units.find((u) => u.id === s.machineUnitId);
  const unitText = unitPairText(unit, s.workerId) || s.machineUnitLabel;
  return unitText ? `${name} · ${unitText}` : name;
}

function newRowKey() {
  return `op-${Math.random().toString(36).slice(2, 10)}`;
}

export default function JobOrderPlanningPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [job, setJob] = useState<JobOrder | null>(null);
  const [wizardStep, setWizardStep] = useState<JobFlowStepId>(2);
  const [operations, setOperations] = useState<OpFormRow[]>([]);
  const [rowWorkers, setRowWorkers] = useState<Record<number, User[]>>({});
  const [machines, setMachines] = useState<MachineInfo[]>(MACHINE_OPTIONS);
  const [operationTypes, setOperationTypes] = useState<OperationType[]>([]);
  const [rowSuggestions, setRowSuggestions] = useState<Record<number, WorkerSuggestion[]>>({});
  const [rowWeights, setRowWeights] = useState<Record<number, ScoringWeights>>({});
  const [recWeights, setRecWeights] = useState<RecommendationWeightSets | null>(null);
  const [rowGates, setRowGates] = useState<Record<number, SuggestionDateGate | undefined>>({});
  const [helperSuggestions, setHelperSuggestions] = useState<Record<string, WorkerSuggestion[]>>({});
  const [allWorkers, setAllWorkers] = useState<User[]>([]);
  const [machineUnits, setMachineUnits] = useState<MachineUnitInfo[]>([]);
  const [scheduleOps, setScheduleOps] = useState<ProposedOperation[] | null>(null);
  const [scheduleMeta, setScheduleMeta] = useState<{
    projectedCompletion?: string | null;
    scheduleFlag?: 'GREEN' | 'AMBER' | 'RED' | null;
    materialNotBefore?: string | null;
    materialConstraintReason?: string | null;
  } | null>(null);
  const materialsNeeded = job?.materialStatus !== 'NOT_REQUIRED';
  const linesReadiness =
    materialsNeeded && job?.materialReadiness?.source === 'PURCHASE_LINES'
      ? job.materialReadiness
      : null;
  const noLeadTimeSuppliers = linesReadiness?.missingLeadTimeSuppliers || [];
  const materialDateUnknown = noLeadTimeSuppliers.length > 0;
  const [scheduleProblems, setScheduleProblems] = useState<ScheduleProblem[]>([]);
  const [scheduleWarnings, setScheduleWarnings] = useState<ScheduleProblem[]>([]);
  const [scheduleNotice, setScheduleNotice] = useState<string | null>(null);
  const [proposing, setProposing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState('');
  const [openRows, setOpenRows] = useState<Record<string, boolean>>({});
  const workerFetchSeq = useRef<Record<number, number>>({});
  const suggestionFetchSeq = useRef<Record<number, number>>({});
  const rowDataRef = useRef<
    Record<number, { workers: User[]; suggestions: WorkerSuggestion[] }>
  >({});

  const syncRowWorkerAssignment = (
    rowIndex: number,
    options?: { preserveExisting?: boolean },
  ) => {
    const { preserveExisting = false } = options || {};
    const rowData = rowDataRef.current[rowIndex];
    if (!rowData) return;
    const { workers, suggestions } = rowData;
    if (!workers.length && !suggestions.length) return;

    setOperations((prev) => {
      const row = prev[rowIndex];
      if (!row) return prev;

      if (preserveExisting && row.assignedWorkerId) {
        const worker = workers.find((w) => w.id === row.assignedWorkerId);
        const suggestion = suggestions.find(
          (s) =>
            s.workerId === row.assignedWorkerId &&
            (!row.machineTypeId || !row.machineUnitId || s.machineUnitId === row.machineUnitId),
        );
        const stillValid =
          worker &&
          worker.available !== false &&
          (suggestion ? suggestion.qualified !== false && suggestion.available !== false : true);
        if (stillValid) return prev;
      }

      // A unit the Admin picked stays; only the worker is chosen for it.
      const pinnedUnit = row.machineTypeId ? row.machineUnitId : undefined;
      const best = pinnedUnit
        ? {
            ...pickBestWorker(
              suggestions.filter((s) => s.machineUnitId === pinnedUnit),
              workers,
            ),
            machineUnitId: pinnedUnit,
          }
        : pickBestWorker(suggestions, workers);
      if (row.assignedWorkerId === best.workerId && (row.machineUnitId || undefined) === best.machineUnitId) {
        return prev;
      }
      const next = [...prev];
      next[rowIndex] = {
        ...row,
        assignedWorkerId: best.workerId,
        machineUnitId: best.machineUnitId || row.machineUnitId,
        helperIds: (row.helperIds || []).filter((h) => h !== best.workerId),
      };
      return next;
    });
  };

  const goToStep = (step: JobFlowStepId) => {
    if (step < 2) return;
    setWizardStep(step);
    setSearchParams({ step: String(step) }, { replace: true });
  };

  const goBackStep = () => {
    if (!job) {
      navigate(jobOrdersListPath('DRAFT'));
      return;
    }
    if (!isPlanningStatus(job.status) || wizardStep === 4) {
      navigate(`/job-orders/${job.id}`);
      return;
    }
    if (wizardStep === 3) {
      goToStep(2);
      return;
    }
    navigate(jobOrdersListPath(job.status));
  };

  const operationsMissingItems = useMemo(() => {
    const items: string[] = [];
    if (operations.length === 0) {
      items.push('Add at least one operation');
      return items;
    }
    operations.forEach((op, index) => {
      const ot = operationTypes.find((t) => t.id === op.operationTypeId);
      const name = op.operationName || ot?.name || `Operation ${index + 1}`;
      if (isOutsourcedType(ot)) {
        if (!op.turnaroundDays) items.push(`#${index + 1} ${name}: set the turnaround in days`);
        return;
      }
      if (!op.assignedWorkerId) items.push(`#${index + 1} ${name}: assign a worker`);
      if (op.estimatedHours == null) items.push(`#${index + 1} ${name}: set target hours`);
    });
    return items;
  }, [operations, operationTypes]);

  const canAdvanceToSchedule =
    operationsMissingItems.length === 0 && !materialDateUnknown;
  const advanceTooltip = !canAdvanceToSchedule
    ? [
        ...operationsMissingItems,
        ...(materialDateUnknown
          ? [`Material arrival unknown: ${noLeadTimeSuppliers.join(', ')} has no lead time`]
          : []),
      ].join('; ')
    : undefined;

  const loadRowWorkers = async (
    rowIndex: number,
    machineTypeId?: string | null,
    clearInvalidAssignment = true,
    operationTypeId?: string | null
  ) => {
    const seq = (workerFetchSeq.current[rowIndex] || 0) + 1;
    workerFetchSeq.current[rowIndex] = seq;
    try {
      const { data } = await workersApi.list({
        machineTypeId: machineTypeId || undefined,
        operationTypeId: operationTypeId || undefined,
      });
      if (workerFetchSeq.current[rowIndex] !== seq) return;
      setRowWorkers((prev) => ({ ...prev, [rowIndex]: data }));
      rowDataRef.current[rowIndex] = {
        workers: data,
        suggestions: rowDataRef.current[rowIndex]?.suggestions || [],
      };
      if (clearInvalidAssignment) {
        syncRowWorkerAssignment(rowIndex);
      }
    } catch {
      if (workerFetchSeq.current[rowIndex] !== seq) return;
      setRowWorkers((prev) => ({ ...prev, [rowIndex]: [] }));
    }
  };

  const loadSuggestions = async (
    rowIndex: number,
    op: OpFormRow,
    options?: { autoAssign?: boolean; preserveExisting?: boolean }
  ) => {
    const { autoAssign = true, preserveExisting = false } = options || {};
    const seq = (suggestionFetchSeq.current[rowIndex] || 0) + 1;
    suggestionFetchSeq.current[rowIndex] = seq;
    const ot = operationTypes.find((t) => t.id === op.operationTypeId);
    if (isOutsourcedType(ot)) {
      setRowSuggestions((prev) => ({ ...prev, [rowIndex]: [] }));
      return;
    }
    if (!op.operationTypeId && !op.machineTypeId && !op.operationName) {
      setRowSuggestions((prev) => ({ ...prev, [rowIndex]: [] }));
      return;
    }
    try {
      // Target hours of the other steps, so the date-required gate leaves
      // room for them (outsourced steps count by their turnaround elsewhere).
      const hoursOf = (rows: OpFormRow[]) =>
        rows
          .filter((r) => !isOutsourcedType(operationTypes.find((t) => t.id === r.operationTypeId)))
          .reduce((sum, r) => sum + (Number(r.estimatedHours) || 0), 0);
      const { data } = await workersApi.suggest([], {
        excludeJobId: id,
        excludeOperationId: op.id,
        machineTypeId: op.machineTypeId,
        operationTypeId: op.operationTypeId,
        operationName: op.operationName,
        pairs: Boolean(op.machineTypeId),
        estimatedHours: op.estimatedHours,
        hoursBefore: hoursOf(operations.slice(0, rowIndex)),
        hoursAfter: hoursOf(operations.slice(rowIndex + 1)),
      });
      if (suggestionFetchSeq.current[rowIndex] !== seq) return;
      const suggestions = data.suggestions || [];
      setRowSuggestions((prev) => ({ ...prev, [rowIndex]: suggestions }));
      setRowGates((prev) => ({ ...prev, [rowIndex]: data.dateGate }));
      if (data.weights) setRowWeights((prev) => ({ ...prev, [rowIndex]: data.weights }));
      if (data.recommendationWeights) setRecWeights(data.recommendationWeights);
      rowDataRef.current[rowIndex] = {
        workers: rowDataRef.current[rowIndex]?.workers || [],
        suggestions,
      };
      if (!autoAssign) return;
      syncRowWorkerAssignment(rowIndex, { preserveExisting });
    } catch {
      if (suggestionFetchSeq.current[rowIndex] !== seq) return;
      setRowSuggestions((prev) => ({ ...prev, [rowIndex]: [] }));
    }
  };

  // Helper suggestions follow each row's lead (keyed by row key).
  const helperQueryKey = operations
    .map((o) => `${o.key}:${o.assignedWorkerId || ''}:${o.operationTypeId || ''}:${(o.helperIds || []).join(',')}`)
    .join('|');
  useEffect(() => {
    let cancelled = false;
    operations.forEach((op) => {
      const ot = operationTypes.find((t) => t.id === op.operationTypeId);
      if (!op.assignedWorkerId || isOutsourcedType(ot)) {
        setHelperSuggestions((prev) => (prev[op.key] ? { ...prev, [op.key]: [] } : prev));
        return;
      }
      void workersApi
        .suggest([], {
          excludeJobId: id,
          excludeOperationId: op.id,
          operationTypeId: op.operationTypeId,
          operationName: op.operationName,
          machineTypeId: op.machineTypeId,
          leadId: op.assignedWorkerId,
          excludeWorkerIds: op.helperIds || [],
        })
        .then(({ data }) => {
          if (!cancelled) setHelperSuggestions((prev) => ({ ...prev, [op.key]: data.suggestions || [] }));
        })
        .catch(() => undefined);
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [helperQueryKey, operationTypes]);

  useEffect(() => {
    workersApi
      .list()
      .then(({ data }) => setAllWorkers(data))
      .catch(() => setAllWorkers([]));
  }, []);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const [jobRes, machinesRes, unitsRes, typesRes] = await Promise.all([
          jobOrdersApi.get(id),
          jobOrdersApi.machines(),
          jobOrdersApi.machineUnits(),
          operationTypesApi.list(),
        ]);
        if (cancelled) return;
        const j = jobRes.data;
        const land = resolveJobFlowStep(j);
        const rawStep = Number(searchParams.get('step'));
        const requested =
          rawStep === 2 || rawStep === 3 || rawStep === 4 ? (rawStep as JobFlowStepId) : null;

        if (!isPlanningStatus(j.status) && requested !== 4 && land !== 4) {
          message.info('This job is already scheduled. Opening confirmation.');
        }

        let initial: JobFlowStepId = requested || land;
        if (!isPlanningStatus(j.status)) {
          initial = requested && requested <= land ? requested : 4;
        } else if (initial === 4) {
          initial = land >= 2 ? land : 2;
        } else if (initial < 2) {
          initial = 2;
        }

        setJob(j);
        setWizardStep(initial);
        if (String(searchParams.get('step')) !== String(initial)) {
          setSearchParams({ step: String(initial) }, { replace: true });
        }
        setMachines(machinesRes.data?.length ? machinesRes.data : MACHINE_OPTIONS);
        setMachineUnits(unitsRes.data || []);
        setOperationTypes(typesRes.data || []);
        const opsList = j.operations || [];
        const rows: OpFormRow[] =
          opsList.length > 0
            ? opsList
                .slice()
                .sort((a, b) => a.sequenceNo - b.sequenceNo)
                .map((op) => ({
                  key: op.id || newRowKey(),
                  id: op.id,
                  operationTypeId: op.operationTypeId || undefined,
                  operationName: op.operationName,
                  machineTypeId: op.machineTypeId || undefined,
                  machineUnitId: op.machineUnitId || undefined,
                  assignedWorkerId: op.assignedWorkerId || undefined,
                  helperIds: op.helperIds || [],
                  estimatedHours: op.estimatedHours,
                  turnaroundDays: op.turnaroundDays ?? null,
                  scheduledStart: op.scheduledStart || undefined,
                  scheduledEnd: op.scheduledEnd || undefined,
                  status: op.status,
                  notes: op.notes || undefined,
                }))
            : prefillRows(typesRes.data || [], j.jobType).length
              ? prefillRows(typesRes.data || [], j.jobType)
              : [
                  {
                    key: newRowKey(),
                    operationTypeId: undefined,
                    operationName: '',
                    machineTypeId: undefined,
                    assignedWorkerId: undefined,
                  },
                ];
        setOperations(rows);

        // Restore schedule stage from persisted draft windows.
        if (initial === 3) {
          const restored: ProposedOperation[] = rows
            .map((op, i) => {
              const start = op.scheduledStart || null;
              const end = op.scheduledEnd || null;
              const scheduled = Boolean(start && end);
              return {
                id: op.id,
                sequenceNo: i + 1,
                operationName: op.operationName,
                assignedWorkerId: op.assignedWorkerId || null,
                helperIds: op.helperIds || [],
                machineTypeId: op.machineTypeId || null,
                machineUnitId: op.machineUnitId || null,
                machineUnitLabel: op.machineUnitId
                  ? unitsRes.data?.find((u) => u.id === op.machineUnitId)?.label || null
                  : null,
                estimatedHours: op.estimatedHours ?? undefined,
                scheduledStart: start,
                scheduledEnd: end,
                segments: scheduled && start && end ? [{ start, end }] : [],
                scheduled,
              };
            })
            .filter((op) => op.scheduled);
          if (restored.length > 0) {
            setScheduleOps(restored);
            const ends = restored
              .map((op) => op.scheduledEnd)
              .filter((v): v is string => Boolean(v));
            const projected =
              ends.length > 0
                ? ends.reduce((a, b) => (dayjs(a).isAfter(dayjs(b)) ? a : b))
                : null;
            setScheduleMeta({
              projectedCompletion: projected,
              scheduleFlag: j.scheduleFlag ?? null,
            });
          } else {
            setScheduleOps(null);
            setScheduleMeta(null);
          }
        } else {
          setScheduleOps(null);
          setScheduleMeta(null);
        }

        if (initial === 3 && isPlanningStatus(j.status)) {
          try {
            const { data } = await jobOrdersApi.proposeSchedule(id, { restoreSaved: true });
            if (cancelled) return;
            applyProposal(data);
            setScheduleNotice(
              data.replacedPastStart
                ? `The saved schedule started on ${formatShopDateTime(data.replacedPastStart)}, which has passed, so a fresh schedule was proposed.`
                : null
            );
          } catch (err) {
            if (!cancelled) setError(getErrorMessage(err));
          }
        }

        const types = typesRes.data || [];
        rows.forEach((row, index) => {
          if (isOutsourcedType(types.find((t) => t.id === row.operationTypeId))) return;
          void loadRowWorkers(index, row.machineTypeId, false, row.operationTypeId);
          void loadSuggestions(index, row, { preserveExisting: true });
        });
      } catch (err) {
        if (!cancelled) setError(getErrorMessage(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const patchRow = (index: number, patch: Partial<OpFormRow>) => {
    setOperations((prev) => {
      const next = [...prev];
      next[index] = { ...next[index], ...patch };
      return next;
    });
  };

  /** Re-key the per-row worker lists after rows move; null drops a row's entry. */
  const remapRowState = (to: (oldIndex: number) => number | null) => {
    const remap = <T,>(m: Record<number, T>) => {
      const out: Record<number, T> = {};
      for (const [k, v] of Object.entries(m)) {
        const n = to(Number(k));
        if (n !== null) out[n] = v;
      }
      return out;
    };
    setRowWorkers((prev) => remap(prev));
    setRowSuggestions((prev) => remap(prev));
    setRowWeights((prev) => remap(prev));
    setRowGates((prev) => remap(prev));
    rowDataRef.current = remap(rowDataRef.current);
  };

  const isFinalRow = (row?: OpFormRow) =>
    isFinalType(operationTypes.find((t) => t.id === row?.operationTypeId), row?.operationName);

  const onOperationTypeChange = async (rowIndex: number, typeId: string) => {
    const ot = operationTypes.find((t) => t.id === typeId);
    const current = operations[rowIndex];
    let index = rowIndex;
    if (isFinalType(ot) && index !== operations.length - 1) {
      const last = operations.length - 1;
      setOperations((prev) => {
        const next = [...prev];
        const [row] = next.splice(index, 1);
        next.push(row);
        return next;
      });
      remapRowState((i) => (i === index ? last : i > index ? i - 1 : i));
      index = last;
    }
    if (isOutsourcedType(ot)) {
      setRowWorkers((prev) => ({ ...prev, [index]: [] }));
      setRowSuggestions((prev) => ({ ...prev, [index]: [] }));
      rowDataRef.current[index] = { workers: [], suggestions: [] };
      patchRow(index, {
        operationTypeId: typeId,
        operationName: ot?.name || '',
        machineTypeId: undefined,
        machineUnitId: undefined,
        assignedWorkerId: undefined,
        helperIds: [],
        estimatedHours: null,
        turnaroundDays: current?.turnaroundDays ?? ot?.defaultTurnaroundDays ?? null,
      });
      return;
    }
    const machineTypeId = ot?.defaultMachineTypeId || undefined;
    patchRow(index, {
      operationTypeId: typeId,
      operationName: ot?.name || '',
      machineTypeId,
      machineUnitId: undefined,
      assignedWorkerId: undefined,
      turnaroundDays: null,
    });
    void loadRowWorkers(index, machineTypeId, true, typeId);
    void loadSuggestions(index, {
      ...current,
      operationTypeId: typeId,
      operationName: ot?.name || '',
      machineTypeId,
    });
  };

  const onMachineTypeChange = (index: number, machineTypeId?: string) => {
    const row = operations[index];
    patchRow(index, { machineTypeId, machineUnitId: undefined, assignedWorkerId: undefined });
    void loadRowWorkers(index, machineTypeId, true, row?.operationTypeId);
    void loadSuggestions(index, {
      ...operations[index],
      machineTypeId,
      assignedWorkerId: undefined,
    });
  };

  /** The Admin picks the machine unit itself; its machine type follows. */
  const onMachineUnitChange = (index: number, unitId?: string) => {
    const row = operations[index];
    if (!unitId) {
      onMachineTypeChange(index, undefined);
      return;
    }
    const unit = machineUnits.find((u) => u.id === unitId);
    if (!unit) return;
    if (row?.machineTypeId === unit.machineTypeId) {
      patchRow(index, { machineUnitId: unitId });
      syncRowWorkerAssignment(index, { preserveExisting: true });
      return;
    }
    const machineTypeId = unit.machineTypeId;
    patchRow(index, { machineTypeId, machineUnitId: unitId, assignedWorkerId: undefined });
    void loadRowWorkers(index, machineTypeId, true, row?.operationTypeId);
    void loadSuggestions(index, {
      ...operations[index],
      machineTypeId,
      machineUnitId: unitId,
      assignedWorkerId: undefined,
    });
  };

  const buildOperationsPayload = () =>
    operations.map((op, i) => {
      const ot = operationTypes.find((t) => t.id === op.operationTypeId);
      const outsourced = isOutsourcedType(ot);
      const mt = outsourced
        ? undefined
        : machines.find((m) => m.id === op.machineTypeId || m.code === op.machineTypeId);
      return {
        id: op.id,
        sequenceNo: i + 1,
        operationTypeId: op.operationTypeId || null,
        operationName: op.operationName || ot?.name,
        ...(mt?.id ? { machineTypeId: mt.id } : { machinesNeeded: [] }),
        assignedWorkerId: outsourced ? null : op.assignedWorkerId || null,
        helperIds:
          outsourced || !op.assignedWorkerId
            ? []
            : (op.helperIds || []).filter((h) => h && h !== op.assignedWorkerId),
        estimatedHours: outsourced ? null : op.estimatedHours ?? null,
        turnaroundDays: outsourced ? op.turnaroundDays ?? null : null,
        machineUnitId: outsourced ? null : op.machineUnitId || null,
        scheduledStart: op.scheduledStart || null,
        scheduledEnd: op.scheduledEnd || null,
        status: op.status || 'PENDING',
        notes: op.notes?.trim() || null,
      };
    });

  const buildConfirmPayload = () =>
    buildOperationsPayload().map((op, i) => {
      const proposed = scheduleOps?.find((p) => p.sequenceNo === i + 1);
      if (!proposed?.scheduled) return op;
      return {
        ...op,
        scheduledStart: proposed.scheduledStart || null,
        scheduledEnd: proposed.scheduledEnd || null,
        machineUnitId: proposed.machineUnitId || op.machineUnitId,
        assignedWorkerId: proposed.assignedWorkerId || op.assignedWorkerId,
        status: 'SCHEDULED',
      };
    });

  const applyProposal = (data: ScheduleProposeResult) => {
    setScheduleOps(data.operations);
    setScheduleMeta({
      projectedCompletion: data.projectedCompletion,
      scheduleFlag: data.scheduleFlag,
      materialNotBefore: data.materialNotBefore,
      materialConstraintReason: data.materialConstraintReason,
    });
    setScheduleProblems(data.problems || []);
    setScheduleWarnings(data.warnings || []);
  };

  const handleViewProposedSchedule = async () => {
    if (!id || !job || !canAdvanceToSchedule) return;
    setProposing(true);
    setError('');
    try {
      const { data: saved } = await jobOrdersApi.update(id, {
        operations: buildOperationsPayload(),
        advanceToPlanning: true,
      });
      setJob(saved);
      const { data } = await jobOrdersApi.proposeSchedule(id, {
        operations: buildOperationsPayload(),
        honorMachinePins: true,
      });
      applyProposal(data);
      setScheduleNotice(null);
      goToStep(3);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setProposing(false);
    }
  };

  const scheduleOpsToPayload = (ops: ProposedOperation[]) =>
    ops.map((op) => ({
      id: op.id,
      sequenceNo: op.sequenceNo,
      operationName: op.operationName,
      operationTypeId: operations[op.sequenceNo - 1]?.operationTypeId || null,
      turnaroundDays: op.turnaroundDays ?? null,
      assignedWorkerId: op.assignedWorkerId,
      helperIds: operations[op.sequenceNo - 1]?.helperIds || [],
      machineTypeId: op.machineTypeId,
      machineUnitId: op.machineUnitId,
      machineUnitLabel: op.machineUnitLabel,
      estimatedHours: op.estimatedHours,
      scheduledStart: op.scheduledStart,
      scheduledEnd: op.scheduledEnd,
    }));

  const handleScheduleOpChange = (sequenceNo: number, patch: Partial<ProposedOperation>) => {
    if (!scheduleOps) return;
    const nextOps = scheduleOps.map((op) =>
      op.sequenceNo === sequenceNo ? { ...op, ...patch } : op
    );
    setScheduleOps(nextOps);
    if (!Object.prototype.hasOwnProperty.call(patch, 'scheduledStart') || !id || readOnly) return;

    void (async () => {
      setProposing(true);
      setError('');
      try {
        const { data } = await jobOrdersApi.proposeSchedule(id, {
          operations: scheduleOpsToPayload(nextOps),
          pinSequence: sequenceNo,
          lockBeforeSequence: sequenceNo,
          honorMachinePins: true,
        });
        applyProposal(data);
        const edited = data.operations.find((o) => o.sequenceNo === sequenceNo);
        if (edited?.scheduled && edited.message) message.info(edited.message);
      } catch (err) {
        setError(getErrorMessage(err));
        message.error(getErrorMessage(err));
      } finally {
        setProposing(false);
      }
    })();
  };

  const handleRefreshProposedSchedule = async () => {
    if (!id || !scheduleOps) return;
    setProposing(true);
    setError('');
    try {
      // Drop pinned windows so earliest-fit rebuilds times; keep machine/worker picks.
      const { data } = await jobOrdersApi.proposeSchedule(id, {
        operations: scheduleOpsToPayload(scheduleOps).map((op) => ({
          ...op,
          scheduledStart: null,
          scheduledEnd: null,
        })),
        honorMachinePins: true,
      });
      applyProposal(data);
      setScheduleNotice(null);
      message.success('Schedule reset to proposal');
    } catch (err) {
      setError(getErrorMessage(err));
      message.error(getErrorMessage(err));
    } finally {
      setProposing(false);
    }
  };

  const handleUsePair = async (sequenceNo: number, pair: FasterPairSuggestion) => {
    if (!id || !scheduleOps) return;
    const nextOps = scheduleOps.map((op) =>
      op.sequenceNo === sequenceNo
        ? {
            ...op,
            assignedWorkerId: pair.assignedWorkerId,
            machineUnitId: pair.machineUnitId || null,
            machineUnitLabel: pair.machineUnitLabel || null,
            scheduledStart: null,
            scheduledEnd: null,
          }
        : op.sequenceNo > sequenceNo
          ? { ...op, scheduledStart: null, scheduledEnd: null }
          : op
    );
    setScheduleOps(nextOps);
    setOperations((prev) =>
      prev.map((row, i) =>
        i === sequenceNo - 1
          ? {
              ...row,
              assignedWorkerId: pair.assignedWorkerId,
              machineUnitId: pair.machineUnitId || undefined,
            }
          : row
      )
    );
    setProposing(true);
    setError('');
    try {
      const { data } = await jobOrdersApi.proposeSchedule(id, {
        operations: scheduleOpsToPayload(nextOps),
        lockBeforeSequence: sequenceNo,
        honorMachinePins: true,
      });
      applyProposal(data);
    } catch (err) {
      setError(getErrorMessage(err));
      message.error(getErrorMessage(err));
    } finally {
      setProposing(false);
    }
  };

  const handleMachineUnitChange = async (
    sequenceNo: number,
    machineUnitId: string | null,
    machineUnitLabel: string | null
  ) => {
    if (!id || !job || !scheduleOps) return;
    const nextOps = scheduleOps.map((op) =>
      op.sequenceNo === sequenceNo
        ? { ...op, machineUnitId, machineUnitLabel }
        : op
    );
    setScheduleOps(nextOps);
    setProposing(true);
    setError('');
    try {
      const { data } = await jobOrdersApi.proposeSchedule(id, {
        operations: scheduleOpsToPayload(nextOps),
        lockBeforeSequence: sequenceNo,
        honorMachinePins: true,
      });
      applyProposal(data);
    } catch (err) {
      setError(getErrorMessage(err));
      message.error(getErrorMessage(err));
    } finally {
      setProposing(false);
    }
  };

  const handleConfirmSchedule = async () => {
    if (!id || !scheduleOps?.some((o) => o.scheduled)) return;
    setConfirming(true);
    setError('');
    try {
      const { data } = await jobOrdersApi.confirmSchedule(id, {
        operations: buildConfirmPayload(),
      });
      setJob(data);
      message.success('Schedule confirmed. The job is now scheduled.');
      goToStep(4);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setConfirming(false);
    }
  };

  /** A move that would put an operation after Finishing is not allowed. */
  const canMoveRow = (index: number, dir: -1 | 1) => {
    const target = index + dir;
    if (target < 0 || target >= operations.length) return false;
    const upper = dir === 1 ? index : target;
    return isFinalRow(operations[upper]) || !isFinalRow(operations[upper + 1]);
  };

  const moveRow = (index: number, dir: -1 | 1) => {
    if (!canMoveRow(index, dir)) return;
    const target = index + dir;
    setOperations((prev) => {
      const next = [...prev];
      const tmp = next[index];
      next[index] = next[target];
      next[target] = tmp;
      return next;
    });
    remapRowState((i) => (i === index ? target : i === target ? index : i));
  };

  const addOperation = () => {
    const finalAt = operations.findIndex((op) => isFinalRow(op));
    const at = finalAt === -1 ? operations.length : finalAt;
    setOperations((prev) => [
      ...prev.slice(0, at),
      {
        key: newRowKey(),
        operationTypeId: undefined,
        operationName: '',
        machineTypeId: undefined,
        assignedWorkerId: undefined,
      },
      ...prev.slice(at),
    ]);
    remapRowState((i) => (i >= at ? i + 1 : i));
  };

  const removeRow = (index: number) => {
    setOperations((prev) => prev.filter((_, i) => i !== index));
    remapRowState((i) => (i === index ? null : i > index ? i - 1 : i));
  };

  if (loading) {
    return (
      <PageSkeleton variant="detail" />
    );
  }

  if (!job) {
    return <Alert type="error" title={error || 'Job order not found'} />;
  }

  const readOnly = !isPlanningStatus(job.status);
  const problemsBySeq: Record<number, ScheduleProblem[]> = {};
  for (const p of scheduleProblems) (problemsBySeq[p.sequenceNo] ||= []).push(p);
  const warningsBySeq: Record<number, ScheduleProblem[]> = {};
  for (const p of scheduleWarnings) (warningsBySeq[p.sequenceNo] ||= []).push(p);
  const confirmBlockers = !scheduleOps?.length
    ? ['Propose a schedule before confirming it.']
    : scheduleProblems.map((p) => p.message);
  const canConfirm = confirmBlockers.length === 0;

  const typeOf = (record?: OpFormRow) => operationTypes.find((t) => t.id === record?.operationTypeId);

  const renderTypeCell = (record: OpFormRow, index: number) => (
    <Select
      showSearch
      optionFilterProp="label"
      style={{ width: '100%' }}
      placeholder="Operation type"
      value={record.operationTypeId}
      disabled={readOnly}
      options={operationTypes.map((t) => ({ value: t.id, label: t.name }))}
      onChange={(v) => onOperationTypeChange(index, v)}
    />
  );

  const renderMachineCell = (record: OpFormRow, index: number) => {
    const ot = typeOf(record);
    const outsourced = isOutsourcedType(ot);
    const options = machineUnitOptions(machineUnits, machines, ot?.defaultMachineTypeId, record.machineUnitId);
    return (
      <Select
        allowClear={!outsourced}
        showSearch
        optionFilterProp="search"
        style={{ width: '100%' }}
        placeholder={outsourced ? 'Outsourced' : record.machineTypeId ? 'Pick a unit' : 'No machine'}
        value={outsourced ? undefined : record.machineUnitId}
        disabled={readOnly || outsourced}
        status={!outsourced && record.machineTypeId && !record.machineUnitId ? 'warning' : undefined}
        options={options}
        onChange={(v) => onMachineUnitChange(index, v)}
      />
    );
  };

  const renderCrewCell = (record: OpFormRow, index: number) => {
    const ot = typeOf(record);
    if (isOutsourcedType(ot)) {
      return <Select style={{ width: '100%' }} placeholder="Outside shop" disabled />;
    }
    const qualifiedWorkers = rowWorkers[index] || [];
    const rowMachineId = record.machineTypeId || ot?.defaultMachineTypeId;
    const helpers = record.helperIds || [];
    const helperChoices = (current?: string) =>
      workerOptions(
        allWorkers.filter(
          (w) =>
            w.id === current ||
            (w.id !== record.assignedWorkerId && !helpers.includes(w.id)),
        ),
      );
    const setHelpers = (next: string[]) => patchRow(index, { helperIds: next });
    const canAddHelper = !readOnly && Boolean(record.assignedWorkerId) && helpers.length < MAX_HELPERS;
    const suggestions = rowSuggestions[index] || [];
    const gate = rowGates[index];
    const applyPair = (workerId?: string, unitId?: string) => {
      const resolvedUnit = record.machineTypeId
        ? record.machineUnitId ||
          (workerId ? unitId || pairedUnitId(workerId, record.machineTypeId, machineUnits) : undefined)
        : undefined;
      patchRow(index, {
        assignedWorkerId: workerId,
        helperIds: workerId ? helpers.filter((h) => h !== workerId) : [],
        machineUnitId: resolvedUnit,
      });
    };
    const pairOptions = rowMachineId
      ? (() => {
          // The unit is picked in the Machine column; here only the worker,
          // best match for that unit first.
          const unitId = record.machineUnitId;
          const seen = new Set<string>();
          const ranked = suggestions.filter(
            (s) => s.workerId && (!unitId || s.machineUnitId === unitId),
          );
          const fromSuggestions = ranked
            .filter((s) => !seen.has(s.workerId) && seen.add(s.workerId))
            .map((s) => {
              const late = s.meetsDate === false && gate?.anyMeetsDate;
              return {
                value: pairKey(s.workerId, unitId),
                search: personLabel(s.fullName, s.nickname),
                label: late
                  ? `${personLabel(s.fullName, s.nickname)} · not free in time`
                  : personLabel(s.fullName, s.nickname),
                disabled: s.qualified === false || Boolean(late),
              };
            });
          const extras = qualifiedWorkers
            .filter((w) => !seen.has(w.id))
            .map((w) => ({
              value: pairKey(w.id, unitId),
              search: personLabel(w.fullName, w.nickname),
              label: personLabel(w.fullName, w.nickname),
            }));
          return [...fromSuggestions, ...extras];
        })()
      : qualifiedWorkers.map((w) => ({
          value: pairKey(w.id),
          search: personLabel(w.fullName, w.nickname),
          label: personLabel(w.fullName, w.nickname),
          disabled: w.available === false,
        }));
    const leadValue = record.assignedWorkerId
      ? pairKey(record.assignedWorkerId, rowMachineId ? record.machineUnitId : undefined)
      : undefined;
    const chosen = suggestions.find(
      (sg) =>
        sg.workerId === record.assignedWorkerId &&
        (!record.machineUnitId || sg.machineUnitId === record.machineUnitId),
    );
    const dateNote =
      gate?.applied && chosen?.meetsDate === false
        ? gate.anyMeetsDate
          ? `Can't finish this step in time for ${job?.dueDate ? formatShop(job.dueDate, 'MMM D') : 'the date required'}`
          : `No one can finish this step in time${
              job?.dueDate ? ` for ${formatShop(job.dueDate, 'MMM D')}` : ''
            }; closest finish ${chosen.nextFreeEnd ? formatShop(chosen.nextFreeEnd, 'MMM D, h:mm A') : '—'}`
        : null;
    return (
      <div className="jo-op__crew">
        <div className="jo-op__crew-line">
          <Select
            allowClear
            showSearch
            optionFilterProp="search"
            style={{ flex: 1, minWidth: 0 }}
            placeholder="Lead worker"
            value={leadValue}
            disabled={readOnly}
            options={pairOptions}
            prefix={
              dateNote ? (
                <Tooltip title={dateNote} trigger={['hover', 'click']}>
                  <span
                    className="jo-op__date-flag"
                    role="img"
                    aria-label={dateNote}
                    tabIndex={0}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                    }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <WarningOutlined />
                  </span>
                </Tooltip>
              ) : undefined
            }
            onChange={(v) => {
              const parsed = parsePairKey(v);
              applyPair(parsed.workerId, parsed.machineUnitId);
            }}
          />
          {canAddHelper ? (
            <Tooltip title={`Add a helper (up to ${MAX_HELPERS})`}>
              <Button
                type="text"
                className="jo-op__icon-btn"
                icon={<UserAddOutlined />}
                aria-label="Add helper"
                onClick={() => setHelpers([...helpers, ''])}
              />
            </Tooltip>
          ) : null}
        </div>
        {helpers.map((hid, hi) => (
          <div key={`${hid}-${hi}`} className="jo-op__crew-line">
            <Select
              showSearch
              size="small"
              optionFilterProp="search"
              style={{ flex: 1, minWidth: 0 }}
              placeholder="Helper"
              value={hid || undefined}
              disabled={readOnly}
              options={helperChoices(hid)}
              onChange={(v) => setHelpers(helpers.map((h, j) => (j === hi ? v : h)))}
            />
            {!readOnly && (
              <Button
                type="text"
                size="small"
                className="jo-op__icon-btn"
                aria-label="Remove helper"
                icon={<CloseOutlined />}
                onClick={() => setHelpers(helpers.filter((_, j) => j !== hi))}
              />
            )}
          </div>
        ))}
      </div>
    );
  };

  const renderTargetCell = (record: OpFormRow, index: number) =>
    isOutsourcedType(typeOf(record)) ? (
      <InputNumber
        style={{ width: '100%' }}
        min={1}
        max={90}
        precision={0}
        placeholder="Days"
        suffix="days"
        value={record.turnaroundDays ?? undefined}
        disabled={readOnly}
        onChange={(v) => patchRow(index, { turnaroundDays: v })}
      />
    ) : (
      <InputNumber
        style={{ width: '100%' }}
        min={0}
        step={0.5}
        placeholder="Hours"
        suffix="h"
        value={record.estimatedHours ?? undefined}
        disabled={readOnly}
        onChange={(v) => {
          patchRow(index, { estimatedHours: v });
          // Keeps a valid lead; a row whose lead was not picked yet still gets
          // one (this request replaces the one started by the operation pick).
          void loadSuggestions(index, { ...record, estimatedHours: v ?? undefined }, { autoAssign: true, preserveExisting: true });
        }}
      />
    );

  const renderNoteButton = (record: OpFormRow, index: number) => {
    const note = record.notes?.trim() || '';
    return (
      <Popover
        trigger="click"
        placement="bottomRight"
        title="Instructions"
        content={
          <div className="jo-op__note-pop">
            <Input.TextArea
              autoSize={{ minRows: 3, maxRows: 8 }}
              maxLength={1000}
              showCount
              placeholder="Tolerances, setup, special handling"
              value={record.notes ?? ''}
              disabled={readOnly}
              onChange={(e) => patchRow(index, { notes: e.target.value })}
            />
          </div>
        }
      >
        <Tooltip title={note ? note.slice(0, 140) : 'Add instructions'}>
          <Badge count={note ? 1 : 0} size="small" offset={[-4, 4]} color="var(--c-primary)">
            <Button
              type="text"
              className={`jo-op__icon-btn${note ? ' is-on' : ''}`}
              icon={<FileTextOutlined />}
              aria-label={note ? 'Edit instructions (1 note)' : 'Add instructions'}
            />
          </Badge>
        </Tooltip>
      </Popover>
    );
  };

  /** Lead suggestions shown in the panel; helper picks only count when there are no leads. */
  const suggestionCount = (index: number, key: string) =>
    Math.min((rowSuggestions[index] || []).length || (helperSuggestions[key] || []).length, 8);

  const renderSuggestions = (index: number) => {
    const row = operations[index];
    if (isOutsourcedType(typeOf(row))) {
      return (
        <Text type="secondary" style={{ fontSize: 12 }}>
          Done by an outside shop: no worker or machine. The schedule blocks out the
          turnaround days, and the next operation waits until it is returned.
        </Text>
      );
    }
    const suggestions = rowSuggestions[index] || [];
    const qualifiedWorkers = rowWorkers[index] || [];
    const helperSugs = (helperSuggestions[row?.key || ''] || []).slice(0, 5);
    const rowHelpers = (row?.helperIds || []).filter(Boolean);
    const canAddHelper =
      !readOnly &&
      Boolean(row?.assignedWorkerId) &&
      rowHelpers.length < MAX_HELPERS;
    if (!suggestions.length && !helperSugs.length) return null;
    const topId = suggestions[0]?.workerId;
    const assignedId = row?.assignedWorkerId;
    return (
      <div>
        <div className="jo-op__more-head">
          <Text type="secondary" style={{ fontSize: 12 }}>
            {rowGates[index]?.applied && rowGates[index]?.anyMeetsDate === false
              ? 'No one can finish this step in time for the date required — closest finish first'
              : 'Best match that can finish in time, auto-selected — click another to override'}
          </Text>
          <RecommendationWeightsButton weights={recWeights} compact />
        </div>
        <div className="jo-op__picks">
          {suggestions.slice(0, 8).map((s) => {
            const inDropdown = qualifiedWorkers.some((w) => w.id === s.workerId);
            const isTop = s.workerId === topId && s.machineUnitId === suggestions[0]?.machineUnitId;
            const isAssigned =
              s.workerId === assignedId &&
              (!row?.machineTypeId || !row.machineUnitId || s.machineUnitId === row.machineUnitId);
            const tip = scoreBreakdown(s, rowWeights[index]);
            const late = s.meetsDate === false;
            const blocked = late && Boolean(rowGates[index]?.anyMeetsDate);
            return (
              <Tooltip key={pairKey(s.workerId, s.machineUnitId)} title={tip || undefined}>
                <button
                  type="button"
                  disabled={!inDropdown || readOnly || blocked}
                  onClick={() => {
                    if (!inDropdown || readOnly) return;
                    patchRow(index, {
                      assignedWorkerId: s.workerId,
                      machineUnitId: s.machineUnitId || undefined,
                      helperIds: rowHelpers.filter((h) => h !== s.workerId),
                    });
                  }}
                  className={`jo-op__pick${isAssigned ? ' is-picked' : ''}${late ? ' is-late' : ''}`}
                >
                  <span className="jo-op__pick-head">
                    {(isTop || isAssigned) && <StarFilled style={{ color: '#c9a227', fontSize: 12 }} />}
                    <PersonAvatar
                      userId={s.workerId}
                      fullName={s.fullName}
                      photoVersion={s.photoVersion}
                      size={22}
                    />
                    <span className="jo-op__pick-name">{suggestionLabel(s, machineUnits)}</span>
                    <Tag color={isAssigned ? 'gold' : 'default'} style={{ margin: 0, fontSize: 11 }}>
                      {(s.score * 100).toFixed(0)}%
                    </Tag>
                  </span>
                  {late ? (
                    <span className="jo-op__pick-late">
                      Not free in time · done {s.nextFreeEnd ? formatShop(s.nextFreeEnd, 'MMM D') : 'later'}
                    </span>
                  ) : s.nextFreeStart ? (
                    <span className="jo-op__pick-free">
                      Next free {formatShop(s.nextFreeStart, 'MMM D, h:mm A')}
                    </span>
                  ) : null}
                  {s.attendanceWarning && (
                    <Tag color="warning" icon={<WarningOutlined />} style={{ margin: '4px 0 0', fontSize: 11 }}>
                      {s.attendanceWarning}
                    </Tag>
                  )}
                  {s.reason && <span className="jo-op__pick-reason">{s.reason}</span>}
                </button>
              </Tooltip>
            );
          })}
        </div>
        {helperSugs.length > 0 && canAddHelper ? (
          <>
            <div className="jo-op__more-head" style={{ marginTop: 10 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                Suggested helpers — click to add (up to {MAX_HELPERS})
              </Text>
            </div>
            <div className="jo-op__picks">
              {helperSugs.map((s) => (
                <button
                  type="button"
                  key={s.workerId}
                  onClick={() =>
                    patchRow(index, {
                      helperIds: [...rowHelpers, s.workerId].slice(0, MAX_HELPERS),
                    })
                  }
                  className="jo-op__pick is-helper"
                >
                  <span className="jo-op__pick-head">
                    <PlusOutlined style={{ fontSize: 11, color: '#8c8c8c' }} />
                    <PersonAvatar
                      userId={s.workerId}
                      fullName={s.fullName}
                      photoVersion={s.photoVersion}
                      size={22}
                    />
                    <span className="jo-op__pick-name">{personLabel(s.fullName, s.nickname)}</span>
                    <Tag style={{ margin: 0, fontSize: 11 }}>{(s.score * 100).toFixed(0)}%</Tag>
                  </span>
                  {s.attendanceWarning && (
                    <Tag color="warning" icon={<WarningOutlined />} style={{ margin: '4px 0 0', fontSize: 11 }}>
                      {s.attendanceWarning}
                    </Tag>
                  )}
                  {s.reason && <span className="jo-op__pick-reason">{s.reason}</span>}
                </button>
              ))}
            </div>
          </>
        ) : null}
      </div>
    );
  };

  const renderOperationRow = (record: OpFormRow, index: number) => {
    const outsourced = isOutsourcedType(typeOf(record));
    const isFinal = isFinalRow(record);
    const count = outsourced ? 0 : suggestionCount(index, record.key);
    const hasMore = outsourced || count > 0;
    const open = Boolean(openRows[record.key]) && hasMore;
    const toggle = () => setOpenRows((prev) => ({ ...prev, [record.key]: !prev[record.key] }));
    return (
      <li key={record.key} className={`jo-op${open ? ' is-open' : ''}`}>
        <div className="jo-op__rail" aria-hidden>
          <span className="jo-op__num">{index + 1}</span>
        </div>
        <div className="jo-op__card">
          <div className="jo-op__grid">
            <div className="jo-op__field jo-op__field--type" data-label="Operation">
              {renderTypeCell(record, index)}
              {isFinal ? (
                <div className="jo-op__hint">
                  <LockOutlined /> Always the last step
                </div>
              ) : null}
            </div>
            <div className="jo-op__field jo-op__field--machine" data-label="Machine unit">
              {renderMachineCell(record, index)}
            </div>
            <div className="jo-op__field jo-op__field--crew" data-label="Crew">
              <div className="jo-op__crew-wrap">
                <div className="jo-op__crew-main">{renderCrewCell(record, index)}</div>
              {hasMore ? (
                <Tooltip title={outsourced ? undefined : open ? 'Hide suggestions' : 'Suggested workers'}>
                  <Button
                    type="text"
                    size="small"
                    className={`jo-op__more-btn jo-op__more-btn--crew${open ? ' is-on' : ''}`}
                    aria-expanded={open}
                    aria-label={outsourced ? 'About' : `Suggested workers (${count})`}
                    onClick={toggle}
                  >
                    {outsourced ? (
                      'About'
                    ) : (
                      <>
                        <StarOutlined /> <span className="jo-op__count">{count}</span>
                      </>
                    )}
                    {open ? <UpOutlined /> : <DownOutlined />}
                  </Button>
                </Tooltip>
              ) : null}
              </div>
            </div>
            <div className="jo-op__field jo-op__field--target" data-label={outsourced ? 'Turnaround' : 'Target'}>
              {renderTargetCell(record, index)}
            </div>
            <div className="jo-op__tools">
              {renderNoteButton(record, index)}
              {!readOnly ? (
                <span className="jo-op__order">
                  <Tooltip title={canMoveRow(index, -1) ? 'Move earlier' : undefined}>
                    <Button
                      type="text"
                      size="small"
                      className="jo-op__icon-btn"
                      icon={<ArrowUpOutlined />}
                      aria-label={`Move step ${index + 1} earlier`}
                      disabled={!canMoveRow(index, -1)}
                      onClick={() => moveRow(index, -1)}
                    />
                  </Tooltip>
                  <Tooltip title={canMoveRow(index, 1) ? 'Move later' : undefined}>
                    <Button
                      type="text"
                      size="small"
                      className="jo-op__icon-btn"
                      icon={<ArrowDownOutlined />}
                      aria-label={`Move step ${index + 1} later`}
                      disabled={!canMoveRow(index, 1)}
                      onClick={() => moveRow(index, 1)}
                    />
                  </Tooltip>
                </span>
              ) : null}
              {!readOnly ? (
                <Tooltip title={operations.length > 1 ? 'Remove step' : undefined}>
                  <Button
                    type="text"
                    size="small"
                    danger
                    className="jo-op__icon-btn"
                    icon={<DeleteOutlined />}
                    aria-label={`Remove step ${index + 1}`}
                    disabled={operations.length <= 1}
                    onClick={() => removeRow(index)}
                  />
                </Tooltip>
              ) : null}
            </div>
          </div>
          {open ? <div className="jo-op__more">{renderSuggestions(index)}</div> : null}
        </div>
      </li>
    );
  };

  const finish =
    wizardStep === 3 && scheduleMeta
      ? { projectedCompletion: scheduleMeta.projectedCompletion, flag: scheduleMeta.scheduleFlag }
      : { projectedCompletion: job.projectedCompletion, flag: job.scheduleFlag };

  const totalHours = operations.reduce((sum, op) => sum + (op.estimatedHours || 0), 0);

  return (
    <Page className="jo-plan-page">
      <JobOrderFlowSteps current={wizardStep} onStepClick={goToStep} locked={readOnly} />

      {error && <Alert type="error" title={error} showIcon />}

      <JobInfoCard job={job} finish={finish} />

      {wizardStep === 4 ? (
        <div className="jo-plan__released">
          <CheckCircleFilled style={{ fontSize: 40, color: '#0f1c2e', marginBottom: 12 }} />
          <h2 className="jo-plan__released-title">Job scheduled</h2>
          <p className="jo-plan__released-copy">
            {job.jobNumber || 'This job'} is on the schedule. Workers can see their assigned
            operations, and the client was notified that the job was received.
          </p>
          <div className="jo-plan__released-actions">
            <Link to={`/job-orders/${job.id}`}>
              <Button type="primary" style={{ fontWeight: 600 }}>
                Open job order
              </Button>
            </Link>
            <Link to="/schedule">
              <Button style={{ fontWeight: 600 }}>View schedule board</Button>
            </Link>
          </div>
        </div>
      ) : null}

      {wizardStep === 2 ? (
        <section className="jo-plan__panel" aria-label="Operations">
          <div className="jo-plan__panel-head">
            <h2 className="jo-plan__section-title">Operations</h2>
            <span className="jo-plan__panel-sub">
              {operations.length} step{operations.length === 1 ? '' : 's'}
              {totalHours > 0 ? ` · ${totalHours.toLocaleString(undefined, { maximumFractionDigits: 1 })} h target` : ''}
              {' · '}done in this order
            </span>
            {!readOnly ? <RecommendationWeightsButton weights={recWeights} /> : null}
          </div>
          <div className="jo-ops__head" aria-hidden>
            <span>#</span>
            <span>Operation</span>
            <span>Machine unit</span>
            <span>Crew</span>
            <span>Target</span>
            <span />
          </div>
          <ol className="jo-ops">{operations.map((record, index) => renderOperationRow(record, index))}</ol>

          {!readOnly ? (
            <div className="jo-plan__footer jo-plan__footer--sticky m-action-bar">
              <Button icon={<PlusOutlined />} onClick={addOperation} aria-label="Add operation" className="jo-plan__add">
                <span className="jo-plan__add-label">Add operation</span>
              </Button>
              {operationsMissingItems.length > 0 || materialDateUnknown ? (
                <Tooltip title={advanceTooltip}>
                  <span className="jo-plan__footer-note m-action-bar__note">
                    <WarningOutlined />{' '}
                    {operationsMissingItems.length + (materialDateUnknown ? 1 : 0)} thing
                    {operationsMissingItems.length + (materialDateUnknown ? 1 : 0) === 1 ? '' : 's'} left to fill in
                  </span>
                </Tooltip>
              ) : null}
              <Tooltip title={advanceTooltip}>
                <span>
                  <Button
                    type="primary"
                    loading={proposing}
                    disabled={!canAdvanceToSchedule}
                    onClick={handleViewProposedSchedule}
                  >
                    View proposed schedule
                  </Button>
                </span>
              </Tooltip>
            </div>
          ) : null}
        </section>
      ) : null}

      {wizardStep === 3 ? (
        <section className="jo-plan__panel" aria-label="Schedule">
          <div className="jo-plan__panel-head">
            <h2 className="jo-plan__section-title">
              Schedule <InfoTip title="Schedule" content={SCHEDULE_HELP} label="How the schedule works" />
            </h2>
          </div>

          {scheduleOps ? (
            <>
              {scheduleNotice && (
                <Alert type="info" showIcon style={{ marginBottom: 12 }} title={scheduleNotice} />
              )}
              {scheduleMeta?.materialNotBefore && (
                <Alert
                  type="info"
                  showIcon
                  style={{ marginBottom: 12 }}
                  title={`Earliest start ${formatShop(scheduleMeta.materialNotBefore, 'MMM D, YYYY')} — ${readableDates(
                    scheduleMeta.materialConstraintReason || 'waiting for material'
                  )}`}
                />
              )}
              <ScheduleProposalPanel
                operations={scheduleOps}
                machineUnits={machineUnits}
                workers={allWorkers}
                projectedCompletion={scheduleMeta?.projectedCompletion}
                scheduleFlag={scheduleMeta?.scheduleFlag}
                dueDate={job.dueDate}
                problemsBySeq={problemsBySeq}
                warningsBySeq={warningsBySeq}
                onChangeOp={handleScheduleOpChange}
                onMachineUnitChange={readOnly ? undefined : handleMachineUnitChange}
                onUsePair={readOnly ? undefined : handleUsePair}
                onRefreshProposal={readOnly ? undefined : handleRefreshProposedSchedule}
                refreshing={proposing}
                readOnly={readOnly}
              />
              <div className="jo-plan__week-wrap">
                <div className="sched-expand__title" style={{ marginBottom: 10 }}>
                  Schedule view
                </div>
                <ScheduleWeekView
                  jobId={job.id}
                  jobNumber={job.jobNumber}
                  jobTitle={job.title}
                  operations={scheduleOps}
                  machineUnits={machineUnits}
                  scheduleColor={job.scheduleColor}
                  colorPickerDisabled={readOnly}
                  onScheduleColorChange={async (hex) => {
                    const prev = job.scheduleColor;
                    setJob({ ...job, scheduleColor: hex });
                    try {
                      const { data } = await jobOrdersApi.update(job.id, {
                        scheduleColor: hex,
                      });
                      setJob((j) =>
                        j ? { ...j, scheduleColor: data.scheduleColor ?? hex } : j
                      );
                    } catch (err) {
                      setJob((j) => (j ? { ...j, scheduleColor: prev } : j));
                      message.error(getErrorMessage(err));
                    }
                  }}
                />
              </div>
            </>
          ) : (
            <Alert
              type="info"
              showIcon
              title="Go back to Operations and use “View proposed schedule” to generate a first draft."
            />
          )}

          {!readOnly && scheduleOps && confirmBlockers.length > 0 ? (
            <Alert
              type="error"
              showIcon
              style={{ marginTop: 12 }}
              title="This schedule can't be confirmed yet"
              description={
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {confirmBlockers.map((reason, i) => (
                    <li key={i}>{reason}</li>
                  ))}
                </ul>
              }
            />
          ) : null}
          {!readOnly ? (
            <div className="jo-plan__footer jo-plan__footer--sticky m-action-bar">
              <Button onClick={goBackStep}>Back</Button>
              <Tooltip title={!canConfirm ? confirmBlockers.join(' ') : undefined}>
                <span>
                  <Button
                    type="primary"
                    loading={confirming}
                    disabled={!canConfirm}
                    onClick={handleConfirmSchedule}
                  >
                    Confirm schedule
                  </Button>
                </span>
              </Tooltip>
            </div>
          ) : null}
        </section>
      ) : null}
    </Page>
  );
}

export type UserRole = 'ADMIN' | 'OFFICE_STAFF' | 'PRODUCTION_WORKER';
export type UserStatus = 'INVITED' | 'ACTIVE' | 'DISABLED';

/** A 1-5 level on a machine type. Operations without a machine take no skill. */
export interface WorkerSkill {
  id?: string;
  workerId?: string;
  machineTypeId: string;
  machineTypeCode?: string | null;
  machineTypeName?: string | null;
  proficiency: number;
  isPrimary: boolean;
}

/** The shop's record behind a skill level (Admin only). */
export interface SkillEvidenceRow {
  machineTypeId: string;
  machineTypeName: string;
  rated: number | null;
  leadOps: number;
  helperOps: number;
  lastDoneAt: string | null;
  efficiencyPct: number | null;
  speedVsPeersPct: number | null;
  onTargetPct: number | null;
  operatorRedos: number;
  operatorRedoPct: number | null;
  suggestedLevel: number | null;
  suggestionReason: string;
  flags: { kind: 'mismatch' | 'unused'; text: string }[];
}

export interface SkillEvidence {
  from: string;
  to: string;
  minLeadOps: number;
  lastReviewed: { at: string; byName: string | null } | null;
  machineTypes: SkillEvidenceRow[];
}

export interface WorkerSchedule {
  id?: string;
  workerId?: string;
  dayOfWeek: number;
  startTime?: string | null;
  endTime?: string | null;
  isWorking: boolean;
}

export interface OperationType {
  id: string;
  code: string;
  name: string;
  defaultMachineTypeId?: string | null;
  defaultMachineTypeCode?: string | null;
  defaultMachineTypeName?: string | null;
  active: boolean;
  /** Done by an outside shop: no worker or machine, a turnaround in days. */
  isOutsourced?: boolean;
  defaultTurnaroundDays?: number | null;
}

export interface WorkerProfile {
  id: string | null;
  userId: string;
  skills: WorkerSkill[] | string[];
  fullName?: string;
  email?: string;
}

export interface User {
  id: string;
  /** Empty for imported employees until the Admin invites them. */
  email: string | null;
  mobileNumber?: string | null;
  fullName: string;
  nickname?: string | null;
  /** Changes when the photo does; null when there is no photo. */
  photoVersion?: number | null;
  role: UserRole;
  status?: UserStatus;
  active: boolean;
  createdAt?: string;
  workerProfile?: WorkerProfile;
  skills?: WorkerSkill[];
  schedules?: WorkerSchedule[];
  /** Machine units the worker is the default operator of (own /auth/me only). */
  assignedMachines?: { id: string; label: string; machineTypeName?: string | null }[];
  available?: boolean;
  activeJobId?: string;
  activeJobTitle?: string;
  conflictOperationId?: string;
  invitation?: {
    id: string;
    channel?: string;
    expiresAt?: string | null;
    active?: boolean;
  };
}

export interface UserDevice {
  id: string;
  userId: string;
  deviceId: string;
  deviceLabel?: string | null;
  hasPin: boolean;
  pinSetAt?: string | null;
  lastUsedAt?: string | null;
  revokedAt?: string | null;
  createdAt?: string | null;
}

export interface Client {
  id: string;
  name: string;
  contact?: string;
  email?: string | null;
  mobileNumber?: string | null;
  notifyByEmail?: boolean;
  /** DETAILED: every job update (default); KEY: received, ready for delivery and delays only. */
  updateLevel?: 'DETAILED' | 'KEY';
  /** @deprecated SMS channel retired — kept optional for prototype/legacy only. */
  notifyBySms?: boolean;
  createdAt?: string;
}

export interface Supplier {
  id: string;
  name: string;
  /** 2–5 uppercase letters that start PO numbers (STP26000001). Needed to issue a PO. */
  code?: string | null;
  contactPerson?: string | null;
  phone?: string | null;
  email?: string | null;
  address?: string | null;
  typicalLeadTimeDays?: number | null;
  notes?: string | null;
  active: boolean;
  isSeed?: boolean;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface AttendanceRecord {
  id: string;
  workerId: string;
  workerName: string | null;
  workDate: string;
  clockIn: string;
  clockOut: string | null;
  /** Clock in to clock out. */
  hoursPresent: number | null;
  /** Hours present minus the daily break. */
  hoursWorked: number | null;
  note: string | null;
  recordedByName: string | null;
  updatedByName: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export type AttendanceStatus =
  | 'PRESENT'
  | 'CLOCKED_IN'
  | 'INCOMPLETE'
  | 'ABSENT'
  | 'NOT_IN'
  | 'NOT_YET'
  | 'OFF';

export interface AttendanceRow {
  workerId: string;
  workerName: string;
  date: string;
  scheduledStart: string | null;
  scheduledEnd: string | null;
  isWorkingDay: boolean;
  status: AttendanceStatus;
  lateMinutes: number;
  /** LATE, OVERTIME, or OUTSIDE. Empty when the clock-in is on time. */
  punctuality?: 'LATE' | 'OVERTIME' | 'OUTSIDE' | null;
  record: AttendanceRecord | null;
}

export interface AttendanceDaySheet {
  date: string;
  rows: AttendanceRow[];
  counts: Partial<Record<AttendanceStatus, number>>;
  lateCount: number;
}

export interface AttendanceHistory {
  workerId: string;
  workerName: string;
  from: string;
  to: string;
  rows: AttendanceRow[];
  summary: {
    daysPresent: number;
    daysAbsent: number;
    daysLate: number;
    hoursPresent: number;
    hoursWorked: number;
  };
}

export interface MaterialPurchase {
  id: string;
  /** JOB_MATERIAL lines belong to a job; CONSUMABLE lines restock a consumable. */
  kind?: 'JOB_MATERIAL' | 'CONSUMABLE';
  jobOrderId: string | null;
  jobNumber?: string | null;
  jobTitle?: string | null;
  toolId?: string | null;
  toolCode?: string | null;
  plannedMaterialId?: string | null;
  materialName: string;
  materialCatalogId?: string | null;
  gradeOrSpec?: string | null;
  sizeSpec?: string | null;
  quantity: number;
  unit: string;
  unitCost: number;
  lineTotal?: number | null;
  supplierId: string;
  supplierName?: string | null;
  /** Null while the line sits on a draft supplier order. */
  dateOrdered: string | null;
  dateReceived?: string | null;
  consumedAt?: string | null;
  cancelledAt?: string | null;
  /** Parent job was cancelled; Office should cancel the PO or keep the material. */
  reviewAfterJobCancel?: boolean;
  /** Null = recorded without a PO (before supplier orders). */
  supplierOrderId?: string | null;
  poNumber?: string | null;
  orderStatus?: SupplierOrderStatus | null;
  expectedDeliveryDate?: string | null;
  /** The PO's expected date as edited, or date ordered + lead time without a PO. */
  currentExpectedDate?: string | null;
  /** Days past the current expected date while not received; 0 otherwise. */
  daysOverdue?: number;
  status?: MaterialPurchaseStatus | string;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface SupplierReliability {
  supplierId: string;
  supplierName: string;
  active: boolean;
  dueDeliveries: number;
  onTimeDeliveries: number;
  lateDeliveries: number;
  overdueDeliveries: number;
  /** Null when fewer than 3 deliveries were due. */
  reliabilityPct: number | null;
  avgDaysLate: number;
  enoughData: boolean;
  label: string;
  rank: number | null;
  /** Weighted 0-100 score; null when fewer than 3 deliveries were due. */
  score?: number | null;
  grade?: 'A' | 'B' | 'C' | 'D' | null;
  scoreParts?: { onTime: number | null; daysLate: number | null; leadTime: number | null };
  scoreWeights?: { onTime: number; daysLate: number; leadTime: number };
  statedLeadTimeDays?: number | null;
  averageActualLeadDays?: number | null;
  leadTimeSamples?: number;
}

export type MaterialPurchaseStatus = 'DRAFT' | 'ORDERED' | 'RECEIVED' | 'CONSUMED' | 'CANCELLED';

export type DeliveryMode = 'PICKUP' | 'DELIVERY';

export type SupplierOrderStatus =
  | 'DRAFT'
  | 'ISSUED'
  | 'PARTIALLY_RECEIVED'
  | 'RECEIVED'
  | 'CANCELLED';

export interface ExpectedDeliveryChange {
  from: string | null;
  to: string | null;
  note: string | null;
  changedByName: string | null;
  changedAt: string | null;
}

export interface MaterialRescheduleOutcome {
  jobId: string;
  jobNumber: string | null;
  outcome: 'MOVED' | 'NO_SLOT' | 'UNCHANGED';
  previousStart?: string;
  newStart?: string;
  materialDate?: string;
  message?: string | null;
}

export interface SupplierOrder {
  id: string;
  poNumber: string | null;
  supplierId: string;
  supplierName?: string | null;
  supplierLeadTimeDays?: number | null;
  supplierAddress?: string | null;
  supplierContactPerson?: string | null;
  supplierPhone?: string | null;
  status: SupplierOrderStatus;
  dateIssued?: string | null;
  expectedDeliveryDate?: string | null;
  originalExpectedDeliveryDate?: string | null;
  expectedDeliveryNote?: string | null;
  expectedDeliveryChanges?: ExpectedDeliveryChange[];
  movedJobs?: MaterialRescheduleOutcome[];
  notMovedJobs?: MaterialRescheduleOutcome[];
  receivedDate?: string | null;
  notes?: string | null;
  vatRate?: number | null;
  /** Printed on the PO; "PDC" unless changed. */
  termsOfPayment?: string;
  deliveryMode?: DeliveryMode;
  preparedById: string;
  preparedByName?: string | null;
  issuedById?: string | null;
  issuedByName?: string | null;
  /** Set on first Print PO; required before receive. */
  printedAt?: string | null;
  printedById?: string | null;
  printedByName?: string | null;
  printCount?: number;
  lineCount: number;
  jobCount: number;
  consumableLineCount?: number;
  subtotal: number;
  /** Days past the expected delivery date for lines not yet received. */
  daysOverdue?: number;
  createdAt?: string | null;
  lines?: MaterialPurchase[];
}

/** An open job whose materials are To order, offered in Order materials. */
export interface OrderingJob {
  id: string;
  jobNumber: string;
  title: string;
  clientName: string | null;
  dueDate: string | null;
  materialStatus: MaterialStatus;
  issuedLineCount: number;
  draftLineCount: number;
  /** No line on an issued supplier order yet. */
  notOrderedYet: boolean;
}

/** A consumable due to be bought again, offered when creating a supplier order. */
export interface DueConsumable {
  toolId: string;
  name: string;
  code: string;
  sizeSpec: string | null;
  shopTerm?: string | null;
  unit: string;
  status: 'OVERDUE' | 'DUE_SOON';
  nextPurchaseOn: string;
  usualQuantity: number | null;
  /** Already on a draft or open supplier order, not yet received. */
  onOrderQuantity: number;
  remainingSuggestedQuantity: number;
}

export interface OutstandingMaterials {
  jobs: OrderingJob[];
  /** Empty when the list is for one job. */
  dueConsumables: DueConsumable[];
}

/** A job material from the catalog (jobOrderId + materialCatalogId) or a consumable restock (toolId). */
export interface SupplierOrderLineInput {
  jobOrderId?: string;
  toolId?: string;
  materialCatalogId?: string;
  /** The job's planned material this line orders. */
  plannedMaterialId?: string | null;
  materialName?: string;
  gradeOrSpec?: string | null;
  sizeSpec?: string | null;
  quantity: number;
  unit?: string;
  unitCost: number;
}

export interface SupplierOrderPrint {
  order: SupplierOrder;
  supplier: {
    name: string;
    contactPerson?: string | null;
    phone?: string | null;
    email?: string | null;
    address?: string | null;
  } | null;
  rows: {
    materialName: string;
    gradeOrSpec?: string | null;
    sizeSpec?: string | null;
    unit: string;
    unitCost: number;
    quantity: number;
    isConsumable?: boolean;
    lineCount: number;
    amount: number;
  }[];
  subtotal: number;
  vatRate: number | null;
  vatAmount: number;
  total: number;
}

export interface MaterialStockBucket {
  count: number;
  value: number;
  quantityByUnit: { unit: string; quantity: number }[];
}

export interface MaterialPurchaseList {
  items: MaterialPurchase[];
  summary: {
    purchaseCount: number;
    totalSpend: number;
    awaitingDeliveryCount: number;
    /** Placed lines past their current expected date and not received. */
    overdueCount?: number;
    receivedThisMonthCount?: number;
    jobsWaitingOnMaterialsCount?: number;
    reviewAfterJobCancelCount?: number;
    onOrder: MaterialStockBucket;
    onHand: MaterialStockBucket;
    consumed: MaterialStockBucket;
  };
}

/** Reference to the shop's BIR-registered sales invoice; the system does not issue invoices. */
export interface SalesInvoice {
  id: string;
  invoiceNumber: string;
  invoiceDate: string;
  jobOrderId: string;
  clientId: string;
  clientName?: string | null;
  amount: number;
  preparedById: string;
  preparedByName?: string | null;
  createdAt?: string | null;
}

export interface ClientDetail extends Client {
  jobs: {
    id: string;
    jobNumber?: string | null;
    title: string;
    createdAt?: string | null;
    /** Client's PO date (date only). */
    poDate?: string | null;
    dueDate?: string | null;
    amount?: number | null;
    status: JobOrderStatus;
    deliveredAt?: string | null;
    deliveredOnTime?: boolean | null;
  }[];
  totals: { jobCount: number; totalValue: number };
}

export type JobOrderStatus =
  | 'DRAFT'
  | 'SCHEDULED'
  | 'IN_PROGRESS'
  | 'COMPLETED'
  | 'FOR_DELIVERY'
  | 'DELIVERED'
  | 'CANCELLED';
export type JobPriority = 'HIGH' | 'MODERATE' | 'LOW';
export type JobType = 'FABRICATION' | 'MACHINING' | 'REPAIR';

export type MaterialStatus = 'NOT_REQUIRED' | 'TO_ORDER' | 'ORDERED' | 'RECEIVED';
export type PartCondition =
  | 'RAW_MATERIAL'
  | 'CLIENT_SUPPLIED_ITEM'
  | 'WORK_IN_PROCESS'
  | 'CUT'
  | 'BLANK'
  | 'FORMED'
  | 'MACHINED'
  | 'ASSEMBLED'
  | 'HEAT_TREATED'
  | 'FINISHED';
export type OperationStatus =
  | 'PENDING'
  | 'SCHEDULED'
  | 'IN_PROGRESS'
  | 'COMPLETED'
  | 'REWORK';

export type ReworkReasonCategory =
  | 'DIMENSION_OUT_OF_TOLERANCE'
  | 'SURFACE_FINISH'
  | 'WRONG_MATERIAL'
  | 'MACHINE_FAULT'
  | 'OPERATOR_ERROR'
  | 'OTHER';
export type MachineCode = 'LATHE' | 'MILLING' | 'SHAPER' | 'GRINDING' | 'DRILLING';

export interface MachineInfo {
  id?: string | null;
  code: MachineCode | string;
  name: string;
  units: number;
  inUse?: number;
  available?: number;
}

export interface RawMaterial {
  id?: string;
  /** Catalog material it was picked from (older records may lack it). */
  materialCatalogId?: string | null;
  name: string;
  gradeOrSpec?: string | null;
  sizeSpec?: string | null;
  quantity?: number;
  unit?: string;
  /** Earlier records only: the shop already had it. */
  fromStock?: boolean;
}

export type OperationTimeEvent = 'START' | 'PAUSE' | 'RESUME' | 'COMPLETE';

export type OperationPauseReason =
  | 'END_OF_SHIFT'
  | 'BREAK'
  | 'MACHINE_DOWN'
  | 'WAITING_MATERIAL'
  | 'WAITING_PRIOR_OPERATION'
  | 'OTHER';

export interface OperationTimeLog {
  id: string;
  operationId: string;
  workerId: string;
  workerName?: string | null;
  event: OperationTimeEvent;
  eventAt: string;
  /** When the server received it; eventAt is the phone's time. Null for older or office-made records. */
  receivedAt?: string | null;
  reason?: OperationPauseReason | null;
  note?: string | null;
}

export type ScheduleFlag = 'GREEN' | 'AMBER' | 'RED';

export interface ScheduleSegment {
  start: string;
  end: string;
}

/** A released, unstarted job whose first operation the start gate would refuse. */
export interface MaterialWait {
  waitingForMaterials?: boolean;
  materialWaitCode?: 'MATERIALS_NOT_ORDERED' | 'MATERIALS_NOT_RECEIVED' | null;
  materialWaitReason?: string | null;
}

export type StaffAlertKind =
  | 'MATERIAL_DELAY'
  | 'SINGLE_UNIT_DOWN'
  | 'NEEDS_REPLAN'
  | 'WORK_MOVED'
  | 'OPERATION_STALLED'
  | 'WORK_ON_DOWN_UNIT'
  | 'REPAIR_DATE_PASSED'
  | 'LOW_STOCK'
  | (string & {});

/** One entry in the header bell. */
export interface StaffAlert {
  id: string;
  kind: StaffAlertKind;
  title: string;
  message: string | null;
  jobOrderId: string | null;
  jobNumber: string | null;
  supplierOrderId: string | null;
  poNumber: string | null;
  read: boolean;
  readAt: string | null;
  createdAt: string | null;
}

/** MATERIAL: late or unordered materials set the new start. RESCHEDULED: only a passed start date.
 * OVERRUN: earlier work on the same worker or unit ran long. BREAKDOWN: its unit broke down. */
export type DelayKind = 'MATERIAL' | 'RESCHEDULED' | 'OVERRUN' | 'BREAKDOWN';

/** Set when the schedule was moved later automatically. */
export interface MaterialDelay {
  originalStart: string | null;
  currentStart: string | null;
  kind: DelayKind;
  reason: string | null;
  supplierOrderId: string | null;
  poNumber: string | null;
  delayedAt: string;
}

export interface Operation extends MaterialWait {
  id: string;
  jobOrderId: string;
  jobTitle?: string;
  jobNumber?: string;
  clientName?: string;
  dueDate?: string;
  jobPriority?: JobPriority;
  sequenceNo: number;
  operationName: string;
  operationTypeId?: string | null;
  operationTypeCode?: string | null;
  isOutsourced?: boolean;
  turnaroundDays?: number | null;
  sentOutDate?: string | null;
  sentTo?: string | null;
  returnedDate?: string | null;
  expectedReturnDate?: string | null;
  machineTypeId?: string | null;
  machineTypeCode?: string | null;
  machineTypeName?: string | null;
  machineUnitId?: string | null;
  machineUnitLabel?: string | null;
  assignedWorkerId?: string | null;
  assignedWorkerName?: string | null;
  assignedWorkerNickname?: string | null;
  assignedWorkerPhotoVersion?: number | null;
  /** Up to two helpers; the lead is assignedWorkerId. */
  helperIds?: string[];
  /** Lead first, then helpers. */
  crew?: CrewMember[];
  crewSize?: number;
  /** Hours worked times crew size, for information. */
  laborHours?: number | null;
  estimatedHours?: number | null;
  scheduledStart?: string | null;
  scheduledEnd?: string | null;
  segments?: ScheduleSegment[];
  actualStart?: string | null;
  actualEnd?: string | null;
  actualWorkedHours?: number | null;
  varianceHours?: number | null;
  variancePct?: number | null;
  startedAt?: string;
  completedAt?: string;
  status: OperationStatus;
  reworkOfOperationId?: string | null;
  reworkReason?: string | null;
  reworkReasonCategory?: ReworkReasonCategory | null;
  notes?: string | null;
  timeLogs?: OperationTimeLog[];
  isPaused?: boolean;
  machineDown?: boolean;
  /** Legacy aliases */
  seq?: number;
  name?: string;
  machinesNeeded?: string[];
  machineNames?: string[];
}

export interface CrewMember {
  id: string;
  fullName: string;
  nickname?: string | null;
  photoVersion?: number | null;
  isLead: boolean;
}

export interface MachineUnitInfo {
  id: string;
  machineTypeId: string;
  machineTypeCode?: string | null;
  machineTypeName?: string | null;
  label: string;
  active?: boolean;
  defaultOperatorId?: string | null;
  defaultOperatorName?: string | null;
  defaultOperatorNickname?: string | null;
  defaultOperatorPhotoVersion?: number | null;
}

export interface MachineDowntimeRecord {
  id: string;
  machineUnitId: string;
  machineUnitLabel?: string | null;
  startedAt: string;
  endedAt?: string | null;
  category?: string | null;
  reason: string;
  jobOrderId?: string | null;
  operationId?: string | null;
  operationName?: string | null;
  reportedById: string;
  reportedByName?: string | null;
  note?: string | null;
  /** Shop-local date (YYYY-MM-DD); the scheduler keeps the unit unavailable through it. */
  expectedRepairDate?: string | null;
  open: boolean;
  createdAt?: string | null;
  affectedCount?: number;
  affectedOperations?: AffectedScheduledOperation[];
  /** Admin/Office: unstarted work booked on the unit and what can be done with it. */
  work?: BreakdownWork;
}

export type BreakdownAction = 'MOVE_UNIT' | 'AFTER_REPAIR' | 'LEAVE';

export interface BreakdownWork {
  downtimeId: string;
  machineUnitId: string;
  machineUnitLabel: string;
  expectedRepairDate: string | null;
  operations: AffectedScheduledOperation[];
  otherUnits: { id: string; label: string; operatorName: string | null }[];
  canMoveToUnit: boolean;
  canMoveAfterRepair: boolean;
  /** No other usable unit and no repair date: the jobs need a person to re-plan them. */
  needsReplan: boolean;
  defaultAction: BreakdownAction | null;
}

export interface BreakdownResolution {
  action: BreakdownAction;
  moved: { jobId: string; jobNumber: string }[];
  notPlaced: { jobId: string; jobNumber: string }[];
  needsReplan: string[];
  left: unknown[];
}

export interface AffectedScheduledOperation {
  id: string;
  jobOrderId: string;
  jobNumber?: string | null;
  jobTitle?: string | null;
  operationName: string;
  status?: string | null;
  scheduledStart?: string | null;
  scheduledEnd?: string | null;
  assignedWorkerName?: string | null;
  assignedWorkerNickname?: string | null;
  assignedWorkerPhotoVersion?: number | null;
  crew?: CrewMember[];
  /** True when the operation was in progress and paused for this breakdown. */
  paused?: boolean;
  pauseReason?: string | null;
}

export interface MachineUnitStatus extends MachineUnitInfo {
  down: boolean;
  openDowntime?: MachineDowntimeRecord | null;
  affectedCount: number;
  currentOperation?: AffectedScheduledOperation | null;
  /** First scheduled operation on the unit, also while another one runs. */
  nextOperation?: AffectedScheduledOperation | null;
  /** Released jobs with work on this unit today (shop time). */
  jobsToday?: number;
}

export interface ProposedOperation {
  id?: string | null;
  sequenceNo: number;
  operationName?: string;
  isOutsourced?: boolean;
  turnaroundDays?: number | null;
  assignedWorkerId?: string | null;
  helperIds?: string[];
  machineTypeId?: string | null;
  machineUnitId?: string | null;
  machineUnitLabel?: string | null;
  estimatedHours?: number;
  estimatedHoursDefaulted?: boolean;
  scheduledStart?: string | null;
  scheduledEnd?: string | null;
  segments?: ScheduleSegment[];
  scheduled: boolean;
  message?: string | null;
  placeableHours?: number | null;
  requiredHours?: number | null;
  /** Another unit of the same type would finish the job meaningfully earlier; never applied by itself. */
  fasterUnit?: FasterUnitSuggestion | null;
  /** Another worker + unit would finish the late job meaningfully earlier; never applied by itself. */
  fasterPair?: FasterPairSuggestion | null;
}

export interface FasterPairSuggestion {
  assignedWorkerId: string;
  workerName?: string | null;
  workerNickname?: string | null;
  machineUnitId?: string | null;
  machineUnitLabel?: string | null;
  scheduledStart: string;
  scheduledEnd: string;
  projectedCompletion: string;
  currentProjectedCompletion?: string | null;
  onTime?: boolean;
}

export interface FasterUnitSuggestion {
  machineUnitId: string;
  machineUnitLabel?: string | null;
  ownership: 'OWN' | 'OPEN' | 'OTHER';
  operatorName?: string | null;
  scheduledStart: string;
  scheduledEnd: string;
  /** Working hours earlier than the saved unit allows; null when the saved unit has no time at all. */
  hoursEarlier?: number | null;
  projectedCompletion: string;
  /** Null when the saved unit leaves part of the job unplaced. */
  currentProjectedCompletion?: string | null;
}

export interface ScheduleProposeResult {
  proposed: boolean;
  anchor?: string;
  horizonDays?: number;
  projectedCompletion?: string | null;
  scheduleFlag?: ScheduleFlag | null;
  materialNotBefore?: string | null;
  materialConstraintReason?: string | null;
  operations: ProposedOperation[];
  /** Everything that blocks confirming this proposal. */
  problems?: ScheduleProblem[];
  /** Worth a look but never blocking, e.g. a lead on another operator's unit. */
  warnings?: ScheduleProblem[];
  /** The saved schedule was shown as it was. */
  restored?: boolean;
  /** The saved schedule started at this time, already past, so a fresh one was proposed. */
  replacedPastStart?: string | null;
}

export interface ScheduleProblem {
  sequenceNo: number;
  operationId?: string | null;
  code: string;
  message: string;
  severity?: 'error' | 'warning';
  /** WORKER_CONFLICT: the worker who is double-booked. */
  workerId?: string | null;
}

export interface ScheduleWarning {
  sequenceNo: number;
  code: string;
  message: string;
}

export interface ScheduleValidateResult {
  warnings: ScheduleWarning[];
  projectedCompletion?: string | null;
  scheduleFlag?: ScheduleFlag | null;
}

export interface CompletionEstimateOperation {
  operationId: string;
  sequenceNo: number;
  operationName: string;
  operationTypeName: string | null;
  ratio: number;
  samples: number;
  enoughHistory: boolean;
  targetHours: number;
  hoursWorked: number;
  predictedHoursLeft: number;
  predictedStart: string;
  predictedEnd: string;
}

/** Finish estimate from past hours-worked-to-target ratios per operation type. */
export interface CompletionEstimate {
  label: string;
  predictedFinish: string;
  minSamples: number;
  operations: CompletionEstimateOperation[];
  notEnoughHistoryNote: string | null;
}

/** One row from schedule_moves (automatic delay or Admin Reschedule). */
export interface ScheduleMove {
  id: string;
  jobOrderId: string;
  kind: string;
  previousStart: string | null;
  newStart: string | null;
  reason: string | null;
  note: string | null;
  changes: {
    operations?: Array<{
      operationId: string;
      sequenceNo?: number;
      scheduledStart?: { from?: string | null; to?: string | null };
      scheduledEnd?: { from?: string | null; to?: string | null };
      assignedWorkerId?: { from?: string | null; to?: string | null };
      machineUnitId?: { from?: string | null; to?: string | null };
      helperIds?: { from?: string[]; to?: string[] };
    }>;
  } | null;
  materialCause?: string | null;
  supplierOrderId?: string | null;
  supplierId?: string | null;
  movedAt: string | null;
  movedById: string | null;
  movedByName: string | null;
}

export interface JobOrder extends MaterialWait {
  id: string;
  jobNumber?: string;
  clientId: string;
  clientName?: string;
  title: string;
  description?: string;
  dueDate: string;
  clientPoNumber?: string | null;
  poDate?: string | null;
  status: JobOrderStatus;
  priority?: JobPriority;
  jobType?: JobType;
  partCondition?: PartCondition;
  quantity?: number | null;
  unitOfMeasure?: string | null;
  amount?: number | null;
  /** Materials needed, listed by Office Staff on the job order form. */
  rawMaterials?: RawMaterial[];
  /** Detail view: the job's ordered material lines, without costs. */
  materialLines?: JobMaterialLine[];
  materialStatus?: MaterialStatus;
  materialExpectedDate?: string | null;
  materialReceivedDate?: string | null;
  supplierId?: string | null;
  supplierName?: string | null;
  supplierReference?: string | null;
  materialReadiness?: MaterialReadiness;
  materialDelay?: MaterialDelay | null;
  /** A change (such as a redo) could not be scheduled automatically. */
  needsReplan?: boolean;
  needsReplanReason?: string | null;
  plannedMaterials?: PlannedMaterialSummary[];
  createdById?: string;
  createdByName?: string | null;
  createdByRole?: UserRole | null;
  draftStage?: string | null;
  deliveredAt?: string | null;
  /** When the Admin set it For Delivery. */
  forDeliveryAt?: string | null;
  cancelledAt?: string | null;
  cancelledById?: string | null;
  cancelledByName?: string | null;
  cancelReason?: string | null;
  cancelNote?: string | null;
  archivedAt?: string | null;
  archivedById?: string | null;
  isArchived?: boolean;
  createdAt?: string;
  updatedAt?: string | null;
  opsCompleted?: number;
  opsTotal?: number;
  nextOperation?: string | null;
  nextOperationWorkerId?: string | null;
  nextOperationWorkerName?: string | null;
  nextOperationCrew?: CrewMember[];
  operations?: Operation[];
  salesInvoice?: SalesInvoice | null;
  projectedCompletion?: string | null;
  /** Estimated finish from past performance (released jobs only). */
  predictedCompletion?: string | null;
  /** Uses the later of the scheduled and estimated finish. */
  scheduleFlag?: ScheduleFlag | null;
  scheduleFlagBasis?: 'SCHEDULE' | 'ESTIMATE' | null;
  /** Detail view only. */
  completionEstimate?: CompletionEstimate | null;
  scheduleColor?: string | null;
}

export interface JobMaterialLine {
  id: string;
  materialName: string;
  gradeOrSpec: string | null;
  sizeSpec?: string | null;
  quantity: number | null;
  unit: string | null;
  status: MaterialPurchaseStatus;
  poNumber: string | null;
  supplierName?: string | null;
  expectedDate: string | null;
  dateReceived: string | null;
}

export interface PlannedMaterialSummary {
  id: string;
  name: string;
  unit?: string | null;
  plannedQuantity: number | null;
  /** On issued supplier orders or recorded without a PO. */
  purchasedQuantity: number;
  /** On a draft supplier order, not yet sent. */
  draftQuantity: number;
  remainingQuantity: number | null;
  fromStock: boolean;
  materialCatalogId?: string | null;
  gradeOrSpec?: string | null;
  sizeSpec?: string | null;
  status: 'TO_ORDER' | 'PARTLY_ORDERED' | 'ON_DRAFT_ORDER' | 'PURCHASED' | 'FROM_STOCK';
}

export interface MaterialLineArrival {
  purchaseId: string;
  materialName: string;
  gradeOrSpec?: string | null;
  supplierName?: string | null;
  dateOrdered: string | null;
  leadTimeDays: number | null;
  dateReceived: string | null;
  poNumber?: string | null;
  expectedArrival: string | null;
  basis: 'RECEIVED' | 'LEAD_TIME' | 'UNKNOWN';
  /** Arrival taken from the supplier order's expected delivery date. */
  fromOrderDeliveryDate?: boolean;
  /** Days past the expected date; arrival is then tomorrow at the earliest. */
  daysOverdue?: number;
}

/** A supplier order holding this job's lines, or lines recorded without a PO. */
export interface JobSupplierOrderSummary {
  supplierOrderId: string | null;
  poNumber: string | null;
  supplierName: string | null;
  /** Null for lines recorded without a PO. */
  status: SupplierOrderStatus | null;
  expectedDeliveryDate: string | null;
  lineCount: number;
  daysOverdue?: number;
}

export interface MaterialReadiness {
  expectedDate: string | null;
  reason: string | null;
  source: 'PURCHASE_LINES' | 'JOB' | null;
  limitingLine: MaterialLineArrival | null;
  missingLeadTimeSuppliers: string[];
  lines: MaterialLineArrival[];
  supplierOrders: JobSupplierOrderSummary[];
  /** To order, with no line on an issued supplier order yet. */
  notOrderedYet: boolean;
}

export type NotificationMilestone =
  | 'JOB_RECEIVED'
  | 'JOB_STARTED'
  | 'JOB_COMPLETED'
  | 'JOB_DELIVERED'
  | 'JOB_HALFWAY'
  | 'JOB_FINAL_STAGE'
  | 'JOB_DELAYED';

/** Historical rows may still be SMS; new sends are email only. */
export type NotificationChannel = 'EMAIL' | 'SMS' | string;

export type NotificationStatus = 'PENDING' | 'SENT' | 'NOT_SENT' | 'FAILED' | 'SKIPPED';

export interface NotificationLog {
  id: string;
  jobOrderId: string;
  jobNumber?: string | null;
  jobTitle?: string | null;
  clientId: string;
  clientName?: string | null;
  milestone: NotificationMilestone;
  /** A milestone name for job updates, or INVITATION / PASSWORD_RESET / TEST (no job or client). */
  event?: string;
  channel: NotificationChannel;
  recipient: string;
  messageBody: string;
  status: NotificationStatus;
  providerReference?: string | null;
  errorMessage?: string | null;
  sentAt?: string | null;
  createdAt?: string | null;
}

/** Machine lead: operator, skill, efficiency, workload. Others: efficiency, workload. */
export interface ScoringComponents {
  operator?: number;
  skill?: number;
  workload: number;
  efficiency: number;
}

export type ScoringWeights = ScoringComponents;

export type SuggestionMode = 'MACHINE_LEAD' | 'NO_MACHINE' | 'HELPER';

export interface WorkerSuggestion {
  workerId: string;
  fullName: string;
  nickname?: string | null;
  photoVersion?: number | null;
  role?: UserRole;
  email: string | null;
  skills: string[];
  score: number;
  matchedSkills: string[];
  available?: boolean;
  proficiency?: number | null;
  qualified?: boolean;
  components?: ScoringComponents;
  reason?: string;
  /** Machine leads are suggested with a unit; choosing the suggestion selects it. */
  machineUnitId?: string | null;
  machineUnitLabel?: string | null;
  isUnitOperator?: boolean;
  /** Set when the operation starts today and the worker is past their start time without a clock-in. */
  attendanceWarning?: string | null;
  /** Earliest moment this worker (and unit, when set) can take the target hours. */
  nextFreeStart?: string | null;
  nextFreeEnd?: string | null;
  /** While planning: can this worker (and unit) do the step in time for the date required. */
  meetsDate?: boolean;
  /** Latest finish for this step that still leaves the later steps time before the date required. */
  latestFinish?: string | null;
}

/** Date-required gate on suggestions while planning a job. */
export interface SuggestionDateGate {
  applied: boolean;
  anyMeetsDate: boolean | null;
  latestFinish: string | null;
}

export type ToolCategory = 'RETURNABLE_TOOL' | 'CONSUMABLE';

export type ToolUnitStatus = 'AVAILABLE' | 'OUT' | 'UNDER_REPAIR' | 'RETIRED';

export interface ToolHolder {
  holderId: string;
  holderName: string | null;
  quantity: number;
  since: string | null;
}

export interface Tool {
  id: string;
  name: string;
  code: string;
  category: ToolCategory;
  unit: string;
  /** Previous unit label when the item was remapped; shown until reviewed. */
  legacyUnit?: string | null;
  unitNeedsReview?: boolean;
  unitCost?: number | null;
  quantityOnHand: number;
  minimumStock: number | null;
  sizeSpec: string | null;
  /** The shop's own word for it, e.g. "Liha" for sandpaper. */
  shopTerm?: string | null;
  lowStock: boolean;
  /** Same as consumable forecast: OK | REORDER_SOON | REORDER_NOW | NO_DATA. */
  status?: ConsumableStockStatus | string | null;
  /** Computed / Estimated / No data yet — same as forecast. */
  label?: ConsumableForecastLabel | string | null;
  reorderPoint?: number | null;
  reorderLevel?: number | null;
  createdAt?: string;
  myOutstanding?: number | null;
  holders?: ToolHolder[];
  custody?: {
    holderId: string;
    holderName: string | null;
    since: string | null;
    quantity?: number;
  } | null;
}

export interface ToolUnit {
  id: string;
  toolTypeId: string;
  toolTypeName: string | null;
  toolTypeCode: string | null;
  assetCode: string;
  status: ToolUnitStatus;
  notes: string | null;
  currentHolderId: string | null;
  currentHolderName: string | null;
  heldSince: string | null;
  createdAt?: string;
  isSeed?: boolean;
}

export interface ToolType {
  id: string;
  name: string;
  code: string;
  description: string | null;
  isSeed: boolean;
  createdAt?: string;
  totalUnits: number;
  availableCount: number;
  outCount: number;
  repairCount: number;
  retiredCount: number;
  units?: ToolUnit[];
}

export type ToolEventType =
  | 'BORROW'
  | 'RETURN'
  | 'ISSUE'
  | 'ADJUST'
  | 'RECEIVE'
  | 'SCAN'
  | 'UNDO'
  | 'CORRECTION';

export type ConsumableStockStatus = 'OK' | 'REORDER_SOON' | 'REORDER_NOW' | 'NO_DATA' | 'LIKELY_OUT';
export type ConsumableForecastLabel = 'Computed' | 'Estimated' | 'No data yet';
export type ConsumableAbcClass = 'A' | 'B' | 'C';

export interface ToolEvent {
  id: string;
  toolId?: string | null;
  toolUnitId?: string | null;
  toolName?: string;
  toolCode?: string;
  toolUnit?: string | null;
  assetCode?: string | null;
  toolCategory?: ToolCategory | 'TOOL_UNIT' | null;
  toolSizeSpec?: string | null;
  quantityOnHandAfter?: number | null;
  unitStatus?: ToolUnitStatus | null;
  currentHolderName?: string | null;
  workerId: string;
  workerName?: string;
  type: ToolEventType;
  quantity: number;
  stockDelta?: number | null;
  reason?: string | null;
  supplier?: string | null;
  receivedOn?: string | null;
  jobOrderId?: string;
  operationId?: string | null;
  undoesEventId?: string | null;
  undoneAt?: string | null;
  clientActionId?: string | null;
  createdAt: string;
  /** Unified scan extras (POST /tools/scan). */
  kind?: 'CONSUMABLE_SCAN' | 'CONSUMABLE_UNDO' | 'TOOL_UNIT' | string;
  needsConfirm?: boolean;
  confirmMessage?: string | null;
  displayLine?: string | null;
  canUndoUntil?: string | null;
}

export interface InventoryPurchaseSuggestion {
  toolId: string;
  name: string;
  code: string;
  category: ToolCategory;
  sizeSpec: string | null;
  unit: string;
  quantityOnHand: number | null;
  minimumStock: number | null;
  reorderLevel?: number | null;
  suggestedOrderQuantity: number | null;
  recentConsumptionQuantity: number | null;
  consumptionPerWorkingDay: number | null;
  lookbackWorkingDays: number;
  consumptionSource?: 'STOCKTAKE' | 'BORROW' | 'SCAN' | string;
  status?: ConsumableStockStatus;
  label?: ConsumableForecastLabel | string | null;
  abcClass?: 'A' | 'B' | 'C' | null;
}

export interface InventoryPurchaseSuggestions {
  label: string;
  description: string;
  period: { from: string; to: string };
  workingDaysInSample: number;
  itemCount: number;
  items: InventoryPurchaseSuggestion[];
  howComputed?: string;
  formulas?: Record<string, string>;
}

export interface InventoryUsageByWorker {
  period: { from: string; to: string };
  workingDaysInPeriod: number;
  byWorkerItem: {
    workerId: string;
    workerName: string | null;
    toolId: string;
    toolName: string | null;
    toolCode: string | null;
    assetCode?: string | null;
    category: ToolCategory | 'TOOL_UNIT' | null;
    sizeSpec: string | null;
    unit: string | null;
    eventCount: number;
    borrowQuantity: number | null;
    returnQuantity: number | null;
    netBorrowQuantity: number | null;
    /** @deprecated legacy field */
    issueQuantity?: number | null;
    netConsumptionQuantity?: number | null;
  }[];
  outstandingUnreturned: {
    workerId: string;
    workerName: string | null;
    totalOutstandingQuantity: number | null;
    items: {
      toolId: string;
      toolName: string;
      toolCode: string;
      quantity: number | null;
    }[];
  }[];
}

export interface InventoryUsageByItem {
  period: { from: string; to: string };
  workingDaysInPeriod: number;
  items: {
    toolId: string;
    name: string;
    code: string;
    category: ToolCategory;
    sizeSpec: string | null;
    unit: string;
    quantityOnHand: number | null;
    minimumStock: number | null;
    lowStock: boolean;
    borrowQuantity: number | null;
    consumptionQuantity: number | null;
    consumptionPerWorkingDay: number | null;
    issueQuantity?: number | null;
  }[];
}

export interface InventoryUsageConsumables {
  period: { from: string; to: string };
  workingDaysInPeriod: number;
  note: string;
  items: {
    toolId: string;
    name: string;
    code: string;
    category: ToolCategory;
    sizeSpec: string | null;
    unit: string;
    quantityOnHand: number | null;
    minimumStock: number | null;
    lowStock: boolean;
    consumptionQuantity: number | null;
    consumptionPerWorkingDay: number | null;
    measuredWorkingDays?: number | null;
  }[];
}

/** @deprecated Stocktake HTTP API removed; types kept for prototype/legacy compile only. */
export interface StocktakeDetail {
  id: string;
  countedOn: string;
  countedById: string;
  countedByName: string | null;
  notes: string | null;
  createdAt: string;
  lineCount: number;
  lines: {
    id: string;
    stocktakeId: string;
    toolId: string;
    toolName: string | null;
    toolCode: string | null;
    unit: string | null;
    sizeSpec: string | null;
    previousQuantity: number | null;
    countedQuantity: number | null;
    delta: number | null;
  }[];
}

export interface LoginResponse {
  accessToken: string;
  refreshToken: string;
  user: User;
  device?: { known: boolean; hasPin: boolean };
}

export interface ApiError {
  error: {
    code: string;
    message: string;
  };
}

export const MACHINE_OPTIONS: MachineInfo[] = [
  { code: 'LATHE', name: 'Lathe', units: 7 },
  { code: 'MILLING', name: 'Milling', units: 8 },
  { code: 'SHAPER', name: 'Shaper', units: 1 },
  { code: 'GRINDING', name: 'Grinding', units: 2 },
  { code: 'DRILLING', name: 'Drilling', units: 1 },
];

/** Analytics API (Admin / Office) */
export interface AnalyticsPeriodMeta {
  period: { from: string; to: string };
  excludedOperationCount: number;
}

export interface AnalyticsOverview extends AnalyticsPeriodMeta {
  jobs: {
    completed: number;
    onTime: number;
    late: number;
    awaitingDelivery: number;
    averageDaysLate: number | null;
    maxDaysLate: number | null;
  };
  efficiency: {
    averageVariancePct: number | null;
    completedOperationsWithVariance: number;
  };
  rework: {
    count: number;
    workedHours: number | null;
    shareOfTotalWorkedHoursPct: number | null;
    finishedOperationCount: number;
    finishedRedoOperationCount: number;
    redoRatePct: number | null;
  };
  downtime: { openCount: number };
  totals: {
    originalWorkedHours: number | null;
    reworkWorkedHours: number | null;
    totalWorkedHours: number | null;
  };
}

export interface AnalyticsJobOrders extends AnalyticsPeriodMeta {
  received: {
    count: number;
    amount: number | null;
    byJobType: { jobType: string; count: number; amount: number | null }[];
  };
  finished: AnalyticsOverview['jobs'];
  delivered: { count: number; onTime: number; late: number; amount: number | null };
  openNow: {
    byStatus: Record<'DRAFT' | 'SCHEDULED' | 'IN_PROGRESS' | 'COMPLETED', number>;
    pastDateRequired: number;
  };
}

export interface MyWorkFigures {
  from: string;
  to: string;
  finishedOperations: number;
  redoOperations: number;
  hoursWorked: number | null;
  targetHours: number | null;
  laborEfficiencyPct: number | null;
  /** Hours the worker's operations were running (weeks only), minus the daily break. */
  loggedHours?: number | null;
}

export interface MyWorkSummary {
  thisWeek: MyWorkFigures;
  /** The same days of last week, Monday up to today's weekday. */
  lastWeek?: MyWorkFigures;
  thisMonth: MyWorkFigures;
}

export interface AnalyticsWorkerRow {
  workerId: string;
  workerName: string;
  operationCount: number;
  totalEstimatedHours: number | null;
  totalActualWorkedHours: number | null;
  averageVariancePct: number | null;
  laborEfficiencyPct: number | null;
  onEstimateRatePct: number | null;
  reworkWorkedHours: number | null;
}

export interface AnalyticsByWorker extends AnalyticsPeriodMeta {
  minimumOperationCount: number;
  workers: AnalyticsWorkerRow[];
  recommendationWeights?: RecommendationWeightSets;
}

/** The three fixed weight sets used to rank worker suggestions. */
export interface RecommendationWeightSets {
  machineLead: Record<string, number>;
  noMachine: Record<string, number>;
  helper: Record<string, number>;
  minimumSamples: number;
}

export interface AnalyticsOperationTypeRow {
  operationTypeId: string;
  operationTypeCode: string;
  operationTypeName: string;
  operationCount: number;
  totalEstimatedHours: number | null;
  totalActualWorkedHours: number | null;
  averageVariancePct: number | null;
  laborEfficiencyPct: number | null;
  onEstimateRatePct: number | null;
  reworkWorkedHours: number | null;
}

export interface AnalyticsByOperationType extends AnalyticsPeriodMeta {
  minimumOperationCount: number;
  operationTypes: AnalyticsOperationTypeRow[];
}

export interface AnalyticsMachineUnitRow {
  machineUnitId: string;
  machineUnitLabel: string;
  machineTypeId: string;
  machineTypeCode: string | null;
  operationCount: number;
  totalEstimatedHours: number | null;
  totalActualWorkedHours: number | null;
  averageVariancePct: number | null;
  laborEfficiencyPct: number | null;
  onEstimateRatePct: number | null;
  belowMinimumSample: boolean;
  reworkWorkedHours: number | null;
  busySegmentHours: number | null;
  availableHours: number | null;
  utilizationPct: number | null;
}

export interface AnalyticsMachineTypeRow {
  machineTypeId: string;
  machineTypeCode: string;
  machineTypeName: string;
  activeUnitCount: number;
  operationCount: number;
  totalEstimatedHours: number | null;
  totalActualWorkedHours: number | null;
  averageVariancePct: number | null;
  laborEfficiencyPct: number | null;
  onEstimateRatePct: number | null;
  belowMinimumSample: boolean;
  reworkWorkedHours: number | null;
  busySegmentHours: number | null;
  availableHours: number | null;
  utilizationPct: number | null;
}

export interface AnalyticsByMachine extends AnalyticsPeriodMeta {
  minimumOperationCount: number;
  availableHoursPerUnit: number | null;
  machineTypes: AnalyticsMachineTypeRow[];
  machineUnits: AnalyticsMachineUnitRow[];
}

export interface AnalyticsTrendWeek {
  weekStart: string;
  operationCount: number;
  averageVariancePct: number | null;
  jobsFinished: number;
}

export interface AnalyticsTrend extends AnalyticsPeriodMeta {
  weeks: AnalyticsTrendWeek[];
}

export interface AnalyticsPauseReasonRow {
  reason: string;
  occurrenceCount: number;
  totalPausedHours: number | null;
}

/** Where a delay cause's hours came from: a machine, supplier, outside shop or operation. */
export interface AnalyticsDelayCauseDetail {
  label: string;
  hours: number | null;
  count: number;
  jobs: string[];
  moreJobs: number;
  note: string | null;
}

export interface AnalyticsDelayCauseRow {
  cause: string;
  causeType: 'PAUSE' | 'DOWNTIME' | 'REWORK' | string;
  label: string;
  hours: number | null;
  occurrenceCount: number;
  shareOfTotalPct: number | null;
  cumulativePct: number | null;
  details?: AnalyticsDelayCauseDetail[];
  moreDetails?: number;
  detailBy?: 'machine' | 'supplier' | 'job' | 'outside shop' | 'operation' | null;
}

export interface AnalyticsDowntimeRow {
  machineUnitId: string;
  machineUnitLabel: string | null;
  machineTypeCode: string | null;
  occurrenceCount: number;
  totalDowntimeHours: number | null;
  openCount: number;
}

export interface AnalyticsMaterialDelayRow {
  jobOrderId: string;
  jobNumber: string;
  cause: 'SUPPLIER_LATE' | 'NOT_ORDERED';
  causeLabel: string;
  originalStart: string;
  firstStart: string;
  started: boolean;
  /** First start, or now while the job has not started. */
  countedUntil: string;
  hours: number | null;
  moveCount: number;
  suppliers: { supplierId: string | null; supplierName: string; poNumber: string | null }[];
  supplierNames: string | null;
  reason: string | null;
}

export interface AnalyticsLateJobCause {
  cause: string;
  label: string;
  hours: number;
  detail: string | null;
}

export interface AnalyticsLateJobRow {
  jobOrderId: string;
  jobNumber: string;
  clientName: string | null;
  dueDate: string;
  deliveredDate: string;
  daysLate: number;
  causes: AnalyticsLateJobCause[];
}

export interface AnalyticsReworkReasonRow {
  reason: string;
  label: string;
  count: number;
  hours: number | null;
}

export interface AnalyticsDelays extends AnalyticsPeriodMeta {
  pauseReasons: AnalyticsPauseReasonRow[];
  machineDowntime: AnalyticsDowntimeRow[];
  causes: AnalyticsDelayCauseRow[];
  totalDelayHours: number | null;
  paretoThresholdPct?: number;
  /** Causes up to and including the one that reaches the threshold. */
  vitalFewCount?: number;
  reworkByReason?: AnalyticsReworkReasonRow[];
  materialDelays?: AnalyticsMaterialDelayRow[];
  breakdownOverlapHours?: number | null;
  lateJobs?: AnalyticsLateJobRow[];
  excludedNonWorkingPauses?: {
    breakHours: number | null;
    endOfShiftHours: number | null;
    totalHours: number | null;
  };
}

export interface AnalyticsSalesMonthRow {
  month: string;
  jobCount: number;
  amount: number | null;
  partialPeriod: boolean;
  workingDaysCovered: number;
}

export interface AnalyticsSalesClientRow {
  clientId: string;
  clientName: string | null;
  jobCount: number;
  amount: number | null;
  averageJobValue: number | null;
}

export interface AnalyticsSalesJobTypeRow {
  jobType: string;
  jobCount: number;
  amount: number | null;
}

export interface AnalyticsSalesSummary {
  period: { from: string; to: string };
  workingDaysInPeriod: number;
  completedJobCount: number;
  totalAmount: number | null;
  byMonth: AnalyticsSalesMonthRow[];
  byClient: AnalyticsSalesClientRow[];
  byJobType: AnalyticsSalesJobTypeRow[];
}

export interface AnalyticsPipelineMonthRow {
  month: string;
  jobCount: number;
  amount: number | null;
}

export interface AnalyticsCommittedPipeline {
  label: string;
  description: string;
  totalAmount: number | null;
  jobCount: number;
  byExpectedCompletionMonth: AnalyticsPipelineMonthRow[];
}

export interface MovingAverageMonthRow {
  month: string;
  actual: number | null;
  forecast: number | null;
  absoluteError: number | null;
}

export type ForecastMethodKey = 'MA3' | 'SES' | 'HOLT';

export interface ForecastHorizonRow {
  month: string;
  forecast: number | null;
  lower: number | null;
  upper: number | null;
}

export interface ForecastMethodResult {
  key: ForecastMethodKey;
  name: string;
  mae: number | null;
  mapePct: number | null;
  backtestedMonths: number;
  selected: boolean;
}

/** Monthly forecast: the method with the lowest back-tested error of MA3, SES and Holt. */
export interface MovingAverageForecast {
  method: string | null;
  methodKey?: ForecastMethodKey | null;
  errorMetric?: 'MAPE' | 'MAE' | null;
  parameters?: Record<string, number>;
  window: number;
  monthsAvailable: number;
  monthsNeeded: number;
  enoughHistory: boolean;
  notEnoughHistoryNote: string | null;
  forecastMonth: string;
  forecast: number | null;
  mae: number | null;
  mapePct: number | null;
  backtestedMonths: number;
  intervalLevelPct?: number;
  horizon?: ForecastHorizonRow[];
  currentMonthSoFar?: number | null;
  excludedMonths?: { month: string; reason: 'PARTIAL_FIRST_MONTH' | 'IN_PROGRESS' }[];
  methods?: ForecastMethodResult[];
  months: MovingAverageMonthRow[];
}

export interface AnalyticsSalesMovingAverage extends MovingAverageForecast {
  label: string;
  description: string;
}

export interface AnalyticsSalesForecast {
  committedPipeline: AnalyticsCommittedPipeline;
  salesForecast: AnalyticsSalesMovingAverage;
}

export interface AnalyticsDemandJobTypeForecast extends MovingAverageForecast {
  jobType: JobType;
}

export interface AnalyticsDemandForecast extends MovingAverageForecast {
  label: string;
  description: string;
  byJobType: AnalyticsDemandJobTypeForecast[];
}

export interface ConsumableRunOutPeriod {
  from: string;
  to: string;
  workingDays: number;
  used: number | null;
}

/** Analytics → Inventory: consumable use and job-material stock for a period. */
export interface AnalyticsInventory {
  from: string;
  to: string;
  today: string;
  months: {
    month: string;
    consumableUseQty: number;
    consumableUseValue: number;
    materialReceivedValue: number;
    materialConsumedValue: number;
  }[];
  consumables: {
    useQty: number;
    useValue: number;
    itemsUsed: number;
    topByValue: {
      toolId: string;
      name: string;
      sizeSpec: string | null;
      unit: string;
      quantity: number;
      value: number;
    }[];
  };
  materials: {
    onHandLines: number;
    onHandValue: number;
    incomingLines: number;
    incomingValue: number;
    incomingOverdueLines: number;
    receivedValue: number;
    consumedValue: number;
    avgDaysInStock: number | null;
    daysInStockSamples: number;
    ageBuckets: { key: string; label: string; lines: number; value: number }[];
    oldestOnHand: {
      id: string;
      materialName: string;
      gradeOrSpec: string | null;
      sizeSpec: string | null;
      quantity: number;
      unit: string;
      value: number;
      dateReceived: string;
      daysInStock: number;
      jobOrderId: string | null;
      jobNumber: string | null;
      jobStatus: string | null;
    }[];
  };
}

export interface ConsumableRunOutRow {
  toolId: string;
  name: string;
  code: string;
  sizeSpec: string | null;
  unit: string;
  legacyUnit?: string | null;
  unitNeedsReview?: boolean;
  quantityOnHand: number | null;
  minimumStock: number | null;
  lowStock: boolean;
  lastCountedOn?: string | null;
  periodsUsed?: number;
  periods?: ConsumableRunOutPeriod[];
  enoughData?: boolean;
  notEnoughDataNote?: string | null;
  /** Average daily usage (scans / working days). */
  adu?: number | null;
  dailyUsage?: number | null;
  maxDailyUsage?: number | null;
  scanWorkingDays?: number | null;
  label?: ConsumableForecastLabel | string | null;
  lastCountQuantity?: number | null;
  deliveriesSinceCount?: number | null;
  shopDaysSinceCount?: number | null;
  estimatedOnHand?: number | null;
  daysLeft: number | null;
  runOutDate: string | null;
  orderByDate?: string | null;
  daysToReorderLevel?: number | null;
  suggestedOrderQty?: number | null;
  likelyOut?: boolean;
  leadTimeDays?: number | null;
  leadTimeSource?: 'MEASURED' | 'SUPPLIER' | 'DEFAULT';
  leadTimeWorkingDays?: number | null;
  leadTimeWorkingDaysAvg?: number | null;
  leadTimeWorkingDaysMax?: number | null;
  safetyStock?: number | null;
  reorderPoint?: number | null;
  reorderLevel?: number | null;
  status?: ConsumableStockStatus;
  abcClass?: ConsumableAbcClass | null;
  usageValue90d?: number | null;
  unitCost?: number | null;
}

export type ReplenishmentStatus = 'OVERDUE' | 'DUE_SOON' | 'OK' | 'LEARNING' | 'NO_HISTORY';

/** One consumable in the replenishment forecast (Croston's method, SBA). */
export interface ReplenishmentItem {
  toolId: string;
  name: string;
  shopTerm: string | null;
  sizeSpec: string | null;
  unit: string;
  purchases: number;
  lastBoughtOn: string | null;
  lastQuantity: number | null;
  lastUnitCost: number | null;
  everyDays: number | null;
  usualQuantity: number | null;
  monthlyNeed: number | null;
  typicalErrorDays: number | null;
  nextPurchaseOn: string | null;
  daysUntilNext: number | null;
  status: ReplenishmentStatus;
}

export interface ConsumableReplenishment {
  asOf: string;
  items: ReplenishmentItem[];
  summary: {
    tracked: number;
    forecast: number;
    dueSoon: number;
    overdue: number;
    spentLast30Days: number;
    purchasesLast30Days: number;
    spendByMonth: { month: string; amount: number }[];
  };
  method: { name: string; alpha: number; minPurchases: number; dueSoonDays: number };
}

/** A consumable purchase: recorded by the Office Staff, or a received supplier order line. */
export interface ConsumablePurchase {
  id: string;
  source: 'RECORDED' | 'SUPPLIER_ORDER';
  toolId: string;
  name: string | null;
  purchasedOn: string;
  quantity: number;
  unit: string;
  unitCost: number | null;
  amount: number | null;
  boughtFrom: string | null;
  note: string | null;
  poNumber: string | null;
  recordedBy: string | null;
  createdAt: string | null;
}

export interface ConsumableRunOut {
  label: string;
  method: string;
  description: string;
  formulas?: Record<string, string>;
  today: string;
  serviceLevelPct?: number;
  defaultLeadTimeDays?: number;
  reorderSoonWorkingDays?: number;
  reviewPeriodDays?: number;
  items: ConsumableRunOutRow[];
}

export interface AnalyticsUtilizationWeekly {
  period: { from: string; to: string };
  weeks: { weekStart: string; from: string; to: string; availableHours: number }[];
  machines: {
    machineUnitId: string;
    machineUnitLabel: string;
    machineTypeCode: string | null;
    machineTypeName: string | null;
    busyHours: number | null;
    utilizationPct: number | null;
    cells: { weekStart: string; busyHours: number | null; utilizationPct: number | null }[];
  }[];
}

export interface CompletionEstimateJob {
  jobOrderId: string;
  jobNumber: string;
  title: string | null;
  clientName: string | null;
  status: string;
  dueDate: string | null;
  scheduledFinish: string | null;
  predictedFinish: string | null;
  basis: string | null;
  flag: 'GREEN' | 'AMBER' | 'RED' | null;
  /** Date required minus the later of predicted and scheduled finish, in days. */
  slackDays: number | null;
  ratioBases: string[];
}

export interface CompletionEstimates {
  minSamples: number;
  jobs: CompletionEstimateJob[];
  operationTypes: {
    operationTypeId: string;
    operationTypeName: string | null;
    samples: number;
    ratio: number;
    enoughHistory: boolean;
    workerRatios: number;
    machineRatios: number;
  }[];
}

export interface AnalyticsCapacityTypeRow {
  machineTypeId: string;
  machineTypeCode: string;
  machineTypeName: string | null;
  activeUnitCount: number;
  unitsDown?: number;
  availableHours: number | null;
  /** Hours booked inside the window, before subtracting hours already worked. */
  bookedHours?: number;
  workedHours?: number;
  scheduledLoadHours: number | null;
  projectedLoadPct: number | null;
  above80Pct: boolean;
}

export interface AnalyticsDemandCapacity {
  horizon: { from: string; to: string };
  horizonWorkingDays: number;
  availableHoursPerUnit: number | null;
  scheduledOperationsInHorizon: number;
  thinSample: boolean;
  thinSampleNote?: string;
  machineTypes: AnalyticsCapacityTypeRow[];
}

export interface AnalyticsPurchasingMaterialRow {
  materialName: string;
  purchaseCount: number;
  totalQuantity: number | null;
  totalSpend: number | null;
  unit: string | null;
}

export interface AnalyticsPurchasingSupplierSpendRow {
  supplierId: string;
  supplierName: string | null;
  purchaseCount: number;
  totalSpend: number | null;
}

export interface AnalyticsPurchasingLeadTimeRow {
  supplierId: string;
  supplierName: string | null;
  statedLeadTimeDays: number | null;
  sampleCount: number;
  averageActualDays: number | null;
  varianceDays: number | null;
}

export interface AnalyticsPurchasing {
  period: { from: string; to: string };
  purchaseCount: number;
  totalSpend: number | null;
  materialsByCount: AnalyticsPurchasingMaterialRow[];
  materialsBySpend: AnalyticsPurchasingMaterialRow[];
  spendBySupplier: AnalyticsPurchasingSupplierSpendRow[];
  supplierLeadTime: AnalyticsPurchasingLeadTimeRow[];
}

export interface WorkerWorkHistorySummary {
  operationsCompleted: number;
  /** Every completed operation in the range, redos included (same as the table). */
  operationsTotal?: number;
  reworkCount: number;
  enoughHistory: boolean;
  minimumForAverages: number;
  totalEstimatedHours: number | null;
  totalActualHours: number | null;
  averageVariancePct: number | null;
  onEstimateRatePct: number | null;
  message: string | null;
}

export interface WorkerHistoryOperation {
  id: string;
  completedAt: string | null;
  jobOrderId: string;
  jobNumber: string | null;
  operationName: string;
  operationTypeName?: string | null;
  machineUnitLabel: string | null;
  estimatedHours: number | null;
  actualHours: number | null;
  differenceHours: number | null;
  isRework: boolean;
  isCrew?: boolean;
  crewSize?: number;
  crewRole?: 'LEAD' | 'HELPER';
  crew?: CrewMember[];
}

export interface WorkerWorkHistory {
  summary: WorkerWorkHistorySummary;
  operations: {
    items: WorkerHistoryOperation[];
    total: number;
    page: number;
    pages: number;
    perPage: number;
  };
  toolsHeld: ToolUnit[];
  toolEvents: ToolEvent[];
}

/** Office Staff dashboard: office work waiting to be done. */
export interface OfficeJobRef {
  id: string;
  jobNumber: string | null;
  title: string;
  clientName: string | null;
  dueDate: string | null;
  status: JobOrderStatus;
}

export interface OfficeDeliveryRef {
  id: string;
  poNumber: string | null;
  supplierName: string | null;
  expectedDeliveryDate: string;
  daysOverdue: number;
}

export interface OfficeRestockItem {
  id: string;
  name: string;
  unit: string;
  status: 'OVERDUE' | 'DUE_SOON';
  nextPurchaseOn: string;
  lastBoughtOn: string | null;
  usualQuantity: number | null;
}

export interface OfficeSummary {
  pendingJobs: number;
  materialsToOrder: OfficeJobRef[];
  invoicesToRecord: OfficeJobRef[];
  lateDeliveries: OfficeDeliveryRef[];
  deliveriesDueSoon: OfficeDeliveryRef[];
  restockDue: OfficeRestockItem[];
}

// ---- Audit trail (Admin only) ----

export type AuditCategory = 'SIGN_IN' | 'CHANGES' | 'ACTIONS';

/** One line of a change: old → new, or a single value for anything else. */
export type AuditDetail =
  | { label: string; from: unknown; to: unknown; value?: never }
  | { label: string; value: unknown; from?: never; to?: never };

export interface AuditEntry {
  id: string;
  createdAt: string | null;
  action: string;
  actionLabel: string;
  category: AuditCategory;
  user: { id: string; name: string; role: UserRole | string } | null;
  entityType: string;
  entityLabel: string;
  entityId: string | null;
  recordLabel: string | null;
  link: string | null;
  summary: string;
  details: AuditDetail[];
  ipAddress: string | null;
}

export interface AuditLogPage {
  items: AuditEntry[];
  total: number;
  page: number;
  pageSize: number;
}

export interface AuditLogOptions {
  users: { id: string; name: string; role: UserRole | string }[];
  entityTypes: { value: string; label: string }[];
  categories: { value: AuditCategory; label: string }[];
}

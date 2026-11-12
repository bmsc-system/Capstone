from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re

from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models.job_order import (
    ARCHIVEABLE_STATUSES,
    CANCEL_REASONS,
    INACTIVE_SCHEDULE_STATUSES,
    LIST_STATUSES,
    JobOrder,
    JobOrderStatus,
    JobPriority,
    JobType,
    MaterialStatus,
    PartCondition,
    PRODUCTION_STATUSES,
    PRODUCTION_VISIBLE_STATUSES,
    default_material_status,
)
from app.models.machine import MachineType
from app.models.operation import JobOperation, OperationStatus
from app.models.user import User, UserRole
from app.services.schedule_color_service import next_schedule_color
from app.utils.errors import AppError


def _parse_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    return value


def _parse_datetime(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _parse_decimal(value, field_name):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise AppError(f"Invalid {field_name}", "VALIDATION_ERROR", 400)


_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _normalize_schedule_color(value):
    if value is None or value == "":
        return None
    color = str(value).strip()
    if not _HEX_COLOR_RE.match(color):
        raise AppError(
            "scheduleColor must be a hex color like #2563eb",
            "VALIDATION_ERROR",
            400,
        )
    return color.upper()


def _resolve_machine_type_id(op_data):
    mid = op_data.get("machineTypeId")
    if mid:
        mt = MachineType.query.get(mid)
        if not mt:
            raise AppError("Invalid machineTypeId", "VALIDATION_ERROR", 400)
        return mt.id
    # Legacy: machinesNeeded: ["MILLING"]
    codes = op_data.get("machinesNeeded") or []
    if codes:
        code = str(codes[0]).strip().upper()
        mt = MachineType.query.filter_by(code=code).first()
        if not mt:
            raise AppError(f"Unknown machine type '{code}'", "VALIDATION_ERROR", 400)
        return mt.id
    return None


def _assert_worker_has_machine_skill(worker, machine_type_id):
    """Refuses a worker without the machine skill, but only once someone has it
    recorded. Operations with no machine type take anyone."""
    if not machine_type_id:
        return
    from app.services.worker_profile_service import machine_skill_holders

    holders = machine_skill_holders(machine_type_id)
    if holders is None or worker.id in holders:
        return
    machine = db.session.get(MachineType, machine_type_id)
    machine_name = machine.name if machine else "this machine type"
    raise AppError(
        f"{worker.full_name} has no skill for {machine_name}. "
        "Add the skill under Worker setup, or assign a qualified worker.",
        "WORKER_NOT_QUALIFIED",
        400,
    )


def _validate_worker(
    worker_id,
    start=None,
    end=None,
    exclude_operation_id=None,
    *,
    machine_type_id=None,
    exclude_operation_ids=None,
):
    from app.services.worker_profile_service import is_assignable_worker

    worker = User.query.get(worker_id)
    if not is_assignable_worker(worker):
        raise AppError("Invalid worker assignment", "VALIDATION_ERROR", 400)
    _assert_worker_has_machine_skill(worker, machine_type_id)
    from app.services.worker_availability import assert_worker_available

    assert_worker_available(
        worker_id,
        start=start,
        end=end,
        exclude_operation_id=exclude_operation_id,
        exclude_operation_ids=exclude_operation_ids,
    )
    return worker


def _parse_helper_ids(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise AppError("helperIds must be a list", "VALIDATION_ERROR", 400)
    return [str(v) for v in value if v]


def _validate_helpers(
    lead_id,
    helper_ids,
    start=None,
    end=None,
    exclude_operation_id=None,
    *,
    outsourced=False,
    exclude_operation_ids=None,
) -> list[str]:
    """Helpers: up to two, distinct, not the lead, assignable and free. They
    need no machine skill. Outsourced work takes no helpers."""
    from app.models.operation import MAX_HELPERS
    from app.services.worker_availability import assert_worker_available
    from app.services.worker_profile_service import is_assignable_worker

    helper_ids = _parse_helper_ids(helper_ids)
    if not helper_ids:
        return []
    if outsourced:
        raise AppError("Outsourced operations have no crew.", "VALIDATION_ERROR", 400)
    if not lead_id:
        raise AppError("Choose the lead worker before adding helpers.", "VALIDATION_ERROR", 400)
    if len(helper_ids) > MAX_HELPERS:
        raise AppError(
            f"An operation has at most {MAX_HELPERS} helpers.", "TOO_MANY_HELPERS", 400
        )
    if len(set(helper_ids)) != len(helper_ids) or lead_id in helper_ids:
        raise AppError(
            "Each crew member can appear only once.", "VALIDATION_ERROR", 400
        )
    crew = [lead_id, *helper_ids]
    for wid in helper_ids:
        if not is_assignable_worker(User.query.get(wid)):
            raise AppError("Invalid helper assignment", "VALIDATION_ERROR", 400)
        assert_worker_available(
            wid,
            start=start,
            end=end,
            exclude_operation_id=exclude_operation_id,
            exclude_operation_ids=exclude_operation_ids,
            crew_ids=crew,
        )
    return helper_ids


def derive_job_status(job: JobOrder) -> JobOrderStatus:
    if job.status == JobOrderStatus.CANCELLED:
        return JobOrderStatus.CANCELLED
    if job.status == JobOrderStatus.DELIVERED or job.delivered_at:
        return JobOrderStatus.DELIVERED
    if job.status == JobOrderStatus.FOR_DELIVERY or getattr(job, "for_delivery_at", None):
        return JobOrderStatus.FOR_DELIVERY
    # Draft is sticky until release — stage is derived from data, not stored.
    if job.status == JobOrderStatus.DRAFT:
        return JobOrderStatus.DRAFT

    ops = list(job.operations or [])
    if not ops:
        return JobOrderStatus.SCHEDULED
    if all(op.status == OperationStatus.COMPLETED for op in ops):
        return JobOrderStatus.COMPLETED
    if any(
        op.status in (OperationStatus.IN_PROGRESS, OperationStatus.COMPLETED, OperationStatus.REWORK)
        for op in ops
    ):
        return JobOrderStatus.IN_PROGRESS
    return JobOrderStatus.SCHEDULED


def _initial_part_condition(job_type: JobType) -> PartCondition:
    if job_type == JobType.REPAIR:
        return PartCondition.CLIENT_SUPPLIED_ITEM
    return PartCondition.RAW_MATERIAL


# Furthest stage wins; never move backwards.
_PART_CONDITION_RANK = {
    PartCondition.RAW_MATERIAL: 0,
    PartCondition.CLIENT_SUPPLIED_ITEM: 0,
    PartCondition.WORK_IN_PROCESS: 1,
    PartCondition.CUT: 2,
    PartCondition.BLANK: 3,
    PartCondition.FORMED: 4,
    PartCondition.MACHINED: 5,
    PartCondition.ASSEMBLED: 6,
    PartCondition.HEAT_TREATED: 7,
    PartCondition.FINISHED: 8,
}

# Completed ops that never change the stage.
_STAGE_NEUTRAL_OP_CODES = frozenset({"CHECKING", "LAYOUT"})

_OP_CODE_STAGE = {
    "CUTTING": PartCondition.CUT,
    "BLANKING": PartCondition.BLANK,
    "BENDING": PartCondition.FORMED,
    "FORMING": PartCondition.FORMED,
    "WELDING": PartCondition.ASSEMBLED,
    "ASSEMBLY": PartCondition.ASSEMBLED,
    "FITTING": PartCondition.ASSEMBLED,
    "HEAT_TREATMENT": PartCondition.HEAT_TREATED,
    "FINISHING": PartCondition.FINISHED,
}

_MACHINING_OP_CODES = frozenset(
    {
        "TURNING",
        "FACING",
        "THREADING",
        "TEETH_CUTTING",
        "SLOTTING",
        "GROOVING",
        "DRILLING",
        "KEYWAY",
        "SPLINE",
        "SURFACE_GRINDING",
    }
)


def _part_condition_from_op_code(code: str | None) -> PartCondition | None:
    """Stage reached by completing an op of this code.

    None means the op does not change the stage (CHECKING, LAYOUT). Any other op with
    no specific stage (e.g. custom names) is generic WORK_IN_PROCESS.
    """
    if code in _STAGE_NEUTRAL_OP_CODES:
        return None
    if code in _MACHINING_OP_CODES:
        return PartCondition.MACHINED
    return _OP_CODE_STAGE.get(code or "", PartCondition.WORK_IN_PROCESS)


def _op_stage_code(op) -> str | None:
    if op.operation_type and op.operation_type.code:
        return op.operation_type.code
    name = (op.operation_name or "").strip().upper()
    return "_".join(name.replace("-", " ").split()) or None


def _rank(condition: PartCondition | None) -> int:
    if condition is None:
        return 0
    return _PART_CONDITION_RANK.get(condition, 0)


def advance_part_condition(job: JobOrder):
    """Advance part stage from completed operation types; never move backwards."""
    ops = list(job.operations or [])
    if not ops:
        return

    current = job.part_condition or PartCondition.RAW_MATERIAL
    best = current

    for op in ops:
        if op.status != OperationStatus.COMPLETED:
            continue
        stage = _part_condition_from_op_code(_op_stage_code(op))
        if stage is not None and _rank(stage) > _rank(best):
            best = stage

    if ops and all(op.status == OperationStatus.COMPLETED for op in ops):
        best = PartCondition.FINISHED

    if _rank(best) > _rank(current):
        job.part_condition = best


def check_job_access(job_order, user_id, user_role):
    if user_role in (UserRole.ADMIN.value, UserRole.OFFICE_STAFF.value):
        return True
    if user_role == UserRole.PRODUCTION_WORKER.value:
        if job_order.status == JobOrderStatus.DRAFT:
            raise AppError("Access denied", "FORBIDDEN", 403)
        has_op = any(user_id in op.crew_ids for op in (job_order.operations or []))
        if not has_op:
            raise AppError("Access denied", "FORBIDDEN", 403)
        return True
    raise AppError("Access denied", "FORBIDDEN", 403)


def job_list_load_options():
    """Everything ``JobOrder.to_dict`` and the completion estimate read, in a
    fixed number of queries however many jobs are loaded."""
    from sqlalchemy.orm import selectinload

    from app.models.material_purchase import MaterialPurchase

    operations = selectinload(JobOrder.operations)
    purchases = selectinload(JobOrder.material_purchases)
    return (
        operations.joinedload(JobOperation.assigned_worker),
        operations.joinedload(JobOperation.machine_type),
        operations.joinedload(JobOperation.operation_type),
        operations.selectinload(JobOperation.time_logs),
        joinedload(JobOrder.client),
        joinedload(JobOrder.supplier),
        joinedload(JobOrder.created_by),
        joinedload(JobOrder.material_delay_supplier_order),
        purchases.joinedload(MaterialPurchase.supplier_order),
        purchases.joinedload(MaterialPurchase.supplier),
    )


def job_list_summary_options():
    """List cards: next operation and material wait, without time logs or estimates."""
    from sqlalchemy.orm import selectinload

    from app.models.material_purchase import MaterialPurchase

    operations = selectinload(JobOrder.operations)
    purchases = selectinload(JobOrder.material_purchases)
    return (
        operations.joinedload(JobOperation.assigned_worker),
        joinedload(JobOrder.client),
        joinedload(JobOrder.created_by),
        purchases.joinedload(MaterialPurchase.supplier_order),
    )


def list_job_orders(user_id, user_role, status=None, scope=None, awaiting_material=False, summary=False):
    query = JobOrder.query.options(
        *(job_list_summary_options() if summary else job_list_load_options())
    )
    if user_role == UserRole.PRODUCTION_WORKER.value:
        query = query.filter(
            JobOrder.status.in_(tuple(PRODUCTION_VISIBLE_STATUSES)),
            JobOrder.archived_at.is_(None),
            JobOrder.operations.any(JobOperation.crew_includes(user_id)),
        )
    elif scope == "drafts":
        query = query.filter_by(status=JobOrderStatus.DRAFT).filter(
            JobOrder.archived_at.is_(None)
        )
    elif scope == "archived":
        query = query.filter(JobOrder.archived_at.isnot(None))
    elif scope != "all":
        # Default production list: active + cancelled history, not archived.
        query = query.filter(
            JobOrder.status.in_(tuple(LIST_STATUSES)),
            JobOrder.archived_at.is_(None),
        )
    if status:
        query = query.filter_by(status=JobOrderStatus(status))
    if awaiting_material:
        query = query.filter(
            JobOrder.material_status.in_(
                (MaterialStatus.TO_ORDER, MaterialStatus.ORDERED)
            )
        )
    # Jobs due the same day: the earlier client PO first, then the earlier job.
    return query.order_by(
        JobOrder.due_date.asc(),
        JobOrder.po_date.asc().nullslast(),
        JobOrder.created_at.asc(),
        JobOrder.id.asc(),
    ).all()


def get_job_order(job_id, user_id, user_role):
    from app.models.material_purchase import MaterialPurchase
    from app.models.operation_time import OperationTimeLog
    from app.models.sales_invoice import SalesInvoice

    # Use filter().first() (not Query.get) so loader options always apply even
    # when the JobOrder is already present in the identity map.
    job = (
        JobOrder.query.options(
            joinedload(JobOrder.client),
            joinedload(JobOrder.created_by),
            joinedload(JobOrder.cancelled_by),
            joinedload(JobOrder.archived_by),
            joinedload(JobOrder.sales_invoice).joinedload(SalesInvoice.prepared_by),
            joinedload(JobOrder.operations).joinedload(JobOperation.assigned_worker),
            joinedload(JobOrder.operations).joinedload(JobOperation.machine_type),
            joinedload(JobOrder.operations).joinedload(JobOperation.operation_type),
            joinedload(JobOrder.operations).joinedload(JobOperation.machine_unit),
            joinedload(JobOrder.operations)
            .joinedload(JobOperation.time_logs)
            .joinedload(OperationTimeLog.worker),
            joinedload(JobOrder.material_purchases).joinedload(MaterialPurchase.supplier),
            joinedload(JobOrder.material_purchases).joinedload(
                MaterialPurchase.supplier_order
            ),
        )
        .filter(JobOrder.id == job_id)
        .first()
    )
    if not job:
        raise AppError("Job order not found", "NOT_FOUND", 404)
    check_job_access(job, user_id, user_role)
    return job


def _parse_turnaround(value):
    if value is None or value == "":
        return None
    try:
        days = int(value)
    except (TypeError, ValueError):
        raise AppError("turnaroundDays must be a whole number of days", "VALIDATION_ERROR", 400)
    if days < 1 or days > 90:
        raise AppError("turnaroundDays must be between 1 and 90", "VALIDATION_ERROR", 400)
    return days


def _is_final_operation(op_type_id=None, operation_name=None) -> bool:
    from app.models.worker_skill import FINAL_OPERATION_CODE, OperationType

    if op_type_id:
        ot = db.session.get(OperationType, op_type_id)
        if ot is not None:
            return ot.code == FINAL_OPERATION_CODE
    return (operation_name or "").strip().lower().startswith("finishing")


def _finishing_last(op_payloads: list) -> list:
    """Finishing stays the final operation: anything listed after it moves
    before it. Sequence numbers follow the new order."""
    rest, final = [], []
    for p in op_payloads:
        is_final = _is_final_operation(p.get("operationTypeId"), p.get("operationName") or p.get("name"))
        (final if is_final else rest).append(dict(p))
    ordered = rest + final
    for i, p in enumerate(ordered, start=1):
        p.pop("seq", None)
        p["sequenceNo"] = i
    return ordered


def _build_operation(job_id, op_data, seq_fallback):
    from app.models.worker_skill import OperationType

    op_type_id = op_data.get("operationTypeId")
    op_type = OperationType.query.get(op_type_id) if op_type_id else None
    if op_type is not None and not op_type.active:
        raise AppError(
            f"{op_type.name} is no longer one of the shop's operations. Choose another.",
            "OPERATION_TYPE_INACTIVE",
            400,
        )
    name = (op_data.get("operationName") or op_data.get("name") or "").strip()
    if not name and op_type:
        name = op_type.name
    if not name:
        raise AppError("Each operation requires a name", "VALIDATION_ERROR", 400)
    seq = op_data.get("sequenceNo", op_data.get("seq", seq_fallback))
    worker_id = op_data.get("assignedWorkerId")
    start = op_data.get("scheduledStart")
    end = op_data.get("scheduledEnd")
    machine_type_id = _resolve_machine_type_id(op_data)
    if not machine_type_id and op_type and op_type.default_machine_type_id:
        machine_type_id = op_type.default_machine_type_id
    outsourced = bool(op_type and op_type.is_outsourced)
    turnaround_days = None
    if outsourced:
        # Done outside the shop: no worker, machine or target hours.
        worker_id = None
        machine_type_id = None
        turnaround_days = _parse_turnaround(op_data.get("turnaroundDays"))
        if turnaround_days is None:
            turnaround_days = op_type.default_turnaround_days
    if worker_id:
        # Clashes are checked on working periods when the schedule is confirmed.
        _validate_worker(worker_id, machine_type_id=machine_type_id)
    helper_ids = _validate_helpers(worker_id, op_data.get("helperIds"), outsourced=outsourced)
    status_raw = op_data.get("status", "PENDING")
    try:
        status = OperationStatus(status_raw)
    except ValueError:
        status = OperationStatus.PENDING

    kwargs = {
        "job_order_id": job_id,
        "sequence_no": int(seq),
        "operation_name": name,
        "operation_type_id": op_type.id if op_type else op_type_id,
        "machine_type_id": machine_type_id,
        "machine_unit_id": None if outsourced else op_data.get("machineUnitId"),
        "assigned_worker_id": worker_id,
        "estimated_hours": (
            None if outsourced else _parse_decimal(op_data.get("estimatedHours"), "estimatedHours")
        ),
        "turnaround_days": turnaround_days,
        "scheduled_start": _parse_datetime(start),
        "scheduled_end": _parse_datetime(end),
        "status": status,
        "rework_of_operation_id": op_data.get("reworkOfOperationId"),
        "notes": op_data.get("notes"),
    }
    op = JobOperation(**kwargs)
    op.set_helpers(helper_ids)
    return op


_DERIVED_MATERIAL_STATUSES = (MaterialStatus.ORDERED, MaterialStatus.RECEIVED)


def _materials_needed(value) -> MaterialStatus:
    """"Materials needed" on the job order form: To order or Not required.
    Ordered and Received follow from the supplier orders and are not set."""
    try:
        status = MaterialStatus(value)
    except ValueError:
        status = None
    if status not in (MaterialStatus.TO_ORDER, MaterialStatus.NOT_REQUIRED):
        raise AppError(
            "Materials needed must be TO_ORDER or NOT_REQUIRED",
            "VALIDATION_ERROR",
            400,
        )
    return status


def _assert_can_set_not_required(job, role):
    """Office Staff choose freely while the job is pending; after release only
    the Admin may set Not required (in place of the earlier From stock)."""
    if job.status != JobOrderStatus.DRAFT and role != UserRole.ADMIN.value:
        raise AppError(
            "Only the Admin can set Materials needed to Not required after release.",
            "FORBIDDEN",
            403,
        )


def _apply_materials_needed(job, value, role):
    derived = {s.value for s in _DERIVED_MATERIAL_STATUSES}
    if value in derived and job.material_status.value in derived:
        return
    wanted = _materials_needed(value)
    if wanted == MaterialStatus.NOT_REQUIRED:
        if job.material_status != MaterialStatus.NOT_REQUIRED:
            _assert_can_set_not_required(job, role)
            job.material_status = MaterialStatus.NOT_REQUIRED
        return
    if job.material_status == MaterialStatus.NOT_REQUIRED:
        from app.services.material_purchase_service import sync_job_material_from_purchases

        job.material_status = MaterialStatus.TO_ORDER
        sync_job_material_from_purchases(job)


def create_job_order(data, created_by_id, actor_role=None):
    """Office creates a DRAFT from the client PO. Operations are optional; no notify.

    Materials needed defaults to To order for Fabrication and Machining and Not
    required for Repair; materials are entered later, when ordering.
    """
    priority = data.get("priority") or JobPriority.MODERATE.value
    try:
        priority_enum = JobPriority(priority)
    except ValueError:
        raise AppError("priority must be HIGH, MODERATE, or LOW", "VALIDATION_ERROR", 400)

    try:
        job_type = JobType(data.get("jobType", "FABRICATION"))
    except ValueError:
        raise AppError("Invalid jobType", "VALIDATION_ERROR", 400)

    material_status = (
        _materials_needed(data["materialStatus"])
        if data.get("materialStatus")
        else default_material_status(job_type)
    )

    try:
        job = JobOrder(
            client_id=data["clientId"],
            title=data["title"],
            description=data.get("description"),
            due_date=_parse_date(data["dueDate"]),
            client_po_number=(data.get("clientPoNumber") or None),
            po_date=_parse_date(data.get("poDate")),
            status=JobOrderStatus.DRAFT,
            priority=priority_enum,
            job_type=job_type,
            part_condition=_initial_part_condition(job_type),
            quantity=_parse_decimal(data.get("quantity"), "quantity"),
            unit_of_measure=(data.get("unitOfMeasure") or None),
            amount=_parse_decimal(data.get("amount"), "amount"),
            raw_materials=_planned_materials_for(data, material_status),
            material_status=material_status,
            material_expected_date=_parse_date(data.get("materialExpectedDate")),
            material_received_date=_parse_date(data.get("materialReceivedDate")),
            supplier_id=(data.get("supplierId") or None),
            supplier_reference=(data.get("supplierReference") or None),
            schedule_color=(
                _normalize_schedule_color(data.get("scheduleColor")) or next_schedule_color()
            ),
            created_by_id=created_by_id,
        )
        db.session.add(job)
        db.session.flush()

        # Optional ops on create (admin tooling); still stays DRAFT — no JOB_RECEIVED.
        for i, op_data in enumerate(_finishing_last(data.get("operations") or []), start=1):
            op = _build_operation(job.id, op_data, i)
            db.session.add(op)

        db.session.commit()
        return get_job_order(job.id, created_by_id, UserRole.OFFICE_STAFF.value)
    except AppError:
        db.session.rollback()
        raise
    except Exception:
        db.session.rollback()
        raise


def _all_operations_done(job) -> bool:
    ops = list(job.operations or [])
    return bool(ops) and all(op.status == OperationStatus.COMPLETED for op in ops)


def mark_job_for_delivery(job):
    """Admin sets a Completed job For Delivery once Office Staff have recorded
    the sales invoice. Locks the invoice and tells the client the job is ready
    for delivery (JOB_DELIVERED milestone)."""
    from datetime import datetime, timezone

    from app.models.notification import NotificationMilestone
    from app.services.notification_service import safe_notify_job_milestone

    if job.status in (JobOrderStatus.FOR_DELIVERY, JobOrderStatus.DELIVERED):
        return job
    if job.status != JobOrderStatus.COMPLETED or not _all_operations_done(job):
        raise AppError(
            "The job must be Completed before it can be set For Delivery.",
            "INVALID_TRANSITION",
            409,
        )
    if job.sales_invoice is None:
        raise AppError(
            "Office Staff must record the sales invoice before the job can be set "
            "For Delivery.",
            "INVOICE_REQUIRED",
            409,
        )

    try:
        job.status = JobOrderStatus.FOR_DELIVERY
        job.for_delivery_at = datetime.now(timezone.utc)
        db.session.commit()
        safe_notify_job_milestone(job.id, NotificationMilestone.JOB_DELIVERED)
        return job
    except Exception:
        db.session.rollback()
        raise


def _delivery_moment(value, for_delivery_at):
    """The delivery date as a timestamp: now for today, else 5:00 PM shop time.

    Not in the future and not before the job was set For Delivery."""
    from datetime import date, datetime, time, timezone

    from app.services.schedule_calendar import SHOP_TZ, shop_local_to_utc, shop_now

    today = shop_now().date()
    if value in (None, ""):
        on = today
    else:
        try:
            on = date.fromisoformat(str(value)[:10])
        except ValueError as exc:
            raise AppError("Invalid deliveredDate (YYYY-MM-DD)", "VALIDATION_ERROR", 400) from exc
    if on > today:
        raise AppError("The delivery date cannot be in the future.", "VALIDATION_ERROR", 400)
    if for_delivery_at is not None:
        ready = for_delivery_at.astimezone(SHOP_TZ).date()
        if on < ready:
            raise AppError(
                f"The delivery date cannot be before the job was set For Delivery ({ready:%b} {ready.day}).",
                "VALIDATION_ERROR",
                400,
            )
    if on == today:
        return datetime.now(timezone.utc)
    return shop_local_to_utc(on, time(17, 0))


def mark_job_delivered(job, data: dict | None = None):
    """Admin confirms the job was handed over to the client. delivered_at is
    the date on-time and lateness are measured on (default today)."""
    data = data or {}
    if job.status == JobOrderStatus.DELIVERED:
        return job
    if job.status != JobOrderStatus.FOR_DELIVERY:
        raise AppError(
            "Set the job For Delivery before marking it delivered.",
            "INVALID_TRANSITION",
            409,
        )

    moment = _delivery_moment(data.get("deliveredDate"), job.for_delivery_at)
    try:
        job.status = JobOrderStatus.DELIVERED
        job.delivered_at = moment
        db.session.commit()
        return job
    except Exception:
        db.session.rollback()
        raise


def _planned_materials_for(data, material_status, job=None):
    """The planned materials to store; a job whose materials are Not required has none."""
    from app.services.planned_materials import parse_planned_materials

    items = data.get("rawMaterials")
    if material_status == MaterialStatus.NOT_REQUIRED:
        if items:
            raise AppError(
                "Materials needed is Not required, so no materials can be listed.",
                "VALIDATION_ERROR",
                400,
            )
        return parse_planned_materials([], job)
    return parse_planned_materials(items, job)


# Job header fields Office may edit only while DRAFT. Admin is view-only for these
# (except materials Not required after release — see _assert_can_edit_job_info).
_JOB_INFO_KEYS = (
    "clientId",
    "title",
    "description",
    "dueDate",
    "clientPoNumber",
    "poDate",
    "jobType",
    "quantity",
    "unitOfMeasure",
    "amount",
    "materialStatus",
    "rawMaterials",
)


def _assert_can_edit_job_info(job, data, role):
    """Office edits job info only while DRAFT; Admin never (except Not required after release)."""
    present = [k for k in _JOB_INFO_KEYS if k in data]
    if not present:
        return
    # Keep existing Admin-only "materials Not required after release".
    if (
        present == ["materialStatus"]
        and data.get("materialStatus") == MaterialStatus.NOT_REQUIRED.value
        and job.status != JobOrderStatus.DRAFT
        and role == UserRole.ADMIN.value
    ):
        return
    if role == UserRole.ADMIN.value:
        raise AppError(
            "Only Office Staff can edit job order information, and only before release.",
            "FORBIDDEN",
            403,
        )
    if job.status != JobOrderStatus.DRAFT:
        raise AppError(
            "Job order information is locked after release.",
            "JOB_INFO_LOCKED",
            409,
        )
    if role != UserRole.OFFICE_STAFF.value:
        raise AppError(
            "Only Office Staff can edit job order information.",
            "FORBIDDEN",
            403,
        )


def update_job_order(job, data, actor_role=None):
    """Update job fields and/or operations.

    Office may edit job information while DRAFT only; after release it is locked.
    Admin may edit operations while DRAFT but not job information (except setting
    materials Not required after release). Status is re-derived after release.
    """
    try:
        _apply_job_update(job, data, actor_role)
        db.session.commit()
        return get_job_order(job.id, job.created_by_id, UserRole.OFFICE_STAFF.value)
    except AppError:
        db.session.rollback()
        raise
    except Exception:
        db.session.rollback()
        raise


def _apply_job_update(job, data, role):
    """Apply an update payload to the job without committing."""
    is_draft = job.status == JobOrderStatus.DRAFT

    if is_draft and role == UserRole.OFFICE_STAFF.value and "operations" in data:
        raise AppError(
            "Office staff cannot edit operations during planning",
            "FORBIDDEN",
            403,
        )

    _assert_can_edit_job_info(job, data, role)

    if "clientId" in data:
        job.client_id = data["clientId"]
    if "title" in data:
        job.title = data["title"]
    if "description" in data:
        job.description = data["description"]
    if "dueDate" in data:
        job.due_date = _parse_date(data["dueDate"])
    if "clientPoNumber" in data:
        job.client_po_number = data.get("clientPoNumber") or None
    if "poDate" in data:
        job.po_date = _parse_date(data.get("poDate"))
    if data.get("priority"):
        try:
            job.priority = JobPriority(data["priority"])
        except ValueError:
            raise AppError("priority must be HIGH, MODERATE, or LOW", "VALIDATION_ERROR", 400)
    if "jobType" in data:
        try:
            job.job_type = JobType(data["jobType"])
        except ValueError:
            raise AppError("Invalid jobType", "VALIDATION_ERROR", 400)
        # Only reset initial stage when no ops have advanced the piece yet.
        if not any(
            op.status == OperationStatus.COMPLETED for op in (job.operations or [])
        ):
            job.part_condition = _initial_part_condition(job.job_type)
    if data.get("partCondition"):
        raise AppError(
            "The part stage is worked out from completed operations and cannot be set.",
            "VALIDATION_ERROR",
            400,
        )
    if "quantity" in data:
        job.quantity = _parse_decimal(data.get("quantity"), "quantity")
    if "unitOfMeasure" in data:
        job.unit_of_measure = data.get("unitOfMeasure") or None
    if "amount" in data:
        job.amount = _parse_decimal(data.get("amount"), "amount")
    if data.get("materialStatus"):
        _apply_materials_needed(job, data["materialStatus"], role)
    if "rawMaterials" in data:
        job.raw_materials = _planned_materials_for(data, job.material_status, job)
    if "materialExpectedDate" in data:
        job.material_expected_date = _parse_date(data.get("materialExpectedDate"))
    if "materialReceivedDate" in data:
        job.material_received_date = _parse_date(data.get("materialReceivedDate"))
    if "supplierId" in data:
        sid = data.get("supplierId") or None
        if sid:
            from app.models.supplier import Supplier

            if not Supplier.query.get(sid):
                raise AppError("Supplier not found", "NOT_FOUND", 404)
        job.supplier_id = sid
    if "supplierReference" in data:
        job.supplier_reference = (data.get("supplierReference") or None)
    if "scheduleColor" in data:
        job.schedule_color = _normalize_schedule_color(data.get("scheduleColor"))

    if "operations" in data:
        if not is_draft:
            raise AppError(
                "Operations can only be replaced while the job is pending. "
                "This job has been released; reassign, reschedule, or add rework "
                "to individual operations instead.",
                "OPERATIONS_LOCKED",
                409,
            )
        _assert_no_started_operations(job)
        JobOperation.query.filter_by(job_order_id=job.id).delete()
        for i, op_data in enumerate(_finishing_last(data["operations"]), start=1):
            payload = dict(op_data)
            payload.pop("id", None)
            op = _build_operation(job.id, payload, i)
            db.session.add(op)
        db.session.flush()
        if is_draft:
            _assert_no_sequence_violation(job)

    if job.status != JobOrderStatus.DRAFT:
        job.status = derive_job_status(job)
    advance_part_condition(job)


def _assert_no_sequence_violation(job: JobOrder) -> None:
    """Draft PATCH may save incomplete windows, but never an earlier-start-after-later-end."""
    from app.services.schedule_service import schedule_problems

    # Relationship may be stale after delete+insert; load the rows just flushed.
    ops = (
        JobOperation.query.filter_by(job_order_id=job.id)
        .order_by(JobOperation.sequence_no.asc())
        .all()
    )
    problems = schedule_problems(ops, exclude_job_id=job.id)
    seq = [p for p in problems if p.get("code") == "SEQUENCE_VIOLATION"]
    if seq:
        raise AppError(
            "; ".join(p["message"] for p in seq) + ".",
            "SEQUENCE_VIOLATION",
            409,
        )


def _release_missing_items(job: JobOrder) -> list[str]:
    ops = sorted(list(job.operations or []), key=lambda o: o.sequence_no or 0)
    if not ops:
        return ["Add at least one operation first."]
    missing = []
    for op in ops:
        label = op.operation_name or f"Operation {op.sequence_no}"
        seq = op.sequence_no
        if op.is_outsourced:
            if not op.turnaround_days:
                missing.append(f"#{seq} {label}: set the turnaround in days")
            continue
        if not op.assigned_worker_id:
            missing.append(f"#{seq} {label}: assign a worker")
        if op.estimated_hours is None:
            missing.append(f"#{seq} {label}: set target hours")
    return missing


def _crew_without_hours_items(job: JobOrder) -> list[str]:
    """'#1 Turning: <Name> has no working hours set; …' for each crew member of
    an unstarted operation who has no working hours."""
    from app.services.schedule_service import no_hours_messages

    items = []
    for op in sorted(job.operations or [], key=lambda o: o.sequence_no or 0):
        if op.is_outsourced or op.status in _STARTED_OPERATION_STATUSES:
            continue
        label = op.operation_name or f"Operation {op.sequence_no}"
        items += [f"#{op.sequence_no} {label}: {m}" for m in no_hours_messages(op.crew_ids)]
    return items


def lock_schedule(refresh=True) -> None:
    """Hold the schedule lock until this transaction ends, so schedule checks and
    saves run one at a time. ``refresh`` drops cached rows so the checks see
    what other transactions saved; call it before changing anything."""
    from sqlalchemy import text

    from app.constants.scheduling import SCHEDULE_LOCK_KEY

    if db.session.get_bind().dialect.name != "postgresql":
        return
    db.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SCHEDULE_LOCK_KEY})
    if refresh:
        db.session.expire_all()


def _next_quarter_hour() -> datetime:
    """Next quarter hour, so a fresh proposal isn't already in the past when confirmed."""
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return now + timedelta(minutes=15 - now.minute % 15)


def _material_floor_utc(job: JobOrder):
    from app.services.material_purchase_service import scheduling_material_floor
    from app.services.schedule_service import resolve_material_not_before_utc

    floor_date, _ = scheduling_material_floor(job)
    return floor_date, resolve_material_not_before_utc(job.material_status, None, floor_date)


def _derive_scheduled_ends(job: JobOrder) -> None:
    """End is never typed: it follows Start and the target hours across the
    worker's working hours, overtime and holidays."""
    from app.constants.scheduling import DEFAULT_ESTIMATED_HOURS
    from app.services.schedule_service import place_from_start

    for op in job.operations:
        if op.status in _STARTED_OPERATION_STATUSES or op.actual_start is not None:
            continue
        if op.is_outsourced:
            if op.scheduled_start and op.turnaround_days:
                op.scheduled_end = op.scheduled_start + timedelta(days=int(op.turnaround_days))
            continue
        if not op.scheduled_start or not op.assigned_worker_id:
            continue
        hours = op.estimated_hours if op.estimated_hours is not None else DEFAULT_ESTIMATED_HOURS
        _start, end, _segments = place_from_start(op.crew_ids, op.scheduled_start, hours)
        if end is not None:
            op.scheduled_end = end


def _assert_schedule_has_no_problems(job: JobOrder, lead: str) -> None:
    from app.services.schedule_service import schedule_problems

    _floor_date, floor_utc = _material_floor_utc(job)
    problems = schedule_problems(
        sorted(job.operations, key=lambda o: o.sequence_no or 0),
        exclude_job_id=job.id,
        material_not_before_utc=floor_utc,
    )
    if problems:
        raise AppError(
            f"{lead} " + "; ".join(p["message"] for p in problems) + ".",
            "SCHEDULE_INVALID",
            409,
        )


def propose_for_job(job: JobOrder, data: dict) -> dict:
    """
    Proposal for the Schedule step or a released job's re-plan, with every
    problem that would stop it being confirmed.

    pinSequence: that operation starts at its scheduledStart; the ones after it
      are re-placed from its end (pending jobs: earliest free time, earlier or
      later; released jobs: never earlier than now planned).
    restoreSaved: show the pending job's saved schedule, unless it starts in the
      past, in which case a fresh one is proposed (replacedPastStart says so).
    """
    from app.services import material_purchase_service as mp_service
    from app.services.schedule_calendar import ensure_utc
    from app.services.schedule_service import (
        propose_schedule,
        resolve_material_not_before_utc,
        schedule_problems,
    )

    mp_service.assert_material_date_known(job)
    ready_date, ready_reason = mp_service.scheduling_material_floor(job)
    material_nb = resolve_material_not_before_utc(job.material_status, None, ready_date)
    is_draft = job.status == JobOrderStatus.DRAFT

    ops = data.get("operations")
    lock_before = data.get("lockBeforeSequence")
    # A saved unit is never swapped unless the caller explicitly asks.
    honor_pins = data.get("honorMachinePins") is not False
    restored = False
    replaced_past_start = None
    if ops is None:
        ops = sorted(job.operations, key=lambda o: o.sequence_no or 0)
        if data.get("restoreSaved") and is_draft and ops:
            not_started = [
                o for o in ops
                if o.status not in _STARTED_OPERATION_STATUSES and o.actual_start is None
            ]
            starts = [ensure_utc(o.scheduled_start) for o in not_started if o.scheduled_start]
            if starts and len(starts) == len(not_started):
                honor_pins = True
                earliest = min(starts)
                if earliest >= datetime.now(timezone.utc):
                    lock_before = max(o.sequence_no or 0 for o in ops) + 1
                    restored = True
                else:
                    replaced_past_start = earliest.isoformat()

    anchor = _parse_datetime(data["anchor"]) if data.get("anchor") else _next_quarter_hour()
    result = propose_schedule(
        ops,
        job.due_date,
        exclude_job_id=job.id,
        anchor_utc=anchor,
        lock_before_sequence=lock_before,
        honor_machine_pins=honor_pins,
        material_not_before_utc=material_nb,
        material_constraint_reason=ready_reason,
        never_earlier=not is_draft,
        pin_sequence=data.get("pinSequence"),
    )
    by_id = {o.id: o for o in job.operations}
    checked = []
    for row in result["operations"]:
        src = by_id.get(row.get("id"))
        checked.append(
            {
                **row,
                "status": src.status.value if src and src.status else "PENDING",
                "actualStart": src.actual_start.isoformat() if src and src.actual_start else None,
            }
        )
    found = schedule_problems(
        checked, exclude_job_id=job.id, material_not_before_utc=material_nb, include_warnings=True
    )
    result["problems"] = [p for p in found if p["severity"] == "error"]
    result["warnings"] = [p for p in found if p["severity"] == "warning"]
    result["restored"] = restored
    result["replacedPastStart"] = replaced_past_start
    return result


def _assert_first_operation_after_material_floor(job: JobOrder) -> None:
    from app.services.schedule_calendar import ensure_utc

    floor_date, floor_utc = _material_floor_utc(job)
    if floor_utc is None:
        return
    first = min(job.operations, key=lambda o: o.sequence_no or 0)
    if ensure_utc(first.scheduled_start) < floor_utc:
        raise AppError(
            f"The first operation cannot start before {floor_date.isoformat()}, "
            "when the materials can be in. Propose the schedule again.",
            "MATERIAL_NOT_READY",
            400,
        )


def confirm_job_schedule(job, data=None, actor_role=None):
    """Admin confirms a pending job's schedule, which releases it to production.

    Saves the operations sent (if any), checks every operation has a worker,
    target hours and a window, then marks the job and its operations Scheduled
    and fires JOB_RECEIVED. Nothing is saved when a check fails.
    """
    from app.models.notification import NotificationMilestone
    from app.services.notification_service import safe_notify_job_milestone

    data = data or {}
    # Confirmations run one at a time: every check below sees the others' bookings.
    lock_schedule()
    if job.status != JobOrderStatus.DRAFT:
        db.session.rollback()
        raise AppError(
            "Only pending jobs can have their schedule confirmed",
            "INVALID_TRANSITION",
            409,
        )

    try:
        update = {k: data[k] for k in ("operations", "materialStatus") if k in data}
        if update:
            _apply_job_update(job, update, actor_role)
            db.session.flush()
            db.session.expire(job, ["operations"])

        missing = _release_missing_items(job) + _crew_without_hours_items(job)
        if missing:
            raise AppError(
                "Cannot confirm the schedule yet — " + "; ".join(missing),
                "VALIDATION_ERROR",
                400,
            )

        _derive_scheduled_ends(job)
        db.session.flush()
        unscheduled = [
            f"#{op.sequence_no} {op.operation_name or f'Operation {op.sequence_no}'}"
            for op in sorted(job.operations, key=lambda o: o.sequence_no or 0)
            if not op.scheduled_start or not op.scheduled_end
        ]
        if unscheduled:
            raise AppError(
                "Cannot confirm the schedule yet — these operations have no "
                "scheduled window: " + ", ".join(unscheduled) + ". Schedule them first.",
                "OPERATIONS_UNSCHEDULED",
                400,
            )

        _assert_first_operation_after_material_floor(job)
        _assert_schedule_has_no_problems(job, "Cannot confirm the schedule —")

        for op in job.operations:
            if op.status == OperationStatus.PENDING:
                op.status = OperationStatus.SCHEDULED
        job.status = JobOrderStatus.SCHEDULED
        job.status = derive_job_status(job)
        job.released_at = datetime.now(timezone.utc)
        db.session.commit()
        safe_notify_job_milestone(job.id, NotificationMilestone.JOB_RECEIVED)
        return get_job_order(job.id, job.created_by_id, UserRole.ADMIN.value)
    except AppError:
        db.session.rollback()
        raise
    except Exception:
        db.session.rollback()
        raise


_STARTED_OPERATION_STATUSES = (OperationStatus.IN_PROGRESS, OperationStatus.COMPLETED)


def _assert_no_started_operations(job):
    """Started or completed operations (and their time logs) are production records."""
    started = [
        op
        for op in (job.operations or [])
        if op.status in _STARTED_OPERATION_STATUSES or op.actual_start is not None
    ]
    if started:
        names = ", ".join(op.operation_name for op in started)
        raise AppError(
            f"Work has already been recorded on {names}. "
            "Started or completed operations cannot be deleted.",
            "OPERATIONS_STARTED",
            409,
        )


def _clear_unstarted_schedule(job: JobOrder) -> None:
    """Free machine/worker slots: clear windows and crew on work that has not started."""
    for op in job.operations or []:
        started = (
            op.status in _STARTED_OPERATION_STATUSES or op.actual_start is not None
        )
        if started:
            continue
        op.scheduled_start = None
        op.scheduled_end = None
        op.machine_unit_id = None
        op.assigned_worker_id = None
        op.set_helpers([])
        if op.status == OperationStatus.SCHEDULED:
            op.status = OperationStatus.PENDING


def _flag_open_material_lines(job: JobOrder) -> None:
    """Mark open purchase lines so Office can cancel the PO or keep the material."""
    for line in job.material_purchases or []:
        if line.cancelled_at is not None:
            continue
        if line.date_received is not None:
            continue
        line.review_after_job_cancel = True


def cancel_job_order(job, actor_id: str, data: dict | None = None, actor_role: str | None = None):
    """Cancel a job before COMPLETED. Keeps history; frees schedule; no client notify.

    Office Staff cancel only a job still to be planned (the client withdrew the PO);
    once released it has crews and bookings, so only the Admin cancels it."""
    data = data or {}
    if actor_role == UserRole.OFFICE_STAFF.value and job.status != JobOrderStatus.DRAFT:
        raise AppError(
            "This job is already released to production. Ask the Admin to cancel it.",
            "FORBIDDEN",
            403,
        )
    if job.status in (
        JobOrderStatus.COMPLETED,
        JobOrderStatus.FOR_DELIVERY,
        JobOrderStatus.DELIVERED,
        JobOrderStatus.CANCELLED,
    ):
        raise AppError(
            "Completed, delivered, or already cancelled jobs cannot be cancelled.",
            "INVALID_TRANSITION",
            409,
        )
    if job.sales_invoice is not None:
        raise AppError(
            "This job has a recorded sales invoice and cannot be cancelled.",
            "INVOICE_EXISTS",
            409,
        )
    reason = data.get("reason")
    note = (data.get("note") or "").strip() or None
    if reason not in CANCEL_REASONS:
        raise AppError("Choose a cancel reason.", "VALIDATION_ERROR", 400)
    if reason == "OTHER" and not note:
        raise AppError(
            "A note is required when the reason is Other.",
            "VALIDATION_ERROR",
            400,
        )
    try:
        now = datetime.now(timezone.utc)
        _clear_unstarted_schedule(job)
        _flag_open_material_lines(job)
        job.status = JobOrderStatus.CANCELLED
        job.cancelled_at = now
        job.cancelled_by_id = actor_id
        job.cancel_reason = reason
        job.cancel_note = note
        job.needs_replan_reason = None
        db.session.commit()
        return get_job_order(job.id, actor_id, UserRole.ADMIN.value)
    except AppError:
        db.session.rollback()
        raise
    except Exception:
        db.session.rollback()
        raise


def archive_job_order(job, actor_id: str):
    if job.status not in ARCHIVEABLE_STATUSES:
        raise AppError(
            "Only completed, delivered, or cancelled jobs can be archived.",
            "INVALID_TRANSITION",
            409,
        )
    if job.archived_at is not None:
        raise AppError("This job is already archived.", "ALREADY_ARCHIVED", 409)
    try:
        job.archived_at = datetime.now(timezone.utc)
        job.archived_by_id = actor_id
        db.session.commit()
        return get_job_order(job.id, actor_id, UserRole.ADMIN.value)
    except Exception:
        db.session.rollback()
        raise


def unarchive_job_order(job, actor_id: str):
    if job.archived_at is None:
        raise AppError("This job is not archived.", "NOT_ARCHIVED", 409)
    try:
        job.archived_at = None
        job.archived_by_id = None
        db.session.commit()
        return get_job_order(job.id, actor_id, UserRole.ADMIN.value)
    except Exception:
        db.session.rollback()
        raise


def assign_operation_worker(operation, worker_id, helper_ids=None):
    """Set the lead and, when ``helper_ids`` is given, the helpers. Without it
    the current helpers stay (less the new lead)."""
    if operation.is_outsourced:
        raise AppError(
            "This operation is done outside the shop and has no worker.",
            "OPERATION_OUTSOURCED",
            409,
        )
    if operation.status in _STARTED_OPERATION_STATUSES or operation.actual_start is not None:
        raise AppError(
            "This operation has already started, so its worker can't be changed.",
            "OPERATION_STARTED",
            409,
        )
    _validate_worker(
        worker_id,
        start=operation.scheduled_start,
        end=operation.scheduled_end,
        exclude_operation_id=operation.id,
        machine_type_id=operation.machine_type_id,
    )
    if helper_ids is None:
        helper_ids = [h for h in operation.helper_ids if h != worker_id]
    helper_ids = _validate_helpers(
        worker_id,
        helper_ids,
        start=operation.scheduled_start,
        end=operation.scheduled_end,
        exclude_operation_id=operation.id,
    )
    try:
        operation.assigned_worker_id = worker_id
        operation.set_helpers(helper_ids)
        if operation.status == OperationStatus.PENDING:
            operation.status = OperationStatus.SCHEDULED
        operation.job_order.status = derive_job_status(operation.job_order)
        db.session.commit()
        return operation
    except Exception:
        db.session.rollback()
        raise


def _same_instant(a, b) -> bool:
    from app.services.schedule_calendar import ensure_utc

    if a is None or b is None:
        return a is None and b is None
    return ensure_utc(a) == ensure_utc(b)


# Admin Reschedule reason codes stored on schedule_moves.reason.
RESCHEDULE_REASONS = (
    "WORKER_ABSENT",
    "MACHINE_BREAKDOWN",
    "MATERIAL_DELAY",
    "WORK_CALENDAR_CHANGE",
    "OTHER",
)


def _op_schedule_snap(op: JobOperation) -> dict:
    start = op.scheduled_start.isoformat() if op.scheduled_start else None
    end = op.scheduled_end.isoformat() if op.scheduled_end else None
    return {
        "sequenceNo": op.sequence_no,
        "scheduledStart": start,
        "scheduledEnd": end,
        "assignedWorkerId": op.assigned_worker_id,
        "machineUnitId": op.machine_unit_id,
        "helperIds": list(op.helper_ids or []),
    }


def _schedule_changes(before: dict, after: dict) -> dict | None:
    ops = []
    for oid, b in before.items():
        a = after.get(oid)
        if a is None:
            continue
        entry = {"operationId": oid, "sequenceNo": b.get("sequenceNo")}
        for key in (
            "scheduledStart",
            "scheduledEnd",
            "assignedWorkerId",
            "machineUnitId",
            "helperIds",
        ):
            if b.get(key) != a.get(key):
                entry[key] = {"from": b.get(key), "to": a.get(key)}
        if len(entry) > 2:
            ops.append(entry)
    return {"operations": ops} if ops else None


def _first_start_pair(before: dict, after: dict, job: JobOrder):
    """previous_start / new_start for the audit row (columns are NOT NULL)."""
    from app.services.schedule_calendar import ensure_utc

    for oid, b in before.items():
        a = after.get(oid) or {}
        if b.get("scheduledStart") != a.get("scheduledStart"):
            old = _parse_datetime(b.get("scheduledStart"))
            new = _parse_datetime(a.get("scheduledStart"))
            if old and new:
                return ensure_utc(old), ensure_utc(new)
    ops = sorted(job.operations, key=lambda o: o.sequence_no or 0)
    for op in ops:
        if op.scheduled_start:
            start = ensure_utc(op.scheduled_start)
            return start, start
    now = datetime.now(timezone.utc)
    return now, now


def apply_released_schedule(
    job, operations, *, actor_id=None, reason_code=None, note=None
):
    """
    Admin confirms a re-proposed schedule for a released job. Only operations
    that have not started get new starts (ends follow from target hours);
    started or completed work never moves. Every operation is checked.
    When actor_id / reason_code are set (Reschedule or calendar apply), writes a
    schedule_moves audit row.
    """
    from app.models.machine import MachineUnit
    from app.models.schedule_move import DelayKind
    from app.services.material_delay_service import record_move

    if job.status in (
        JobOrderStatus.DRAFT,
        JobOrderStatus.COMPLETED,
        JobOrderStatus.FOR_DELIVERY,
        JobOrderStatus.DELIVERED,
    ):
        raise AppError(
            "Only released jobs with work still to do can be re-scheduled.",
            "INVALID_TRANSITION",
            409,
        )
    if not operations:
        raise AppError("operations is required", "VALIDATION_ERROR", 400)

    lock_schedule()
    by_id = {op.id: op for op in job.operations}
    before = {op.id: _op_schedule_snap(op) for op in job.operations}
    try:
        for data in operations:
            op = by_id.get(data.get("id"))
            if op is None:
                raise AppError(
                    "Operation does not belong to this job", "VALIDATION_ERROR", 400
                )
            start = _parse_datetime(data.get("scheduledStart"))
            end = _parse_datetime(data.get("scheduledEnd"))
            started = op.status in _STARTED_OPERATION_STATUSES or op.actual_start is not None
            if started:
                start_kept = (
                    start is None
                    or _same_instant(start, op.scheduled_start)
                    or _same_instant(start, op.actual_start)
                )
                end_kept = end is None or _same_instant(end, op.scheduled_end)
                if not (start_kept and end_kept):
                    raise AppError(
                        f"{op.operation_name or f'Operation {op.sequence_no}'} has already "
                        "started, so its schedule can't be moved.",
                        "OPERATION_STARTED",
                        409,
                    )
                continue
            if not start:
                raise AppError(
                    f"{op.operation_name or f'Operation {op.sequence_no}'} needs a start time.",
                    "VALIDATION_ERROR",
                    400,
                )
            unit_id = data.get("machineUnitId") or None
            if unit_id:
                unit = MachineUnit.query.get(unit_id)
                if unit is None or (
                    op.machine_type_id and unit.machine_type_id != op.machine_type_id
                ):
                    raise AppError(
                        "Machine unit does not match the operation's machine type",
                        "VALIDATION_ERROR",
                        400,
                    )
            label = op.operation_name or f"Operation {op.sequence_no}"
            if op.is_outsourced:
                op.scheduled_start = start
                if op.status == OperationStatus.PENDING:
                    op.status = OperationStatus.SCHEDULED
                continue
            worker_id = data.get("assignedWorkerId") or op.assigned_worker_id
            if not worker_id:
                raise AppError(
                    f"{label}: assign a worker to schedule this operation.",
                    "VALIDATION_ERROR",
                    400,
                )
            if "helperIds" in data:
                helper_ids = data.get("helperIds")
            else:
                helper_ids = [h for h in op.helper_ids if h != worker_id]
            try:
                _validate_worker(worker_id, machine_type_id=op.machine_type_id)
                helper_ids = _validate_helpers(worker_id, helper_ids)
            except AppError as exc:
                raise AppError(f"{label}: {exc.message}", exc.code, exc.status_code)

            op.scheduled_start = start
            op.machine_unit_id = unit_id or op.machine_unit_id
            op.assigned_worker_id = worker_id
            op.set_helpers(helper_ids)
            if op.status == OperationStatus.PENDING and worker_id:
                op.status = OperationStatus.SCHEDULED

        _derive_scheduled_ends(job)
        db.session.flush()
        _assert_schedule_has_no_problems(job, "This schedule can't be applied:")

        after = {op.id: _op_schedule_snap(op) for op in job.operations}
        changes = _schedule_changes(before, after)
        if reason_code or actor_id:
            old_start, new_start = _first_start_pair(before, after, job)
            record_move(
                job,
                DelayKind.RESCHEDULED,
                old_start,
                new_start,
                reason_code or "RESCHEDULED",
                moved_by_id=actor_id,
                note=note,
                changes=changes,
            )

        job.status = derive_job_status(job)
        job.needs_replan_reason = None
        db.session.commit()
        return get_job_order(job.id, job.created_by_id, UserRole.ADMIN.value)
    except AppError:
        db.session.rollback()
        raise
    except Exception:
        db.session.rollback()
        raise


def reschedule_released_job(job, data, actor_id: str):
    """Admin Reschedule: same apply rules, but a reason code is required."""
    reason = data.get("reason")
    note = (data.get("note") or "").strip() or None
    if reason not in RESCHEDULE_REASONS:
        raise AppError(
            "Choose a reschedule reason.",
            "VALIDATION_ERROR",
            400,
        )
    if reason == "OTHER" and not note:
        raise AppError(
            "A note is required when the reason is Other.",
            "VALIDATION_ERROR",
            400,
        )
    return apply_released_schedule(
        job,
        data.get("operations"),
        actor_id=actor_id,
        reason_code=reason,
        note=note,
    )


def list_schedule_moves(job_id: str) -> list:
    from app.models.schedule_move import ScheduleMove

    rows = (
        ScheduleMove.query.options(joinedload(ScheduleMove.moved_by))
        .filter_by(job_order_id=job_id)
        .order_by(ScheduleMove.moved_at.desc())
        .all()
    )
    return [row.to_dict() for row in rows]


def replan_after_redo(job_id, redo_id) -> bool:
    """Schedule a new redo on a released job right away: it goes directly after
    its original, the operations after it move later only as needed, every
    worker stays, nothing moves earlier and started work never moves. When it
    can't be placed within the horizon nothing moves; the job is marked Needs
    re-plan and the Admin is told. Commits; True when the redo was placed."""
    import logging

    from app.services.schedule_service import propose_schedule, schedule_problems

    try:
        lock_schedule()
        job = db.session.get(JobOrder, job_id)
        redo = db.session.get(JobOperation, redo_id)
        if job is None or redo is None or job.status == JobOrderStatus.DRAFT:
            db.session.rollback()
            return False
        reason = _place_redo(job, redo, propose_schedule, schedule_problems)
        if reason is None:
            job.needs_replan_reason = None
            db.session.commit()
            return True
    except Exception:
        db.session.rollback()
        logging.getLogger(__name__).exception("Re-plan after redo failed for job %s", job_id)
        reason = "the redo could not be placed automatically"
    _mark_needs_replan(job_id, reason)
    return False


def _place_redo(job, redo, propose_schedule, schedule_problems) -> str | None:
    """Apply the re-plan to the job's unstarted operations; the reason it can't
    be placed instead (nothing changed then)."""
    from app.services.schedule_calendar import ensure_utc

    ops = sorted(job.operations, key=lambda o: o.sequence_no or 0)
    proposal = propose_schedule(
        ops,
        job.due_date,
        exclude_job_id=job.id,
        anchor_utc=_next_quarter_hour(),
        lock_before_sequence=redo.sequence_no,
        never_earlier=True,
        honor_machine_pins=True,
        suggest_faster_units=False,
    )
    rows = proposal["operations"]
    failed = [r for r in rows if not r.get("scheduled")]
    if failed:
        return "; ".join(
            f"#{r['sequenceNo']} {r.get('operationName') or 'Operation'}: {r.get('message')}"
            for r in failed
        )
    by_id = {op.id: op for op in ops}
    for row in rows:
        op = by_id.get(row.get("id"))
        if op is None or op.status in _STARTED_OPERATION_STATUSES or op.actual_start is not None:
            continue
        start = _parse_datetime(row["scheduledStart"])
        if op.scheduled_start and ensure_utc(start) < ensure_utc(op.scheduled_start):
            db.session.rollback()
            return f"#{op.sequence_no} {op.operation_name} would have moved earlier"
        op.scheduled_start = start
        op.scheduled_end = _parse_datetime(row["scheduledEnd"])
        if row.get("machineUnitId"):
            op.machine_unit_id = row["machineUnitId"]
        if op.status == OperationStatus.PENDING and op.assigned_worker_id:
            op.status = OperationStatus.SCHEDULED
    db.session.flush()
    problems = schedule_problems(ops, exclude_job_id=job.id)
    if problems:
        db.session.rollback()
        return "; ".join(p["message"] for p in problems)
    return None


def _mark_needs_replan(job_id, reason: str) -> None:
    job = db.session.get(JobOrder, job_id)
    if job is None:
        return
    mark_needs_replan(job, reason, f"A redo could not be scheduled: {reason}")
    db.session.commit()


def mark_needs_replan(job: JobOrder, reason: str, message: str, dedupe_key: str | None = None) -> None:
    """The job shows "Needs re-plan" until the Admin re-plans it; the Admin is told. Does not commit."""
    from app.models.staff_alert import StaffAlertKind
    from app.services.staff_alert_service import raise_alert

    job.needs_replan_reason = reason
    raise_alert(
        roles=[UserRole.ADMIN],
        kind=StaffAlertKind.NEEDS_REPLAN,
        title=f"{job.job_number} needs re-plan",
        message=message,
        job_order_id=job.id,
        dedupe_key=dedupe_key,
    )

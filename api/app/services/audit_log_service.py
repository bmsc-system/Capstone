"""Read side of the audit trail: the Admin's Audit Log page and a job's History.

Rows are written by ``audit_service`` and are never edited or deleted here.
This module turns them into plain sentences: who, did what, to which record,
what changed (old → new), when, and from which address.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from sqlalchemy import or_

from app.models.audit_log import AuditLog
from app.models.client import Client
from app.models.job_order import JobOrder
from app.models.machine import MachineType, MachineUnit
from app.models.material_catalog import MaterialCatalogItem
from app.models.material_purchase import MaterialPurchase
from app.models.operation import JobOperation
from app.models.operation_time import MachineDowntime, OperationTimeLog
from app.models.sales_invoice import SalesInvoice
from app.models.supplier import Supplier
from app.models.supplier_order import SupplierOrder
from app.models.tool import Tool
from app.models.tool_type import ToolType, ToolUnit
from app.models.user import User
from app.models.worker_skill import OperationType
from app.services.schedule_calendar import SHOP_TZ
from app.utils.errors import AppError

MAX_PAGE_SIZE = 100

SIGN_IN_ACTIONS = (
    "LOGIN_SUCCEEDED",
    "LOGIN_FAILED",
    "LOGIN_BLOCKED",
    "LOGOUT",
    "PASSWORD_CHANGED",
    "PASSWORD_SET",
    "PASSWORD_RESET_REQUESTED",
    "PASSWORD_RESET_COMPLETED",
    "PIN_SET",
    "PIN_FAILED",
    "PIN_REMOVED",
    "DEVICE_REVOKED",
    "INVITATION_SENT",
    "INVITATION_USED",
    "INVITATION_REVOKED",
)
CHANGE_ACTIONS = ("CREATE", "UPDATE", "DELETE")

CATEGORIES = {
    "SIGN_IN": "Sign-in and security",
    "CHANGES": "Record changes",
    "ACTIONS": "Other actions",
}

ACTION_LABELS = {
    "CREATE": "Created",
    "UPDATE": "Changed",
    "DELETE": "Deleted",
    "LOGIN_SUCCEEDED": "Signed in",
    "LOGIN_FAILED": "Failed sign-in",
    "LOGIN_BLOCKED": "Sign-in blocked",
    "LOGOUT": "Signed out",
    "PASSWORD_CHANGED": "Changed password",
    "PASSWORD_SET": "Set password",
    "PASSWORD_RESET_REQUESTED": "Asked for a password reset",
    "PASSWORD_RESET_COMPLETED": "Reset password",
    "PIN_SET": "Set a PIN",
    "PIN_FAILED": "Wrong PIN",
    "PIN_REMOVED": "Removed a PIN",
    "DEVICE_REVOKED": "Signed out a device",
    "INVITATION_SENT": "Sent an invitation",
    "INVITATION_USED": "Accepted an invitation",
    "INVITATION_REVOKED": "Cancelled an invitation",
    "SALES_INVOICE_CORRECTED": "Corrected a sales invoice",
    "SHOP_BREAK_CHANGED": "Changed the shop break",
    "EXPECTED_DELIVERY_CHANGED": "Changed an expected delivery date",
    "SUPPLIER_ORDER_PRINTED": "Printed a supplier order",
}

ENTITY_LABELS = {
    "JobOrder": "Job order",
    "JobOperation": "Operation",
    "OperationHelper": "Operation helpers",
    "OperationTimeLog": "Work time entry",
    "MachineDowntime": "Machine breakdown",
    "User": "User account",
    "UserDevice": "Device",
    "UserInvitation": "Invitation",
    "Client": "Client",
    "Supplier": "Supplier",
    "SupplierOrder": "Supplier order",
    "MaterialPurchase": "Material purchase",
    "SalesInvoice": "Sales invoice",
    "Tool": "Consumable",
    "ConsumablePurchase": "Consumable purchase",
    "ConsumableRequest": "Running low report",
    "ToolType": "Tool type",
    "ToolUnit": "Tool",
    "Stocktake": "Stock count",
    "AttendanceRecord": "Attendance",
    "NotificationLog": "Client email",
    "WorkCalendarException": "Shop calendar",
    "MachineType": "Machine type",
    "MachineUnit": "Machine",
    "WorkerProfile": "Worker profile",
    "WorkerSkill": "Worker skill",
    "WorkerSchedule": "Work hours",
    "ShopSettings": "Shop settings",
    "ScoringWeight": "Recommendation weights",
    "MaterialCatalogItem": "Material catalog",
}

REASON_LABELS = {
    "UNKNOWN_ACCOUNT": "No account with that email or mobile",
    "ACCOUNT_NOT_ACTIVE": "Account is disabled or not yet set up",
    "NO_PASSWORD_SET": "Account has no password yet",
    "WRONG_PASSWORD": "Wrong password",
    "TOO_MANY_ATTEMPTS": "Too many attempts from this address",
    "PASSWORD": "Password",
    "PIN": "PIN",
    "INVITATION": "Invitation link",
    "PASSWORD_RESET": "Password reset link",
}

# Never shown, even as a changed field: ids repeat the record, and these are
# bookkeeping.
_SKIP_FIELDS = {"id", "created_at", "createdAt", "updated_at", "updatedAt"}
_FIELD_LABELS = {
    "assigned_worker_id": "Lead worker",
    "default_operator_id": "Default operator",
    "machine_unit_id": "Machine",
    "machine_type_id": "Machine type",
    "operation_type_id": "Operation type",
    "client_id": "Client",
    "supplier_id": "Supplier",
    "job_order_id": "Job order",
    "worker_id": "Worker",
    "user_id": "User",
    "userId": "User",
    "via": "Signed in with",
    "reason": "Reason",
    "identifier": "Email or mobile entered",
    "device": "Device",
    "amount": "Amount",
    "status": "Status",
    "due_date": "Due date",
    "client_po_number": "Client PO number",
}
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_JOB_NUMBER_RE = re.compile(r"^JO-\d{4}-([0-9A-Za-z]{1,8})$")


def _category(action: str) -> str:
    if action in SIGN_IN_ACTIONS:
        return "SIGN_IN"
    if action in CHANGE_ACTIONS:
        return "CHANGES"
    return "ACTIONS"


def _humanize(key: str) -> str:
    if key in _FIELD_LABELS:
        return _FIELD_LABELS[key]
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key).replace("_", " ").strip()
    if words.lower().endswith(" id"):
        words = words[:-3]
    return words[:1].upper() + words[1:].lower()


def _action_label(action: str) -> str:
    return ACTION_LABELS.get(action) or _humanize(action.lower())


# ---------------------------------------------------------------- names


def _names_for(ids: set[str]) -> dict[str, str]:
    """Readable names for ids that appear in rows or changed values."""
    ids = {i for i in ids if i and _UUID_RE.match(i)}
    if not ids:
        return {}
    out: dict[str, str] = {}
    lookups = (
        (User, lambda r: r.full_name),
        (Client, lambda r: r.name),
        (Supplier, lambda r: r.name),
        (MachineUnit, lambda r: r.label),
        (MachineType, lambda r: r.name),
        (OperationType, lambda r: r.name),
        (JobOrder, lambda r: f"{r.job_number} · {r.title}"),
        (SupplierOrder, lambda r: r.po_number or "Draft supplier order"),
        (Tool, lambda r: r.name),
        (ToolType, lambda r: r.name),
        (ToolUnit, lambda r: r.asset_code),
        (MaterialCatalogItem, lambda r: r.name),
    )
    remaining = set(ids)
    for model, label in lookups:
        if not remaining:
            break
        for row in model.query.filter(model.id.in_(list(remaining))).all():
            out[row.id] = label(row)
        remaining -= set(out)
    if remaining:
        ops = JobOperation.query.filter(JobOperation.id.in_(list(remaining))).all()
        job_ids = {op.job_order_id for op in ops}
        jobs = {j.id: j for j in JobOrder.query.filter(JobOrder.id.in_(list(job_ids))).all()}
        for op in ops:
            job = jobs.get(op.job_order_id)
            prefix = f"{job.job_number} · " if job else ""
            out[op.id] = f"{prefix}Op {op.sequence_no} {op.operation_name}"
    return out


def _record_label(row: AuditLog, names: dict[str, str]) -> str | None:
    snapshot = row.after_json or row.before_json or {}
    if row.entity_id and row.entity_id in names:
        return names[row.entity_id]
    if row.entity_type == "MachineDowntime" and isinstance(snapshot, dict):
        unit = snapshot.get("machineUnitLabel") or names.get(
            snapshot.get("machineUnitId") or snapshot.get("machine_unit_id") or ""
        )
        if unit:
            return str(unit)
    for key in ("jobNumber", "poNumber", "name", "fullName", "invoiceNumber", "materialName", "label"):
        if isinstance(snapshot, dict) and snapshot.get(key):
            return str(snapshot[key])
    if row.entity_type in ("MaterialPurchase", "SalesInvoice", "MachineDowntime", "OperationTimeLog"):
        job_id = (snapshot or {}).get("job_order_id") or (snapshot or {}).get("jobOrderId")
        op_id = (snapshot or {}).get("operation_id") or (snapshot or {}).get("operationId")
        if job_id in names:
            return names[job_id]
        if op_id in names:
            return names[op_id]
    return None


def _link(row: AuditLog, snapshot: dict) -> str | None:
    t, eid = row.entity_type, row.entity_id
    if not eid or row.action in SIGN_IN_ACTIONS:
        return None
    if t == "JobOrder":
        return f"/job-orders/{eid}"
    if t == "SupplierOrder":
        return f"/supplier-orders/{eid}"
    job_id = snapshot.get("job_order_id") or snapshot.get("jobOrderId")
    if job_id:
        return f"/job-orders/{job_id}"
    if t in ("User", "WorkerProfile"):
        return "/users"
    if t in ("MachineUnit", "MachineType", "MachineDowntime"):
        return "/machines"
    if t in ("Tool", "ToolType", "ToolUnit", "Stocktake"):
        return "/tools"
    if t == "Client":
        return "/clients"
    if t == "Supplier":
        return "/suppliers"
    return None


# ---------------------------------------------------------------- details


def _details(row: AuditLog, names: dict[str, str]) -> list[dict]:
    """[{label, from, to}] for a change; [{label, value}] for anything else."""
    before = row.before_json if isinstance(row.before_json, dict) else {}
    after = row.after_json if isinstance(row.after_json, dict) else {}

    def show(key, value):
        if isinstance(value, str) and value in names:
            return names[value]
        if key in ("reason", "via") and isinstance(value, str):
            return REASON_LABELS.get(value, value)
        return value

    if row.action == "UPDATE" and not before:
        # Older entries kept only the record as saved, not what changed.
        if "id" in after:
            return []
        return [
            {"label": _humanize(k), "value": show(k, v)}
            for k, v in after.items()
            if k not in _SKIP_FIELDS
        ]
    if row.action == "UPDATE" or (before and after):
        keys = [k for k in after if k not in _SKIP_FIELDS] + [
            k for k in before if k not in after and k not in _SKIP_FIELDS
        ]
        return [
            {"label": _humanize(k), "from": show(k, before.get(k)), "to": show(k, after.get(k))}
            for k in keys
        ]
    data = after or before
    items = []
    for k, v in data.items():
        if k in _SKIP_FIELDS or v in (None, "", [], {}):
            continue
        if isinstance(v, (dict, list)):
            continue
        if k.endswith("_id") or k.endswith("Id"):
            if not (isinstance(v, str) and v in names):
                continue
        items.append({"label": _humanize(k), "value": show(k, v)})
    return items[:14]


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def _summary(row: AuditLog, user_name: str | None, record: str | None, details: list[dict]) -> str:
    # No user on a change means the system did it (scheduled checks, imports).
    who = user_name or ("Someone" if row.action in SIGN_IN_ACTIONS else "The system")
    entity = ENTITY_LABELS.get(row.entity_type, _humanize(row.entity_type))
    target = f"{entity} {record}" if record else entity
    after = row.after_json if isinstance(row.after_json, dict) else {}
    before = row.before_json if isinstance(row.before_json, dict) else {}
    if row.action == "CREATE":
        return f"{who} created {target[0].lower() + target[1:]}"
    if row.action == "DELETE":
        return f"{who} deleted {target[0].lower() + target[1:]}"
    if row.action == "UPDATE" and not before and "id" in after:
        return f"{who} updated {_lower_first(target)} (older entry: the changed fields were not kept)"
    if row.action == "UPDATE":
        fields = ", ".join(d["label"].lower() for d in details[:4])
        more = "" if len(details) <= 4 else f" and {len(details) - 4} more"
        return f"{who} changed {fields}{more} on {target[0].lower() + target[1:]}"
    if row.action == "LOGIN_SUCCEEDED":
        via = REASON_LABELS.get(after.get("via"), "password").lower()
        return f"{who} signed in with {via}"
    if row.action == "LOGIN_FAILED":
        reason = REASON_LABELS.get(after.get("reason"), "failed")
        entered = after.get("identifier")
        if user_name:
            return f"Failed sign-in for {user_name}: {reason.lower()}"
        return f"Failed sign-in for {entered or 'an unknown account'}: {reason.lower()}"
    if row.action == "LOGIN_BLOCKED":
        return "Sign-in blocked after too many attempts from this address"
    if row.action == "LOGOUT":
        return f"{who} signed out"
    return f"{who} {_lower_first(_action_label(row.action))}" + (f": {record}" if record else "")


# ---------------------------------------------------------------- queries


def _shop_day_start_utc(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=SHOP_TZ)


def _parse_date(value: str | None, field: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AppError(f"{field} must be a date (YYYY-MM-DD)", "VALIDATION_ERROR", 400) from exc


def _search_ids(q: str) -> tuple[set[str], set[str]]:
    """(entity ids, user ids) matching a search: names, job and PO numbers."""
    q = q.strip()
    like = f"%{q}%"
    entity_ids: set[str] = set()
    user_ids = {u.id for u in User.query.filter(User.full_name.ilike(like)).limit(200)}
    entity_ids |= user_ids
    entity_ids |= {c.id for c in Client.query.filter(Client.name.ilike(like)).limit(200)}
    entity_ids |= {s.id for s in Supplier.query.filter(Supplier.name.ilike(like)).limit(200)}
    entity_ids |= {
        o.id for o in SupplierOrder.query.filter(SupplierOrder.po_number.ilike(like)).limit(200)
    }
    entity_ids |= {j.id for j in JobOrder.query.filter(JobOrder.title.ilike(like)).limit(200)}
    m = _JOB_NUMBER_RE.match(q.upper())
    if m:
        prefix = m.group(1).lower()
        entity_ids |= {
            j.id for j in JobOrder.query.filter(JobOrder.id.ilike(f"{prefix}%")).limit(50)
        }
    return entity_ids, user_ids


def _serialize(rows: list[AuditLog]) -> list[dict]:
    ids: set[str] = set()
    for r in rows:
        ids.add(r.user_id or "")
        ids.add(r.entity_id or "")
        for blob in (r.before_json, r.after_json):
            if isinstance(blob, dict):
                ids |= {v for v in blob.values() if isinstance(v, str)}
    names = _names_for(ids)
    users = {u.id: u for u in User.query.filter(User.id.in_([r.user_id for r in rows if r.user_id])).all()}

    items = []
    for r in rows:
        user = users.get(r.user_id)
        snapshot = (r.after_json if isinstance(r.after_json, dict) else None) or (
            r.before_json if isinstance(r.before_json, dict) else {}
        )
        record = _record_label(r, names)
        details = _details(r, names)
        items.append(
            {
                "id": r.id,
                "createdAt": r.created_at.isoformat() if r.created_at else None,
                "action": r.action,
                "actionLabel": _action_label(r.action),
                "category": _category(r.action),
                "user": (
                    {
                        "id": user.id,
                        "name": user.full_name,
                        "role": r.user_role or user.role.value,
                    }
                    if user
                    else None
                ),
                "entityType": r.entity_type,
                "entityLabel": ENTITY_LABELS.get(r.entity_type, _humanize(r.entity_type)),
                "entityId": r.entity_id,
                "recordLabel": record,
                "link": _link(r, snapshot or {}),
                "summary": _summary(r, user.full_name if user else None, record, details),
                "details": details,
                "ipAddress": r.ip_address,
            }
        )
    return items


def list_audit_logs(args) -> dict:
    page = max(int(args.get("page") or 1), 1)
    page_size = min(max(int(args.get("pageSize") or 25), 1), MAX_PAGE_SIZE)
    query = AuditLog.query

    category = args.get("category")
    if category == "SIGN_IN":
        query = query.filter(AuditLog.action.in_(SIGN_IN_ACTIONS))
    elif category == "CHANGES":
        query = query.filter(AuditLog.action.in_(CHANGE_ACTIONS))
    elif category == "ACTIONS":
        query = query.filter(~AuditLog.action.in_(SIGN_IN_ACTIONS + CHANGE_ACTIONS))
    elif category:
        raise AppError("Unknown category", "VALIDATION_ERROR", 400)

    if args.get("action"):
        query = query.filter(AuditLog.action == args["action"])
    if args.get("userId"):
        query = query.filter(AuditLog.user_id == args["userId"])
    if args.get("entityType"):
        query = query.filter(AuditLog.entity_type == args["entityType"])
    if args.get("entityId"):
        query = query.filter(AuditLog.entity_id == args["entityId"])

    start = _parse_date(args.get("from"), "from")
    end = _parse_date(args.get("to"), "to")
    if start and end and end < start:
        raise AppError("The end date is before the start date", "VALIDATION_ERROR", 400)
    if start:
        query = query.filter(AuditLog.created_at >= _shop_day_start_utc(start))
    if end:
        query = query.filter(AuditLog.created_at < _shop_day_start_utc(end + timedelta(days=1)))

    q = (args.get("q") or "").strip()
    if q:
        entity_ids, user_ids = _search_ids(q)
        conds = [AuditLog.ip_address == q]
        if entity_ids:
            conds.append(AuditLog.entity_id.in_(list(entity_ids)))
        if user_ids:
            conds.append(AuditLog.user_id.in_(list(user_ids)))
        query = query.filter(or_(*conds))

    total = query.count()
    rows = (
        query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {"items": _serialize(rows), "total": total, "page": page, "pageSize": page_size}


def audit_log_options() -> dict:
    """Choices for the filters: people who appear in the log, and record types."""
    user_ids = [u for (u,) in AuditLog.query.with_entities(AuditLog.user_id).distinct() if u]
    users = (
        User.query.filter(User.id.in_(user_ids)).order_by(User.full_name).all() if user_ids else []
    )
    types = sorted(
        {t for (t,) in AuditLog.query.with_entities(AuditLog.entity_type).distinct() if t},
        key=lambda t: ENTITY_LABELS.get(t, t),
    )
    return {
        "users": [{"id": u.id, "name": u.full_name, "role": u.role.value} for u in users],
        "entityTypes": [{"value": t, "label": ENTITY_LABELS.get(t, _humanize(t))} for t in types],
        "categories": [{"value": k, "label": v} for k, v in CATEGORIES.items()],
    }


def job_order_history(job_id: str, limit: int = 300) -> dict:
    """Everything the log holds about one job: the job, its operations and
    helpers, work time entries, breakdowns, material purchases, and invoice."""
    job = JobOrder.query.get(job_id)
    if not job:
        raise AppError("Job order not found", "NOT_FOUND", 404)
    op_ids = [i for (i,) in JobOperation.query.with_entities(JobOperation.id).filter_by(job_order_id=job_id)]
    related = {job_id, *op_ids}
    related |= {
        i for (i,) in OperationTimeLog.query.with_entities(OperationTimeLog.id).filter(
            OperationTimeLog.operation_id.in_(op_ids or [""])
        )
    }
    related |= {
        i for (i,) in MachineDowntime.query.with_entities(MachineDowntime.id).filter(
            MachineDowntime.job_order_id == job_id
        )
    }
    related |= {
        i for (i,) in MaterialPurchase.query.with_entities(MaterialPurchase.id).filter(
            MaterialPurchase.job_order_id == job_id
        )
    }
    related |= {
        i for (i,) in SalesInvoice.query.with_entities(SalesInvoice.id).filter(
            SalesInvoice.job_order_id == job_id
        )
    }
    rows = (
        AuditLog.query.filter(AuditLog.entity_id.in_(list(related)))
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
        .all()
    )
    return {"items": _serialize(rows)}

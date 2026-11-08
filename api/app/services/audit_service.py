"""SQLAlchemy audit listeners for production, user, inventory, and purchasing records."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from flask import has_request_context, request
from flask_jwt_extended import get_jwt, get_jwt_identity, verify_jwt_in_request
from sqlalchemy import event, insert, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.extensions import db
from app.models.attendance import AttendanceRecord
from app.models.audit_log import AuditLog
from app.models.client import Client
from app.models.job_order import JobOrder
from app.models.machine import MachineType, MachineUnit
from app.models.material_catalog import MaterialCatalogItem
from app.models.consumable_purchase import ConsumablePurchase
from app.models.consumable_request import ConsumableRequest
from app.models.material_purchase import MaterialPurchase
from app.models.notification import NotificationLog
from app.models.operation import JobOperation, OperationHelper
from app.models.operation_time import MachineDowntime, OperationTimeLog
from app.models.sales_invoice import SalesInvoice
from app.models.scoring_weight import ScoringWeight
from app.models.shop_settings import ShopSettings
from app.models.stocktake import Stocktake
from app.models.supplier import Supplier
from app.models.supplier_order import SupplierOrder
from app.models.tool import Tool
from app.models.tool_type import ToolType, ToolUnit
from app.models.user import User
from app.models.worker_profile import WorkerProfile
from app.models.worker_skill import WorkCalendarException, WorkerSchedule, WorkerSkill

_COLUMN_SNAPSHOT_MODELS = (
    AttendanceRecord,
    Supplier,
    SupplierOrder,
    MaterialPurchase,
    ConsumablePurchase,
    ConsumableRequest,
    SalesInvoice,
    ToolType,
    ToolUnit,
    Stocktake,
    MachineType,
    MachineUnit,
    WorkerProfile,
    WorkerSkill,
    WorkerSchedule,
    ShopSettings,
    ScoringWeight,
    MaterialCatalogItem,
    OperationHelper,
)

_REGISTERED = False
_PENDING_KEY = "pending_audit_logs"


def _utcnow():
    return datetime.now(timezone.utc)


def _json_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    return str(value)


def _column_dict(obj):
    """Stored columns only, so the snapshot never lazy-loads relationships mid-flush."""
    return {
        attr.key: _json_value(getattr(obj, attr.key, None))
        for attr in sa_inspect(obj).mapper.column_attrs
    }


def _safe_dict(obj):
    if isinstance(obj, _COLUMN_SNAPSHOT_MODELS):
        return _column_dict(obj)
    try:
        if hasattr(obj, "to_dict"):
            # Prefer richer dicts when available
            try:
                return obj.to_dict(include_operations=False)
            except TypeError:
                try:
                    return obj.to_dict(include_custody=False)
                except TypeError:
                    return obj.to_dict()
    except Exception:
        pass
    return {"id": getattr(obj, "id", None)}


def _actor():
    user_id = None
    user_role = None
    ip = None
    if has_request_context():
        # ProxyFix (TRUSTED_PROXY_COUNT) puts the real client in remote_addr;
        # a raw X-Forwarded-For header can be typed by anyone.
        ip = request.remote_addr
        try:
            verify_jwt_in_request(optional=True)
            user_id = get_jwt_identity()
            claims = get_jwt() or {}
            user_role = claims.get("role")
        except Exception:
            pass
    return user_id, user_role, ip


def _row_values(action, entity_type, entity_id, before=None, after=None, actor=None):
    user_id, user_role, ip = _actor()
    if actor is not None:
        # Sign-in events happen before the request carries a token.
        user_id = actor.id
        user_role = getattr(actor.role, "value", actor.role)
    return {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "user_role": user_role,
        "action": action,
        "entity_type": entity_type,
        "entity_id": str(entity_id) if entity_id else None,
        "before_json": before,
        "after_json": after,
        "ip_address": ip,
        "created_at": _utcnow(),
    }


def _insert_audit(connection, values: dict) -> None:
    """Write an audit row on the given connection (same transaction, no Session.add)."""
    connection.execute(insert(AuditLog.__table__), values)


def _log(action, entity_type, entity_id, before=None, after=None, actor=None):
    """Explicit / non-mapper audit writes.

    If the Session is mid-flush, queue the row and insert after flush completes.
    Otherwise insert immediately via the current connection.
    """
    values = _row_values(action, entity_type, entity_id, before=before, after=after, actor=actor)
    session = db.session
    if getattr(session, "_flushing", False):
        session.info.setdefault(_PENDING_KEY, []).append(values)
        return
    _insert_audit(session.connection(), values)


def write_audit_event(action, entity_type, entity_id=None, before=None, after=None, actor=None):
    """Explicit security/audit events (invitation, PIN, password, etc.).

    ``actor`` names the user when the request has no token yet (sign-in).
    """
    _log(action, entity_type, entity_id, before=before, after=after, actor=actor)


def _entity_type(obj):
    return obj.__class__.__name__


def _entity_id(obj):
    # A helper row has no id of its own; file it under its operation so it
    # shows in that job's history.
    if isinstance(obj, OperationHelper):
        return obj.operation_id
    return getattr(obj, "id", None)


# Never copied into the log: secrets are only noted as "changed".
_SECRET_MARKERS = ("password", "pin_hash", "pinhash", "token", "secret")
_HIDDEN = "[hidden]"
# Bookkeeping columns that change on every save and say nothing to a reader.
_IGNORED_COLUMNS = {"updated_at", "updatedAt"}


def _is_secret(key: str) -> bool:
    k = key.lower()
    return any(m in k for m in _SECRET_MARKERS)


def _redact(data):
    if not isinstance(data, dict):
        return data
    return {k: (_HIDDEN if _is_secret(k) and v is not None else v) for k, v in data.items()}


def _changes(mapper, connection, target):
    """Old and new values of the stored columns this flush changes.

    Runs before the UPDATE. After a commit the old values are no longer in
    memory, so any that are missing are read from the row as it still stands.
    """
    state = sa_inspect(target)
    changed = []
    missing = []
    for attr in state.mapper.column_attrs:
        key = attr.key
        if key in _IGNORED_COLUMNS:
            continue
        hist = state.attrs[key].history
        if not hist.added:
            continue
        changed.append((attr, hist))
        if not hist.deleted:
            missing.append(attr)

    stored = {}
    if missing:
        pk_cols = mapper.primary_key
        pk_vals = mapper.primary_key_from_instance(target)
        if state.key is not None:  # persistent: use the identity it was loaded with
            pk_vals = list(state.key[1])
        cond = [c == v for c, v in zip(pk_cols, pk_vals)]
        cols = [a.columns[0] for a in missing]
        row = connection.execute(select(*cols).where(*cond)).first()
        if row is not None:
            stored = {a.key: row[i] for i, a in enumerate(missing)}

    before, after = {}, {}
    for attr, hist in changed:
        key = attr.key
        old = hist.deleted[0] if hist.deleted else stored.get(key)
        new = hist.added[0]
        old, new = _json_value(old), _json_value(new)
        if old == new:
            continue
        if _is_secret(key):
            old, new = (_HIDDEN if old is not None else None), (_HIDDEN if new is not None else None)
        before[key] = old
        after[key] = new
    return before, after


def _after_insert(mapper, connection, target):
    _insert_audit(
        connection,
        _row_values(
            "CREATE",
            _entity_type(target),
            _entity_id(target),
            before=None,
            after=_redact(_safe_dict(target)),
        ),
    )


def _before_update(mapper, connection, target):
    before, after = _changes(mapper, connection, target)
    if not after:
        return  # saved without a real change (only timestamps or relationships)
    _insert_audit(
        connection,
        _row_values(
            "UPDATE",
            _entity_type(target),
            _entity_id(target),
            before=before,
            after=after,
        ),
    )


def _after_delete(mapper, connection, target):
    _insert_audit(
        connection,
        _row_values(
            "DELETE",
            _entity_type(target),
            _entity_id(target),
            before=_redact(_safe_dict(target)),
            after=None,
        ),
    )


def _after_flush_postexec(session, flush_context):
    pending = session.info.pop(_PENDING_KEY, None)
    if not pending:
        return
    connection = session.connection()
    for values in pending:
        _insert_audit(connection, values)


def register_audit_listeners():
    global _REGISTERED
    if _REGISTERED:
        return

    for model in (
        JobOrder,
        JobOperation,
        User,
        Tool,
        OperationTimeLog,
        MachineDowntime,
        Client,
        NotificationLog,
        WorkCalendarException,
        *_COLUMN_SNAPSHOT_MODELS,
    ):
        event.listen(model, "after_insert", _after_insert)
        event.listen(model, "before_update", _before_update)
        event.listen(model, "after_delete", _after_delete)

    event.listen(Session, "after_flush_postexec", _after_flush_postexec)
    _REGISTERED = True

"""Evidence behind a worker's skill levels, and a suggested level per machine.

The Administrator still sets every level. This only shows what the shop's own
records say, so a rating has a basis besides memory:

* how many operations the worker finished on each machine type in the last
  12 months, as the lead and as a helper, and when he last did one;
* speed against peers: for each of his operations, the hours his peers
  usually take for the same operation type (their target hours over their
  actual hours) are compared with the hours he took, so generous targets or
  an easy job mix do not make him look better than he is;
* how often he finishes within 10% of target or faster;
* redo caused by the operator: completed operations he led that were sent
  back for operator error, out-of-tolerance dimensions, or surface finish.
  Machine faults and wrong material are not his doing and are left out.

Suggested level (never applied by itself):

  fewer than 5 operations as lead  -> no suggestion, except 1 when he has only
                                      helped on that machine (5 or more times)
  volume sets the ceiling           -> 5+ lead ops: up to 3, 15+: up to 4,
                                      30+: up to 5
  quality caps it                   -> operator redo above 10%: at most 2
  speed against peers places it     -> 95% or more of peers' speed: the
                                      ceiling; 85-95%: one below; slower: 2

The on-target rate is shown for information only: it depends on how tight
the Administrator set each target, which the comparison with peers does not.

A level 2 or more away from the suggestion, or a skill unused for six months,
is flagged for the Administrator to review.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.machine import MachineType
from app.models.operation import (
    JobOperation,
    OperationHelper,
    OperationStatus,
    ReworkReasonCategory,
)
from app.models.user import User
from app.models.worker_skill import WorkerSkill
from app.services.worker_profile_service import get_worker_or_404

WINDOW_DAYS = 365
MIN_LEAD_OPS = 5
HELPER_ONLY_MIN = 5
CEILINGS = ((30, 5), (15, 4), (MIN_LEAD_OPS, 3))
REDO_CAP_PCT = 10.0
REDO_CAP_LEVEL = 2
ON_TARGET_BAND = 1.10
STALE_DAYS = 180
MIN_PEER_OPS = 3

OPERATOR_REDO = (
    ReworkReasonCategory.OPERATOR_ERROR,
    ReworkReasonCategory.DIMENSION_OUT_OF_TOLERANCE,
    ReworkReasonCategory.SURFACE_FINISH,
)


def _actual_hours(op: JobOperation) -> float | None:
    if op.actual_worked_hours is not None:
        return float(op.actual_worked_hours)
    if op.actual_start and op.actual_end:
        return (op.actual_end - op.actual_start).total_seconds() / 3600.0
    return None


def _pair(op: JobOperation) -> tuple[float, float] | None:
    target = float(op.estimated_hours) if op.estimated_hours is not None else None
    actual = _actual_hours(op)
    if not target or not actual or actual <= 0:
        return None
    return target, actual


def _iso(dt):
    return dt.isoformat() if dt else None


def suggest_level(
    lead_ops: int,
    helper_ops: int,
    speed_pct: float | None,
    redo_pct: float | None,
) -> tuple[int | None, str]:
    """(suggested level or None, plain reason)."""
    if lead_ops < MIN_LEAD_OPS:
        if lead_ops == 0 and helper_ops >= HELPER_ONLY_MIN:
            return 1, f"Has only helped on this machine ({helper_ops} operations)"
        return None, f"Not enough history: has led {lead_ops} of the {MIN_LEAD_OPS} operations needed"

    ceiling = next(level for need, level in CEILINGS if lead_ops >= need)
    if redo_pct is not None and redo_pct > REDO_CAP_PCT:
        return REDO_CAP_LEVEL, f"{redo_pct:.0f}% of his operations needed redo for his own errors"

    if speed_pct is None or speed_pct >= 95:
        level = ceiling
    elif speed_pct >= 85:
        level = max(2, ceiling - 1)
    else:
        level = 2
    reason = f"Led {lead_ops} operations"
    if speed_pct is not None:
        if speed_pct >= 110:
            reason += ", faster than others"
        elif speed_pct <= 90:
            reason += ", slower than others"
        else:
            reason += ", same pace as others"
    return level, reason


def _peer_ratios(op_type_ids, worker_id, since):
    """{operation type id: peers' total target hours / total actual hours}."""
    if not op_type_ids:
        return {}
    rows = (
        JobOperation.query.filter(
            JobOperation.status == OperationStatus.COMPLETED,
            JobOperation.operation_type_id.in_(op_type_ids),
            JobOperation.assigned_worker_id.isnot(None),
            JobOperation.assigned_worker_id != worker_id,
            JobOperation.actual_end >= since,
        )
        .all()
    )
    sums = defaultdict(lambda: [0.0, 0.0, 0])
    for op in rows:
        pair = _pair(op)
        if not pair:
            continue
        s = sums[op.operation_type_id]
        s[0] += pair[0]
        s[1] += pair[1]
        s[2] += 1
    return {k: v[0] / v[1] for k, v in sums.items() if v[2] >= MIN_PEER_OPS and v[1] > 0}


def _last_reviewed(worker_id):
    """When the worker's skills were last saved, and by whom."""
    row = (
        AuditLog.query.filter(
            AuditLog.entity_type == WorkerSkill.__name__,
            AuditLog.action == "CREATE",
            AuditLog.after_json["worker_id"].astext == worker_id,
        )
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    if not row:
        return None
    by = db.session.get(User, row.user_id) if row.user_id else None
    return {"at": _iso(row.created_at), "byName": by.full_name if by else None}


def worker_skill_evidence(worker_id: str, now: datetime | None = None) -> dict:
    get_worker_or_404(worker_id)
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=WINDOW_DAYS)

    ops = (
        JobOperation.query.filter(
            JobOperation.status == OperationStatus.COMPLETED,
            JobOperation.machine_type_id.isnot(None),
            JobOperation.actual_end >= since,
            JobOperation.crew_includes(worker_id),
        )
        .all()
    )
    lead = [op for op in ops if op.assigned_worker_id == worker_id]
    helped = [op for op in ops if op.assigned_worker_id != worker_id]

    redone: set[str] = set()
    if lead:
        redo_rows = (
            db.session.query(JobOperation.rework_of_operation_id)
            .filter(
                JobOperation.rework_of_operation_id.in_([op.id for op in lead]),
                JobOperation.rework_reason_category.in_(OPERATOR_REDO),
            )
            .all()
        )
        redone = {r[0] for r in redo_rows}

    peers = _peer_ratios({op.operation_type_id for op in lead if op.operation_type_id}, worker_id, since)
    skills = {s.machine_type_id: s for s in WorkerSkill.query.filter_by(worker_id=worker_id).all()}

    by_type: dict[str, dict] = defaultdict(lambda: {"lead": [], "helped": []})
    for op in lead:
        by_type[op.machine_type_id]["lead"].append(op)
    for op in helped:
        by_type[op.machine_type_id]["helped"].append(op)

    out = []
    for mt in MachineType.query.order_by(MachineType.name).all():
        group = by_type.get(mt.id, {"lead": [], "helped": []})
        lead_ops, helper_ops = group["lead"], group["helped"]
        skill = skills.get(mt.id)
        if not lead_ops and not helper_ops and not skill:
            continue

        pairs = [(op, p) for op in lead_ops if (p := _pair(op))]
        target_sum = sum(p[0] for _, p in pairs)
        actual_sum = sum(p[1] for _, p in pairs)
        efficiency = target_sum / actual_sum * 100 if actual_sum else None
        on_target = (
            sum(1 for _, (t, a) in pairs if a <= t * ON_TARGET_BAND) / len(pairs) * 100
            if pairs
            else None
        )
        # Hours peers would usually take for the same operations, over his hours.
        expected = actual = 0.0
        for op, (t, a) in pairs:
            ratio = peers.get(op.operation_type_id)
            if ratio:
                expected += t / ratio
                actual += a
        speed = expected / actual * 100 if actual else None

        redo_count = sum(1 for op in lead_ops if op.id in redone)
        redo_pct = redo_count / len(lead_ops) * 100 if lead_ops else None
        last_done = max((op.actual_end for op in lead_ops + helper_ops if op.actual_end), default=None)

        # Judge on the rounded figures the Administrator sees.
        level, reason = suggest_level(
            len(lead_ops),
            len(helper_ops),
            round(speed) if speed is not None else None,
            round(redo_pct, 1) if redo_pct is not None else None,
        )

        flags = []
        rated = skill.proficiency if skill else None
        if rated is not None and level is not None and abs(rated - level) >= 2:
            direction = "higher" if level > rated else "lower"
            flags.append(
                {
                    "kind": "mismatch",
                    "text": f"Rated {rated}, but the record suggests {level}. Consider a {direction} level.",
                }
            )
        if skill:
            if last_done is None:
                flags.append({"kind": "unused", "text": "No finished operation on this machine in the last 12 months."})
            elif (now - last_done).days > STALE_DAYS:
                flags.append({"kind": "unused", "text": "Not used in over 6 months."})

        out.append(
            {
                "machineTypeId": mt.id,
                "machineTypeName": mt.name,
                "rated": rated,
                "leadOps": len(lead_ops),
                "helperOps": len(helper_ops),
                "lastDoneAt": _iso(last_done),
                "efficiencyPct": round(efficiency, 1) if efficiency is not None else None,
                "speedVsPeersPct": round(speed, 1) if speed is not None else None,
                "onTargetPct": round(on_target, 1) if on_target is not None else None,
                "operatorRedos": redo_count,
                "operatorRedoPct": round(redo_pct, 1) if redo_pct is not None else None,
                "suggestedLevel": level,
                "suggestionReason": reason,
                "flags": flags,
            }
        )

    return {
        "from": _iso(since),
        "to": _iso(now),
        "minLeadOps": MIN_LEAD_OPS,
        "lastReviewed": _last_reviewed(worker_id),
        "machineTypes": out,
    }

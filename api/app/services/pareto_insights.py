"""What the delay Pareto needs beyond its bars.

The chart ranks the causes of lost working hours. To make it something the
Administrator can act on, each cause also gets:

* its incidents (the pauses, breakdowns, late materials and late outside work
  behind the bar), so a bar can be opened and checked;
* a suggested action, written from the cause and its biggest source;
* a weekly trend of the top causes, to see whether a fix worked;

and the page gets a second ranking, late jobs by the cause that cost each of
them the most hours, plus how much of the lost time is still "Other".
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from app.extensions import db
from app.models.job_order import JobOrder
from app.models.machine import MachineUnit
from app.models.operation_time import (
    DOWNTIME_CATEGORY_LABELS,
    OperationPauseReason,
    OperationTimeEvent,
    OperationTimeLog,
    pause_reason_label,
)
from app.models.user import User
from app.services.schedule_calendar import SHOP_TZ, ensure_utc, shop_working_hours
from app.utils.errors import AppError

INCIDENT_LIMIT = 60
TREND_TOP = 3
OTHER = OperationPauseReason.OTHER.value


def _iso(dt):
    return ensure_utc(dt).isoformat() if dt else None


def _lookup(pauses, downtimes, material, late_back):
    unit_ids = {p["machineUnitId"] for p in pauses if p["machineUnitId"]}
    unit_ids |= {d["machineUnitId"] for d in downtimes}
    job_ids = {p["jobOrderId"] for p in pauses} | {d["jobOrderId"] for d in downtimes if d["jobOrderId"]}
    worker_ids = {p["workerId"] for p in pauses if p.get("workerId")}
    units = (
        {u.id: u.label for u in MachineUnit.query.filter(MachineUnit.id.in_(list(unit_ids)))}
        if unit_ids
        else {}
    )
    jobs = (
        {j.id: j.job_number for j in JobOrder.query.filter(JobOrder.id.in_(list(job_ids)))}
        if job_ids
        else {}
    )
    workers = (
        {u.id: u.full_name for u in User.query.filter(User.id.in_(list(worker_ids)))}
        if worker_ids
        else {}
    )
    return units, jobs, workers


def attach_incidents(pareto, *, pauses, downtimes, material, late_back):
    """Put the incidents behind each bar on its row, newest first."""
    units, jobs, workers = _lookup(pauses, downtimes, material, late_back)
    by_reason = defaultdict(list)
    for p in pauses:
        by_reason[p["reason"]].append(
            {
                "kind": "PAUSE",
                "logId": p.get("logId"),
                "jobOrderId": p["jobOrderId"],
                "jobNumber": jobs.get(p["jobOrderId"]),
                "operation": p.get("operationName"),
                "machine": units.get(p["machineUnitId"]),
                "worker": workers.get(p.get("workerId")),
                "start": _iso(p["start"]),
                "hours": round(p["hours"], 2),
                "note": p.get("note"),
                "originalReason": p.get("originalReason"),
            }
        )
    down = [
        {
            "kind": "DOWNTIME",
            "jobOrderId": d["jobOrderId"],
            "jobNumber": jobs.get(d["jobOrderId"]),
            "machine": units.get(d["machineUnitId"]),
            "start": _iso(d["start"]),
            "end": _iso(d["end"]) if not d["open"] else None,
            "hours": round(d["hours"], 2),
            "note": d["label"],
        }
        for d in downtimes
    ]
    by_material = defaultdict(list)
    for r in material:
        by_material[r["cause"]].append(
            {
                "kind": "MATERIAL",
                "jobOrderId": r["jobOrderId"],
                "jobNumber": r["jobNumber"],
                "supplier": r.get("supplierNames"),
                "start": r.get("firstStart"),
                "hours": round(r["hours"], 2),
                "note": r.get("reason"),
            }
        )
    outside = [
        {
            "kind": "OUTSOURCED",
            "jobOrderId": r["jobOrderId"],
            "jobNumber": r["jobNumber"],
            "operation": r.get("operationName"),
            "supplier": r.get("sentTo"),
            "start": r.get("expectedReturn"),
            "hours": round(r["hours"], 2),
            "note": None if r.get("returned") else "not back yet",
        }
        for r in late_back
    ]
    from app.services.delay_analysis_service import PARETO_CAUSE

    reverse_material = {v: k for k, v in PARETO_CAUSE.items()}
    for row in pareto:
        kind = row["causeType"]
        if kind == "PAUSE":
            items = by_reason.get(row["cause"], [])
        elif kind == "DOWNTIME":
            items = down
        elif kind == "MATERIAL":
            items = by_material.get(reverse_material.get(row["cause"]), [])
        elif kind == "OUTSOURCED":
            items = outside
        else:
            items = []
        items = sorted(items, key=lambda i: i.get("start") or "", reverse=True)
        row["incidentCount"] = len(items)
        row["incidents"] = items[:INCIDENT_LIMIT]
        row["action"] = suggested_action(row)


def _top(row):
    details = row.get("details") or []
    return details[0] if details else None


def _h(v):
    return f"{v:.1f} h"


def suggested_action(row) -> str | None:
    """One sentence on what to look at first, from the cause and its biggest source."""
    cause, top = row["cause"], _top(row)
    where = top["label"] if top else None
    hours = _h(top["hours"]) if top else None
    if cause in ("MACHINE_DOWNTIME", OperationPauseReason.MACHINE_DOWN.value):
        return (
            f"Check {where} first ({hours} over {top['count']} stops): plan its maintenance, "
            "or keep another unit free for its jobs."
            if top
            else "Plan maintenance for the machines that stop most."
        )
    if cause == "MATERIAL_DELAY_SUPPLIER_LATE":
        return (
            f"{where} cost {hours}: order from it earlier, or check the supplier scores "
            "before ordering for urgent jobs."
            if top
            else "Order earlier from suppliers that deliver late."
        )
    if cause == "MATERIAL_DELAY_NOT_ORDERED":
        n = row.get("occurrenceCount") or 0
        return f"Materials for {n} job{'s' if n != 1 else ''} were not ordered in time: order them when the job is encoded."
    if cause == "OUTSOURCED_DELAY":
        return (
            f"{where} returned work late: agree on a firm return date, or send the work out earlier."
            if top
            else "Agree on firm return dates with outside shops."
        )
    actions = {
        OperationPauseReason.WAITING_PRIOR_OPERATION.value: "Operations waited for the step before them: check that step's machine and crew{at}.",
        OperationPauseReason.WAITING_INSTRUCTIONS.value: "Confirm drawings and instructions with the client before the job is released{at}.",
        OperationPauseReason.WAITING_TOOL.value: "Tools were not ready{at}: check the tool borrow log and keep common tools at the machine.",
        OperationPauseReason.WAITING_MATERIAL.value: "Work started before its material was at the machine{at}: check material before starting.",
        OperationPauseReason.POWER_INTERRUPTION.value: "Record outage times, and plan backup power for the machines that cannot stop.",
        OperationPauseReason.MOVED_TO_OTHER_JOB.value: "Workers were pulled to other jobs{at}: put rush jobs on the schedule so the move is planned.",
        OperationPauseReason.WORKER_AWAY.value: "Work stopped when a worker was away: mark leave ahead so the work is given to others earlier.",
        OperationPauseReason.EMERGENCY.value: "Work stopped for emergencies: hand the work over sooner so the operation keeps moving.",
        OTHER: "Ask workers to pick a specific reason, or re-label these pauses so this bar shrinks.",
    }
    text = actions.get(cause)
    if not text:
        return None
    return text.format(at=f", mostly on {where}" if where and where != "No machine set" else "")


def other_share(pareto, pauses) -> dict:
    total = sum(r["hours"] for r in pareto) or 0.0
    other = next((r["hours"] for r in pareto if r["cause"] == OTHER), 0.0)
    no_note = sum(1 for p in pauses if p["reason"] == OTHER and not (p.get("note") or "").strip())
    return {
        "hours": round(other, 2),
        "pct": round(other / total * 100.0, 1) if total else 0.0,
        "withoutNote": no_note,
    }


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _split_by_week(s, e):
    """Pieces of [s, e) cut at Monday 00:00 shop time."""
    s, e = ensure_utc(s), ensure_utc(e)
    out = []
    while s < e:
        local = s.astimezone(SHOP_TZ)
        nxt = datetime.combine(_week_start(local.date()) + timedelta(days=7), time.min, tzinfo=SHOP_TZ)
        cut = min(e, nxt.astimezone(timezone.utc))
        out.append((_week_start(local.date()), s, cut))
        s = cut
    return out


def weekly_trend(pareto, *, pauses, downtimes, material, late_back, period_from, period_to):
    """Hours per week for the top causes of the period."""
    weeks = []
    w = _week_start(period_from)
    while w <= period_to:
        weeks.append(w)
        w += timedelta(days=7)
    top = [r for r in pareto[:TREND_TOP]]
    index = {wk: i for i, wk in enumerate(weeks)}
    series = []
    for row in top:
        vals = [0.0] * len(weeks)

        def add(wk, hrs):
            if wk in index:
                vals[index[wk]] += hrs

        kind, cause = row["causeType"], row["cause"]
        if kind == "PAUSE":
            for p in pauses:
                if p["reason"] == cause:
                    for wk, s, e in _split_by_week(p["start"], p["end"]):
                        add(wk, (e - s).total_seconds() / 3600.0)
        elif kind == "DOWNTIME":
            for d in downtimes:
                for wk, s, e in _split_by_week(d["start"], d["end"]):
                    add(wk, shop_working_hours([(s, e)]))
        elif kind == "MATERIAL":
            from app.services.delay_analysis_service import PARETO_CAUSE

            reverse = {v: k for k, v in PARETO_CAUSE.items()}
            for r in material:
                if r["cause"] == reverse.get(cause) and r.get("firstStart"):
                    add(_week_start(datetime.fromisoformat(r["firstStart"]).astimezone(SHOP_TZ).date()), r["hours"])
        elif kind == "OUTSOURCED":
            for r in late_back:
                if r.get("expectedReturn"):
                    add(_week_start(datetime.fromisoformat(r["expectedReturn"]).astimezone(SHOP_TZ).date()), r["hours"])
        series.append({"cause": cause, "label": row["label"], "hours": [round(v, 1) for v in vals]})
    return {"weeks": [w.isoformat() for w in weeks], "series": series}


def late_job_causes(late_rows):
    """Late jobs ranked by the cause that cost each job the most hours."""
    from app.services.analytics_service import pareto_rows

    counts = {}
    for job in late_rows:
        if not job["causes"]:
            key, label = "UNRECORDED", "No delay recorded"
        else:
            key, label = job["causes"][0]["cause"], job["causes"][0]["label"]
        row = counts.setdefault(key, {"cause": key, "label": label, "jobs": 0, "jobNumbers": []})
        row["jobs"] += 1
        row["jobNumbers"].append(job["jobNumber"])
    rows = pareto_rows(list(counts.values()), value_key="jobs")
    for r in rows:
        r["shareOfTotalPct"] = round(r["shareOfTotalPct"], 1)
        r["cumulativePct"] = round(r["cumulativePct"], 1)
    return rows


RELABEL_REASONS = {r.value for r in OperationPauseReason}


def relabel_pause(log_id, reason, actor_id):
    """The Admin corrects a pause's reason. The first reason is kept."""
    log = db.session.get(OperationTimeLog, log_id)
    if log is None or log.event != OperationTimeEvent.PAUSE:
        raise AppError("Pause not found", "NOT_FOUND", 404)
    if reason not in RELABEL_REASONS:
        raise AppError("Choose a pause reason from the list", "VALIDATION_ERROR", 400)
    new = OperationPauseReason(reason)
    if log.reason == new:
        return log
    before = log.reason.value if log.reason else None
    if log.original_reason is None:
        log.original_reason = before
    log.reason = new
    log.relabeled_by_id = actor_id
    log.relabeled_at = datetime.now(timezone.utc)
    from app.services.audit_service import write_audit_event

    write_audit_event(
        "PAUSE_REASON_CHANGED",
        "OperationTimeLog",
        log.id,
        before={"reason": before},
        after={"reason": reason, "label": pause_reason_label(reason)},
    )
    db.session.commit()
    return log

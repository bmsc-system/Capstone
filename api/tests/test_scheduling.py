"""Tests for earliest-fit scheduling."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.constants.scheduling import SCHEDULE_HORIZON_DAYS
from app.models.worker_skill import CalendarExceptionType
from app.services.schedule_calendar import (
    SHOP_TZ,
    build_worker_working_windows,
    place_duration,
    place_unbroken,
    shop_local_to_utc,
    utc_to_shop,
    subtract_intervals,
)
from app.services.schedule_service import (
    MISSING_WORKER_MESSAGE,
    compute_schedule_flag,
    propose_schedule,
    validate_schedule,
)

SHOP = SHOP_TZ


def _mon_sat_schedule_by_dow():
    rows = {}
    for dow in range(7):
        working = dow < 6
        rows[dow] = SimpleNamespace(
            day_of_week=dow,
            is_working=working,
            start_time=time(8, 0) if working else None,
            end_time=time(17, 0) if working else None,
        )
    return rows


def _lathe_units(lathe_id="lathe-1", unit_a="u1", unit_b="u2"):
    units = [
        SimpleNamespace(id=unit_a, machine_type_id=lathe_id, label="Lathe #1", active=True),
        SimpleNamespace(id=unit_b, machine_type_id=lathe_id, label="Lathe #2", active=True),
    ]
    return {lathe_id: units}


def _anchor(year=2026, month=8, day=10, hour=8):
    return shop_local_to_utc(date(year, month, day), time(hour, 0))


@pytest.fixture
def schedule_patches(monkeypatch):
    schedule_by_dow = _mon_sat_schedule_by_dow()
    lathe_id = "lathe-type-1"

    monkeypatch.setattr(
        "app.services.schedule_service.load_crew_schedule_map",
        lambda worker_id: schedule_by_dow,
    )
    monkeypatch.setattr(
        "app.services.schedule_service.load_calendar_exceptions",
        lambda start, end: {},
    )
    monkeypatch.setattr(
        "app.services.schedule_service._load_external_bookings",
        lambda exclude_job_id=None, exclude_operation_ids=None: ({}, {}),
    )
    monkeypatch.setattr(
        "app.services.schedule_service._machine_units_by_type",
        lambda: _lathe_units(lathe_id),
    )
    monkeypatch.setattr("app.services.schedule_service._open_downtimes", lambda: {})
    # A late job looks for faster worker/unit pairs; the stubbed shop has none.
    monkeypatch.setattr(
        "app.services.schedule_service._alternative_pairs",
        lambda op, units_by_type: [],
    )
    return {"lathe_id": lathe_id, "schedule_by_dow": schedule_by_dow}


def test_place_duration_spans_multiple_working_days():
    schedule_by_dow = _mon_sat_schedule_by_dow()
    anchor = _anchor(hour=15)
    end = anchor + timedelta(days=SCHEDULE_HORIZON_DAYS)
    windows = build_worker_working_windows(schedule_by_dow, {}, anchor, end)
    start, finish, _ = place_duration(windows, timedelta(hours=5), anchor, end)
    assert start is not None
    assert finish is not None
    # Calendar span includes non-working overnight gap between shifts.
    assert start.astimezone(SHOP).hour == 15
    assert start.astimezone(SHOP).date() == date(2026, 8, 10)
    assert finish.astimezone(SHOP).date() == date(2026, 8, 11)
    assert finish.astimezone(SHOP).hour == 11
    assert (finish - start).total_seconds() == pytest.approx(20 * 3600, rel=1e-6)


def test_six_hour_op_from_1400_waits_for_next_morning_instead_of_splitting(monkeypatch):
    """6h from 14:00 on Mon–Sat 08–17 fits in one day, so it is not split across
    Monday and Tuesday: it starts Tuesday 08:00 and finishes Tuesday; validate clean."""
    schedule_by_dow = _mon_sat_schedule_by_dow()
    worker_id = "worker-1"
    monkeypatch.setattr(
        "app.services.schedule_service.load_crew_schedule_map",
        lambda wid: schedule_by_dow,
    )
    monkeypatch.setattr(
        "app.services.schedule_service.load_calendar_exceptions",
        lambda start_d, end_d: {},
    )
    monkeypatch.setattr(
        "app.services.schedule_service._load_external_bookings",
        lambda **kwargs: ({}, {}),
    )
    monkeypatch.setattr(
        "app.services.schedule_service._machine_units_by_type",
        lambda: {},
    )

    anchor = shop_local_to_utc(date(2026, 8, 10), time(14, 0))  # Monday
    result = propose_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Long turn",
                "assignedWorkerId": worker_id,
                "machineTypeId": None,
                "estimatedHours": 6,
            }
        ],
        date(2026, 8, 20),
        anchor_utc=anchor,
    )
    op = result["operations"][0]
    assert op["scheduled"] is True
    start = datetime.fromisoformat(op["scheduledStart"]).astimezone(SHOP)
    finish = datetime.fromisoformat(op["scheduledEnd"]).astimezone(SHOP)
    assert start.date() == date(2026, 8, 11)
    assert (start.hour, start.minute) == (8, 0)
    assert finish.date() == date(2026, 8, 11)
    segment_days = {datetime.fromisoformat(s["start"]).astimezone(SHOP).date() for s in op["segments"]}
    assert segment_days == {date(2026, 8, 11)}

    validation = validate_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Long turn",
                "assignedWorkerId": worker_id,
                "scheduledStart": op["scheduledStart"],
                "scheduledEnd": op["scheduledEnd"],
            }
        ],
        due_date=date(2026, 8, 20),
    )
    outside = [w for w in validation["warnings"] if w["code"] == "OUTSIDE_WORKING_HOURS"]
    assert outside == []


def test_overnight_gap_not_counted_as_machine_busy(schedule_patches, monkeypatch):
    """Envelope spans overnight but busy intervals are only working segments."""
    from app.models.operation import OperationStatus
    from app.services.schedule_service import _busy_intervals_for_operation

    schedule_by_dow = schedule_patches["schedule_by_dow"]
    monkeypatch.setattr(
        "app.services.schedule_service.load_crew_schedule_map",
        lambda wid: schedule_by_dow,
    )
    monkeypatch.setattr(
        "app.services.schedule_service.load_calendar_exceptions",
        lambda start_d, end_d: {},
    )

    start = shop_local_to_utc(date(2026, 8, 10), time(14, 0))
    end = shop_local_to_utc(date(2026, 8, 11), time(11, 0))
    unit_id = "unit-1"
    op = SimpleNamespace(
        status=OperationStatus.SCHEDULED,
        assigned_worker_id="worker-1",
        crew_ids=["worker-1"],
        machine_unit_id=unit_id,
        scheduled_start=start,
        scheduled_end=end,
        actual_start=None,
        actual_end=None,
    )

    intervals = _busy_intervals_for_operation(op)
    assert len(intervals) == 2
    gap_probe = shop_local_to_utc(date(2026, 8, 10), time(20, 0))
    for s, e in intervals:
        assert not (s <= gap_probe < e)

    machine_busy = {unit_id: list(intervals)}
    worker_busy = {"worker-1": list(intervals)}
    monkeypatch.setattr(
        "app.services.schedule_service._load_external_bookings",
        lambda **kwargs: (worker_busy, machine_busy),
    )
    result = propose_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Follow-on",
                "assignedWorkerId": "worker-1",
                "machineTypeId": None,
                "estimatedHours": 1,
            }
        ],
        date(2026, 8, 20),
        anchor_utc=shop_local_to_utc(date(2026, 8, 10), time(18, 0)),
    )
    follow = result["operations"][0]
    assert follow["scheduled"] is True
    follow_start = datetime.fromisoformat(follow["scheduledStart"]).astimezone(SHOP)
    assert follow_start.date() == date(2026, 8, 11)
    assert follow_start.hour == 11


def test_manual_window_outside_hours_still_warns(monkeypatch):
    schedule_by_dow = _mon_sat_schedule_by_dow()
    monkeypatch.setattr(
        "app.services.schedule_service.load_crew_schedule_map",
        lambda wid: schedule_by_dow,
    )
    monkeypatch.setattr(
        "app.services.schedule_service.load_calendar_exceptions",
        lambda start_d, end_d: {},
    )
    # 16:00–20:00 overhangs past 17:00
    start = shop_local_to_utc(date(2026, 8, 10), time(16, 0))
    end = shop_local_to_utc(date(2026, 8, 10), time(20, 0))
    validation = validate_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Late",
                "assignedWorkerId": "worker-1",
                "scheduledStart": start.isoformat(),
                "scheduledEnd": end.isoformat(),
            }
        ],
        due_date=date(2026, 8, 20),
    )
    outside = [w for w in validation["warnings"] if w["code"] == "OUTSIDE_WORKING_HOURS"]
    assert len(outside) >= 1


def test_existing_booking_forces_later_slot(schedule_patches, monkeypatch):
    f = schedule_patches
    worker_id = "worker-1"
    block_start = _anchor(hour=8)
    block_end = _anchor(hour=12)
    monkeypatch.setattr(
        "app.services.schedule_service._load_external_bookings",
        lambda **kwargs: ({worker_id: [(block_start, block_end)]}, {}),
    )
    result = propose_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Turning",
                "assignedWorkerId": worker_id,
                "machineTypeId": f["lathe_id"],
                "estimatedHours": 2,
            }
        ],
        date(2026, 8, 20),
        anchor_utc=_anchor(hour=8),
    )
    op = result["operations"][0]
    assert op["scheduled"]
    # Booked until 12:00, then the 12:00-13:00 break.
    assert op["scheduledStart"] == _anchor(hour=13).isoformat()


def test_all_machine_units_busy(schedule_patches, monkeypatch):
    f = schedule_patches
    worker_id = "worker-1"
    anchor = _anchor(hour=8)
    machine_busy = {
        "u1": [(anchor, anchor + timedelta(days=SCHEDULE_HORIZON_DAYS))],
        "u2": [(anchor, anchor + timedelta(days=SCHEDULE_HORIZON_DAYS))],
    }
    monkeypatch.setattr(
        "app.services.schedule_service._load_external_bookings",
        lambda **kwargs: ({}, machine_busy),
    )
    result = propose_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Turning",
                "assignedWorkerId": worker_id,
                "machineTypeId": f["lathe_id"],
                "estimatedHours": 2,
            }
        ],
        date(2026, 8, 20),
        anchor_utc=anchor,
    )
    op = result["operations"][0]
    assert not op["scheduled"]
    assert "all machine units busy" in op["message"]
    assert op["placeableHours"] == 0.0


def test_no_machine_type_worker_only(schedule_patches):
    result = propose_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Heat Treatment",
                "assignedWorkerId": "worker-1",
                "machineTypeId": None,
                "estimatedHours": 2,
            }
        ],
        date(2026, 8, 20),
        anchor_utc=_anchor(hour=8),
    )
    op = result["operations"][0]
    assert op["scheduled"]
    assert op["machineUnitId"] is None


def test_missing_worker_not_proposed(schedule_patches):
    result = propose_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Turning",
                "assignedWorkerId": None,
                "machineTypeId": schedule_patches["lathe_id"],
                "estimatedHours": 2,
            }
        ],
        date(2026, 8, 20),
        anchor_utc=_anchor(hour=8),
    )
    op = result["operations"][0]
    assert not op["scheduled"]
    assert op["message"] == MISSING_WORKER_MESSAGE


def test_machine_type_without_assigned_worker_is_flagged(schedule_patches):
    """The proposal never picks a worker; the Admin must assign one first."""
    result = propose_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Turning",
                "assignedWorkerId": None,
                "machineTypeId": schedule_patches["lathe_id"],
                "estimatedHours": 2,
            }
        ],
        date(2026, 8, 20),
        anchor_utc=_anchor(hour=8),
    )
    op = result["operations"][0]
    assert op["scheduled"] is False
    assert op["message"] == MISSING_WORKER_MESSAGE
    assert op.get("assignedWorkerId") is None


def test_estimated_hours_default_flag(schedule_patches):
    result = propose_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Finishing",
                "assignedWorkerId": "worker-1",
                "estimatedHours": None,
            }
        ],
        date(2026, 8, 20),
        anchor_utc=_anchor(hour=8),
    )
    op = result["operations"][0]
    assert op["estimatedHoursDefaulted"] is True
    assert op["estimatedHours"] == 1.0


def test_schedule_flags_green_amber_red():
    due = date(2026, 8, 15)
    green_end = shop_local_to_utc(date(2026, 8, 15), time(17, 0))
    amber_end = shop_local_to_utc(date(2026, 8, 16), time(10, 0))
    red_end = shop_local_to_utc(date(2026, 8, 17), time(8, 0))
    assert compute_schedule_flag(green_end, due) == "GREEN"
    assert compute_schedule_flag(amber_end, due) == "AMBER"
    assert compute_schedule_flag(red_end, due) == "RED"


def test_schedule_flag_uses_shop_local_date_not_utc():
    """23:00 Manila on the due date must be GREEN (not AMBER via UTC date)."""
    due = date(2026, 8, 15)
    end_manila_2300 = shop_local_to_utc(due, time(23, 0))
    assert end_manila_2300.astimezone(SHOP).date() == due
    assert compute_schedule_flag(end_manila_2300, due) == "GREEN"

    # After midnight Manila = next local day; UTC is still due_date (16:30Z).
    # Using UTC .date() would wrongly stay GREEN — shop local must yield AMBER.
    just_after_midnight = shop_local_to_utc(date(2026, 8, 16), time(0, 30))
    assert just_after_midnight.astimezone(timezone.utc).date() == due
    assert compute_schedule_flag(just_after_midnight, due) == "AMBER"


def test_cannot_place_within_horizon(schedule_patches):
    result = propose_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Marathon",
                "assignedWorkerId": "worker-1",
                "estimatedHours": 9999,
            }
        ],
        date(2027, 1, 1),
        anchor_utc=_anchor(hour=8),
    )
    op = result["operations"][0]
    assert not op["scheduled"]
    assert str(SCHEDULE_HORIZON_DAYS) in op["message"]
    assert op["placeableHours"] > 0
    assert op["requiredHours"] == 9999


def test_operation_spanning_midnight_local():
    schedule_by_dow = _mon_sat_schedule_by_dow()
    schedule_by_dow[0] = SimpleNamespace(
        day_of_week=0,
        is_working=True,
        start_time=time(18, 0),
        end_time=time(22, 0),
    )
    anchor = shop_local_to_utc(date(2026, 8, 10), time(20, 0))
    end = anchor + timedelta(days=7)
    windows = build_worker_working_windows(schedule_by_dow, {}, anchor, end)
    start, finish, _ = place_duration(windows, timedelta(hours=4), anchor, end)
    assert start.astimezone(SHOP).hour == 20
    assert finish.astimezone(SHOP).date() == date(2026, 8, 11)
    assert finish.astimezone(SHOP).hour == 10


def test_sunday_special_working_day():
    schedule_by_dow = _mon_sat_schedule_by_dow()
    sunday = date(2026, 8, 16)
    exceptions = {
        sunday: SimpleNamespace(
            date=sunday,
            type=CalendarExceptionType.SPECIAL_WORKING_DAY,
            start_time=time(8, 0),
            end_time=time(17, 0),
        )
    }
    anchor = shop_local_to_utc(sunday, time(8, 0))
    end = anchor + timedelta(days=1)
    windows = build_worker_working_windows(schedule_by_dow, exceptions, anchor, end)
    start, finish, _ = place_duration(windows, timedelta(hours=2), anchor, end)
    assert start is not None
    assert finish is not None
    assert start.astimezone(SHOP).date() == sunday


def _mon(hh):
    return shop_local_to_utc(date(2026, 8, 10), time(hh, 0))


def test_place_unbroken_moves_past_a_booking_instead_of_splitting_around_it():
    schedule_by_dow = _mon_sat_schedule_by_dow()
    anchor = _anchor(hour=8)
    end = anchor + timedelta(days=7)
    windows = build_worker_working_windows(schedule_by_dow, {}, anchor, end)
    busy = [(_mon(10), _mon(11))]

    start, finish, _ = place_unbroken(windows, busy, timedelta(hours=3), anchor, end)

    # 11:00-12:00, the break, then 13:00-15:00.
    assert (start, finish) == (_mon(11), _mon(15))
    # Two hours fit before the booking, so place_duration alone would straddle it.
    split_start, split_end, _ = place_duration(
        subtract_intervals(windows, busy), timedelta(hours=3), anchor, end
    )
    assert (split_start, split_end) == (_mon(8), _mon(12))


def test_place_unbroken_still_pauses_overnight():
    schedule_by_dow = _mon_sat_schedule_by_dow()
    anchor = _anchor(hour=15)
    end = anchor + timedelta(days=7)
    windows = build_worker_working_windows(schedule_by_dow, {}, anchor, end)

    start, finish, _ = place_unbroken(windows, [], timedelta(hours=4), anchor, end, tidy=False)

    assert start == _mon(15)
    assert finish == shop_local_to_utc(date(2026, 8, 11), time(10, 0))


def test_tidy_placement_starts_on_the_half_hour_and_keeps_a_short_job_in_one_day():
    schedule_by_dow = _mon_sat_schedule_by_dow()
    anchor = shop_local_to_utc(date(2026, 8, 10), time(9, 26))
    end = anchor + timedelta(days=7)
    windows = build_worker_working_windows(schedule_by_dow, {}, anchor, end)

    start, finish, _ = place_unbroken(windows, [], timedelta(hours=1.3), anchor, end)
    assert start == shop_local_to_utc(date(2026, 8, 10), time(9, 30))
    # 1.3 h is booked as 1.5 h.
    assert finish == shop_local_to_utc(date(2026, 8, 10), time(11, 0))

    late = shop_local_to_utc(date(2026, 8, 10), time(15, 0))
    start, finish, _ = place_unbroken(windows, [], timedelta(hours=4), late, end)
    assert start == shop_local_to_utc(date(2026, 8, 11), time(8, 0))
    assert utc_to_shop(finish).date() == date(2026, 8, 11)


def test_tidy_placement_still_splits_work_longer_than_a_day():
    schedule_by_dow = _mon_sat_schedule_by_dow()
    anchor = shop_local_to_utc(date(2026, 8, 10), time(14, 0))
    end = anchor + timedelta(days=7)
    windows = build_worker_working_windows(schedule_by_dow, {}, anchor, end)

    start, finish, _ = place_unbroken(windows, [], timedelta(hours=12), anchor, end)
    assert start == anchor
    assert utc_to_shop(finish).date() > date(2026, 8, 10)


def test_proposal_never_straddles_another_jobs_machine_booking(schedule_patches, monkeypatch):
    booked = [(_mon(10), _mon(11))]
    monkeypatch.setattr(
        "app.services.schedule_service._load_external_bookings",
        lambda exclude_job_id=None, exclude_operation_ids=None: ({}, {"u1": booked, "u2": booked}),
    )
    result = propose_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Turning",
                "assignedWorkerId": "worker-1",
                "machineTypeId": schedule_patches["lathe_id"],
                "estimatedHours": 3,
            }
        ],
        date(2026, 8, 20),
        anchor_utc=_anchor(hour=8),
    )
    op = result["operations"][0]
    assert datetime.fromisoformat(op["scheduledStart"]) == _mon(11)
    assert datetime.fromisoformat(op["scheduledEnd"]) == _mon(15)


def test_sequential_operations_chain(schedule_patches):
    result = propose_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Turning",
                "assignedWorkerId": "worker-1",
                "machineTypeId": schedule_patches["lathe_id"],
                "estimatedHours": 3,
            },
            {
                "sequenceNo": 2,
                "operationName": "Heat Treatment",
                "assignedWorkerId": "worker-1",
                "machineTypeId": None,
                "estimatedHours": 1,
            },
        ],
        date(2026, 8, 20),
        anchor_utc=_anchor(hour=8),
    )
    ops = result["operations"]
    assert len(ops) == 2
    assert all(op["scheduled"] for op in ops)
    end1 = datetime.fromisoformat(ops[0]["scheduledEnd"])
    start2 = datetime.fromisoformat(ops[1]["scheduledStart"])
    assert start2 >= end1


def test_completed_predecessor_after_anchor_blocks_next(schedule_patches):
    """COMPLETED op with actual_end after the anchor — next op starts at/after that end."""
    anchor = _anchor(hour=8)  # Mon 2026-08-10 08:00 Manila
    # Finished Monday 14:00 Manila (after anchor)
    completed_start = shop_local_to_utc(date(2026, 8, 10), time(10, 0))
    completed_end = shop_local_to_utc(date(2026, 8, 10), time(14, 0))
    assert completed_end > anchor

    result = propose_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Blanking",
                "assignedWorkerId": "worker-1",
                "machineTypeId": schedule_patches["lathe_id"],
                "status": "COMPLETED",
                "actualStart": completed_start.isoformat(),
                "actualEnd": completed_end.isoformat(),
                "estimatedHours": 4,
            },
            {
                "sequenceNo": 2,
                "operationName": "Teeth cutting",
                "assignedWorkerId": "worker-1",
                "machineTypeId": schedule_patches["lathe_id"],
                "estimatedHours": 2,
            },
        ],
        date(2026, 8, 20),
        anchor_utc=anchor,
    )
    ops = result["operations"]
    assert ops[0]["scheduled"]
    assert ops[1]["scheduled"]
    start2 = datetime.fromisoformat(ops[1]["scheduledStart"])
    assert start2 >= completed_end
    # Must not fall back to the anchor when the frozen predecessor ends later
    assert start2 > anchor


def test_keeps_assigned_worker_and_moves_time(schedule_patches, monkeypatch):
    """
    Assigned worker busy until noon while Lathe #2 is free — the proposal keeps
    the worker and starts after the break instead of swapping in someone else.
    """
    f = schedule_patches
    busy_worker = "worker-busy"
    anchor = _anchor(hour=8)
    block_end = _anchor(hour=12)
    monkeypatch.setattr(
        "app.services.schedule_service._load_external_bookings",
        lambda **kwargs: (
            {busy_worker: [(anchor, block_end)]},
            {"u1": [(anchor, block_end)]},
        ),
    )
    result = propose_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Blanking",
                "assignedWorkerId": busy_worker,
                "machineTypeId": f["lathe_id"],
                "estimatedHours": 2,
            }
        ],
        date(2026, 8, 20),
        anchor_utc=anchor,
    )
    op = result["operations"][0]
    assert op["scheduled"] is True
    assert op["assignedWorkerId"] == busy_worker
    # Busy until 12:00, then the 12:00-13:00 break.
    assert op["scheduledStart"] == _anchor(hour=13).isoformat()


def test_assigned_worker_without_room_is_flagged(schedule_patches, monkeypatch):
    """If the assigned worker has no free time in the horizon, flag it; never swap."""
    f = schedule_patches
    busy_worker = "worker-busy"
    anchor = _anchor(hour=8)
    monkeypatch.setattr(
        "app.services.schedule_service._load_external_bookings",
        lambda **kwargs: (
            {busy_worker: [(anchor, anchor + timedelta(days=SCHEDULE_HORIZON_DAYS + 1))]},
            {},
        ),
    )
    result = propose_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Finishing",
                "assignedWorkerId": busy_worker,
                "machineTypeId": None,
                "estimatedHours": 2,
            }
        ],
        date(2026, 8, 20),
        anchor_utc=anchor,
    )
    op = result["operations"][0]
    assert op["scheduled"] is False
    assert str(SCHEDULE_HORIZON_DAYS) in op["message"]
    assert "Assign another worker" in op["message"]
    assert op.get("assignedWorkerId") in (None, busy_worker)


def test_pinned_unit_keeps_assigned_worker(schedule_patches, monkeypatch):
    """Machine pin to Lathe #2 still waits for the assigned worker to be free."""
    f = schedule_patches
    busy_worker = "worker-busy"
    anchor = _anchor(hour=8)
    block_end = _anchor(hour=12)
    monkeypatch.setattr(
        "app.services.schedule_service._load_external_bookings",
        lambda **kwargs: (
            {busy_worker: [(anchor, block_end)]},
            {"u1": [(anchor, block_end)]},
        ),
    )
    result = propose_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Blanking",
                "assignedWorkerId": busy_worker,
                "machineTypeId": f["lathe_id"],
                "machineUnitId": "u2",
                "estimatedHours": 2,
            }
        ],
        date(2026, 8, 20),
        anchor_utc=anchor,
        honor_machine_pins=True,
    )
    op = result["operations"][0]
    assert op["scheduled"] is True
    assert op["machineUnitId"] == "u2"
    assert op["assignedWorkerId"] == busy_worker
    # Busy until 12:00, then the 12:00-13:00 break.
    assert op["scheduledStart"] == _anchor(hour=13).isoformat()


def test_no_machine_op_keeps_busy_assigned_worker(schedule_patches, monkeypatch):
    """Finishing waits for its assigned worker instead of switching to a free one."""
    busy_worker = "worker-busy"
    anchor = _anchor(hour=8)
    block_end = _anchor(hour=12)
    monkeypatch.setattr(
        "app.services.schedule_service._load_external_bookings",
        lambda **kwargs: ({busy_worker: [(anchor, block_end)]}, {}),
    )
    result = propose_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Finishing",
                "assignedWorkerId": busy_worker,
                "machineTypeId": None,
                "estimatedHours": 2,
            }
        ],
        date(2026, 8, 20),
        anchor_utc=anchor,
    )
    op = result["operations"][0]
    assert op["scheduled"] is True
    assert op["machineUnitId"] is None
    assert op["assignedWorkerId"] == busy_worker
    # Busy until 12:00, then the 12:00-13:00 break.
    assert op["scheduledStart"] == _anchor(hour=13).isoformat()


def test_first_operation_not_before_material_date(schedule_patches):
    """Material expected date raises the first-op not_before floor."""
    lathe_id = schedule_patches["lathe_id"]
    anchor = _anchor(2026, 8, 10, 8)
    material_day = date(2026, 8, 12)
    material_nb = shop_local_to_utc(material_day, time(0, 0))
    result = propose_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Turning",
                "assignedWorkerId": "worker-1",
                "machineTypeId": lathe_id,
                "estimatedHours": 2,
            }
        ],
        date(2026, 8, 20),
        anchor_utc=anchor,
        material_not_before_utc=material_nb,
        material_constraint_reason="material expected 2026-08-12",
    )
    op = result["operations"][0]
    assert op["scheduled"] is True
    start_shop = datetime.fromisoformat(op["scheduledStart"]).astimezone(SHOP)
    assert start_shop.date() == material_day
    assert start_shop.hour == 8
    assert result["materialNotBefore"] == material_nb.isoformat()
    assert "material expected" in (result["materialConstraintReason"] or "")


def test_late_material_flags_job_red(schedule_patches):
    """Material that arrives too late to finish by due date yields RED (via delayed projection)."""
    lathe_id = schedule_patches["lathe_id"]
    # Due Monday Aug 10; material only available Aug 12 → 8h job cannot finish Mon.
    anchor = _anchor(2026, 8, 10, 8)
    material_nb = shop_local_to_utc(date(2026, 8, 12), time(0, 0))
    result = propose_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Turning",
                "assignedWorkerId": "worker-1",
                "machineTypeId": lathe_id,
                "estimatedHours": 8,
            }
        ],
        date(2026, 8, 10),
        anchor_utc=anchor,
        material_not_before_utc=material_nb,
    )
    assert result["operations"][0]["scheduled"] is True
    assert result["scheduleFlag"] == "RED"
    completion = datetime.fromisoformat(result["projectedCompletion"]).astimezone(SHOP)
    assert completion.date() > date(2026, 8, 10)


def test_not_required_material_unconstrained(schedule_patches):
    """NOT_REQUIRED leaves first-op start at the capacity anchor."""
    from app.services.schedule_service import resolve_material_not_before_utc

    assert resolve_material_not_before_utc("NOT_REQUIRED", None, date(2026, 8, 20)) is None

    lathe_id = schedule_patches["lathe_id"]
    anchor = _anchor(2026, 8, 10, 8)
    result = propose_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Turning",
                "assignedWorkerId": "worker-1",
                "machineTypeId": lathe_id,
                "estimatedHours": 2,
            }
        ],
        date(2026, 8, 20),
        anchor_utc=anchor,
        material_not_before_utc=None,
    )
    assert result["operations"][0]["scheduledStart"] == anchor.isoformat()
    assert result["materialNotBefore"] is None


def _calendar_patches(monkeypatch, exceptions):
    schedule_by_dow = _mon_sat_schedule_by_dow()
    monkeypatch.setattr(
        "app.services.schedule_service.load_crew_schedule_map",
        lambda wid: schedule_by_dow,
    )
    monkeypatch.setattr(
        "app.services.schedule_service.load_calendar_exceptions",
        lambda start_d, end_d: {
            d: e for d, e in exceptions.items() if start_d <= d <= end_d
        },
    )
    monkeypatch.setattr(
        "app.services.schedule_service._load_external_bookings",
        lambda **kwargs: ({}, {}),
    )
    monkeypatch.setattr("app.services.schedule_service._machine_units_by_type", lambda: {})


def _single_op_proposal(anchor, hours):
    return propose_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Turning",
                "assignedWorkerId": "worker-1",
                "machineTypeId": None,
                "estimatedHours": hours,
            }
        ],
        date(2026, 8, 31),
        anchor_utc=anchor,
    )


def test_overtime_day_places_work_after_normal_closing(monkeypatch):
    monday = date(2026, 8, 10)
    _calendar_patches(
        monkeypatch,
        {
            monday: SimpleNamespace(
                date=monday,
                type=CalendarExceptionType.OVERTIME,
                start_time=time(17, 0),
                end_time=time(20, 0),
            )
        },
    )
    op = _single_op_proposal(shop_local_to_utc(monday, time(14, 0)), 5)["operations"][0]

    assert op["scheduled"] is True
    finish = datetime.fromisoformat(op["scheduledEnd"]).astimezone(SHOP)
    # 14:00–17:00 regular + 17:00–19:00 overtime, same day.
    assert finish.date() == monday
    assert (finish.hour, finish.minute) == (19, 0)

    validation = validate_schedule(
        [
            {
                "sequenceNo": 1,
                "operationName": "Turning",
                "assignedWorkerId": "worker-1",
                "estimatedHours": 5,
                "scheduledStart": op["scheduledStart"],
                "scheduledEnd": op["scheduledEnd"],
            }
        ]
    )
    assert not [w for w in validation["warnings"] if w["code"] == "OUTSIDE_WORKING_HOURS"]


def test_holiday_gets_no_work(monkeypatch):
    monday = date(2026, 8, 10)
    tuesday = date(2026, 8, 11)
    _calendar_patches(
        monkeypatch,
        {
            tuesday: SimpleNamespace(
                date=tuesday,
                type=CalendarExceptionType.HOLIDAY_NO_WORK,
                start_time=None,
                end_time=None,
            )
        },
    )
    op = _single_op_proposal(shop_local_to_utc(monday, time(14, 0)), 6)["operations"][0]

    assert op["scheduled"] is True
    segment_days = {
        datetime.fromisoformat(s["start"]).astimezone(SHOP).date() for s in op["segments"]
    }
    assert tuesday not in segment_days
    finish = datetime.fromisoformat(op["scheduledEnd"]).astimezone(SHOP)
    # 6h fits in one day: not 3h Monday + 3h Wednesday (Tuesday is closed),
    # but Wednesday from 08:00.
    assert monday not in segment_days
    assert finish.date() == date(2026, 8, 12)
    start = datetime.fromisoformat(op["scheduledStart"]).astimezone(SHOP)
    assert (start.hour, start.minute) == (8, 0)


def test_gap_between_closing_and_overtime_is_not_working(monkeypatch):
    monday = date(2026, 8, 10)
    _calendar_patches(
        monkeypatch,
        {
            monday: SimpleNamespace(
                date=monday,
                type=CalendarExceptionType.OVERTIME,
                start_time=time(18, 0),
                end_time=time(20, 0),
            )
        },
    )
    op = _single_op_proposal(shop_local_to_utc(monday, time(8, 0)), 10)["operations"][0]

    finish = datetime.fromisoformat(op["scheduledEnd"]).astimezone(SHOP)
    # 08:00–17:00 less the break (8h), 17:00–18:00 skipped, 18:00–20:00 (2h).
    assert finish.date() == monday
    assert (finish.hour, finish.minute) == (20, 0)
    starts = [datetime.fromisoformat(s["start"]).astimezone(SHOP) for s in op["segments"]]
    ends = [datetime.fromisoformat(s["end"]).astimezone(SHOP) for s in op["segments"]]
    assert [(s.hour, e.hour) for s, e in zip(starts, ends)] == [(8, 12), (13, 17), (18, 20)]


def test_repropose_never_moves_in_progress_operations(monkeypatch):
    _calendar_patches(monkeypatch, {})
    monday = date(2026, 8, 10)
    started_start = shop_local_to_utc(monday, time(8, 0)).isoformat()
    started_end = shop_local_to_utc(monday, time(12, 0)).isoformat()
    paused_start = shop_local_to_utc(monday, time(13, 0)).isoformat()
    paused_end = shop_local_to_utc(monday, time(15, 0)).isoformat()
    ops = [
        {
            "id": "op-1",
            "sequenceNo": 1,
            "operationName": "Turning",
            "assignedWorkerId": "worker-1",
            "estimatedHours": 4,
            "status": "IN_PROGRESS",
            "actualStart": started_start,
            "scheduledStart": started_start,
            "scheduledEnd": started_end,
        },
        {
            # In progress with no recorded start still keeps its window.
            "id": "op-2",
            "sequenceNo": 2,
            "operationName": "Facing",
            "assignedWorkerId": "worker-1",
            "estimatedHours": 2,
            "status": "IN_PROGRESS",
            "scheduledStart": paused_start,
            "scheduledEnd": paused_end,
        },
        {
            "id": "op-3",
            "sequenceNo": 3,
            "operationName": "Threading",
            "assignedWorkerId": "worker-1",
            "estimatedHours": 1,
            "status": "SCHEDULED",
            "scheduledStart": shop_local_to_utc(date(2026, 8, 14), time(8, 0)).isoformat(),
            "scheduledEnd": shop_local_to_utc(date(2026, 8, 14), time(9, 0)).isoformat(),
        },
    ]
    result = propose_schedule(
        ops, date(2026, 8, 31), anchor_utc=shop_local_to_utc(monday, time(10, 0))
    )
    by_id = {o["id"]: o for o in result["operations"]}

    assert by_id["op-1"]["scheduledStart"] == started_start
    assert by_id["op-1"]["scheduledEnd"] == started_end
    assert by_id["op-2"]["scheduledStart"] == paused_start
    assert by_id["op-2"]["scheduledEnd"] == paused_end
    # Not-started work is re-fitted right after the in-progress operations.
    moved = datetime.fromisoformat(by_id["op-3"]["scheduledStart"]).astimezone(SHOP)
    assert (moved.date(), moved.hour) == (monday, 15)

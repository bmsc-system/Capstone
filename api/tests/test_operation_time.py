"""Tests for operation time logs, variance, and machine downtime."""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models.operation_time import OperationTimeEvent
from app.services.operation_service import (
    compute_worked_hours,
    open_downtime_intervals_by_unit,
    recompute_variance,
)


def _ts(hour, minute=0, day=10):
    return datetime(2026, 8, day, hour, minute, tzinfo=timezone.utc)


def test_worked_hours_excludes_pause_interval():
    op = SimpleNamespace(
        time_logs=[
            SimpleNamespace(event=OperationTimeEvent.START, event_at=_ts(8), created_at=_ts(8)),
            SimpleNamespace(event=OperationTimeEvent.PAUSE, event_at=_ts(10), created_at=_ts(10)),
            SimpleNamespace(event=OperationTimeEvent.RESUME, event_at=_ts(11), created_at=_ts(11)),
            SimpleNamespace(event=OperationTimeEvent.COMPLETE, event_at=_ts(13), created_at=_ts(13)),
        ]
    )
    # 08–10 + 11–13 = 4h (pause 10–11 excluded)
    assert compute_worked_hours(op) == Decimal("4.0000")


def test_worked_hours_overnight_pause_excludes_gap():
    op = SimpleNamespace(
        time_logs=[
            SimpleNamespace(
                event=OperationTimeEvent.START, event_at=_ts(14), created_at=_ts(14)
            ),
            SimpleNamespace(
                event=OperationTimeEvent.PAUSE, event_at=_ts(17), created_at=_ts(17)
            ),
            SimpleNamespace(
                event=OperationTimeEvent.RESUME,
                event_at=_ts(8, day=11),
                created_at=_ts(8, day=11),
            ),
            SimpleNamespace(
                event=OperationTimeEvent.COMPLETE,
                event_at=_ts(11, day=11),
                created_at=_ts(11, day=11),
            ),
        ]
    )
    # 14–17 + 08–11 = 6h, not the overnight envelope
    assert compute_worked_hours(op) == Decimal("6.0000")


def test_variance_positive_negative_and_null():
    op = SimpleNamespace(
        estimated_hours=Decimal("4"),
        time_logs=[
            SimpleNamespace(event=OperationTimeEvent.START, event_at=_ts(8), created_at=_ts(8)),
            SimpleNamespace(
                event=OperationTimeEvent.COMPLETE, event_at=_ts(13), created_at=_ts(13)
            ),
        ],
        actual_worked_hours=None,
        variance_hours=None,
        variance_pct=None,
    )
    recompute_variance(op)
    assert op.actual_worked_hours == Decimal("5.0000")
    assert op.variance_hours == Decimal("1.0000")
    assert op.variance_pct == Decimal("25.0000")

    op.estimated_hours = Decimal("6")
    recompute_variance(op)
    assert op.variance_hours == Decimal("-1.0000")
    assert float(op.variance_pct) == pytest.approx(-16.6667, rel=1e-3)

    op.estimated_hours = None
    recompute_variance(op)
    assert op.variance_hours is None
    assert op.variance_pct is None


def test_open_machine_downtime_blocks_unit_in_scheduler(monkeypatch):
    """Open downtime intervals feed the scheduler busy map for that unit."""
    import app.services.operation_service as op_svc
    import app.services.schedule_service as sched

    row = SimpleNamespace(
        machine_unit_id="unit-1",
        started_at=_ts(8),
        ended_at=None,
        expected_repair_date=None,
    )
    mock_md = MagicMock()
    mock_md.query.filter.return_value.all.return_value = [row]
    monkeypatch.setattr(op_svc, "MachineDowntime", mock_md)

    intervals = open_downtime_intervals_by_unit()
    assert "unit-1" in intervals
    start, end = intervals["unit-1"][0]
    assert start == _ts(8)
    assert end > start

    fake_query = MagicMock()
    fake_query.join.return_value = fake_query
    fake_query.filter.return_value = fake_query
    fake_query.all.return_value = []
    monkeypatch.setattr(
        sched,
        "JobOperation",
        SimpleNamespace(
            status=SimpleNamespace(in_=lambda *a, **k: True),
            job_order_id=None,
            query=fake_query,
        ),
    )
    monkeypatch.setattr(
        op_svc,
        "open_downtime_intervals_by_unit",
        lambda: {"unit-1": [(_ts(8), _ts(18))]},
    )

    from app.services import worker_cover_service

    monkeypatch.setattr(worker_cover_service, "absence_intervals", lambda *a, **k: {})
    _, machine_busy = sched._load_external_bookings()
    assert "unit-1" in machine_busy
    assert machine_busy["unit-1"][0] == (_ts(8), _ts(18))

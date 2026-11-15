"""Moving-average forecasts: method, back-test error (MAE / MAPE), monthly series,
and consumable run-out dates on the shop calendar."""

from datetime import date, datetime, time, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.extensions import bcrypt, db
from app.models.tool import Tool, ToolCategory
from app.models.tool_event import ToolEvent, ToolEventType
from app.models.user import User, UserRole, UserStatus
from app.models.worker_skill import CalendarExceptionType
from app.services import forecast_service as fs
from app.services.schedule_calendar import default_shop_schedule_by_dow

# --- Moving average -----------------------------------------------------------


def test_moving_average_is_mean_of_last_three():
    assert fs.moving_average([10, 20, 30, 40]) == pytest.approx(30.0)
    assert fs.moving_average([5, 5]) is None


def test_backtest_mae_and_mape():
    # forecasts: m3 = (10+20+30)/3 = 20 vs 40; m4 = (20+30+40)/3 = 30 vs 30
    rows, mae, mape = fs.backtest([10, 20, 30, 40, 30])
    assert [r["forecast"] for r in rows] == pytest.approx([20.0, 30.0])
    assert [r["absoluteError"] for r in rows] == pytest.approx([20.0, 0.0])
    assert mae == pytest.approx(10.0)
    # |40-20|/40 = 50%, |30-30|/30 = 0% -> mean 25%
    assert mape == pytest.approx(25.0)


def test_mape_skips_months_with_zero_actual():
    # m3 forecast 2 vs 0 (skipped for MAPE), m4 forecast 1 vs 4 -> 75%
    rows, mae, mape = fs.backtest([3, 3, 0, 0, 4])
    assert len(rows) == 2
    assert rows[0]["absolutePctError"] is None
    assert mae == pytest.approx((2.0 + 3.0) / 2)
    assert mape == pytest.approx(75.0)


def test_backtest_needs_window_plus_one_values():
    rows, mae, mape = fs.backtest([1, 2, 3])
    assert rows == [] and mae is None and mape is None


# --- Monthly series -------------------------------------------------------------


def test_forecast_range_accepts_one_three_or_six_months_only():
    assert [fs.horizon_months(v) for v in ("1", "3", "6", 6)] == [1, 3, 6, 6]
    for bad in (None, "", "12", "abc", 0):
        assert fs.horizon_months(bad) == fs.FORECAST_HORIZON_MONTHS
    dated = [(date(2031, m, 5), 100) for m in range(1, 7)]
    out = fs.monthly_forecast(dated, today=date(2031, 7, 15), horizon=6)
    assert [h["month"] for h in out["horizon"]] == [
        "2031-07", "2031-08", "2031-09", "2031-10", "2031-11", "2031-12",
    ]


def test_monthly_forecast_fills_gaps_and_excludes_current_month():
    dated = [
        (date(2031, 1, 1), 100),
        (date(2031, 1, 20), 50),
        # February has nothing -> 0
        (date(2031, 3, 2), 300),
        (date(2031, 4, 9), 60),
        (date(2031, 5, 9), 60),
        (date(2031, 6, 9), 60),
        (date(2031, 7, 3), 999),  # current, partial month: left out
    ]
    out = fs.monthly_forecast(dated, today=date(2031, 7, 15))
    assert [m["month"] for m in out["months"]] == [
        "2031-01", "2031-02", "2031-03", "2031-04", "2031-05", "2031-06",
    ]
    assert [m["actual"] for m in out["months"]] == [150.0, 0.0, 300.0, 60.0, 60.0, 60.0]
    assert out["enoughHistory"] is True
    assert out["forecastMonth"] == "2031-07"
    assert out["currentMonthSoFar"] == pytest.approx(999.0)
    # The zero February only fits the methods; the back-tested months are not 0.
    assert out["errorMetric"] == "MAPE"
    best = min(out["methods"], key=lambda m: m["mapePct"])
    assert out["methodKey"] == best["key"] and out["mapePct"] == best["mapePct"]
    # Back-tested from April on; the first three months only fit the methods.
    assert [m["forecast"] is None for m in out["months"]] == [True, True, True, False, False, False]
    if out["methodKey"] == "MA3":
        assert out["forecast"] == pytest.approx(60.0)


def test_monthly_forecast_not_enough_history():
    dated = [(date(2031, 2, 1), 10), (date(2031, 4, 1), 20)]
    out = fs.monthly_forecast(dated, today=date(2031, 5, 2))
    assert out["monthsAvailable"] == 3
    assert out["enoughHistory"] is False
    assert out["notEnoughHistoryNote"] == "Not enough history yet"
    assert out["forecast"] is None and out["mae"] is None and out["mapePct"] is None


def test_monthly_forecast_empty():
    out = fs.monthly_forecast([], today=date(2031, 5, 2))
    assert out["monthsAvailable"] == 0
    assert out["enoughHistory"] is False


# --- Run-out date ------------------------------------------------------------------


def test_run_out_date_counts_shop_working_days_skipping_sunday_and_holidays():
    schedule = default_shop_schedule_by_dow()
    holiday = {date(2031, 3, 10): SimpleNamespace(type=CalendarExceptionType.HOLIDAY_NO_WORK)}
    # 10 on hand / 4 a day = 2.5 working days after Fri 7 Mar:
    # Sat 8, (Sun 9 off), (Mon 10 holiday), Tue 11, runs out during Wed 12
    days_left, out_on = fs.run_out_date(10, 4, date(2031, 3, 7), schedule, holiday)
    assert days_left == pytest.approx(2.5)
    assert out_on == date(2031, 3, 12)


def test_run_out_date_whole_days_and_edge_cases():
    schedule = default_shop_schedule_by_dow()
    # 6 / 2 = 3 working days after Thu 6 Mar: Fri 7, Sat 8, Mon 10
    assert fs.run_out_date(6, 2, date(2031, 3, 6), schedule, {}) == (3.0, date(2031, 3, 10))
    assert fs.run_out_date(0, 2, date(2031, 3, 6), schedule, {}) == (0.0, date(2031, 3, 6))
    assert fs.run_out_date(5, 0, date(2031, 3, 6), schedule, {}) == (None, None)


def test_working_days_between_excludes_start_and_sundays():
    schedule = default_shop_schedule_by_dow()
    # Mon 3 Mar -> Mon 10 Mar: Tue..Sat + Mon = 6
    assert fs.shop_working_days_between(date(2031, 3, 3), date(2031, 3, 10), schedule, {}) == 6


# --- Consumable run-out list (DB) ---------------------------------------------


@pytest.fixture
def counter(app):
    user = User(
        email="fc_counter@test.local",
        password_hash=bcrypt.generate_password_hash("Passw0rd!").decode("utf-8"),
        full_name="Counter",
        role=UserRole.OFFICE_STAFF,
        status=UserStatus.ACTIVE,
        active=True,
    )
    db.session.add(user)
    db.session.commit()
    return user


def _add_scans(tool, worker, on_dates, qty_each=1):
    """One SCAN event per shop date (qty_each units), non-undone."""
    for on in on_dates:
        for _ in range(qty_each):
            db.session.add(
                ToolEvent(
                    tool_id=tool.id,
                    worker_id=worker.id,
                    type=ToolEventType.SCAN,
                    quantity=Decimal("1"),
                    stock_delta=Decimal("-1"),
                    created_at=datetime.combine(on, time(8, 0), tzinfo=timezone.utc),
                )
            )
    db.session.commit()


def test_consumable_run_out_scan_based_adu_and_labels(counter):
    """≥14 working days of scans → Computed; no history → No data yet."""
    tracked = Tool(
        name="FC Insert",
        code="FC-INS",
        category=ToolCategory.CONSUMABLE,
        unit="pcs",
        quantity_on_hand=Decimal("10"),
    )
    never = Tool(
        name="FC Rag",
        code="FC-RAG",
        category=ToolCategory.CONSUMABLE,
        unit="pcs",
        quantity_on_hand=Decimal("5"),
    )
    db.session.add_all([tracked, never])
    db.session.commit()

    # 18 weekdays of 2 scans/day starting Mon 2031-03-03 → ADU = 2
    days = []
    d = date(2031, 3, 3)
    while len(days) < 18:
        if d.weekday() < 6:  # Mon–Sat shop
            days.append(d)
        d += __import__("datetime").timedelta(days=1)
    _add_scans(tracked, counter, days, qty_each=2)
    tracked.quantity_on_hand = Decimal("10")
    db.session.commit()

    today = days[-1]
    out = fs.consumable_run_out(today=today)
    rows = {r["code"]: r for r in out["items"]}

    ins = rows["FC-INS"]
    assert ins["label"] == "Computed"
    assert ins["enoughData"] is True
    assert ins["adu"] == pytest.approx(2.0)
    assert ins["quantityOnHand"] == pytest.approx(10.0)
    assert ins["daysLeft"] == pytest.approx(5.0)
    assert out.get("formulas")
    assert "safety" in out["description"].lower()

    rag = rows["FC-RAG"]
    assert rag["label"] == "No data yet"
    assert rag["status"] == "NO_DATA"
    assert rag["runOutDate"] is None


def test_safety_stock_scan_and_receive_raises_on_hand(counter):
    tool = Tool(
        name="FC Disc",
        code="FC-DSC",
        category=ToolCategory.CONSUMABLE,
        unit="pcs",
        quantity_on_hand=Decimal("20"),
    )
    db.session.add(tool)
    db.session.commit()

    days = []
    d = date(2031, 5, 5)
    while len(days) < 15:
        if d.weekday() < 6:
            days.append(d)
        d += __import__("datetime").timedelta(days=1)
    _add_scans(tool, counter, days, qty_each=2)
    tool.quantity_on_hand = Decimal("20")
    db.session.commit()
    today = days[-1]

    def row(on_hand=None):
        if on_hand is not None:
            tool.quantity_on_hand = Decimal(str(on_hand))
            db.session.commit()
        out = fs.consumable_run_out(today=today)
        return next(r for r in out["items"] if r["code"] == "FC-DSC")

    before = row(on_hand=20)
    assert before["adu"] == pytest.approx(2.0)
    assert before["label"] == "Computed"
    run_out_before = before["runOutDate"]
    assert run_out_before

    after = row(on_hand=30)
    assert after["quantityOnHand"] == pytest.approx(30.0)
    assert after["daysLeft"] == pytest.approx(15.0)
    assert after["runOutDate"] > run_out_before

    # max(0, 4×10 − 2×5) = 30
    assert fs.safety_stock_scan(adu=2, max_du=4, avg_lead_wd=5, max_lead_wd=10) == pytest.approx(30.0)
    assert fs.safety_stock_scan(adu=2, max_du=2, avg_lead_wd=5, max_lead_wd=5) == pytest.approx(0.0)

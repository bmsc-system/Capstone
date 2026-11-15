"""Sample-history generate and remove, on the test database only."""

from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest
from sqlalchemy.dialects.postgresql import JSONB

from app.extensions import db
from app.models.attendance import AttendanceRecord
from app.models.client import Client
from app.models.consumable_purchase import ConsumablePurchase
from app.models.job_order import JobOrder, JobOrderStatus, JobType, MaterialStatus, PartCondition
from app.models.material_purchase import MaterialPurchase
from app.models.supplier_order import SupplierOrder, SupplierOrderStatus
from app.models.tool_event import ToolEvent, ToolEventType
from app.models.machine import MachineType, MachineUnit
from app.models.material_catalog import MaterialCatalogItem
from app.models.notification import NotificationLog
from app.models.sample_record import SampleRecord
from app.models.supplier import Supplier
from app.models.tool import Tool, ToolCategory
from app.models.tool_type import ToolType, ToolUnit, ToolUnitStatus
from app.models.user import User, UserRole, UserStatus
from app.models.worker_skill import (
    CalendarExceptionType,
    OperationType,
    WorkCalendarException,
    WorkerSchedule,
    WorkerSkill,
)
from app.services.sample_history_service import clear_old_demo, generate, remove


def _person(email, role, name):
    user = User(
        email=email,
        password_hash="x",
        full_name=name,
        role=role,
        status=UserStatus.ACTIVE,
        active=True,
    )
    db.session.add(user)
    db.session.flush()
    if role == UserRole.PRODUCTION_WORKER:
        for dow in range(7):
            working = dow < 6
            db.session.add(
                WorkerSchedule(
                    worker_id=user.id,
                    day_of_week=dow,
                    is_working=working,
                    start_time=time(8, 0) if working else None,
                    end_time=time(17, 0) if working else None,
                )
            )
    return user


def _shop():
    admin = _person("ada.quill@example.test", UserRole.ADMIN, "Ada Quill")
    _person("nia.holt@example.test", UserRole.OFFICE_STAFF, "Nia Holt")
    wren = _person("wren.calder@example.test", UserRole.PRODUCTION_WORKER, "Wren Calder")
    moss = _person("moss.ellery@example.test", UserRole.PRODUCTION_WORKER, "Moss Ellery")
    db.session.add(Client(name="Northline Works", notify_by_email=False, notify_by_sms=False))
    db.session.add(Client(name="Bramble Assembly", notify_by_email=False, notify_by_sms=False))
    db.session.add(
        Supplier(name="Harbor Metals", code="HRB", typical_lead_time_days=5, active=True)
    )
    db.session.add(
        Supplier(name="Cedar Alloys", code="CDR", typical_lead_time_days=7, active=True)
    )
    lathe = MachineType(code="LATHE", name="Lathe", units=2)
    db.session.add(lathe)
    db.session.flush()
    for label, person in (("Lathe 1", wren), ("Lathe 2", moss)):
        db.session.add(
            MachineUnit(
                machine_type_id=lathe.id,
                label=label,
                active=True,
                default_operator_id=person.id,
            )
        )
        db.session.add(
            WorkerSkill(worker_id=person.id, machine_type_id=lathe.id, proficiency=3, is_primary=True)
        )
    db.session.add(
        OperationType(
            code="TURN",
            name="Turning",
            default_machine_type_id=lathe.id,
            active=True,
            is_outsourced=False,
        )
    )
    db.session.add(
        MaterialCatalogItem(
            name="Round bar",
            grades=["1018", "1045"],
            default_unit="kg",
            category="Steel",
            active=True,
        )
    )
    for name, code, qty in (
        ("Cutting disc 4 inch", "CD4", Decimal("40")),
        ("Grinding disc 7 inch", "GD7", Decimal("30")),
        ("Hand tap", "TAP", Decimal("20")),
        ("Carbide insert", "INS", Decimal("16")),
    ):
        db.session.add(
            Tool(
                name=name,
                code=code,
                category=ToolCategory.CONSUMABLE,
                unit="pcs",
                quantity_on_hand=qty,
                unit_cost=Decimal("25"),
                active=True,
            )
        )
    mic = ToolType(name="Micrometer", code="MIC")
    db.session.add(mic)
    db.session.flush()
    db.session.add(
        ToolUnit(tool_type_id=mic.id, asset_code="MIC-01", status=ToolUnitStatus.AVAILABLE)
    )
    db.session.commit()
    return admin


def test_record_id_is_text_and_detail_is_json():
    assert isinstance(SampleRecord.record_id.type, db.String)
    assert not isinstance(SampleRecord.record_id.type, db.Integer)
    assert isinstance(SampleRecord.detail.type, JSONB)


def test_dry_run_rolls_back_and_is_repeatable(app):
    _shop()
    before = JobOrder.query.count()
    first = generate(date(2026, 4, 6), date(2026, 4, 17), seed=7, apply=False)
    assert JobOrder.query.count() == before
    assert SampleRecord.query.count() == 0
    second = generate(date(2026, 4, 6), date(2026, 4, 17), seed=7, apply=False)
    assert second["jobs"] == first["jobs"]
    assert second["firstTitles"] == first["firstTitles"]
    assert first["jobs"] > 0


def test_apply_then_remove_restores_stock_and_deletes_holidays(app):
    _shop()
    client = Client.query.filter_by(name="Northline Works").one()
    unit = ToolUnit.query.filter_by(asset_code="MIC-01").one()
    disc = Tool.query.filter_by(code="CD4").one()
    opening = Decimal(str(disc.quantity_on_hand))
    held = unit.current_holder_id

    summary = generate(date(2026, 4, 6), date(2026, 4, 17), seed=7, apply=True)
    assert summary["applied"] is True
    assert summary["jobs"] > 0
    assert "2026-04-09" in summary["holidaysAdded"]
    assert "2026-05-01" in summary["holidaysAdded"]
    holiday = WorkCalendarException.query.filter_by(date=date(2026, 4, 9)).one()
    assert holiday.type == CalendarExceptionType.HOLIDAY_NO_WORK
    assert (
        SampleRecord.query.filter_by(
            table_name="work_calendar_exceptions", record_id=holiday.id
        ).count()
        == 1
    )
    # Consumables are bought and handed out to workers for their operations;
    # stock on hand is what was bought less what was handed out.
    assert summary["consumablePurchases"] > 0
    assert ConsumablePurchase.query.count() > 0
    assert summary["handOuts"] > 0
    issued = ToolEvent.query.filter_by(type=ToolEventType.ISSUE, tool_id=disc.id).all()
    bought = [p.quantity for p in ConsumablePurchase.query.filter_by(tool_id=disc.id)]
    bought += [
        ln.quantity
        for ln in MaterialPurchase.query.filter_by(tool_id=disc.id)
        if ln.date_received is not None
    ]
    db.session.refresh(disc)
    assert Decimal(str(disc.quantity_on_hand)) == sum(bought, Decimal("0")) - sum(
        (e.quantity for e in issued), Decimal("0")
    )
    assert all(e.operation_id and e.recorded_by_id for e in issued)
    db.session.refresh(unit)
    assert unit.status == ToolUnitStatus.AVAILABLE
    assert unit.current_holder_id == held
    assert summary["toolUnitsOpenBorrow"] == 0
    assert summary["toolBorrows"] == summary["toolReturns"]
    assert Client.query.filter_by(id=client.id).one().name == "Northline Works"
    assert NotificationLog.query.count() == 0
    assert JobOrder.query.filter_by(created_by_id=User.query.filter_by(full_name="Ada Quill").one().id).count() > 0

    with pytest.raises(ValueError, match="already exists"):
        generate(date(2026, 4, 6), date(2026, 4, 17), seed=7, apply=True)

    preview = remove(apply=False)
    assert preview["ledgerRows"] > 0
    assert JobOrder.query.count() == summary["jobs"]
    assert WorkCalendarException.query.filter_by(date=date(2026, 5, 1)).count() == 1

    done = remove(apply=True)
    assert JobOrder.query.count() == 0
    assert ConsumablePurchase.query.count() == 0
    assert SampleRecord.query.count() == 0
    assert WorkCalendarException.query.filter_by(date=date(2026, 4, 9)).count() == 0
    assert WorkCalendarException.query.filter_by(date=date(2026, 5, 1)).count() == 0
    db.session.refresh(disc)
    assert Decimal(str(disc.quantity_on_hand)) == opening
    assert Client.query.filter_by(name="Northline Works").count() == 1
    assert User.query.filter_by(full_name="Wren Calder").count() == 1


def test_apply_aborts_when_the_host_does_not_match(app):
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=[
            "sample-history",
            "generate",
            "--start",
            "2026-04-06",
            "--end",
            "2026-04-17",
            "--seed",
            "7",
            "--apply",
        ],
        input="not-the-host\n",
    )
    assert result.exit_code != 0
    assert "Host confirmation failed" in result.output
    assert JobOrder.query.count() == 0


def test_clear_old_demo_deletes_marked_rows_and_rewinds_stock(app):
    admin = _shop()
    disc = Tool.query.filter_by(code="CD4").one()
    disc.quantity_on_hand = Decimal("50")
    supplier = Supplier.query.filter_by(code="HRB").one()
    client = Client.query.filter_by(name="Northline Works").one()
    worker = User.query.filter_by(full_name="Wren Calder").one()
    when = datetime(2026, 10, 3, 1, 0, tzinfo=timezone.utc)

    def job(title, po, amount):
        row = JobOrder(
            client_id=client.id,
            client_po_number=po,
            title=title,
            due_date=date(2026, 10, 20),
            status=JobOrderStatus.DRAFT,
            job_type=JobType.FABRICATION,
            part_condition=PartCondition.RAW_MATERIAL,
            material_status=MaterialStatus.NOT_REQUIRED,
            raw_materials=[],
            amount=Decimal(amount),
            created_by_id=admin.id,
            created_at=when,
        )
        db.session.add(row)
        return row

    job("Kept bushing", "PO-REAL-1", "12000")
    job("Flange drilling run", "HIST-SEED-1", "55000")
    job("frfr", "12738", "56")
    db.session.add(
        AttendanceRecord(
            worker_id=worker.id,
            work_date=date(2026, 10, 3),
            clock_in=when,
            clock_out=when,
            recorded_by_id=admin.id,
        )
    )
    order = SupplierOrder(
        supplier_id=supplier.id,
        po_number="HRB-OLD-1",
        status=SupplierOrderStatus.RECEIVED,
        date_issued=date(2026, 9, 1),
        notes="consumable restock",
        prepared_by_id=admin.id,
    )
    db.session.add(order)
    db.session.flush()
    line = MaterialPurchase(
        tool_id=disc.id,
        material_name=disc.name,
        quantity=Decimal("10"),
        unit="pcs",
        unit_cost=Decimal("25"),
        supplier_id=supplier.id,
        supplier_order_id=order.id,
        date_ordered=date(2026, 9, 1),
        date_received=date(2026, 9, 8),
    )
    db.session.add(line)
    db.session.flush()
    db.session.add(
        ToolEvent(
            tool_id=disc.id,
            worker_id=admin.id,
            type=ToolEventType.RECEIVE,
            quantity=Decimal("10"),
            stock_delta=Decimal("10"),
            material_purchase_id=line.id,
        )
    )
    db.session.add(
        ToolEvent(
            tool_id=disc.id,
            worker_id=admin.id,
            type=ToolEventType.ADJUST,
            quantity=Decimal("2"),
            stock_delta=Decimal("-2"),
            reason="Orphan count",
        )
    )
    db.session.commit()

    preview = clear_old_demo(apply=False)
    assert preview["applied"] is False
    assert preview["counts"]["HIST-SEED jobs"] == 1
    assert preview["counts"]["manual scraps"] == 1
    assert preview["counts"]["consumable restock orders"] == 1
    assert preview["counts"]["receive events"] == 1
    assert preview["counts"]["adjust events"] == 1
    assert JobOrder.query.count() == 3
    db.session.refresh(disc)
    assert Decimal(str(disc.quantity_on_hand)) == Decimal("50")

    done = clear_old_demo(apply=True)
    assert done["applied"] is True
    assert JobOrder.query.count() == 1
    assert JobOrder.query.filter_by(title="Kept bushing").count() == 1
    assert SupplierOrder.query.count() == 0
    assert MaterialPurchase.query.count() == 0
    assert ToolEvent.query.count() == 0
    assert AttendanceRecord.query.count() == 1
    db.session.refresh(disc)
    assert Decimal(str(disc.quantity_on_hand)) == Decimal("42")
    assert done["stock"][0]["before"] == "50.00"
    assert done["stock"][0]["after"] == "42.00"
    assert Client.query.filter_by(name="Northline Works").count() == 1
    assert User.query.filter_by(full_name="Wren Calder").count() == 1


def test_consumables_get_different_buying_rhythms(app):
    _shop()
    generate(date(2026, 1, 5), date(2026, 4, 17), seed=7, apply=True)
    from app.services.replenishment_service import replenishment

    data = replenishment(date(2026, 4, 17))
    forecast = [r for r in data["items"] if r["everyDays"] is not None]
    assert len(forecast) >= 2
    assert len({r["everyDays"] for r in forecast}) > 1
    assert len({r["nextPurchaseOn"] for r in forecast}) > 1

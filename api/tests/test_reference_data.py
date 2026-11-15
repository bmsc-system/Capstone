"""`flask load-reference-data`: idempotent, safe in production, updates
existing records in place and never touches accounts or job data."""

from datetime import date
from decimal import Decimal

import pytest
from flask_jwt_extended import create_access_token

from app.extensions import bcrypt, db
from app.models.client import Client
from app.models.job_order import JobOrder, JobOrderStatus, JobType, MaterialStatus, PartCondition
from app.models.machine import MachineType, MachineUnit
from app.models.material_catalog import MaterialCatalogItem
from app.models.operation import JobOperation, OperationStatus
from app.models.shop_settings import ShopSettings
from app.models.supplier import Supplier
from app.models.supplier_order import SupplierOrder, SupplierOrderStatus
from app.models.tool import Tool, ToolCategory
from app.models.user import User, UserRole, UserStatus
from app.models.worker_skill import OperationType
from app.services import supplier_order_service as so_service
from app.services.reference_data_service import load_reference_data
from app.utils.errors import AppError


def _user(email, role):
    user = User(
        email=email,
        password_hash=bcrypt.generate_password_hash("Passw0rd!").decode("utf-8"),
        full_name=email.split("@")[0],
        role=role,
        status=UserStatus.ACTIVE,
        active=True,
    )
    db.session.add(user)
    db.session.flush()
    return user


def _headers(user):
    token = create_access_token(identity=user.id, additional_claims={"role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


def _job(client, creator, supplier=None, status=JobOrderStatus.COMPLETED):
    job = JobOrder(
        client_id=client.id,
        supplier_id=supplier.id if supplier else None,
        title="Bracket set",
        due_date=date(2026, 10, 30),
        status=status,
        job_type=JobType.FABRICATION,
        part_condition=PartCondition.RAW_MATERIAL,
        material_status=MaterialStatus.NOT_REQUIRED,
        raw_materials=[],
        amount=Decimal("1000"),
        created_by_id=creator.id,
    )
    db.session.add(job)
    db.session.flush()
    return job


def _counts():
    return {
        "users": User.query.count(),
        "jobs": JobOrder.query.count(),
        "operations": JobOperation.query.count(),
        "supplier_orders": SupplierOrder.query.count(),
    }


def test_fresh_database_gets_full_reference_data(app):
    report = load_reference_data()
    assert report.changes

    suppliers = {s.code: s for s in Supplier.query.all()}
    assert set(suppliers) == {"RIC", "STP", "RTC"}
    assert suppliers["RIC"].name == "Railim Industrial Corporation"
    assert suppliers["RIC"].contact_person == "Claudia"
    assert suppliers["RIC"].typical_lead_time_days == 7
    assert suppliers["STP"].address == "378-376 Rizal Avenue Extension, Caloocan"
    assert suppliers["RTC"].typical_lead_time_days == 5
    assert all(s.active for s in suppliers.values())

    machines = {mt.code: mt for mt in MachineType.query.all()}
    assert machines["LATHE"].units == 8
    assert machines["BENDING"].name == "Bending"
    assert machines["LASER"].name == "Laser"
    labels = {u.label: u for u in MachineUnit.query.all()}
    for label in ("Bending #1", "Laser #1", "Lathe #8"):
        assert labels[label].active
        assert labels[label].default_operator_id is None

    ops = {ot.code: ot for ot in OperationType.query.all()}
    assert ops["CUTTING"].default_machine_type_id == machines["LASER"].id
    assert ops["BENDING"].default_machine_type_id == machines["BENDING"].id
    assert ops["TURNING"].default_machine_type_id == machines["LATHE"].id

    assert MaterialCatalogItem.query.count() == 15
    teflon = MaterialCatalogItem.query.filter_by(name="Teflon").one()
    assert teflon.shop_term == "PTFE" and teflon.grades == []
    assert {i.default_unit for i in MaterialCatalogItem.query.all()} <= {"pcs", "sheet"}

    tools = {t.name: t for t in Tool.query.all()}
    assert tools["Sandpaper"].shop_term == "Liha"
    assert tools["Sandpaper"].unit == "pack"
    assert tools["Tungsten carbide tip"].shop_term == "Tungatip"
    assert all(t.minimum_stock is None for t in tools.values())

    settings = db.session.get(ShopSettings, 1)
    assert settings.shop_name == "BROTHERS MACHINE SHOP and SERVICES CORP."
    assert settings.po_approver_name == "Approver name"
    assert settings.jo_approver_name == "Prepared by"
    assert settings.jo_approver_title == "Production Head"


def test_second_run_changes_nothing(app):
    load_reference_data()
    snapshot = {
        "suppliers": sorted((s.id, s.name, s.code, s.active) for s in Supplier.query.all()),
        "units": MachineUnit.query.count(),
        "op_types": OperationType.query.count(),
        "materials": MaterialCatalogItem.query.count(),
        "tools": Tool.query.count(),
    }
    report = load_reference_data()
    assert report.changes == []
    assert snapshot == {
        "suppliers": sorted((s.id, s.name, s.code, s.active) for s in Supplier.query.all()),
        "units": MachineUnit.query.count(),
        "op_types": OperationType.query.count(),
        "materials": MaterialCatalogItem.query.count(),
        "tools": Tool.query.count(),
    }


def test_rerun_keeps_supplier_status_changed_by_the_shop(app):
    seno = Supplier(name="Seno Metals", typical_lead_time_days=1, active=True)
    metro = Supplier(name="Metro Hardware", typical_lead_time_days=2, active=True)
    db.session.add_all([seno, metro])
    db.session.commit()

    load_reference_data()
    assert not db.session.get(Supplier, seno.id).active
    assert not db.session.get(Supplier, metro.id).active
    assert db.session.get(Supplier, seno.id).reference_loaded_at is not None

    # The shop reactivates Seno Metals and pauses Raitech.
    db.session.get(Supplier, seno.id).active = True
    Supplier.query.filter_by(code="RTC").one().active = False
    db.session.commit()

    report = load_reference_data()
    assert report.changes == []
    assert db.session.get(Supplier, seno.id).active
    assert not db.session.get(Supplier, metro.id).active
    assert not Supplier.query.filter_by(code="RTC").one().active
    assert any("Seno Metals" in n and "(active" in n for n in report.notes)


def test_first_update_of_an_existing_supplier_sets_its_status(app):
    railim = Supplier(name="Railim", typical_lead_time_days=5, active=False)
    db.session.add(railim)
    db.session.commit()

    load_reference_data(dry_run=True)
    assert db.session.get(Supplier, railim.id).reference_loaded_at is None

    load_reference_data()
    railim = db.session.get(Supplier, railim.id)
    assert railim.code == "RIC" and railim.active
    assert railim.reference_loaded_at is not None


def test_cli_runs_in_production_without_accounts_or_job_data(app):
    app.config["ENV"] = "production"
    runner = app.test_cli_runner()

    result = runner.invoke(args=["load-reference-data"])
    assert result.exit_code == 0, result.output
    assert "Changed (" in result.output
    assert _counts() == {"users": 0, "jobs": 0, "operations": 0, "supplier_orders": 0}

    again = runner.invoke(args=["load-reference-data"])
    assert again.exit_code == 0, again.output
    assert "Changed (0)" in again.output


def test_dry_run_saves_nothing(app):
    result = app.test_cli_runner().invoke(args=["load-reference-data", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Dry run: nothing was saved." in result.output
    assert "Supplier RTC: created" in result.output
    db.session.expire_all()
    assert Supplier.query.count() == 0
    assert MachineType.query.count() == 0
    assert MaterialCatalogItem.query.count() == 0


def test_existing_records_updated_in_place_and_history_kept(app):
    office = _user("office@test.local", UserRole.OFFICE_STAFF)
    client = Client(name="Client A")
    railim = Supplier(name="Railim", typical_lead_time_days=5, is_seed=True)
    railim_dup = Supplier(name="Railim Industrial Corporation", typical_lead_time_days=7)
    stp = Supplier(name="STP", typical_lead_time_days=5)
    seno = Supplier(name="Seno Metals", typical_lead_time_days=1)
    metro = Supplier(name="Metro Hardware", typical_lead_time_days=2)
    db.session.add_all([client, railim, railim_dup, stp, seno, metro])
    lathe = MachineType(code="LATHE", name="Lathe", units=7)
    db.session.add(lathe)
    db.session.flush()
    for n in range(1, 8):
        db.session.add(MachineUnit(machine_type_id=lathe.id, label=f"Lathe #{n}", active=True))
    cutting = OperationType(code="CUTTING", name="Cutting")
    tonga = Tool(
        name="Tonga tip", code="INV-TONGA-STD", category=ToolCategory.CONSUMABLE,
        unit="pcs", quantity_on_hand=Decimal("40"), minimum_stock=Decimal("12"),
        ordered_from_supplier=True,
    )
    db.session.add_all([cutting, tonga])
    db.session.flush()

    job = _job(client, office, supplier=railim)
    done_cut = JobOperation(
        job_order_id=job.id, sequence_no=1, operation_name="Cutting",
        operation_type_id=cutting.id, status=OperationStatus.COMPLETED,
    )
    open_order = SupplierOrder(
        supplier_id=seno.id, status=SupplierOrderStatus.ISSUED, po_seq=1,
        po_number="BMSC-PO-00001", date_issued=date(2026, 10, 1),
        expected_delivery_date=date(2026, 10, 2), prepared_by_id=office.id,
    )
    db.session.add_all([done_cut, open_order])
    db.session.commit()
    ids = {"railim": railim.id, "stp": stp.id, "tonga": tonga.id, "lathe": lathe.id}
    before = _counts()

    report = load_reference_data()
    db.session.expire_all()

    ric = db.session.get(Supplier, ids["railim"])
    assert (ric.name, ric.code, ric.typical_lead_time_days) == (
        "Railim Industrial Corporation", "RIC", 7,
    )
    assert ric.address == "Barangay San Antonio, Sto. Tomas, Batangas"
    dup = db.session.get(Supplier, railim_dup.id)
    assert not dup.active and dup.code is None
    assert dup.name.endswith("(duplicate)")
    assert db.session.get(Supplier, ids["stp"]).name == "STP Industrial Inc."
    assert not db.session.get(Supplier, seno.id).active
    assert not db.session.get(Supplier, metro.id).active
    assert any("Seno Metals" in n and "1 supplier orders (1 open)" in n for n in report.notes)

    assert _counts() == before
    assert db.session.get(JobOrder, job.id).supplier_id == ids["railim"]
    order = db.session.get(SupplierOrder, open_order.id)
    assert order.expected_delivery_date == date(2026, 10, 2)
    assert order.po_number == "BMSC-PO-00001"
    op = db.session.get(JobOperation, done_cut.id)
    assert op.machine_type_id is None and op.machine_unit_id is None

    assert db.session.get(MachineType, ids["lathe"]).units == 8
    tip = db.session.get(Tool, ids["tonga"])
    assert (tip.name, tip.shop_term, tip.minimum_stock) == (
        "Tungsten carbide tip", "Tungatip", Decimal("12.00"),
    )
    assert Tool.query.filter_by(name="Tungsten carbide tip").count() == 1


def test_po_number_carries_supplier_code(app):
    office = _user("office@test.local", UserRole.OFFICE_STAFF)
    load_reference_data()
    ric = Supplier.query.filter_by(code="RIC").one()
    tool = Tool.query.filter_by(name="Sandpaper").one()
    tool.ordered_from_supplier = True

    order, _ = so_service.add_lines_to_draft(ric.id, [{"toolId": tool.id, "quantity": 10}], office.id)
    so_service.submit_for_approval(order, office.id, "2026-10-05")
    so_service.issue_order(order, office.id, "2026-10-05")
    assert order.po_number == "RIC26000001"

    plain = Supplier(name="No Code Supplier", typical_lead_time_days=3)
    db.session.add(plain)
    db.session.commit()
    order2, _ = so_service.add_lines_to_draft(plain.id, [{"toolId": tool.id, "quantity": 5}], office.id)
    with pytest.raises(AppError) as exc:
        so_service.submit_for_approval(order2, office.id, "2026-10-05")
        so_service.issue_order(order2, office.id, "2026-10-05")
    assert exc.value.code == "SUPPLIER_CODE_MISSING"
    assert order2.po_number is None


def test_inactive_supplier_draft_cannot_be_issued(app):
    office = _user("office@test.local", UserRole.OFFICE_STAFF)
    load_reference_data()
    stp = Supplier.query.filter_by(code="STP").one()
    tool = Tool.query.filter_by(name="Sandpaper").one()
    tool.ordered_from_supplier = True
    order, _ = so_service.add_lines_to_draft(stp.id, [{"toolId": tool.id, "quantity": 1}], office.id)
    so_service.submit_for_approval(order, office.id)
    stp.active = False
    db.session.commit()
    with pytest.raises(AppError) as exc:
        so_service.issue_order(order, office.id)
    assert exc.value.code == "SUPPLIER_INACTIVE"


def test_supplier_code_validation(app, client):
    office = _user("office@test.local", UserRole.OFFICE_STAFF)
    load_reference_data()
    rtc = Supplier.query.filter_by(code="RTC").one()
    headers = _headers(office)

    bad = client.patch(f"/api/v1/suppliers/{rtc.id}", json={"code": "R1"}, headers=headers)
    assert bad.status_code == 400
    taken = client.patch(f"/api/v1/suppliers/{rtc.id}", json={"code": "ric"}, headers=headers)
    assert taken.status_code == 409
    ok = client.patch(f"/api/v1/suppliers/{rtc.id}", json={"code": "rtx"}, headers=headers)
    assert ok.status_code == 200
    assert ok.get_json()["code"] == "RTX"


def test_material_catalog_search_and_roles(app, client):
    office = _user("office@test.local", UserRole.OFFICE_STAFF)
    admin = _user("admin@test.local", UserRole.ADMIN)
    load_reference_data()

    res = client.get("/api/v1/material-catalog?search=41-40", headers=_headers(admin))
    assert res.status_code == 200
    assert [i["name"] for i in res.get_json()["items"]] == ["4140"]
    res = client.get("/api/v1/material-catalog?search=delrin", headers=_headers(office))
    assert [i["name"] for i in res.get_json()["items"]] == ["Acetal"]

    by_weight = {"name": "304", "defaultUnit": "kg", "category": "Stainless steel"}
    assert client.post("/api/v1/material-catalog", json=by_weight, headers=_headers(office)).status_code == 400
    body = {"name": "304", "defaultUnit": "pcs", "category": "Stainless steel"}
    assert client.post("/api/v1/material-catalog", json=body, headers=_headers(admin)).status_code == 403
    created = client.post("/api/v1/material-catalog", json=body, headers=_headers(office))
    assert created.status_code == 201
    dup = client.post("/api/v1/material-catalog", json=body, headers=_headers(office))
    assert dup.status_code == 409


def test_shop_details_admin_edit(app, client):
    office = _user("office@test.local", UserRole.OFFICE_STAFF)
    admin = _user("admin@test.local", UserRole.ADMIN)
    load_reference_data()

    res = client.get("/api/v1/shop-details", headers=_headers(office))
    assert res.status_code == 200
    assert res.get_json()["mobileNumbers"] == "09260056680 / 09157859720"

    change = {"joApproverName": "Juan Dela Cruz"}
    assert client.put("/api/v1/shop-details", json=change, headers=_headers(office)).status_code == 403
    res = client.put("/api/v1/shop-details", json=change, headers=_headers(admin))
    assert res.status_code == 200
    assert res.get_json()["joApproverName"] == "Juan Dela Cruz"
    assert client.put(
        "/api/v1/shop-details", json={"shopName": ""}, headers=_headers(admin)
    ).status_code == 400

from datetime import date, time, timedelta
from decimal import Decimal
import os

from app.extensions import bcrypt, db
from app.models import (
    Client,
    JobOperation,
    JobOrder,
    JobOrderStatus,
    JobPriority,
    JobType,
    MachineType,
    MaterialPurchase,
    MaterialStatus,
    OperationStatus,
    OperationType,
    PartCondition,
    ScoringWeight,
    Tool,
    ToolCategory,
    ToolEvent,
    ToolEventType,
    User,
    UserRole,
    UserStatus,
    WorkerProfile,
    WorkerSchedule,
    WorkerSkill,
)
from app.models.scoring_weight import DEFAULT_SCORING_WEIGHTS
from app.models.worker_skill import SKILL_TOKEN_TO_MACHINE


def _load_reference_data():
    """Suppliers, machines, operation types, material catalog, consumables and
    shop details come from the same loader used in production."""
    from app.services.reference_data_service import load_reference_data

    report = load_reference_data(commit=False)
    if report.changes:
        print(f"Reference data: {len(report.changes)} changes.")
    return (
        {mt.code: mt for mt in MachineType.query.all()},
        {ot.code: ot for ot in OperationType.query.all()},
    )


def _default_schedule(worker_id):
    """Mon–Sat 08:00–17:00; Sunday off. day_of_week: 0=Mon … 6=Sun."""
    rows = []
    for dow in range(7):
        working = dow < 6
        rows.append(
            WorkerSchedule(
                worker_id=worker_id,
                day_of_week=dow,
                start_time=time(8, 0) if working else None,
                end_time=time(17, 0) if working else None,
                is_working=working,
            )
        )
    return rows


def _skills_from_tokens(worker_id, tokens, machines):
    mapped = []
    for token in tokens:
        code = SKILL_TOKEN_TO_MACHINE.get(str(token).strip().lower())
        if code and code in machines and code not in mapped:
            mapped.append(code)
    rows = []
    for i, code in enumerate(mapped):
        rows.append(
            WorkerSkill(
                worker_id=worker_id,
                machine_type_id=machines[code].id,
                proficiency=3,
                is_primary=(i == 0),
            )
        )
    return rows


def _ensure_scoring_weights():
    if ScoringWeight.query.first():
        return
    for key, value in DEFAULT_SCORING_WEIGHTS.items():
        db.session.add(ScoringWeight(key=key, value=value))
    db.session.flush()


def _inventory_catalog():
    """Consumable item types — stocktake model (one code per size/spec)."""
    C = ToolCategory.CONSUMABLE
    return [
        # Names from the shop's list (app/constants/inventory_names.py).
        ("Drill Bits", "INV-DRILL-06", C, "6mm", "pcs", 30, 10),
        ("Drill Bits", "INV-DRILL-08", C, "8mm", "pcs", 28, 10),
        ("Drill Bits", "INV-DRILL-10", C, "10mm", "pcs", 24, 8),
        ("Drill Bits", "INV-DRILL-12", C, "12mm", "pcs", 18, 6),
        ("Endmills", "INV-ENDMILL-10", C, "10mm", "pcs", 16, 5),
        ("Endmills", "INV-ENDMILL-12", C, "12mm", "pcs", 14, 5),
        ("Handtaps", "INV-HANDTAP-M8", C, "M8", "pcs", 20, 6),
        ("Handtaps", "INV-HANDTAP-M10", C, "M10", "pcs", 16, 5),
        ("Fluids", "INV-OIL-CUT", C, "Cutting oil", "bottle", 20, 5),
        ("Sandpaper", "INV-SANDPAPER", C, "grit 120", "pack", 30, 10),
        ("Bandsaw Blade", "INV-BANDSAW", C, None, "pcs", 6, 2),
        ("Grinding Stone", "INV-GRIND-ST", C, None, "pcs", 10, 3),
        ("Flap Disk", "INV-FLAP-115", C, "115mm", "pcs", 36, 12),
        ("Cutting Disk", "INV-CUT-115", C, "115mm", "pcs", 40, 12),
    ]


def _tool_asset_catalog():
    """
    Individually tracked tools from the shop's list (app/constants/inventory_names.py).
    Each entry: (name, type_code, description, unit_count)
    """
    return [
        ("Caliper", "TL-CALIPER", None, 3),
        ("Micrometer", "TL-MICROMETER", None, 2),
        ("Hammers", "TL-HAMMERS", None, 3),
        ("Collets", "TL-COLLETS", None, 4),
        ("Clamps", "TL-CLAMPS", None, 4),
    ]


def _ensure_inventory_catalog():
    """
    Replace seeded machine-as-tool rows (duplicates of MachineUnit) with
    consumable catalog. Safe to re-run: only wipes when legacy codes
    are present or the catalog is empty.
    """
    legacy_codes = {
        "TOOL-MILL-001",
        "TOOL-LATH-002",
        "TOOL-WELD-003",
        "TOOL-GRND-004",
        "TOOL-DRL-005",
        "TOOL-CNC-006",
        "TOOL-ANGL-007",
        "TOOL-VISE-008",
    }
    has_legacy = Tool.query.filter(Tool.code.in_(legacy_codes)).first() is not None
    empty = Tool.query.count() == 0
    # Countable-container remaps (old unit kept as legacy_unit for Office review).
    unit_remap = {
        "INV-OIL-CUT": ("bottle", "litre"),
        "INV-SANDPAPER": ("pack", "sheet"),
    }
    if not has_legacy and not empty:
        # Reclassify any leftover returnables to consumable (idempotent)
        for t in Tool.query.filter_by(category=ToolCategory.RETURNABLE_TOOL).all():
            t.category = ToolCategory.CONSUMABLE
        for code, (new_unit, old_unit) in unit_remap.items():
            t = Tool.query.filter_by(code=code).first()
            if t and t.unit == old_unit:
                t.legacy_unit = old_unit
                t.unit = new_unit
                t.unit_needs_review = True
        return Tool.query.filter_by(category=ToolCategory.CONSUMABLE).all()

    ToolEvent.query.filter(ToolEvent.tool_id.isnot(None)).delete(synchronize_session=False)
    Tool.query.delete()
    db.session.flush()

    tools = []
    for name, code, category, size, unit, qty, minimum in _inventory_catalog():
        tools.append(
            Tool(
                name=name,
                code=code,
                category=category,
                size_spec=size,
                unit=unit,
                quantity_on_hand=Decimal(str(qty)),
                minimum_stock=Decimal(str(minimum)),
                # The shop orders cutting oil from a supplier by the gallon;
                # the other consumables are bought at nearby stores.
                ordered_from_supplier=name == "Cutting oil",
            )
        )
    db.session.add_all(tools)
    db.session.flush()
    print(f"Consumable catalog: {len(tools)} item types seeded.")
    return tools


def _ensure_suppliers():
    """The shop's active suppliers (RIC, STP, RTC) from the reference loader,
    in that order."""
    from app.models.supplier import Supplier
    from app.services.reference_data_service import SUPPLIERS

    _load_reference_data()
    by_code = {s.code: s for s in Supplier.query.filter(Supplier.code.isnot(None)).all()}
    return [by_code[spec["code"]] for spec in SUPPLIERS if spec["code"] in by_code]


def _ensure_tool_assets():
    """The shop's tool types and a few units each, when none exist."""
    from app.models.tool_type import ToolType, ToolUnit, ToolUnitStatus

    if ToolType.query.first():
        return list(ToolType.query.order_by(ToolType.name).all())

    types = []
    for name, code, desc, count in _tool_asset_catalog():
        tt = ToolType(
            name=name,
            code=code,
            description=desc,
            is_seed=True,
        )
        db.session.add(tt)
        db.session.flush()
        for i in range(1, count + 1):
            db.session.add(
                ToolUnit(
                    tool_type_id=tt.id,
                    asset_code=f"{code}-{i:03d}",
                    status=ToolUnitStatus.AVAILABLE,
                    notes=None,
                )
            )
        types.append(tt)
    db.session.flush()
    print(f"Tool assets: {len(types)} types seeded.")
    return types


def _seed_password(env_name: str, *, role_label: str) -> str:
    """Password for flask seed — from env (preferred) so docs never publish it."""
    value = (os.getenv(env_name) or "").strip()
    if value:
        return value
    # Development-only fallback so `flask seed` still works locally without docs
    # advertising the value. Production seed is refused by the CLI.
    fallbacks = {
        "SEED_ADMIN_PASSWORD": "Admin123!",
        "SEED_OFFICE_PASSWORD": "Office123!",
        "SEED_WORKER_PASSWORD": "Worker123!",
    }
    if env_name in fallbacks and (os.getenv("FLASK_ENV") or "").lower() == "development":
        return fallbacks[env_name]
    raise RuntimeError(
        f"Set {env_name} in api/.env before seeding ({role_label}). "
        "See api/.env.example. Never commit real passwords."
    )


def seed_database():
    _ensure_scoring_weights()
    inventory_tools = _ensure_inventory_catalog()
    tool_types = _ensure_tool_assets()
    suppliers = _ensure_suppliers()
    if User.query.first():
        # Backfill Production In-charge assignability on already-seeded DBs.
        for admin in User.query.filter_by(role=UserRole.ADMIN, active=True).all():
            if not admin.worker_profile:
                db.session.add(WorkerProfile(user_id=admin.id))
            if not WorkerSchedule.query.filter_by(worker_id=admin.id).first():
                db.session.add_all(_default_schedule(admin.id))
        print("Database already seeded, skipping.")
        from app.seed.consumable_forecast_demo import seed_consumable_forecast_demo

        fc = seed_consumable_forecast_demo()
        if not fc.get("skipped"):
            print(
                f"Forecast demo: {fc.get('scanEvents', 0)} scans, "
                f"{fc.get('poLines', 0)} restock PO lines."
            )
        db.session.commit()
        return

    machines, op_types = _load_reference_data()

    admin_pw = _seed_password("SEED_ADMIN_PASSWORD", role_label="Admin")
    office_pw = _seed_password("SEED_OFFICE_PASSWORD", role_label="Office Staff")
    worker_pw = _seed_password("SEED_WORKER_PASSWORD", role_label="Production Worker")

    admin = User(
        email="admin@bmsc.local",
        mobile_number="+639171000001",
        password_hash=bcrypt.generate_password_hash(admin_pw).decode("utf-8"),
        full_name="Admin User",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        active=True,
    )
    office = User(
        email="office@bmsc.local",
        mobile_number="+639171000002",
        password_hash=bcrypt.generate_password_hash(office_pw).decode("utf-8"),
        full_name="Office Staff",
        role=UserRole.OFFICE_STAFF,
        status=UserStatus.ACTIVE,
        active=True,
    )

    # Seed: 19 production workers (1–4 real demo names; 5–19 placeholders)
    workers_data = [
        ("worker1@bmsc.local", "Juan Dela Cruz", ["milling", "lathe", "drilling"]),
        ("worker2@bmsc.local", "Maria Santos", ["grinding", "milling"]),
        ("worker3@bmsc.local", "Pedro Reyes", ["milling", "grinding"]),
        ("worker4@bmsc.local", "Ana Lopez", ["lathe", "drilling"]),
        # --- seed placeholders (flagged) ---
        ("worker5@bmsc.local", "Seed Worker 05", ["lathe", "milling"]),
        ("worker6@bmsc.local", "Seed Worker 06", ["milling", "drilling"]),
        ("worker7@bmsc.local", "Seed Worker 07", ["grinding", "shaper", "laser"]),
        ("worker8@bmsc.local", "Seed Worker 08", ["lathe", "grinding"]),
        ("worker9@bmsc.local", "Seed Worker 09", ["milling", "shaper"]),
        ("worker10@bmsc.local", "Seed Worker 10", ["drilling", "lathe"]),
        ("worker11@bmsc.local", "Seed Worker 11", ["milling", "bending"]),
        ("worker12@bmsc.local", "Seed Worker 12", ["lathe", "laser"]),
        ("worker13@bmsc.local", "Seed Worker 13", ["grinding", "bending"]),
        ("worker14@bmsc.local", "Seed Worker 14", ["shaper", "milling"]),
        ("worker15@bmsc.local", "Seed Worker 15", ["lathe", "drilling", "milling"]),
        ("worker16@bmsc.local", "Seed Worker 16", ["milling", "grinding"]),
        ("worker17@bmsc.local", "Seed Worker 17", ["drilling", "grinding"]),
        ("worker18@bmsc.local", "Seed Worker 18", ["lathe", "shaper"]),
        ("worker19@bmsc.local", "Seed Worker 19", ["milling", "lathe", "grinding"]),
    ]

    workers = []
    for idx, (email, name, skill_tokens) in enumerate(workers_data, start=1):
        w = User(
            email=email,
            mobile_number=f"+63917{100000 + idx:06d}",
            password_hash=bcrypt.generate_password_hash(worker_pw).decode("utf-8"),
            full_name=name,
            role=UserRole.PRODUCTION_WORKER,
            status=UserStatus.ACTIVE,
            active=True,
        )
        workers.append((w, skill_tokens))

    db.session.add_all([admin, office] + [w for w, _ in workers])
    db.session.flush()

    # Admins can be assigned to operations too (WorkerProfile for schedule hours).
    db.session.add(WorkerProfile(user_id=admin.id))
    db.session.add_all(_default_schedule(admin.id))

    for w, skill_tokens in workers:
        db.session.add(WorkerProfile(user_id=w.id))
        db.session.add_all(_skills_from_tokens(w.id, skill_tokens, machines))
        db.session.add_all(_default_schedule(w.id))

    from app.services.seed_cleanup_service import REAL_CLIENTS

    # The shop's client companies (no contact people: those are entered by Office).
    clients = [Client(name=name) for name in REAL_CLIENTS]
    db.session.add_all(clients)
    db.session.flush()

    tools = inventory_tools or _ensure_inventory_catalog()
    tool_types = tool_types or _ensure_tool_assets()
    _ = tools  # consumables available for stocktake demos

    job1 = JobOrder(
        client_id=clients[0].id,
        title="Shaft Repair - Line 3",
        description="Repair and re-machine drive shaft for conveyor line 3.",
        due_date=date.today() + timedelta(days=7),
        client_po_number="PO-ABC-1042",
        po_date=date.today() - timedelta(days=3),
        status=JobOrderStatus.SCHEDULED,
        priority=JobPriority.HIGH,
        job_type=JobType.REPAIR,
        part_condition=PartCondition.CLIENT_SUPPLIED_ITEM,
        material_status=MaterialStatus.NOT_REQUIRED,
        quantity=1,
        unit_of_measure="lot",
        amount=31360.00,
        created_by_id=office.id,
    )
    job2 = JobOrder(
        client_id=clients[1].id,
        title="Custom Bracket Fabrication",
        description="Fabricate 12 custom steel brackets per drawing BR-2024-15.",
        due_date=date.today() + timedelta(days=14),
        client_po_number="PO-MSC-778",
        po_date=date.today() - timedelta(days=5),
        status=JobOrderStatus.IN_PROGRESS,
        priority=JobPriority.MODERATE,
        job_type=JobType.FABRICATION,
        part_condition=PartCondition.WORK_IN_PROCESS,
        # Started job: RECEIVED is derived from the purchase lines added below.
        material_status=MaterialStatus.TO_ORDER,
        quantity=12,
        unit_of_measure="pcs",
        amount=26880.00,
        created_by_id=office.id,
    )
    job3 = JobOrder(
        client_id=clients[2].id,
        title="Pump Housing Refurbishment",
        description="Refurbish pump housing units - milling and finishing required.",
        due_date=date.today() + timedelta(days=21),
        client_po_number="PO-PE-331",
        po_date=date.today() - timedelta(days=1),
        status=JobOrderStatus.SCHEDULED,
        priority=JobPriority.LOW,
        job_type=JobType.MACHINING,
        part_condition=PartCondition.CLIENT_SUPPLIED_ITEM,
        material_status=MaterialStatus.NOT_REQUIRED,
        quantity=2,
        unit_of_measure="pcs",
        amount=15400.00,
        created_by_id=admin.id,
    )

    db.session.add_all([job1, job2, job3])
    db.session.flush()

    def mt(code):
        return machines[code].id if code in machines else None

    def ot(code):
        return op_types[code].id if code in op_types else None

    ops = [
        JobOperation(
            job_order_id=job1.id,
            sequence_no=1,
            operation_name="Teeth Cutting",
            operation_type_id=ot("TEETH_CUTTING"),
            machine_type_id=mt("MILLING"),
            assigned_worker_id=workers[0][0].id,
            estimated_hours=4,
            status=OperationStatus.PENDING,
        ),
        JobOperation(
            job_order_id=job1.id,
            sequence_no=2,
            operation_name="Surface Grinding",
            operation_type_id=ot("SURFACE_GRINDING"),
            machine_type_id=mt("GRINDING"),
            assigned_worker_id=workers[0][0].id,
            estimated_hours=2,
            status=OperationStatus.PENDING,
        ),
        JobOperation(
            job_order_id=job2.id,
            sequence_no=1,
            operation_name="Welding",
            operation_type_id=ot("WELDING"),
            machine_type_id=None,
            assigned_worker_id=workers[1][0].id,
            estimated_hours=6,
            status=OperationStatus.COMPLETED,
        ),
        JobOperation(
            job_order_id=job2.id,
            sequence_no=2,
            operation_name="Surface Grinding",
            operation_type_id=ot("SURFACE_GRINDING"),
            machine_type_id=mt("GRINDING"),
            assigned_worker_id=workers[1][0].id,
            estimated_hours=3,
            status=OperationStatus.IN_PROGRESS,
        ),
        JobOperation(
            job_order_id=job2.id,
            sequence_no=3,
            operation_name="Drilling",
            operation_type_id=ot("DRILLING"),
            machine_type_id=mt("DRILLING"),
            assigned_worker_id=workers[1][0].id,
            estimated_hours=2,
            status=OperationStatus.PENDING,
        ),
        JobOperation(
            job_order_id=job3.id,
            sequence_no=1,
            operation_name="Teeth Cutting",
            operation_type_id=ot("TEETH_CUTTING"),
            machine_type_id=mt("MILLING"),
            assigned_worker_id=workers[2][0].id,
            estimated_hours=5,
            status=OperationStatus.PENDING,
        ),
        JobOperation(
            job_order_id=job3.id,
            sequence_no=2,
            operation_name="Turning",
            operation_type_id=ot("TURNING"),
            machine_type_id=mt("LATHE"),
            assigned_worker_id=workers[2][0].id,
            estimated_hours=4,
            status=OperationStatus.PENDING,
        ),
    ]
    db.session.add_all(ops)

    from app.models.tool_type import ToolUnit, ToolUnitStatus
    from app.services.material_purchase_service import sync_job_material_from_purchases
    from datetime import datetime, timezone

    if suppliers:
        ordered_on = date.today() - timedelta(days=5)
        received_on = date.today() - timedelta(days=2)
        consumed_at = datetime.now(timezone.utc) - timedelta(days=1)
        for name, qty, unit, cost in (
            ("Mild steel plate 6mm", Decimal("12"), "pcs", Decimal("850")),
            ("Welding rod E6013", Decimal("2"), "kg", Decimal("320")),
        ):
            db.session.add(
                MaterialPurchase(
                    job_order_id=job2.id,
                    material_name=name,
                    quantity=qty,
                    unit=unit,
                    unit_cost=cost,
                    supplier_id=suppliers[0].id,
                    date_ordered=ordered_on,
                    date_received=received_on,
                    consumed_at=consumed_at,
                )
            )
        db.session.flush()
        db.session.refresh(job2)
        sync_job_material_from_purchases(job2)

    sample_unit = (
        ToolUnit.query.filter_by(asset_code="TL-CALIPER-001").first()
        or ToolUnit.query.first()
    )
    if sample_unit and workers:
        sample_unit.status = ToolUnitStatus.OUT
        sample_unit.current_holder_id = workers[0][0].id
        sample_unit.held_since = datetime.now(timezone.utc)
        db.session.add(
            ToolEvent(
                tool_unit_id=sample_unit.id,
                worker_id=workers[0][0].id,
                type=ToolEventType.BORROW,
                quantity=Decimal("1"),
                job_order_id=job1.id,
            )
        )

    from app.seed.consumable_forecast_demo import seed_consumable_forecast_demo

    fc = seed_consumable_forecast_demo()
    if not fc.get("skipped"):
        print(
            f"Forecast demo: {fc.get('scanEvents', 0)} scans, "
            f"{fc.get('poLines', 0)} restock PO lines "
            f"(no-data item {fc.get('noDataTool')})."
        )

    db.session.commit()

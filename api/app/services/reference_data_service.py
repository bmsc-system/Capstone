"""The shop's real reference data, loaded by `flask load-reference-data`.

Creates or updates suppliers, machine types and units, operation types, the
material catalog, consumables and shop details. Safe to run repeatedly and
in production: it never creates accounts and never touches job orders,
operations, supplier orders or history. Records are updated in place so
their IDs and history are kept; nothing is deleted.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func

from app.constants.inventory_names import CONSUMABLES as INVENTORY_CONSUMABLES
from app.constants.inventory_names import MATERIALS as INVENTORY_MATERIALS
from app.constants.machines import MACHINE_CATALOG, machine_unit_label
from app.extensions import db
from app.models.job_order import JobOrder
from app.models.machine import MachineType, MachineUnit
from app.models.material_catalog import MaterialCatalogItem
from app.models.material_purchase import MaterialPurchase
from app.models.shop_settings import SHOP_DETAIL_DEFAULTS, ShopSettings
from app.models.supplier import Supplier
from app.models.supplier_order import SupplierOrder, SupplierOrderStatus
from app.models.tool import Tool, ToolCategory
from app.models.worker_skill import OPERATION_TYPE_SEED, OperationType

# Matched by code first, then by name or any alias (case-insensitive).
SUPPLIERS = [
    {
        "code": "RIC",
        "name": "Railim Industrial Corporation",
        "aliases": ["Railim"],
        "contact_person": "Claudia",
        "typical_lead_time_days": 7,
        "address": "Barangay San Antonio, Sto. Tomas, Batangas",
    },
    {
        "code": "STP",
        "name": "STP Industrial Inc.",
        "aliases": ["STP", "STP Industrial Hardware Inc."],
        "contact_person": "James",
        "typical_lead_time_days": 5,
        "address": "378-376 Rizal Avenue Extension, Caloocan",
    },
    {
        "code": "RTC",
        "name": "Raitech Industrial Corporation",
        "aliases": ["Raitech"],
        "contact_person": "Richard",
        "typical_lead_time_days": 5,
        "address": "Barangay San Antonio, Santo Tomas, Batangas",
    },
]

# Set inactive (hidden from new orders, history kept) the first time the
# loader handles them; a later reactivation survives re-runs.
INACTIVE_SUPPLIERS = ["Seno Metals", "Metro Hardware"]

DUPLICATE_SUFFIX = " (duplicate)"

# The shop's own lists (app/constants/inventory_names.py); the size goes on
# each order line. An existing consumable found under an alias is renamed to
# the shop's name. Minimum stock is left blank.
MATERIAL_CATALOG = [{**m, "grades": []} for m in INVENTORY_MATERIALS]
CONSUMABLES = INVENTORY_CONSUMABLES


@dataclass
class LoadReport:
    changes: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def change(self, text):
        self.changes.append(text)

    def note(self, text):
        self.notes.append(text)


def _ci(column, value):
    return func.lower(column) == value.strip().lower()


def supplier_references(supplier):
    orders = SupplierOrder.query.filter_by(supplier_id=supplier.id).count()
    open_orders = SupplierOrder.query.filter(
        SupplierOrder.supplier_id == supplier.id,
        SupplierOrder.status.in_(
            [
                SupplierOrderStatus.DRAFT,
                SupplierOrderStatus.ISSUED,
                SupplierOrderStatus.PARTIALLY_RECEIVED,
            ]
        ),
    ).count()
    lines = MaterialPurchase.query.filter_by(supplier_id=supplier.id).count()
    jobs = JobOrder.query.filter_by(supplier_id=supplier.id).count()
    return {"orders": orders, "open_orders": open_orders, "lines": lines, "jobs": jobs}


def _refs_text(refs):
    return (
        f"{refs['orders']} supplier orders ({refs['open_orders']} open), "
        f"{refs['lines']} order lines, {refs['jobs']} job orders"
    )


def _set(obj, attr, value, report, label):
    current = getattr(obj, attr)
    if current != value:
        setattr(obj, attr, value)
        report.change(f"{label}: {attr} {current!r} -> {value!r}")


def _set_active_once(supplier, active, report, label):
    """Set active status only the first time the loader handles a supplier,
    so a later reactivation (or deactivation) by the shop is kept."""
    if supplier.reference_loaded_at is not None:
        return
    if bool(supplier.active) != active:
        supplier.active = active
        report.change(f"{label}: marked {'active' if active else 'inactive'}")
    supplier.reference_loaded_at = datetime.now(timezone.utc)


def _load_suppliers(report):
    for spec in SUPPLIERS:
        names = [spec["name"], *spec["aliases"]]
        candidates = {
            s.id: s
            for s in Supplier.query.filter(
                (Supplier.code == spec["code"])
                | func.lower(Supplier.name).in_([n.lower() for n in names])
            ).all()
        }
        if not candidates:
            supplier = Supplier(
                name=spec["name"],
                code=spec["code"],
                contact_person=spec["contact_person"],
                typical_lead_time_days=spec["typical_lead_time_days"],
                address=spec["address"],
                active=True,
                reference_loaded_at=datetime.now(timezone.utc),
            )
            db.session.add(supplier)
            db.session.flush()
            report.change(f"Supplier {spec['code']}: created {spec['name']!r}")
            continue

        refs = {sid: supplier_references(s) for sid, s in candidates.items()}

        def rank(s):
            r = refs[s.id]
            return (s.code == spec["code"], r["orders"] + r["lines"] + r["jobs"], s.is_seed)

        ordered = sorted(candidates.values(), key=rank, reverse=True)
        supplier, duplicates = ordered[0], ordered[1:]

        for dup in duplicates:
            label = f"Supplier {dup.name!r}"
            if dup.code == spec["code"]:
                _set(dup, "code", None, report, label)
            _set_active_once(dup, False, report, f"{label} (duplicate of {spec['code']})")
            if not dup.name.endswith(DUPLICATE_SUFFIX):
                _set(dup, "name", dup.name + DUPLICATE_SUFFIX, report, label)
            report.note(f"Duplicate supplier {dup.name!r}: {_refs_text(refs[dup.id])}")
        db.session.flush()

        label = f"Supplier {spec['code']}"
        for attr in ("name", "code", "contact_person", "typical_lead_time_days", "address"):
            _set(supplier, attr, spec[attr], report, label)
        _set_active_once(supplier, True, report, label)
        report.note(f"Supplier {spec['code']} {spec['name']!r}: {_refs_text(refs[supplier.id])}")

    for name in INACTIVE_SUPPLIERS:
        for supplier in Supplier.query.filter(_ci(Supplier.name, name)).all():
            _set_active_once(supplier, False, report, f"Supplier {supplier.name!r}")
            status = "active" if supplier.active else "inactive"
            report.note(
                f"Retired supplier {supplier.name!r} ({status}, history kept): "
                f"{_refs_text(supplier_references(supplier))}"
            )
    db.session.flush()


def _load_machines(report):
    by_code = {}
    for spec in MACHINE_CATALOG:
        names = [spec["name"], *spec.get("old_names", [])]
        mt = MachineType.query.filter_by(code=spec["code"]).first()
        if mt is None:
            mt = MachineType.query.filter(
                func.lower(MachineType.name).in_([n.lower() for n in names])
            ).first()
        if mt is None:
            mt = MachineType(code=spec["code"], name=spec["name"], units=0)
            db.session.add(mt)
            db.session.flush()
            report.change(f"Machine type {spec['code']}: created {spec['name']!r}")
        elif mt.name in spec.get("old_names", []):
            _set(mt, "name", spec["name"], report, f"Machine type {mt.code}")
        by_code[spec["code"]] = mt

        labels = {
            (u.label or "").strip().lower()
            for u in MachineUnit.query.filter_by(machine_type_id=mt.id).all()
        }
        for n in range(1, spec["units"] + 1):
            label = machine_unit_label(spec, n)
            if label.lower() not in labels:
                db.session.add(MachineUnit(machine_type_id=mt.id, label=label, active=True))
                report.change(f"Machine unit {label!r}: created ({mt.name})")
        db.session.flush()

        active = MachineUnit.query.filter_by(machine_type_id=mt.id, active=True).count()
        _set(mt, "units", active, report, f"Machine type {mt.code}")
    db.session.flush()
    return by_code


def _load_operation_types(report, machines):
    for spec in OPERATION_TYPE_SEED:
        machine = machines.get(spec["machine"]) if spec["machine"] else None
        ot = OperationType.query.filter_by(code=spec["code"]).first()
        if ot is None:
            db.session.add(
                OperationType(
                    code=spec["code"],
                    name=spec["name"],
                    default_machine_type_id=machine.id if machine else None,
                    active=spec.get("active", True),
                    is_outsourced=bool(spec.get("outsourced")),
                    default_turnaround_days=spec.get("turnaround_days"),
                )
            )
            report.change(f"Operation type {spec['code']}: created")
            continue
        if spec.get("active") is False and ot.active:
            ot.active = False
            report.change(f"Operation type {spec['code']}: deactivated")
        if machine is not None and ot.default_machine_type_id != machine.id:
            before = ot.default_machine_type.code if ot.default_machine_type else None
            ot.default_machine_type_id = machine.id
            report.change(
                f"Operation type {spec['code']}: machine {before!r} -> {machine.code!r}"
            )
    db.session.flush()


def _load_material_catalog(report):
    for spec in MATERIAL_CATALOG:
        item = MaterialCatalogItem.query.filter(_ci(MaterialCatalogItem.name, spec["name"])).first()
        if item is None:
            db.session.add(
                MaterialCatalogItem(
                    name=spec["name"],
                    shop_term=spec["shop_term"],
                    grades=list(spec["grades"]),
                    default_unit=spec["default_unit"],
                    category=spec["category"],
                    active=True,
                )
            )
            report.change(f"Material {spec['name']!r}: added to catalog")
            continue
        # Office Staff may have edited an entry: only fill gaps.
        label = f"Material {item.name!r}"
        if not item.shop_term and spec["shop_term"]:
            _set(item, "shop_term", spec["shop_term"], report, label)
        if not item.category and spec["category"]:
            _set(item, "category", spec["category"], report, label)
        grades = list(item.grades or [])
        known = {g.lower() for g in grades}
        missing = [g for g in spec["grades"] if g.lower() not in known]
        if missing:
            _set(item, "grades", grades + missing, report, label)
    db.session.flush()


def _unique_tool_code(code):
    candidate, n = code, 2
    while Tool.query.filter_by(code=candidate).first() is not None:
        candidate, n = f"{code}-{n}", n + 1
    return candidate


def _load_consumables(report):
    for spec in CONSUMABLES:
        exact = Tool.query.filter(_ci(Tool.name, spec["name"])).all()
        if not exact and spec["aliases"]:
            aliased = Tool.query.filter(
                func.lower(Tool.name).in_([a.lower() for a in spec["aliases"]])
            ).all()
            for tool in aliased:
                _set(tool, "name", spec["name"], report, f"Consumable {tool.code}")
            exact = aliased
        if not exact:
            tool = Tool(
                name=spec["name"],
                code=_unique_tool_code(spec["code"]),
                category=ToolCategory.CONSUMABLE,
                unit=spec["unit"],
                quantity_on_hand=Decimal("0"),
                minimum_stock=None,
                shop_term=spec["shop_term"],
                ordered_from_supplier=bool(spec.get("ordered_from_supplier")),
            )
            db.session.add(tool)
            report.change(f"Consumable {spec['name']!r}: created ({tool.code}, {spec['unit']})")
            continue
        for tool in exact:
            if spec["shop_term"] and not tool.shop_term:
                _set(tool, "shop_term", spec["shop_term"], report, f"Consumable {tool.code}")
    db.session.flush()


def _load_shop_details(report):
    settings = db.session.get(ShopSettings, 1)
    if settings is None:
        settings = ShopSettings(id=1)
        db.session.add(settings)
        report.change("Shop settings: created")
    for attr, value in SHOP_DETAIL_DEFAULTS.items():
        if not getattr(settings, attr):
            _set(settings, attr, value, report, "Shop details")
    db.session.flush()


def load_reference_data(dry_run: bool = False, commit: bool = True) -> LoadReport:
    """Apply the reference data; with dry_run, report and roll back. Seeds
    pass commit=False to keep the changes in their own transaction."""
    report = LoadReport()
    try:
        _load_suppliers(report)
        machines = _load_machines(report)
        _load_operation_types(report, machines)
        _load_material_catalog(report)
        _load_consumables(report)
        _load_shop_details(report)
    except Exception:
        db.session.rollback()
        raise
    if dry_run:
        db.session.rollback()
    elif commit:
        db.session.commit()
    return report

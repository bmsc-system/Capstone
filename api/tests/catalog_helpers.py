"""Shared test helpers for catalog-based PO lines."""

from sqlalchemy import func

from app.extensions import db
from app.models.material_catalog import MaterialCatalogItem


def ensure_material(name, unit="pcs", *, active=True):
    """The catalog material a PO line must reference (created when missing)."""
    item = MaterialCatalogItem.query.filter(
        func.lower(MaterialCatalogItem.name) == name.lower()
    ).first()
    if item is None:
        item = MaterialCatalogItem(name=name, default_unit=unit, grades=[], active=active)
        db.session.add(item)
        db.session.commit()
    return item.id


def approve_issue(client, order_url, json, office_headers):
    """Office Staff send the draft for approval, then an Admin approves and
    issues it. Returns the first response that is not a success."""
    from flask_jwt_extended import create_access_token

    from app.extensions import db
    from app.models.user import User, UserRole, UserStatus

    res = client.post(f"{order_url}/submit", json=json, headers=office_headers)
    if res.status_code != 200:
        return res
    admin = User.query.filter_by(email="po_approver@test.local").first()
    if admin is None:
        admin = User(
            email="po_approver@test.local",
            password_hash="x",
            full_name="Approver Admin",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
            active=True,
        )
        db.session.add(admin)
        db.session.commit()
    token = create_access_token(identity=admin.id, additional_claims={"role": "ADMIN"})
    return client.post(
        f"{order_url}/issue", json={}, headers={"Authorization": f"Bearer {token}"}
    )

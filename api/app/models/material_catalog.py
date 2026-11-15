"""Raw materials the shop buys, named the way the shop writes them on a PO
(e.g. "1045"). Each order line adds its own size; ``grades`` is kept only for
entries saved before grades were folded into the name.
"""

import uuid
from datetime import datetime, timezone

from app.extensions import db

# Steel is bought by the piece or length (sizes like 320mm dia x 170mmL), not by weight.
MATERIAL_UNITS = ["pcs", "m", "ft", "sheet"]
# Older lines may still read "kg"; they stay valid when their unit is not changed.
MATERIAL_CATEGORIES = [
    "Steel",
    "Stainless steel",
    "Cast iron",
    "Aluminium",
    "Non-ferrous",
    "Plastic",
    "Other",
]
# Units a consumable (stocked by count/pack) may be bought and counted in.
CONSUMABLE_UNITS = ["pcs", "pack", "roll", "box", "spool", "can", "pail", "bottle", "pair", "gallon", "liter"]


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class MaterialCatalogItem(db.Model):
    __tablename__ = "material_catalog"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    name = db.Column(db.String(255), nullable=False, unique=True)
    shop_term = db.Column(db.String(100), nullable=True)
    grades = db.Column(db.JSON, nullable=False, default=list)
    default_unit = db.Column(db.String(32), nullable=False, default="pcs")
    category = db.Column(db.String(64), nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "shopTerm": self.shop_term,
            "grades": list(self.grades or []),
            "defaultUnit": self.default_unit,
            "category": self.category,
            "active": bool(self.active),
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }

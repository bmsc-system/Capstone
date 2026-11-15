"""Restock reminders: tell the Office Staff when a consumable is due to be bought again.

The replenishment forecast (``replenishment_service``) gives the day each item
reaches its reorder point (from stock on hand and how fast it is handed out),
or, before enough hand-outs are recorded, the day it is usually bought next;
one alert is raised per item and expected date once that date is
``DUE_SOON_DAYS`` away or has passed. Recording the purchase moves the
expected date, so the next cycle alerts again. The alert kind keeps its stored
name, LOW_STOCK. Runs with the page-open and daily background checks. Commits.
"""

from __future__ import annotations

from datetime import date

from app.extensions import db
from app.models.staff_alert import StaffAlertKind
from app.models.user import UserRole
from app.services import replenishment_service, staff_alert_service


def _qty(value) -> str:
    return f"{float(value or 0):g}"


def restock_due_items(today=None) -> list[dict]:
    """Consumables due or overdue for buying, soonest first."""
    return replenishment_service.due_items(today)


def check_restock_due(today=None) -> int:
    """Raise one reminder per consumable and expected date. Returns how many were raised."""
    raised = 0
    for item in restock_due_items(today):
        when = date.fromisoformat(item["nextPurchaseOn"]).strftime("%b %d")
        if item.get("basis") == "USAGE":
            # Stock on hand against how fast the item is handed out.
            message = (
                f"{_qty(item['onHand'])} {item['unit']} on hand, used about "
                f"{_qty(item['dailyUse'])} a day. Runs out around "
                f"{date.fromisoformat(item['runOutOn']).strftime('%b %d')}. "
                f"Suggested order: {_qty(item['usualQuantity'])} {item['unit']}."
            )
            title = (
                f"{item['name']} is at its reorder point"
                if item["status"] == "OVERDUE"
                else f"{item['name']} reaches its reorder point around {when}"
            )
        else:
            message = (
                f"Usually bought every {round(item['everyDays'])} days, about "
                f"{_qty(item['usualQuantity'])} {item['unit']} at a time. "
                f"Last bought {date.fromisoformat(item['lastBoughtOn']).strftime('%b %d')}."
            )
            title = (
                f"{item['name']} was due to be bought by {when}"
                if item["status"] == "OVERDUE"
                else f"{item['name']} is due to be bought around {when}"
            )
        raised += len(
            staff_alert_service.raise_alert(
                roles=[UserRole.OFFICE_STAFF],
                kind=StaffAlertKind.LOW_STOCK,
                title=title,
                message=message,
                dedupe_key=f"restock:{item['toolId']}:{item['nextPurchaseOn']}",
            )
        )
    db.session.commit()
    return raised

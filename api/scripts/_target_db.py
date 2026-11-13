"""Open the app against a database chosen at a hidden prompt (or DATABASE_URL).

Shared by the maintenance scripts. The URL is never printed: only its host
and database name are shown so the target can be confirmed.
"""

from __future__ import annotations

import os
import sys
from getpass import getpass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def target_label(url: str) -> str:
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    return f"{parsed.host or 'localhost'}:{parsed.port or ''}/{parsed.database or ''}"


def open_app(ask: bool = True):
    """(app, label). Asks for the URL unless ``ask`` is False."""
    if ask:
        entered = getpass("Database URL (hidden; Enter to use DATABASE_URL): ").strip()
        if entered:
            os.environ["DATABASE_URL"] = entered

    from app import create_app
    from app.config import Config, _normalize_database_url

    url = os.environ.get("DATABASE_URL") or Config.SQLALCHEMY_DATABASE_URI
    if not url:
        raise SystemExit("No database URL given.")

    class ScriptConfig(Config):
        ENV = "maintenance"
        SQLALCHEMY_DATABASE_URI = _normalize_database_url(url)
        RATELIMIT_ENABLED = False

    return create_app(ScriptConfig), target_label(ScriptConfig.SQLALCHEMY_DATABASE_URI)


def confirm_database(label: str) -> None:
    typed = input(f"Type the database name to confirm changes to {label}: ").strip()
    if typed != label.rsplit("/", 1)[-1]:
        raise SystemExit("Not confirmed. Nothing changed.")


def name_key(name: str) -> str:
    """Names compared without case, punctuation or extra spaces
    ("Gregorio Agao Jr." matches "gregorio agao jr")."""
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in (name or "").lower())
    return " ".join(cleaned.split())

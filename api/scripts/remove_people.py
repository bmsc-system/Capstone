"""Remove the accounts of people who do not work at the shop.

Give a text file with the employees to keep, one full name per line (the
names stay on your computer, out of the repository). Every account whose
name is not in the file is removed, except Admin accounts (kept so you can
still sign in) and any email given with --keep-email.

Removing an account disables it at once (no sign-in, phone PINs revoked).
When nothing else in the system points at the person, the account and its
own rows (profile, skills, hours, devices, photo, invitations) are deleted,
so it no longer appears in Users & Roles or Worker setup. A person who still
has work on record (job history, attendance, purchases) is only disabled;
run this again after `flask sample-history reset` to delete them too.
Machine units they were the usual operator of are opened to all.

Preview (changes nothing), then apply, from the api/ folder:
    .venv\\Scripts\\python.exe scripts\\remove_people.py --keep-file C:\\path\\employees.txt
    .venv\\Scripts\\python.exe scripts\\remove_people.py --keep-file C:\\path\\employees.txt --apply --expect N

--expect N must equal the number of accounts the preview lists, so a typo
in the file cannot remove someone you did not see.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from _target_db import confirm_database, name_key, open_app

# The person's own rows: deleted with the account.
OWNED = {
    ("worker_profiles", "user_id"),
    ("worker_skills", "worker_id"),
    ("worker_schedules", "worker_id"),
    ("user_devices", "user_id"),
    ("user_photos", "user_id"),
    ("user_invitations", "user_id"),
    ("password_reset_tokens", "user_id"),
    ("offline_action_receipts", "user_id"),
    ("staff_alerts", "recipient_id"),
}
# Pointers that may simply be cleared (the row stays).
CLEARABLE = {
    ("machine_units", "default_operator_id"),
    ("shop_settings", "updated_by_id"),
    ("audit_logs", "user_id"),
}


def read_keep(path):
    with open(path, encoding="utf-8-sig") as fh:
        return {name_key(line) for line in fh if name_key(line)}


def user_references():
    """(table, column) of every foreign key to users.id in the database."""
    from sqlalchemy import text

    from app.extensions import db

    rows = db.session.execute(
        text(
            """
            SELECT cl.relname AS tbl, att.attname AS col
            FROM pg_constraint con
            JOIN pg_class cl ON cl.oid = con.conrelid
            JOIN pg_class ref ON ref.oid = con.confrelid
            JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey)
            WHERE con.contype = 'f' AND ref.relname = 'users'
            ORDER BY 1, 2
            """
        )
    ).all()
    return [(r.tbl, r.col) for r in rows]


def blocking(user_id, refs):
    """Tables (other than the person's own rows) that still point at them."""
    from sqlalchemy import text

    from app.extensions import db

    counts = {}
    for table, column in refs:
        if (table, column) in OWNED or (table, column) in CLEARABLE:
            continue
        n = db.session.execute(
            text(f'SELECT count(*) FROM "{table}" WHERE "{column}" = :uid'), {"uid": user_id}
        ).scalar()
        if n:
            counts[table] = max(counts.get(table, 0), int(n))
    return [f"{table} ({n})" for table, n in sorted(counts.items())]


def candidates(keep, keep_emails):
    from app.models.user import User, UserRole

    out = []
    for user in User.query.order_by(User.full_name).all():
        if user.role == UserRole.ADMIN:
            continue
        if (user.email or "").lower() in keep_emails:
            continue
        if name_key(user.full_name) in keep:
            continue
        out.append(user)
    return out


def remove(users, refs):
    from sqlalchemy import text

    from app.extensions import db
    from app.models.user import UserStatus
    from app.models.user_security import UserDevice

    now = datetime.now(timezone.utc)
    deleted, disabled = [], []
    for user in users:
        if user.status != UserStatus.DISABLED:
            user.status = UserStatus.DISABLED
            user.sync_active_flag()
        for device in UserDevice.query.filter(
            UserDevice.user_id == user.id, UserDevice.revoked_at.is_(None)
        ).all():
            device.revoked_at = now
            device.pin_hash = None
            device.pin_set_at = None
        db.session.flush()
        still = blocking(user.id, refs)
        if still:
            disabled.append((user, still))
            continue
        for table, column in refs:
            if (table, column) in CLEARABLE:
                db.session.execute(
                    text(f'UPDATE "{table}" SET "{column}" = NULL WHERE "{column}" = :uid'),
                    {"uid": user.id},
                )
            elif (table, column) in OWNED:
                db.session.execute(
                    text(f'DELETE FROM "{table}" WHERE "{column}" = :uid'), {"uid": user.id}
                )
        db.session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
        deleted.append(user)
    db.session.flush()
    return deleted, disabled


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep-file", required=True, help="Text file: one employee name per line")
    parser.add_argument("--keep-email", action="append", default=[], help="Also keep this email")
    parser.add_argument("--apply", action="store_true", help="Remove the accounts listed")
    parser.add_argument("--expect", type=int, help="With --apply: the number of accounts the preview listed")
    args = parser.parse_args()
    keep = read_keep(args.keep_file)
    if not keep:
        raise SystemExit("The keep file has no names.")

    app, label = open_app()
    from app.extensions import db
    from app.models.user import User, UserRole

    with app.app_context():
        print(f"Target database: {label}")
        refs = user_references()
        users = candidates(keep, {e.lower() for e in args.keep_email})
        kept_names = {name_key(u.full_name) for u in User.query.all()}
        for name in sorted(keep - kept_names):
            print(f"  note: no account called {name!r} (nothing to keep)")
        admins = User.query.filter_by(role=UserRole.ADMIN).order_by(User.full_name).all()
        print("Kept (Admin accounts): " + ", ".join(f"{a.full_name} <{a.email or '-'}>" for a in admins))
        print(f"Accounts to remove: {len(users)}")
        for u in users:
            still = blocking(u.id, refs)
            how = "disable (has records: " + ", ".join(still) + ")" if still else "delete"
            print(f"  {u.full_name:<26} {u.email or '-':<34} {u.role.value:<18} {u.status.value:<9} -> {how}")
        if not args.apply:
            print("Preview: nothing changed. To remove these, run again with --apply --expect "
                  f"{len(users)}.")
            return
        if args.expect is None or args.expect != len(users):
            raise SystemExit(f"--expect must be {len(users)} (the number listed above). Nothing changed.")
        if not users:
            print("Nothing to remove.")
            return
        confirm_database(label)
        try:
            deleted, disabled = remove(users, refs)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            raise SystemExit(f"Nothing changed: {getattr(exc, 'message', None) or exc}")
        print(f"Deleted {len(deleted)} account(s).")
        if disabled:
            print(f"Disabled {len(disabled)} account(s) that still have records; run again after the reset to delete them:")
            for u, still in disabled:
                print(f"  {u.full_name}: {', '.join(still)}")


if __name__ == "__main__":
    main()

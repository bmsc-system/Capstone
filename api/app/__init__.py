import logging

import click
import uuid
from datetime import datetime, timezone

from flask import Flask
from flask_cors import CORS
from flask_migrate import Migrate
from werkzeug.middleware.proxy_fix import ProxyFix

from app.extensions import bcrypt, db, jwt, limiter, resolve_ratelimit_storage_uri
from app.utils.errors import register_error_handlers
from app.utils.request_metrics import init_request_metrics


def create_app(config_object=None):
    app = Flask(__name__)

    if config_object:
        app.config.from_object(config_object)
    else:
        from app.config import Config

        app.config.from_object(Config)

    _assert_production_secrets(app)

    proxies = int(app.config.get("TRUSTED_PROXY_COUNT", 1) or 0)
    if proxies > 0:
        # Render's proxy appends the client address to X-Forwarded-For; trust
        # exactly that many hops so rate limits key on the real client.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies)

    app.config["RATELIMIT_STORAGE_URI"] = resolve_ratelimit_storage_uri(app)
    app.config.setdefault("RATELIMIT_SWALLOW_ERRORS", True)
    app.config.setdefault("RATELIMIT_IN_MEMORY_FALLBACK_ENABLED", True)

    db.init_app(app)
    migrate = Migrate(app, db)
    jwt.init_app(app)
    from app.services.session_service import register_jwt_callbacks

    register_jwt_callbacks(jwt)
    bcrypt.init_app(app)
    limiter.init_app(app)
    from app.services import read_cache as _read_cache

    _read_cache.register()

    cors_origins = app.config.get("CORS_ORIGINS", "http://localhost:5173")
    if isinstance(cors_origins, str):
        cors_origins = [o.strip() for o in cors_origins.split(",")]
    CORS(app, origins=cors_origins, supports_credentials=True)


    if not app.logger.level:
        app.logger.setLevel(logging.INFO)
    init_request_metrics(app)

    @app.route("/api/health", methods=["GET", "HEAD"])
    @app.route("/api/v1/health", methods=["GET", "HEAD"])
    def health():
        return {"status": "ok"}

    @app.route("/api/v1/health/db", methods=["GET", "HEAD"])
    def health_db():
        """For uptime monitors: also checks that the database answers quickly.

        Wakes a suspended database, so monitor it every 30-60 minutes rather
        than every few minutes on a plan with a monthly compute limit.
        """
        import time

        from sqlalchemy import text

        started = time.monotonic()
        try:
            if db.session.get_bind().dialect.name == "postgresql":
                db.session.execute(text("SET LOCAL statement_timeout = '5s'"))
                db.session.execute(text("SET LOCAL lock_timeout = '5s'"))
            db.session.execute(text("SELECT 1"))
            db.session.rollback()
        except Exception:  # noqa: BLE001 - any failure means "database down"
            db.session.rollback()
            app.logger.exception("Database health check failed")
            return {"status": "error", "database": "unreachable"}, 503
        return {"status": "ok", "database": "ok", "ms": round((time.monotonic() - started) * 1000)}

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # JSON API: deny framing and embedding foreign scripts by default.
        ctype = (response.content_type or "").lower()
        if "json" in ctype or response.status_code >= 400:
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
            )
        else:
            response.headers.setdefault(
                "Content-Security-Policy", "frame-ancestors 'none'"
            )
        from flask import request as _req

        if _req.is_secure or app.config.get("ENV") == "production":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    register_error_handlers(app)
    _register_blueprints(app)
    _register_cli(app)

    from app.services.audit_service import register_audit_listeners

    register_audit_listeners()

    # Work out the model relationships once, here, rather than lazily on the
    # first query while request threads and the background checks start at once.
    import app.models as _models  # noqa: F401
    from sqlalchemy.orm import configure_mappers

    configure_mappers()

    # Warn about mail misconfiguration without printing secrets.
    try:
        from app.services.email_backends import warn_if_mail_from_mismatch
        from app.services.email_templates import frontend_base_url

        frontend_base_url(app.config)
        warn_if_mail_from_mismatch(app.config)
    except Exception:  # noqa: BLE001 - startup warnings must never block boot
        app.logger.warning("Email configuration check failed", exc_info=True)

    return app


def _assert_production_secrets(app):
    from app.config import DEV_JWT_SECRET_KEY, DEV_SECRET_KEY

    if app.config.get("TESTING") or app.config.get("ENV") != "production":
        return
    missing = [
        name
        for name, dev_value in (
            ("SECRET_KEY", DEV_SECRET_KEY),
            ("JWT_SECRET_KEY", DEV_JWT_SECRET_KEY),
        )
        if not app.config.get(name) or app.config.get(name) == dev_value
    ]
    if missing:
        raise RuntimeError(
            f"Refusing to start in production without {', '.join(missing)}. "
            "Set them in the environment (FLASK_ENV=development allows local fallbacks)."
        )


def _register_blueprints(app):
    from app.blueprints.auth.routes import auth_bp
    from app.blueprints.users.routes import users_bp
    from app.blueprints.workers.routes import workers_bp
    from app.blueprints.clients.routes import clients_bp
    from app.blueprints.suppliers.routes import suppliers_bp
    from app.blueprints.supplier_orders.routes import supplier_orders_bp
    from app.blueprints.job_orders.routes import job_orders_bp
    from app.blueprints.operations.routes import operations_bp
    from app.blueprints.tools.routes import tools_bp
    from app.blueprints.inventory.routes import inventory_bp
    from app.blueprints.consumables.routes import consumables_bp
    from app.blueprints.analytics.routes import analytics_bp
    from app.blueprints.worker_profiles.routes import (
        calendar_bp,
        operation_types_bp,
        worker_profiles_bp,
    )

    prefix = "/api/v1"
    app.register_blueprint(auth_bp, url_prefix=f"{prefix}/auth")
    app.register_blueprint(users_bp, url_prefix=f"{prefix}/users")
    app.register_blueprint(workers_bp, url_prefix=f"{prefix}/workers")
    app.register_blueprint(worker_profiles_bp, url_prefix=f"{prefix}/workers")
    app.register_blueprint(clients_bp, url_prefix=f"{prefix}/clients")
    app.register_blueprint(suppliers_bp, url_prefix=f"{prefix}/suppliers")
    app.register_blueprint(supplier_orders_bp, url_prefix=f"{prefix}/supplier-orders")
    app.register_blueprint(job_orders_bp, url_prefix=f"{prefix}/job-orders")
    app.register_blueprint(operations_bp, url_prefix=f"{prefix}/operations")
    app.register_blueprint(tools_bp, url_prefix=f"{prefix}/tools")
    app.register_blueprint(inventory_bp, url_prefix=f"{prefix}/inventory")
    app.register_blueprint(consumables_bp, url_prefix=f"{prefix}/consumables")
    app.register_blueprint(calendar_bp, url_prefix=f"{prefix}/calendar")
    app.register_blueprint(operation_types_bp, url_prefix=f"{prefix}/operation-types")
    app.register_blueprint(analytics_bp, url_prefix=f"{prefix}/analytics")
    from app.blueprints.admin.routes import admin_bp
    from app.blueprints.notifications.routes import notifications_bp
    from app.blueprints.schedule.routes import schedule_bp
    from app.blueprints.attendance.routes import attendance_bp
    from app.blueprints.alerts.routes import alerts_bp
    from app.blueprints.reference.routes import (
        catalog_bp,
        material_catalog_bp,
        shop_details_bp,
    )

    app.register_blueprint(material_catalog_bp, url_prefix=f"{prefix}/material-catalog")
    app.register_blueprint(catalog_bp, url_prefix=f"{prefix}/catalog")
    app.register_blueprint(shop_details_bp, url_prefix=f"{prefix}/shop-details")
    app.register_blueprint(alerts_bp, url_prefix=f"{prefix}/alerts")
    app.register_blueprint(notifications_bp, url_prefix=f"{prefix}/notifications")
    app.register_blueprint(admin_bp, url_prefix=f"{prefix}/admin")
    app.register_blueprint(schedule_bp, url_prefix=f"{prefix}/schedule")
    app.register_blueprint(attendance_bp, url_prefix=f"{prefix}/attendance")


def _register_cli(app):
    @app.cli.command("seed")
    def seed_command():
        """Seed the database with demo data (refused in production)."""
        if app.config.get("ENV") == "production":
            raise click.ClickException(
                "Refusing to seed: FLASK_ENV is production (or unset). The seed creates "
                "demo accounts with published passwords. To create the first account on "
                "a live database, run `flask create-admin` instead."
            )
        from app.seed.seed_data import seed_database

        seed_database()
        print("Database seeded successfully.")

    @app.cli.command("assign-schedule-colors")
    def assign_schedule_colors_command():
        """Give open job orders without a saved color their own board color."""
        from app.services.schedule_color_service import assign_missing_colors

        print(f"Gave {assign_missing_colors()} job order(s) a schedule color.")

    @app.cli.command("create-admin")
    def create_admin_command():
        """Create an admin user interactively."""
        from getpass import getpass

        from app.models.user import User, UserRole, UserStatus
        from app.extensions import bcrypt
        from app.utils.errors import AppError
        from app.utils.passwords import validate_password

        email = input("Admin email: ")
        password = getpass("Admin password (hidden): ")
        if getpass("Repeat password: ") != password:
            raise click.ClickException("Passwords do not match.")
        try:
            validate_password(password)
        except AppError as exc:
            raise click.ClickException(exc.message)
        full_name = input("Full name: ")
        mobile = input("Mobile number: ")

        if User.query.filter_by(email=email.strip().lower()).first():
            print("User already exists.")
            return

        from app.utils.phone import normalize_ph_mobile

        user = User(
            email=email.strip().lower(),
            mobile_number=normalize_ph_mobile(mobile, required=True),
            password_hash=bcrypt.generate_password_hash(password).decode("utf-8"),
            full_name=full_name,
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
            active=True,
        )
        db.session.add(user)
        db.session.flush()
        from app.services.worker_profile_service import ensure_worker_profile

        ensure_worker_profile(user)
        db.session.commit()
        print(f"Admin user {email} created.")

    @app.cli.command("load-reference-data")
    @click.option("--dry-run", is_flag=True, help="Show the changes without saving them.")
    def load_reference_data_command(dry_run):
        """Create or update the shop's suppliers, machines, operation types,
        material catalog, consumables and shop details. Safe in production:
        never creates accounts or touches job data."""
        from app.services.reference_data_service import load_reference_data

        report = load_reference_data(dry_run=dry_run)
        heading = "Would change" if dry_run else "Changed"
        print(f"{heading} ({len(report.changes)}):")
        for line in report.changes or ["nothing; reference data is up to date"]:
            print(f"  - {line}")
        if report.notes:
            print("References:")
            for line in report.notes:
                print(f"  - {line}")
        if dry_run:
            print("Dry run: nothing was saved.")

    @app.cli.command("cleanup-seed-data")
    @click.option(
        "--apply",
        "apply_changes",
        is_flag=True,
        help="Commit the cleanup. Without it, dry run (prints the report then rolls back).",
    )
    def cleanup_seed_data_command(apply_changes):
        """Merge seed suppliers/clients, remap @bmsc.local workers onto real
        employees, strip leftover seed text, then delete the seed records.
        Dry run by default. With --apply, confirms the database host first.
        Cleanup (not seeding) — allowed in any environment; never auto-run."""
        from app.services.seed_cleanup_service import database_label, report_lines, run

        masked, host, _db_name = database_label()
        print(f"Target database: {masked}")
        confirm_host = None
        if apply_changes:
            typed = click.prompt(
                f"Type the host name to confirm ({host})",
                default="",
                show_default=False,
            )
            confirm_host = typed.strip()
            if confirm_host.lower() != host.lower():
                raise click.ClickException(
                    f"Host confirmation failed (expected {host!r}, got {confirm_host!r}). Aborted."
                )
        try:
            report = run(apply=apply_changes, confirm_host=confirm_host)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        for line in report_lines(report, applied=apply_changes):
            print(line)
        print(
            "Applied."
            if apply_changes
            else "Dry run: nothing was saved. Run with --apply to commit."
        )

    @app.cli.group("sample-history")
    def sample_history_group():
        """Add or remove local sample transaction history.

        Dry run unless --apply. --apply asks for the database host before it
        commits. Never points this at a hosted database.
        """

    def _confirm_sample_history_host(apply_changes):
        from app.services.seed_cleanup_service import database_label

        masked, host, _db_name = database_label()
        print(f"Target database: {masked}")
        if not apply_changes:
            return
        typed = click.prompt(
            f"Type the host name to confirm ({host})",
            default="",
            show_default=False,
        )
        if typed.strip().lower() != host.lower():
            raise click.ClickException(
                f"Host confirmation failed (expected {host!r}, got {typed.strip()!r}). Aborted."
            )

    @sample_history_group.command("generate")
    @click.option("--start", required=True, help="First shop day, YYYY-MM-DD.")
    @click.option("--end", required=True, help="Last shop day, YYYY-MM-DD.")
    @click.option("--seed", type=int, default=42, show_default=True)
    @click.option(
        "--apply",
        "apply_changes",
        is_flag=True,
        help="Commit. Without it, build the history, print the summary, and roll back.",
    )
    def sample_history_generate(start, end, seed, apply_changes):
        """Add sample job, purchasing, attendance and stock history on this machine only."""
        from app.services.sample_history_service import format_summary, generate

        _confirm_sample_history_host(apply_changes)
        try:
            summary = generate(start, end, seed=seed, apply=apply_changes)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        for line in format_summary(summary):
            print(line)
        print(
            "Applied."
            if apply_changes
            else "Dry run: nothing was saved. Run with --apply to commit."
        )

    @sample_history_group.command("reset")
    @click.option("--start", required=True, help="First shop day, YYYY-MM-DD.")
    @click.option("--end", required=True, help="Last shop day, YYYY-MM-DD.")
    @click.option("--seed", type=int, default=42, show_default=True)
    @click.option(
        "--apply",
        "apply_changes",
        is_flag=True,
        help="Commit. Without it, wipe and rebuild, print the summary, and roll back.",
    )
    def sample_history_reset(start, end, seed, apply_changes):
        """Delete ALL job orders and other shop activity, then load sample history.

        Keeps users, machines, clients, suppliers, the catalog, tools and
        settings. Works on a hosted database too: take a backup first.
        --apply asks you to type the database host before it commits.
        """
        from app.services.sample_history_service import format_summary, reset_demo_data

        _confirm_sample_history_host(apply_changes)
        try:
            summary = reset_demo_data(start, end, seed=seed, apply=apply_changes)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        wiped = summary.pop("wiped", {})
        print("Removed: " + ", ".join(f"{k} {v}" for k, v in wiped.items() if v))
        for line in format_summary(summary):
            print(line)
        print(
            "Applied."
            if apply_changes
            else "Dry run: nothing was saved. Run with --apply to commit."
        )

    @sample_history_group.command("remove")
    @click.option(
        "--apply",
        "apply_changes",
        is_flag=True,
        help="Commit the delete. Without it, delete and roll back after the summary.",
    )
    def sample_history_remove(apply_changes):
        """Delete sample-history rows and restore snapshotted stock. Nothing else."""
        from app.services.sample_history_service import format_summary, remove

        _confirm_sample_history_host(apply_changes)
        try:
            summary = remove(apply=apply_changes)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        for line in format_summary(summary):
            print(line)
        print(
            "Applied."
            if apply_changes
            else "Dry run: nothing was saved. Run with --apply to commit."
        )

    @sample_history_group.command("clear-old-demo")
    @click.option(
        "--apply",
        "apply_changes",
        is_flag=True,
        help="Commit the delete. Without it, delete and roll back after the summary.",
    )
    def sample_history_clear_old_demo(apply_changes):
        """Delete the old HIST-SEED, TC-SEED, and scrap demo rows on this machine only."""
        from app.services.sample_history_service import clear_old_demo, format_clear_summary

        _confirm_sample_history_host(apply_changes)
        try:
            summary = clear_old_demo(apply=apply_changes)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        for line in format_clear_summary(summary):
            print(line)
        print(
            "Applied."
            if apply_changes
            else "Dry run: nothing was saved. Run with --apply to commit."
        )

    @app.cli.command("import-employees")
    @click.option("--file", "file_path", required=True, help="CSV file, e.g. api/data/employees.local.csv")
    @click.option("--dry-run", is_flag=True, help="Show the changes without saving them.")
    def import_employees_command(file_path, dry_run):
        """Create the shop's employees from a git-ignored CSV. Accounts are not
        activated; the Admin invites each person later. Safe to run twice."""
        from pathlib import Path

        from app.services.employee_import_service import import_employees
        from app.utils.errors import AppError

        path = Path(file_path)
        if not path.is_absolute() and not path.exists():
            # Also accept paths given from the repository root (api/data/...).
            repo_root = Path(__file__).resolve().parents[2]
            if (repo_root / path).exists():
                path = repo_root / path
        try:
            report = import_employees(path, dry_run=dry_run)
        except AppError as exc:
            raise click.ClickException(exc.message)

        heading = "Would create" if dry_run else "Created"
        print(f"{heading} ({len(report.created)}):")
        for line in report.created or ["nobody"]:
            print(f"  - {line}")
        heading = "Would change" if dry_run else "Changed"
        print(f"{heading} ({len(report.changes)}):")
        for line in report.changes or ["nothing"]:
            print(f"  - {line}")
        print(f"Already up to date: {report.unchanged}")
        print(f"Not imported or needs a look ({len(report.problems)}):")
        for line in report.problems or ["none"]:
            print(f"  - {line}")
        if dry_run:
            print("Dry run: nothing was saved.")

    @app.cli.command("export-people")
    @click.option(
        "--out",
        "out_path",
        default=None,
        help=r"JSON path (default C:\Users\Janvie\Project\people-export.json, outside the repo).",
    )
    def export_people_command(out_path):
        """Write production workers + real-client contacts to a JSON file.
        Run on LOCAL. No passwords or photos. Safe; read-only."""
        from app.services.people_sync_service import (
            DEFAULT_EXPORT_PATH,
            export_people,
            export_summary,
        )

        path, payload = export_people(out_path or DEFAULT_EXPORT_PATH)
        print(f"Wrote {path}")
        for line in export_summary(payload):
            print(line)

    @app.cli.command("import-people")
    @click.option(
        "--file",
        "file_path",
        default=None,
        help=r"JSON from export-people (default C:\Users\Janvie\Project\people-export.json).",
    )
    @click.option(
        "--apply",
        "apply_changes",
        is_flag=True,
        help="Commit the import. Without it, dry run (prints then rolls back).",
    )
    def import_people_command(file_path, apply_changes):
        """Create missing workers and fill empty real-client contacts from a
        people-export JSON. Existing users are never modified. Dry run by
        default; --apply confirms the database host first."""
        from pathlib import Path

        from app.services.people_sync_service import (
            DEFAULT_EXPORT_PATH,
            import_people,
            import_report_lines,
        )
        from app.services.seed_cleanup_service import database_label

        path = Path(file_path) if file_path else DEFAULT_EXPORT_PATH
        if not path.exists():
            raise click.ClickException(f"File not found: {path}")

        masked, host, _db_name = database_label()
        print(f"Target database: {masked}")
        confirm_host = None
        if apply_changes:
            typed = click.prompt(
                f"Type the host name to confirm ({host})",
                default="",
                show_default=False,
            )
            confirm_host = typed.strip()
            if confirm_host.lower() != host.lower():
                raise click.ClickException(
                    f"Host confirmation failed (expected {host!r}, got {confirm_host!r}). Aborted."
                )
        try:
            report = import_people(
                path, apply=apply_changes, confirm_host=confirm_host
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        for line in import_report_lines(report, applied=apply_changes):
            print(line)
        print(
            "Applied."
            if apply_changes
            else "Dry run: nothing was saved. Run with --apply to commit."
        )


def utcnow():
    return datetime.now(timezone.utc)


def generate_uuid():
    return str(uuid.uuid4())

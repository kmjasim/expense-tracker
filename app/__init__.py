# app/__init__.py
# ------------------------------------------------------------
# Flask application factory with clear, layered registration:
# - register_extensions()
# - register_blueprints()
# - register_template_filters()
# - register_context_processors()
# - register_cli()
# - register_error_handlers()  (placeholder)
#
# Notes:
# - We call load_dotenv() once at import time.
# - We keep your login_manager.user_loader near extension setup.
# - We remove the old ".services.routes" import (no longer needed after splitting routes).
# - We make sure context processor returns ACTUAL menu data (calls filtered_menu()).
# ------------------------------------------------------------

import os
from dotenv import load_dotenv

from flask import Flask, url_for, request
from flask_login import current_user

from .config import Config
from .extensions import db, migrate, login_manager, mail
from .models import User  # ensure models registered
from .navigation import MENU

# Load environment from .env exactly once
load_dotenv()


def create_app() -> Flask:
    app = Flask(__name__)
    env = os.getenv("FLASK_ENV", "development").lower()
    cfg = "app.config.Production" if env == "production" else "app.config.Development"
    app.config.from_object(cfg)

    register_extensions(app)
    register_blueprints(app)   # <--- this runs the block above
    register_template_filters(app)
    register_context_processors(app)
    register_cli(app)
    register_error_handlers(app)
    # Start background scheduler
    from .scheduler import start_scheduler
    start_scheduler(app)
    return app



# ---------------------------
# Registrations (by concern)
# ---------------------------
def register_extensions(app: Flask) -> None:
    """Initialize Flask extensions (db, migrate, login manager, mail)."""
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    mail.init_app(app)

    # Where to redirect unauthenticated users
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id: str):
        # Simple integer PK loader
        try:
            return User.query.get(int(user_id))
        except Exception:
            return None


def register_blueprints(app: Flask) -> None:
    """
    Register all blueprints. Keep imports local to avoid circulars.
    """
    # Import blueprints locally
    from .main import main as main_blueprint
    from .auth import auth as auth_blueprint
    from .settings import settings as settings_blueprint

    # Register them with optional URL prefixes
    app.register_blueprint(main_blueprint)                      # /
    app.register_blueprint(auth_blueprint, url_prefix="/auth")  # /auth/login, /auth/register
    app.register_blueprint(settings_blueprint)                  # /settings/...            # /transactions/...


    # ❌ No longer needed:
    # from .services import routes as _service_routes  # noqa: F401


def register_template_filters(app: Flask) -> None:
    """Add Jinja filters and globals."""

    # --- Filters ---
    def _money(value):
        """Format a number as currency with two decimals and thousands separators."""
        try:
            return "{:,.2f}".format(float(value))
        except (ValueError, TypeError):
            return value

    app.add_template_filter(_money, name="money")

    # --- Globals ---
    def _currency_symbol(code_or_account) -> str:
        """
        Accepts an enum/string or an Account object with a `.currency` attr.
        Returns a symbol for known currencies, else empty string.
        """
        code = None
        try:
            code = getattr(code_or_account, "currency", None)
            code = getattr(code, "value", code)  # enum -> value
        except Exception:
            pass
        if not code:
            code = str(code_or_account)
        return {"KRW": "₩", "BDT": "৳"}.get(str(code), "")

    app.jinja_env.globals["currency_symbol"] = _currency_symbol


def register_context_processors(app: Flask) -> None:
    """Inject navigation menu (filtered by role + active state) into templates."""

    def user_has_access(item: dict) -> bool:
        roles = item.get("roles")
        if not roles:
            return True
        if not getattr(current_user, "is_authenticated", False):
            return False
        return getattr(current_user, "role", None) in roles

    def normalize_href(item: dict) -> str:
        if "endpoint" in item:
            try:
                return url_for(item["endpoint"])
            except Exception:
                return "#"  # endpoint missing; avoid crash during dev
        return item.get("url", "#")

    def is_active(endpoint_name: str | None, href: str | None = None) -> bool:
        if endpoint_name and request.endpoint == endpoint_name:
            return True
        if href and href != "#" and request.path == href:
            return True
        return False

    def any_child_active(children: list | None) -> bool:
        if not children:
            return False
        for c in children:
            href = normalize_href(c)
            if is_active(c.get("endpoint"), href) or any_child_active(c.get("children")):
                return True
        return False

    def filtered_menu() -> list[dict]:
        def allow(item: dict) -> bool:
            return user_has_access(item)

        def walk(items: list[dict]) -> list[dict]:
            out: list[dict] = []
            for it in items:
                if not allow(it):
                    continue
                new_it = dict(it)
                # Normalize children first
                if "children" in it:
                    new_it["children"] = walk(it["children"])
                # Precompute href/active/open
                new_it["_href"] = normalize_href(it)
                new_it["_active"] = is_active(it.get("endpoint"), new_it["_href"])
                new_it["_open"] = new_it["_active"] or any_child_active(new_it.get("children"))
                out.append(new_it)
            return out

        return walk(MENU)

    @app.context_processor
    def _inject_navigation():
        # IMPORTANT: return the menu DATA, not the function
        return dict(menu=filtered_menu())


def register_cli(app: Flask) -> None:
    """Register custom CLI commands (if you have a cli.register_cli factory)."""
    try:
        from .cli import register_cli as _register_cli
        _register_cli(app)
    except Exception:
        # If no CLI module yet, ignore silently
        pass


def register_error_handlers(app: Flask) -> None:
    """Attach custom error handlers if desired."""
    # Example scaffold:
    # @app.errorhandler(404)
    # def not_found(e):
    #     return render_template("errors/404.html"), 404
    pass

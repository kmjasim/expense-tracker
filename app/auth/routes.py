# app/auth/routes.py
# ------------------------------------------------------------
# Authentication routes:
# - GET/POST /auth/login        -> login form + submit
# - GET      /auth/logout       -> log out the current user
# - GET/POST /auth/register     -> optional self-serve signup
#
# Notes:
# - No flash() calls (uses query params for status messages).
# - Keeps "next" redirect safe.
# - Uses werkzeug.security for password hashing & checking.
# - Templates you should have: auth/login.html, auth/register.html
# ------------------------------------------------------------

from urllib.parse import urlencode, urlparse

from flask import request, render_template, redirect, url_for
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash

from app.utils.email_utils import send_mail

from ..extensions import db
from ..models import User
from . import auth  # blueprint: defined in app/auth/__init__.py
from app.utils.helpers import get_page_title  # optional, if you use it in templates


# ---------------------------
# Helpers
# ---------------------------
# app/utils/tokens.py
from itsdangerous import URLSafeTimedSerializer
from flask import current_app

def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])

def generate_reset_token(email: str) -> str:
    s = _serializer()
    salt = current_app.config["SECURITY_PASSWORD_SALT"]
    return s.dumps(email, salt=salt)

def verify_reset_token(token: str, max_age_seconds: int = 3600) -> str | None:
    s = _serializer()
    salt = current_app.config["SECURITY_PASSWORD_SALT"]
    try:
        return s.loads(token, salt=salt, max_age=max_age_seconds)
    except Exception:
        return None

def _is_safe_url(target: str) -> bool:
    """
    Allow relative redirects within the same site only.
    Example safe: "/payments"
    Example unsafe: "https://evil.com/login"
    """
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urlparse(target).geturl())
    # Relative path is OK; absolute must match our host
    if not test_url.netloc:
        return True
    return (test_url.scheme, test_url.netloc) == (ref_url.scheme, ref_url.netloc)


def _redirect_with_message(endpoint: str, *, warning: str | None = None, success: str | None = None, **kwargs):
    """
    Redirect to endpoint with optional ?warning=... or ?success=...
    """
    base = url_for(endpoint, **kwargs)
    qs = {}
    if warning:
        qs["warning"] = warning
    if success:
        qs["success"] = success
    if qs:
        return redirect(f"{base}?{urlencode(qs)}")
    return redirect(base)


# ---------------------------
# Login
# ---------------------------
@auth.route("/login", methods=["GET", "POST"])
def login():
    """
    Render login page and authenticate users.
    - If already logged in, go to the default landing page.
    - On success, redirect to 'next' (if safe) else to main.index (or payments page).
    """
    if current_user.is_authenticated:
        # Already logged in; go home
        return redirect(url_for("main.index"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        next_url = request.args.get("next") or request.form.get("next") or ""

        # Basic validation
        if not email or not password:
            return _redirect_with_message("auth.login", warning="Email and password are required")

        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(getattr(user, "password_hash", ""), password):
            return _redirect_with_message("auth.login", warning="Invalid email or password")

        # Log in
        login_user(user)

        # Safe redirect
        if next_url and _is_safe_url(next_url):
            return redirect(next_url)

        # Default post-login page — change to your preferred page
        # e.g., "main.payments_page"
        return redirect(url_for("main.index"))

    # GET
    # If you pass a 'next' param in the URL, keep it in a hidden field in the form.
    next_url = request.args.get("next", "")
    return render_template("auth/login.html", next=next_url, page_title=get_page_title("Login"))


# ---------------------------
# Logout
# ---------------------------
@auth.route("/logout", methods=["GET"])
@login_required
def logout():
    logout_user()
    # After logout, send to login with a success message (no flash)
    return _redirect_with_message("auth.login", success="You have been logged out")


# ---------------------------
# Register (optional)
# ---------------------------
@auth.route("/register", methods=["GET", "POST"])
def register():
    """
    Self-serve sign-up (optional).
    - If you don't want public registration, protect with @login_required
      and limit to admins, or remove entirely.
    """
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    if request.method == "POST":
        # TODO: Adjust field names to your form
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""

        # Basic validation
        if not name or not email or not password or not password2:
            return _redirect_with_message("auth.register", warning="All fields are required")

        if password != password2:
            return _redirect_with_message("auth.register", warning="Passwords do not match")

        # Prevent duplicate users
        if User.query.filter_by(email=email).first():
            return _redirect_with_message("auth.register", warning="Email is already registered")

        # Create user
        user = User(
            name=name,
            email=email,
            # TODO: adapt to your User model fields. If you store 'role', set a default here.
        )
        # Assuming your model uses a 'password_hash' column:
        user.password_hash = generate_password_hash(password)

        db.session.add(user)
        db.session.commit()

        # Auto-login after register (optional)
        login_user(user)

        # Land on your preferred page
        return _redirect_with_message("main.index", success="Welcome!")

    # GET
    return render_template("auth/register.html", page_title=get_page_title("Register"))

# ---------------------------
# Forgot Password (optional)
def _redir(endpoint, **qs):
    base = url_for(endpoint)
    return redirect(f"{base}?{urlencode(qs)}") if qs else redirect(base)

@auth.route("/forgot", methods=["GET", "POST"], endpoint="forgot_password")
def forgot_password():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        # Always act like it worked (don’t leak if email exists)
        if email:
            token = generate_reset_token(email)
            reset_url = url_for("auth.reset_password", token=token, _external=True)
            # Build a simple HTML email
            html = f"""
                <p>Hello,</p>
                <p>Click the link below to reset your password (valid for 1 hour):</p>
                <p><a href="{reset_url}">{reset_url}</a></p>
                <p>If you didn't request this, you can ignore this email.</p>
            """
            try:
                send_mail(email, "Reset your password", html, f"Reset your password: {reset_url}")
            except Exception as e:
                # In dev you can print(e); in prod you might log it.
                pass
        return render_template("auth/forgot_sent.html", email=email)

    return render_template("auth/forgot.html")

@auth.route("/reset/<token>", methods=["GET", "POST"], endpoint="reset_password")
def reset_password(token):
    # token -> email (or None)
    email = verify_reset_token(token, max_age_seconds=3600)
    if not email:
        return _redir("auth.login", warning="Reset link is invalid or expired")

    if request.method == "POST":
        pw = request.form.get("password") or ""
        pw2 = request.form.get("password2") or ""
        if not pw or not pw2:
            return _redir("auth.reset_password", token=token, warning="Both fields are required")
        if pw != pw2:
            return _redir("auth.reset_password", token=token, warning="Passwords do not match")
        user = User.query.filter_by(email=email).first()
        if not user:
            return _redir("auth.login", warning="Account not found")
        user.password_hash = generate_password_hash(pw)
        db.session.commit()
        return _redir("auth.login", success="Password updated. Please sign in.")

    return render_template("auth/reset.html", token=token)
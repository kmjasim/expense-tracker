# app/auth/__init__.py
from flask import Blueprint

auth = Blueprint("auth", __name__, url_prefix="/auth")

# Import routes so decorators run (keep at end)
from . import routes  # noqa: E402,F401

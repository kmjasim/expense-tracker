# app/main/__init__.py
# ---------------------------------
# Single blueprint named `main` so all your existing @main.route(...) keep working.
# Import the feature modules at the bottom so their routes register.

from flask import Blueprint

from ..services import expense_breakdown, kpi

main = Blueprint("main", __name__)

# Route modules (keep these imports at the end)
from . import index, debt, payments, transfers, categories, accounts, recipients, finance_score_routes, transactions, transactions_actions,routes_recurring, routes_budget, salary, lotto # noqa: E402,F401

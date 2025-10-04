# app/main/api.py
from datetime import date
from flask import request, jsonify
from flask_login import login_required, current_user
from ..main import main
from ..services.expense_breakdown import expense_breakdown

_SYMBOL = {"KRW": "₩", "BDT": "৳"}

def _fmt_money(sym: str, n: float) -> str:
    as_int = int(n)
    return f"{sym}{n:,.2f}" if n != as_int else f"{sym}{as_int:,}"

@main.route("/api/expense_breakdown", methods=["GET"])
@login_required
def api_expense_breakdown():
    # Defaults to current month/year
    today = date.today()
    year = request.args.get("year", type=int) or today.year
    month = request.args.get("month", type=int) or today.month

    currency = request.args.get("currency", default="KRW")
    if currency not in ("KRW", "BDT"):
        currency = "KRW"

    # This service already excludes notes containing "credit ... card" AND "settlement"
    data = expense_breakdown(current_user.id, currency, year, month)

    sym = _SYMBOL.get(currency, "₩")
    payload = {
        **data,
        "total_fmt": _fmt_money(sym, data["total"]),
        "currency": currency,
        "year": year,
        "month": month,
    }
    return jsonify(payload)

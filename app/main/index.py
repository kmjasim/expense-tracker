# app/main/dashboard.py
from datetime import date
from urllib.parse import _DefragResultBase
from flask import jsonify, request, render_template
from flask_login import login_required, current_user
from ..main import main
from ..services.kpi import kpi_for_month
from app.utils.helpers import get_page_title
from ..services.cashflow import monthly_cashflow
from ..services.expense_breakdown import expense_breakdown
from app.services.finance_score import get_finance_score
from ..models import Account, Currency
from decimal import Decimal
from sqlalchemy import desc
from ..extensions import db

_SYMBOL = {"KRW": "₩", "BDT": "৳"}

def _fmt(sym: str, n: float) -> str:
    # show decimals only if needed
    as_int = int(n)
    return f"{sym}{n:,.2f}" if n != as_int else f"{sym}{as_int:,}"
def _fmt_money(sym: str, n: float) -> str:
    as_int = int(n)
    return f"{sym}{n:,.2f}" if n != as_int else f"{sym}{as_int:,}"


@main.route("/", methods=["GET"])
@login_required
def index():
    today = date.today()
    year = request.args.get("year", type=int) or today.year
    month = request.args.get("month", type=int) or today.month
    currency = request.args.get("currency", default="KRW")
    if currency not in ("KRW", "BDT"):
        currency = "KRW"

    cards = kpi_for_month(current_user.id, currency, year, month)

    # Pre-format strings for the template (keeps Jinja simple & fast)
    sym = _SYMBOL.get(currency, "")
    def fmt(n):
        # human-ish formatting; keep decimals if present
        return f"{sym}{n:,.2f}" if (n % 1) else f"{sym}{int(n):,}"

    def hydrate(card):
        val = card["value"]
        d_abs = card["delta_abs"]
        d_pct = card["delta_pct"]
        up = d_abs >= 0
        return {
            "value": val,
            "value_fmt": fmt(val),
            "delta_abs_fmt": (("+" if up else "") + fmt(d_abs)) if d_abs != 0 else fmt(0),
            "delta_pct_fmt": (("↑ " if up else "↓ ") + f"{abs(d_pct):.2f} %") if d_pct is not None else "—",
            "badge_class": (
                # green good for Income & Savings; red good for Expenses & Pending
                "bg-success-subtle text-success" if up else "bg-danger-subtle text-danger"
            ),
            "badge_class_inv": (
                "bg-danger-subtle text-danger" if up else "bg-success-subtle text-success"
            ),
            "is_up": up,
        }

    # For Expenses & Pending, an "up" change is usually bad → invert color
    income = hydrate(cards["income"])

    exp_raw = hydrate(cards["expenses"])
    expenses = exp_raw | {"badge_class": exp_raw["badge_class_inv"]}

    pend_raw = hydrate(cards["pending"])
    pending = pend_raw | {"badge_class": pend_raw["badge_class_inv"]}

    savings = hydrate(cards["savings"])

    # ---- Override Savings badge to show "Saved XX %" for THIS month ----
    # rate = savings_this_month / income_this_month (when income > 0)
    income_val = income["value"]
    savings_val = savings["value"]
    if income_val and income_val > 0:
        rate = (savings_val / income_val) * 100
        savings["delta_pct_fmt"] = f"Saved {rate:.2f} %"
        savings["badge_class"] = "bg-success-subtle text-success" if savings_val > 0 else "bg-danger-subtle text-danger"
    else:
        savings["delta_pct_fmt"] = "Saved —"
        savings["badge_class"] = "bg-secondary-subtle text-secondary"
    fs = get_finance_score(current_user.id, year=today.year, month=today.month) 
    today = date.today() 
    # --- BDT widget: read balances directly from Account.initial_balance ---
    bdt_accounts = (
        db.session.query(Account)
        .filter(
            Account.user_id == current_user.id,
            Account.currency == Currency.BDT,
            Account.is_active.is_(True),
        )
        .order_by(desc(Account.initial_balance))   # sort by balance
        .all()
    )

    def _bal(a):
        v = getattr(a, "initial_balance", None)
        return Decimal(v or 0)

    bdt_accounts_with_bal = [
        {"id": a.id, "name": a.name, "balance": _bal(a)}
        for a in bdt_accounts
    ]

    # Top 2 by initial_balance
    bdt_top2 = bdt_accounts_with_bal[:2]
    bdt_total_all = sum((x["balance"] for x in bdt_accounts_with_bal), Decimal("0"))
    return render_template(
        "index.html",
        cards={
            "income": income,
            "expenses": expenses,
            "pending": pending,
            "savings": savings,
        },
        year=year,
        month=month,
        currency=currency,
        page_title=get_page_title(),
        finance_score=fs,
        today=today,
        bdt_accounts_with_bal=bdt_accounts_with_bal,
        bdt_total_all=bdt_total_all,
    )


@main.route("/api/cashflow", methods=["GET"])
@login_required
def api_cashflow():
    year = request.args.get("year", type=int)
    currency = request.args.get("currency", default="KRW")
    if currency not in ("KRW", "BDT"):
        currency = "KRW"
    if not year:
        from datetime import date
        year = date.today().year

    data = monthly_cashflow(current_user.id, currency, year)
    sym = _SYMBOL.get(currency, "")
    data["total_balance_fmt"] = _fmt(sym, data["total_balance"])
    data["currency"] = currency
    data["year"] = year
    return jsonify(data)


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
    

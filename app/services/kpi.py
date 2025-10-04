# app/services/kpi.py
from datetime import date
from decimal import Decimal
from sqlalchemy import func, case
from ..extensions import db
from ..models import TransactionKRW, TransactionBDT, TxnType
from sqlalchemy import func, case, and_, not_   # add and_ / not_

def month_bounds(year: int, month: int):
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    return start, end

# app/services/kpi.py


def _totals_for_month(Model, user_id: int, year: int, month: int):
    start, end = month_bounds(year, month)

    # Case-insensitive: note contains both "credit card" and "settlement"
    note_norm = func.lower(func.trim(func.coalesce(Model.note, "")))
    is_settlement = and_(note_norm.like("%credit%card%"), note_norm.like("%settlement%"))

    # ✅ Income: ONLY true income (exclude refunds)
    income_expr = case(
        (Model.type.in_([TxnType.income]), Model.amount),
        else_=0
    )

    # ✅ Expenses: posted + pending expense/fee, but exclude settlements
    expense_expr = case(
        (and_(Model.type.in_([TxnType.expense, TxnType.fee,TxnType.transfer_international]),
              not_(is_settlement)), Model.amount),
        else_=0
    )

    # ✅ Pending KPI: only pending expense/fee, exclude settlements
    pending_expr = case(
        (and_(Model.is_pending.is_(True),
              Model.type.in_([TxnType.expense, TxnType.fee]),
              not_(is_settlement)), Model.amount),
        else_=0
    )

    row = (
        db.session.query(
            func.coalesce(func.sum(income_expr), 0),
            func.coalesce(func.sum(expense_expr), 0),
            func.coalesce(func.sum(pending_expr), 0),
        )
        .filter(
            Model.user_id == user_id,
            Model.is_deleted.is_(False),
            Model.date >= start,
            Model.date < end,
        )
        .one()
    )

    income  = Decimal(row[0] or 0)
    expenses = -Decimal(row[1] or 0)   # show positive
    pending  = -Decimal(row[2] or 0)   # show positive
    savings  = income - expenses       # (includes pending in expenses)

    return {"income": income, "expenses": expenses, "pending": pending, "savings": savings}

def _delta_pct(curr: Decimal, prev: Decimal):
    if prev == 0:
        return None
    return ( (curr - prev) / abs(prev) ) * 100
# app/services/kpi.py  (only the kpi_for_month function changes)
def kpi_for_month(user_id: int, currency: str, year: int, month: int):
    Model = TransactionKRW if currency == "KRW" else TransactionBDT

    # current
    curr = _totals_for_month(Model, user_id, year, month)

    # previous month
    if month == 1:
        py, pm = year - 1, 12
    else:
        py, pm = year, month - 1
    prev = _totals_for_month(Model, user_id, py, pm)

    def pack(name):
        return {
            "value": curr[name],
            "delta_abs": curr[name] - prev[name],
            "delta_pct": _delta_pct(curr[name], prev[name]),
            "prev": prev[name],
        }

    data = {
        "income":  pack("income"),
        "expenses": pack("expenses"),
        "pending": pack("pending"),
        "savings": pack("savings"),
    }

    # NEW: savings rate = this month's savings / this month's income
    if curr["income"] > 0:
        data["savings_rate_pct"] = (curr["savings"] / curr["income"]) * 100
    else:
        data["savings_rate_pct"] = None

    return data

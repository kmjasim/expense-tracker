# app/services/krw_overview.py
from datetime import date
from decimal import Decimal
from sqlalchemy import func, and_, or_
from app.extensions import db
from app.models import TransactionKRW, TxnType
def _month_bounds(y: int, m: int):
    start = date(y, m, 1)
    # next month
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    end = date(ny, nm, 1)
    return start, end

def krw_income_spent(user_id: int, year: int, month: int):
    start, end = _month_bounds(year, month)
    base_filters = [
        TransactionKRW.user_id == user_id,
        TransactionKRW.date >= start,
        TransactionKRW.date <  end,
        TransactionKRW.is_deleted.is_(False),
    ]

    # Income (positive inflow) in the month
    income = (db.session.query(func.coalesce(func.sum(TransactionKRW.amount), 0))
              .filter(*base_filters, TransactionKRW.type == TxnType.income)
              .scalar())
    income = Decimal(income or 0)

    # Spent = expenses + transfer_international (outflow; stored negative)

    spent = (
        db.session.query(func.coalesce(func.sum(TransactionKRW.amount), 0))
        .filter(
            *base_filters,
            TransactionKRW.amount < 0,
            or_(
                TransactionKRW.type == TxnType.expense,
                TransactionKRW.type == TxnType.transfer_international
            ),
            TransactionKRW.note != "Credit card settlement"
        )
        .scalar()
    )

    spent_abs = abs(Decimal(spent or 0))

    pct = Decimal("0")
    if income > 0:
        pct = (spent_abs / income * 100).quantize(Decimal("1"))

    return {
        "income": float(income),
        "spent": float(spent_abs),
        "pct": float(pct),
    }
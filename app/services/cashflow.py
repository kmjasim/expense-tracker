# app/services/cashflow.py
from datetime import date, timedelta
from decimal import Decimal
from sqlalchemy import func
from ..extensions import db
from ..models import TransactionKRW, TransactionBDT, TxnType, Account, Currency
from app.services.textutils import is_cc_settlement

def _model_for_currency(currency: str):
    return TransactionKRW if currency == "KRW" else TransactionBDT

def _labels():
    return ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

def monthly_cashflow(user_id: int, currency: str, year: int):
    """Return arrays (len=12) for income, expenses(positive), net, plus total balance as of year end."""
    Model = _model_for_currency(currency)
    start = date(year, 1, 1)
    end   = date(year + 1, 1, 1)  # exclusive

    # Pull all this-year transactions once and aggregate in Python (portable across DBs)
    txns = (
        db.session.query(Model)
        .filter(
            Model.user_id == user_id,
            Model.is_deleted.is_(False),
            Model.date >= start,
            Model.date < end,
        )
        .all()
    )

    income = [Decimal(0)] * 12
    expenses = [Decimal(0)] * 12  # store as positive numbers for chart
    # app/services/cashflow.py  (inside monthly_cashflow loop)
    for t in txns:
        if is_cc_settlement(t.note):
            continue

        m = t.date.month - 1

        # ✅ Only true income
        if t.type in (TxnType.income,):
            income[m] += Decimal(t.amount or 0)

        elif t.type in (TxnType.expense, TxnType.fee):
            expenses[m] += -Decimal(t.amount or 0)

        elif t.type == TxnType.transfer_international and (t.amount or 0) < 0:
            expenses[m] += -Decimal(t.amount or 0)



    net = [income[i] - expenses[i] for i in range(12)]

    # ---- Total balance as of year-end (or today if the year is current) ----
    cutoff = min(end, date.today() + timedelta(days=1))  # exclusive upper bound
    # ---- Total balance: sum directly from accounts (no txn math) ---

    acc_sum_q = db.session.query(func.coalesce(func.sum(Account.initial_balance), 0)).filter(
        Account.user_id == user_id,
        Account.currency == Currency(currency),
        Account.is_active.is_(True),
    )
    total_balance = Decimal(acc_sum_q.scalar() or 0)

    return {
        "labels": _labels(),
        "income": [float(x) for x in income],
        "expenses": [float(x) for x in expenses],
        "net": [float(x) for x in net],
        "total_balance": float(total_balance),
    }


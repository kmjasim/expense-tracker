# app/services/credit_cards.py
from datetime import date
from decimal import Decimal
from sqlalchemy import func, and_, or_
from ..extensions import db
from ..models import Account, Category, TransactionKRW, AccountType

def _month_bounds(y: int, m: int):
    start = date(y, m, 1)
    end  = date(y + (m == 12), (m % 12) + 1, 1)  # first day of next month
    return start, end

def _is_credit_card_account():
    """Predicate that robustly identifies credit-card accounts."""
    # Handles enum naming differences + legacy data
    return or_(
        Account.type == AccountType.credit,          # preferred
        Account.credit_limit.isnot(None),                 # credit cards usually have a limit
        func.lower(Account.name).contains("card"),        # name contains 'card'
    )

def get_credit_card_accounts(user_id: int):
    """All active credit-card accounts for this user."""
    return (
        db.session.query(Account)
        .filter(
            Account.user_id == user_id,
            Account.is_active == True,
            _is_credit_card_account(),
        )
        .order_by(Account.display_order, func.lower(Account.name))
        .all()
    )

def cc_totals_by_account(user_id: int, year: int, month: int):
    """
    Return a list of dicts: [{id, name, total_spent}]
    Shows every credit-card account (even if total is zero).
    """
    start, end = _month_bounds(year, month)

    # 1) All candidate accounts
    accounts = get_credit_card_accounts(user_id)
    if not accounts:
        return []

    # 2) Totals for this month per account (expenses only, not pending/deleted)
    totals_rows = (
        db.session.query(
            TransactionKRW.account_id.label("aid"),
            func.coalesce(func.sum(func.abs(TransactionKRW.amount)), 0).label("spent"),
        )
        .join(Account, Account.id == TransactionKRW.account_id)
        .filter(
            TransactionKRW.user_id == user_id,
            Account.user_id == user_id,
            Account.is_active == True,
            _is_credit_card_account(),
            TransactionKRW.date >= start, TransactionKRW.date < end,
            TransactionKRW.amount < 0,            # expenses only
        )
        .group_by(TransactionKRW.account_id)
        .all()
    )
    totals_map = {row.aid: Decimal(row.spent or 0) for row in totals_rows}

    # 3) Merge (ensure every account appears, even with 0)
    out = []
    for a in accounts:
        out.append({
            "id": a.id,
            "name": a.name,
            "total_spent": totals_map.get(a.id, Decimal(0)),
        })
    return out

def cc_transactions(user_id: int, year: int, month: int, account_id: int | None = None):
    start, end = _month_bounds(year, month)

    q = (
        db.session.query(
            TransactionKRW.id,
            TransactionKRW.date,
            TransactionKRW.note,
            TransactionKRW.amount,
            TransactionKRW.is_pending,
            Account.name.label("account_name"),
            Account.id.label("account_id"),
            Category.name.label("category_name"),   # 👈 pick category name
        )
        .join(Account, Account.id == TransactionKRW.account_id)
        .outerjoin(Category, Category.id == TransactionKRW.category_id)  # 👈 LEFT JOIN
        .filter(
            TransactionKRW.user_id == user_id,
            Account.user_id == user_id,
            Account.is_active == True,
            _is_credit_card_account(),
            TransactionKRW.is_deleted == False,
            TransactionKRW.date >= start, TransactionKRW.date < end,
        )
        .order_by(TransactionKRW.date.desc(), TransactionKRW.id.desc())
    )

    if account_id and account_id != 0:
        q = q.filter(Account.id == account_id)

    rows = q.all()
    return [
        {
            "id": r.id,
            "date": r.date,
            "account_name": r.account_name,
            "account_id": r.account_id,
            "category_name": r.category_name,   # 👈 pass name to template
            "note": r.note,
            "amount": r.amount,
            "is_pending": bool(r.is_pending),
        }
        for r in rows
    ]

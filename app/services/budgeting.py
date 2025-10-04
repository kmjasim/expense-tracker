# app/services/budgeting.py
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from sqlalchemy import func, and_, not_
from ..extensions import db
from ..models import (
    Category, Budget, TxnType,
    TransactionKRW, TransactionBDT, Currency
)
from .textutils import is_cc_settlement  # if you already have something similar, else ignore

# app/services/budgeting.py (only the changed / key parts)

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from sqlalchemy import func, and_, not_
# ... imports as before

@dataclass
class Row:
    id: int | None
    name: str
    parent_id: int | None
    budget: Decimal          # parent-only budget; children will be 0
    spent: Decimal
    pct_parent_budget: float | None   # child’s spent % of parent budget
    pct_of_cat_budget: float | None   # parent’s spent % of its own budget
    children: list

def _month_bounds(y: int, m: int):
    from datetime import date
    start = date(y, m, 1)
    end_y = y + (m == 12)
    end_m = (m % 12) + 1
    end = date(end_y, end_m, 1)
    return start, end

def _model_for_currency(currency: str):
    return TransactionKRW if currency == "KRW" else TransactionBDT

def compute_budget_page(user_id: int, currency: str, year: int, month: int):
    Model = _model_for_currency(currency)
    start, end = _month_bounds(year, month)

    # --- categories for THIS user only
    cats = db.session.query(Category.id, Category.name, Category.parent_id)\
        .filter(Category.user_id == user_id).all()
    by_id = {cid: (name, pid) for cid, name, pid in cats}
    children_of = defaultdict(list)
    roots = []
    for cid, (name, pid) in by_id.items():
        if pid is None:
            roots.append(cid)
        else:
            children_of[pid].append(cid)

    # --- budgets: parent-only (enforce by ignoring children here)
    raw_budgets = db.session.query(Budget.category_id, func.coalesce(func.sum(Budget.amount), 0))\
        .filter(Budget.user_id == user_id, Budget.year == year, Budget.month == month,
                Budget.category_id.in_(roots))\
        .group_by(Budget.category_id).all()
    budget_map = {cid: Decimal(amt or 0) for cid, amt in raw_budgets}  # parents only

    # --- spent per category (exclude settlements)
    note_l = func.lower(func.coalesce(Model.note, ""))
    is_settlement = and_(note_l.like("%credit%card%"), note_l.like("%settlement%"))
    spent_rows = db.session.query(
        Model.category_id,
        func.coalesce(func.sum(-Model.amount), 0)
    ).filter(
        Model.user_id == user_id,
        Model.is_deleted.is_(False),
        Model.date >= start, Model.date < end,
        Model.type.in_([TxnType.expense, TxnType.fee]),
        not_(is_settlement),
    ).group_by(Model.category_id).all()
    spent_map = {cid: Decimal(val or 0) for cid, val in spent_rows}

    # --- build rows: parents first
    def build_parent_row(pid: int) -> Row:
        pname, _ = by_id[pid]
        p_budget = budget_map.get(pid, Decimal(0))

        # sum spent of parent and ALL descendants
        stack = [pid]
        p_spent = Decimal(0)
        while stack:
            cur = stack.pop()
            p_spent += spent_map.get(cur, Decimal(0))
            stack.extend(children_of.get(cur, []))

        # parent progress vs own budget
        pct_of_cat_budget = float((p_spent / p_budget) * 100) if p_budget > 0 else None

        # build children rows (direct children only for UI; their % is child_spent / parent_budget)
        kids = []
        for cid in children_of.get(pid, []):
            cname, _ = by_id[cid]
            c_spent = spent_map.get(cid, Decimal(0))
            pct_parent = float((c_spent / p_budget) * 100) if p_budget > 0 else None
            kids.append(Row(
                id=cid, name=cname, parent_id=pid,
                budget=Decimal(0), spent=c_spent,
                pct_parent_budget=pct_parent, pct_of_cat_budget=None,
                children=[]
            ))

        return Row(
            id=pid, name=pname, parent_id=None,
            budget=p_budget, spent=p_spent,
            pct_parent_budget=None, pct_of_cat_budget=pct_of_cat_budget,
            children=kids
        )

    parent_rows = [build_parent_row(pid) for pid in roots]

    # totals
    total_budget = sum((r.budget for r in parent_rows), Decimal(0))
    total_spent  = sum((r.spent for r in parent_rows), Decimal(0))
    remaining = total_budget - total_spent
    status = "under" if remaining >= 0 else "over"
    pct_of_budget = float((total_spent / total_budget) * 100) if total_budget > 0 else None

    # table payload
    def to_dict(r: Row):
        return {
            "id": r.id, "name": r.name,
            "budget": float(r.budget), "spent": float(r.spent),
            "pct_of_cat_budget": (round(r.pct_of_cat_budget, 1) if r.pct_of_cat_budget is not None else None),
            "pct_parent_budget": (round(r.pct_parent_budget, 1) if r.pct_parent_budget is not None else None),
            "children": [to_dict(c) for c in r.children]
        }
    table = sorted([to_dict(r) for r in parent_rows], key=lambda x: x["name"].lower())

    # bar chart (parents only)
    bar_labels = [r["name"] for r in table]
    bar_values = [r["spent"] for r in table]

    return {
        "totals": {
            "budget": float(total_budget),
            "spent": float(total_spent),
            "remaining": float(remaining),
            "status": status,
            "pct_of_budget": round(pct_of_budget, 1) if pct_of_budget is not None else None
        },
        "table": table,
        "bar_chart": {"labels": bar_labels, "values": bar_values},
        "symbol": "₩",  # KRW
    }
def month_income_total(user_id: int, currency: str, year: int, month: int) -> Decimal:
    """Sum of INCOME transactions for the month (amounts are positive for income)."""
    Model = _model_for_currency(currency)
    start, end = _month_bounds(year, month)

    val = (db.session.query(func.coalesce(func.sum(Model.amount), 0))
           .filter(
               Model.user_id == user_id,
               Model.is_deleted.is_(False),
               Model.date >= start, Model.date < end,
               Model.type == TxnType.income
           )
           .scalar() or 0)
    return Decimal(val)
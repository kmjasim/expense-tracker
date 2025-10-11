# app/services/budgeting.py
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, and_, not_, select

from ..extensions import db
from ..models import (
    Category,
    Budget,
    BudgetType,        # <-- NEW: table for type-scoped budgets
    TxnType,
    TransactionKRW,
    TransactionBDT,    # (unused but kept for parity)
    Currency,          # (unused here; KRW-only per requirement)
)

# Pseudo-row constants for type budget
TYPE_ROW_ID = "type:transfer_international"
TYPE_ROW_LABEL = "International Transfer (Type)"


# --------------------------
# Utilities
# --------------------------
def prev_month(y: int, m: int) -> tuple[int, int]:
    return (y - 1, 12) if m == 1 else (y, m - 1)


def load_parent_categories(user_id: int) -> list[dict]:
    rows = db.session.execute(
        select(Category.id, Category.name)
        .where(Category.user_id == user_id, Category.parent_id.is_(None))
        .order_by(Category.name)
    ).all()
    return [{"id": r.id, "name": r.name} for r in rows]


def budgets_for(user_id: int, year: int, month: int) -> dict[int, Decimal]:
    rows = db.session.execute(
        select(Budget.category_id, Budget.amount)
        .where(Budget.user_id == user_id,
               Budget.year == year,
               Budget.month == month)
    ).all()
    # Only normal category budgets (exclude None, but your schema likely disallows NULL anyway)
    return {r.category_id: r.amount for r in rows if r.category_id is not None}


def resolve_budget_map_with_fallback(user_id: int, year: int, month: int) -> tuple[dict[int, Decimal], bool]:
    current = budgets_for(user_id, year, month)
    if current:
        return current, False
    py, pm = prev_month(year, month)
    prev = budgets_for(user_id, py, pm)
    return prev, bool(prev)


@dataclass
class Row:
    id: int | str | None
    name: str
    parent_id: int | None
    budget: Decimal          # parent-only budget; children will be 0
    spent: Decimal
    pct_parent_budget: float | None   # child’s spent % of parent budget
    pct_of_cat_budget: float | None   # parent’s spent % of its own budget
    children: list


def _month_bounds(y: int, m: int) -> tuple[date, date]:
    start = date(y, m, 1)
    end_y = y + (m == 12)
    end_m = (m % 12) + 1
    end = date(end_y, end_m, 1)
    return start, end


def _model_for_currency(_currency_ignored: str):
    """Force KRW per requirement."""
    return TransactionKRW


# --------------------------
# Core: compute budget page
# --------------------------
def compute_budget_page(user_id: int, currency: str, year: int, month: int) -> dict:
    """
    KRW-only:
      - Parent-category budgets from Budget
      - 'International Transfer' shown as a separate TYPE pseudo-row:
          budget from BudgetType(txn_type=transfer_international)
          spent  from transactions of type transfer_international
    """
    Model = _model_for_currency("KRW")
    start, end = _month_bounds(year, month)

    # --- categories for THIS user only
    cats = db.session.query(Category.id, Category.name, Category.parent_id)\
        .filter(Category.user_id == user_id).all()
    by_id = {cid: (name, pid) for cid, name, pid in cats}
    children_of: dict[int, list[int]] = defaultdict(list)
    roots: list[int] = []
    for cid, (name, pid) in by_id.items():
        if pid is None:
            roots.append(cid)
        else:
            children_of[pid].append(cid)

    # --- budgets: parent-only (ignore children here)
    raw_budgets = (
        db.session.query(Budget.category_id, func.coalesce(func.sum(Budget.amount), 0))
        .filter(Budget.user_id == user_id,
                Budget.year == year, Budget.month == month,
                Budget.category_id.in_(roots))
        .group_by(Budget.category_id)
        .all()
    )
    budget_map: dict[int, Decimal] = {cid: Decimal(amt or 0) for cid, amt in raw_budgets}

    # --- TYPE budget: transfer_international (from BudgetType)
    type_budget_val = (
        db.session.query(func.coalesce(func.sum(BudgetType.amount), 0))
        .filter(BudgetType.user_id == user_id,
                BudgetType.year == year, BudgetType.month == month,
                BudgetType.txn_type == TxnType.transfer_international)
        .scalar() or 0
    )
    type_budget = Decimal(type_budget_val or 0)

    # --- spent per category (exclude credit-card settlements)
    note_l = func.lower(func.coalesce(Model.note, ""))
    is_settlement = and_(note_l.like("%credit%card%"), note_l.like("%settlement%"))

    spent_rows = (
        db.session.query(Model.category_id, func.coalesce(func.sum(-Model.amount), 0))
        .filter(Model.user_id == user_id,
                Model.is_deleted.is_(False),
                Model.date >= start, Model.date < end,
                Model.type.in_([TxnType.expense, TxnType.fee]),
                not_(is_settlement))
        .group_by(Model.category_id)
        .all()
    )
    spent_map: dict[int | None, Decimal] = {cid: Decimal(val or 0) for cid, val in spent_rows}

    # --- spent for the TYPE row (transfer_international)
    type_spent_val = (
        db.session.query(func.coalesce(func.sum(-Model.amount), 0))
        .filter(Model.user_id == user_id,
                Model.is_deleted.is_(False),
                Model.date >= start, Model.date < end,
                Model.type == TxnType.transfer_international)
        .scalar() or 0
    )
    type_spent = Decimal(type_spent_val or 0)

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

        # children rows (only direct children for UI; % = child_spent / parent_budget)
        kids: list[Row] = []
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

    parent_rows: list[Row] = [build_parent_row(pid) for pid in roots]

    # --- append TYPE pseudo-parent (no children)
    pct_type = float((type_spent / type_budget) * 100) if type_budget > 0 else None
    parent_rows.append(Row(
        id=TYPE_ROW_ID,
        name=TYPE_ROW_LABEL,
        parent_id=None,
        budget=type_budget,
        spent=type_spent,
        pct_parent_budget=None,
        pct_of_cat_budget=pct_type,
        children=[]
    ))

    # --- totals
    total_budget = sum((r.budget for r in parent_rows), Decimal(0))
    total_spent  = sum((r.spent for r in parent_rows), Decimal(0))
    remaining = total_budget - total_spent
    status = "under" if remaining >= 0 else "over"
    pct_of_budget = float((total_spent / total_budget) * 100) if total_budget > 0 else None

    # --- serialize table for template/JS (sorted: categories first, type row last)
    def to_dict(r: Row) -> dict:
        return {
            "id": r.id, "name": r.name,
            "budget": float(r.budget), "spent": float(r.spent),
            "pct_of_cat_budget": (round(r.pct_of_cat_budget, 1) if r.pct_of_cat_budget is not None else None),
            "pct_parent_budget": (round(r.pct_parent_budget, 1) if r.pct_parent_budget is not None else None),
            "children": [to_dict(c) for c in r.children]
        }

    table = sorted(
        (to_dict(r) for r in parent_rows),
        key=lambda x: (x["id"] == TYPE_ROW_ID, x["name"].lower())
    )

    # bar chart (parents + type)
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


# --------------------------
# KPIs
# --------------------------
def month_income_total(user_id: int, currency: str, year: int, month: int) -> Decimal:
    """Sum of INCOME (KRW) transactions for the month (amounts are positive for income)."""
    Model = TransactionKRW  # forced
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

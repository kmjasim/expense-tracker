# app/services/expense_breakdown.py
from collections import defaultdict
from datetime import date
from decimal import Decimal
from sqlalchemy import func, not_, and_
from ..extensions import db
from ..models import TransactionKRW, TransactionBDT, TxnType, Category
from app.services.textutils import is_cc_settlement
def _model_for_currency(currency: str):
    return TransactionKRW if currency == "KRW" else TransactionBDT

def _month_bounds(y: int, m: int):
    start = date(y, m, 1)
    end = date(y + (m == 12), (m % 12) + 1, 1)
    return start, end

def _prev_month(y: int, m: int):
    return (y - 1, 12) if m == 1 else (y, m - 1)

def expense_breakdown(user_id: int, currency: str, year: int, month: int, top_n: int = 7):
    Model = _model_for_currency(currency)
    start, end = _month_bounds(year, month)

    # --- settlement detector (same as before)
    note_l = func.lower(func.coalesce(Model.note, ""))
    is_settlement = and_(note_l.like("%credit%card%"), note_l.like("%settlement%"))

    # --- current month raw rows by leaf category (same as before)
    rows = (
        db.session.query(
            Model.category_id,
            func.coalesce(func.sum(-Model.amount), 0).label("amt")
        )
        .outerjoin(Category, Category.id == Model.category_id)
        .filter(
            Model.user_id == user_id,
            Model.is_deleted.is_(False),
            Model.date >= start, Model.date < end,
            Model.type.in_([TxnType.expense, TxnType.fee]),
            not_(is_settlement),
        )
        .group_by(Model.category_id)
        .all()
    )

    # --- load all categories for this user (to resolve parents)
    # If your Category has user_id, include that filter; otherwise remove it.
    cats = db.session.query(Category.id, Category.name, Category.parent_id).all()
    by_id = {cid: {"name": name, "parent_id": pid} for cid, name, pid in cats}

    # find top-level ancestor (root) with memoization
    root_cache: dict[int, int] = {}
    def root_of(cid: int | None) -> int | None:
        if cid is None:
            return None
        if cid in root_cache:
            return root_cache[cid]
        seen = set()
        cur = cid
        while cur is not None and cur in by_id and by_id[cur]["parent_id"] is not None and cur not in seen:
            seen.add(cur)
            cur = by_id[cur]["parent_id"]
        root_cache[cid] = cur
        return cur

    # --- roll up amounts to root parent
    totals_by_root: dict[int | None, Decimal] = defaultdict(Decimal)
    for cat_id, amt in rows:
        amt = Decimal(amt or 0)
        root = root_of(cat_id)  # None stays None (Uncategorized)
        totals_by_root[root] += amt

    # --- build items (parent rows only)
    items = []
    total = Decimal(0)
    for root_id, amt in totals_by_root.items():
        name = "Uncategorized" if root_id is None else by_id.get(root_id, {}).get("name", "Unknown")
        amt = Decimal(amt or 0)
        total += amt
        items.append({"name": name, "amount": amt})

    # --- International transfers bucket (kept as a separate parent-like bucket)
    intl_out = db.session.query(
        func.coalesce(func.sum(-Model.amount), 0)
    ).filter(
        Model.user_id == user_id,
        Model.is_deleted.is_(False),
        Model.date >= start, Model.date < end,
        Model.type == TxnType.transfer_international,
        Model.amount < 0,
        not_(is_settlement),
    ).scalar() or 0
    intl_out = Decimal(intl_out)
    if intl_out > 0:
        items.append({"name": "International Transfer", "amount": intl_out})
        total += intl_out  # keep in total; remove if you don't want it counted

    # --- sort + pct
    items.sort(key=lambda x: x["amount"], reverse=True)
    total = total or Decimal(0)
    for it in items:
        it["pct"] = float((it["amount"] / total) * 100) if total > 0 else 0.0

    # --- chart top N + Other
    top = items[:top_n]
    other_amt = sum((x["amount"] for x in items[top_n:]), Decimal(0))
    labels = [x["name"] for x in top]
    values = [float(x["amount"]) for x in top]
    if other_amt > 0:
        labels.append("Other")
        values.append(float(other_amt))

    # --- previous month total (unchanged, still only expenses/fees)
    py, pm = _prev_month(year, month)
    pstart, pend = _month_bounds(py, pm)
    prev_total = db.session.query(func.coalesce(func.sum(-Model.amount), 0)).filter(
        Model.user_id == user_id,
        Model.is_deleted.is_(False),
        Model.date >= pstart, Model.date < pend,
        Model.type.in_([TxnType.expense, TxnType.fee]),
        not_(and_(func.lower(func.coalesce(Model.note, "")).like("%credit%card%"),
                  func.lower(func.coalesce(Model.note, "")).like("%settlement%"))),
    ).scalar() or 0
    prev_total = Decimal(prev_total)

    if prev_total > 0:
        change_pct = float(((total - prev_total) / prev_total) * 100)
        change_up = (total - prev_total) >= 0
    else:
        change_pct, change_up = None, None

    return {
        "total": float(total),
        "change_pct": change_pct,
        "change_up": change_up,
        "labels": labels,
        "values": values,
        "items": [
            {"name": it["name"], "amount": float(it["amount"]), "pct": it["pct"]}
            for it in items
        ],
    }
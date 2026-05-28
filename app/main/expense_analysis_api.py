from datetime import date, datetime, timedelta

from flask import request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import and_, or_, not_, func, cast, String

from . import main
from ..models import TransactionKRW, TransactionBDT, Account, Category


# --------------------------
# Helpers
# --------------------------
def _apply_income_rules(query, txn_model):
    return (
        query
        .filter(txn_model.amount > 0)
        .filter(_type_contains(txn_model, "income"))
        .filter(not_(_type_contains(txn_model, "transfer")))
        .filter(not_(_type_contains(txn_model, "refund")))
    )
def _to_float(value):
    return round(float(value or 0), 2)


def _parse_bool(value, default=False):
    if value is None:
        return default
    return str(value).lower() in ("1", "true", "yes", "on")


def _parse_date(value, fallback):
    if not value:
        return fallback
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return fallback


def _money(amount, currency):
    symbol = "৳" if currency == "BDT" else "₩"
    return f"{symbol}{float(amount or 0):,.0f}"


def _type_text(txn_model):
    return func.lower(cast(txn_model.type, String))


def _type_contains(txn_model, keyword):
    return _type_text(txn_model).like(f"%{keyword.lower()}%")


def _settlement_filter(txn_model):
    return and_(
        txn_model.note.isnot(None),
        txn_model.note.ilike("%credit%"),
        txn_model.note.ilike("%card%"),
        txn_model.note.ilike("%settlement%"),
    )


def _internal_or_self_transfer_filter(txn_model):
    self_transfer_filter = and_(
        txn_model.recipient_name.isnot(None),
        func.lower(txn_model.recipient_name).in_(["self", "myself"]),
    )

    internal_transfer_filter = and_(
        txn_model.note.isnot(None),
        or_(
            txn_model.note.ilike("%internal transfer%"),
            txn_model.note.ilike("%own account transfer%"),
            txn_model.note.ilike("%transfer to self%"),
        ),
    )

    return or_(self_transfer_filter, internal_transfer_filter)


def _child_category_ids(category_id):
    """
    Selected category + direct child categories.
    Useful when you select a parent category.
    """
    if not category_id:
        return []

    children = (
        Category.query
        .filter(
            Category.user_id == current_user.id,
            Category.parent_id == category_id,
        )
        .all()
    )

    return [category_id] + [child.id for child in children]


def _base_query(txn_model, start_date, end_date, account_id=None, category_id=None, include_pending=False):
    query = txn_model.query.filter(
        txn_model.user_id == current_user.id,
        txn_model.is_deleted.is_(False),
        txn_model.date >= start_date,
        txn_model.date <= end_date,
    )

    if not include_pending:
        query = query.filter(txn_model.is_pending.is_(False))

    if account_id:
        query = query.filter(txn_model.account_id == account_id)

    if category_id:
        category_ids = _child_category_ids(category_id)
        query = query.filter(txn_model.category_id.in_(category_ids))

    return query


def _apply_expense_rules(query, txn_model, include_fees=True, include_transfers=False, include_settlements=False):
    query = query.filter(txn_model.amount < 0)

    if not include_settlements:
        query = query.filter(not_(_settlement_filter(txn_model)))

    if not include_transfers:
        query = query.filter(not_(_type_contains(txn_model, "transfer")))
        query = query.filter(not_(_internal_or_self_transfer_filter(txn_model)))

    if not include_fees:
        query = query.filter(not_(_type_contains(txn_model, "fee")))

    return query


def _sum_abs(query, txn_model):
    value = query.with_entities(
        func.coalesce(func.sum(txn_model.amount), 0)
    ).scalar()
    return abs(_to_float(value))


def _sum_positive(query, txn_model):
    value = (
        query
        .filter(txn_model.amount > 0)
        .with_entities(func.coalesce(func.sum(txn_model.amount), 0))
        .scalar()
    )
    return _to_float(value)


def _count(query, txn_model):
    return int(query.with_entities(func.count(txn_model.id)).scalar() or 0)


def _category_options():
    categories = (
        Category.query
        .filter(Category.user_id == current_user.id)
        .order_by(Category.name.asc())
        .all()
    )

    by_parent = {}
    for cat in categories:
        by_parent.setdefault(cat.parent_id, []).append(cat)

    options = []

    for parent in sorted(by_parent.get(None, []), key=lambda c: c.name.lower()):
        options.append({
            "id": parent.id,
            "name": parent.name,
            "parent_id": parent.parent_id,
            "level": 0,
        })

        for child in sorted(by_parent.get(parent.id, []), key=lambda c: c.name.lower()):
            options.append({
                "id": child.id,
                "name": f"— {child.name}",
                "parent_id": child.parent_id,
                "level": 1,
            })

    # In case old data has children whose parent is missing
    added_ids = {item["id"] for item in options}
    for cat in categories:
        if cat.id not in added_ids:
            options.append({
                "id": cat.id,
                "name": cat.name,
                "parent_id": cat.parent_id,
                "level": 0,
            })

    return options


def _account_options(currency):
    accounts = (
        Account.query
        .filter(
            Account.user_id == current_user.id,
            Account.is_active.is_(True),
        )
        .order_by(Account.display_order.asc(), Account.name.asc())
        .all()
    )

    result = []
    for acc in accounts:
        acc_currency = getattr(acc.currency, "value", acc.currency)
        if acc_currency == currency:
            result.append({
                "id": acc.id,
                "name": acc.name,
            })

    return result


def _build_category_breakdown(expense_query, txn_model, total_expense):
    rows = (
        expense_query
        .with_entities(
            txn_model.category_id,
            txn_model.category_name,
            func.coalesce(func.sum(txn_model.amount), 0).label("total_amount"),
            func.count(txn_model.id).label("txn_count"),
        )
        .group_by(txn_model.category_id, txn_model.category_name)
        .all()
    )

    result = []

    for row in rows:
        amount = abs(_to_float(row.total_amount))
        count = int(row.txn_count or 0)
        share = round((amount / total_expense * 100), 2) if total_expense else 0
        average = round(amount / count, 2) if count else 0

        if share >= 30:
            status = "High"
        elif share >= 15:
            status = "Medium"
        else:
            status = "Normal"

        result.append({
            "category_id": row.category_id,
            "category_name": row.category_name or "Transfers",
            "amount": amount,
            "share": share,
            "transaction_count": count,
            "average_amount": average,
            "status": status,
        })

    result.sort(key=lambda item: item["amount"], reverse=True)
    return result


def _build_account_breakdown(expense_query, txn_model, total_expense):
    rows = (
        expense_query
        .join(Account, Account.id == txn_model.account_id)
        .with_entities(
            txn_model.account_id,
            Account.name.label("account_name"),
            func.coalesce(func.sum(txn_model.amount), 0).label("total_amount"),
            func.count(txn_model.id).label("txn_count"),
        )
        .group_by(txn_model.account_id, Account.name)
        .all()
    )

    result = []

    for row in rows:
        amount = abs(_to_float(row.total_amount))

        result.append({
            "account_id": row.account_id,
            "account_name": row.account_name or "Unknown Account",
            "amount": amount,
            "share": round((amount / total_expense * 100), 2) if total_expense else 0,
            "transaction_count": int(row.txn_count or 0),
        })

    result.sort(key=lambda item: item["amount"], reverse=True)
    return result


def _build_recipient_breakdown(expense_query, txn_model, total_expense):
    recipient_query = expense_query.filter(
        txn_model.recipient_name.isnot(None),
        func.trim(txn_model.recipient_name) != "",
    )

    rows = (
        recipient_query
        .with_entities(
            txn_model.recipient_name.label("recipient_name"),
            func.coalesce(func.sum(txn_model.amount), 0).label("total_amount"),
            func.count(txn_model.id).label("txn_count"),
        )
        .group_by(txn_model.recipient_name)
        .all()
    )

    result = []

    for row in rows:
        amount = abs(_to_float(row.total_amount))

        if amount <= 0:
            continue

        result.append({
            "recipient_name": row.recipient_name,
            "amount": amount,
            "share": round((amount / total_expense * 100), 2) if total_expense else 0,
            "transaction_count": int(row.txn_count or 0),
        })

    result.sort(key=lambda item: item["amount"], reverse=True)
    return result[:10]

def _build_trend(expense_query, txn_model, start_date, end_date, group_by):
    rows = (
        expense_query
        .with_entities(
            txn_model.date,
            func.coalesce(func.sum(txn_model.amount), 0).label("total_amount"),
        )
        .group_by(txn_model.date)
        .order_by(txn_model.date.asc())
        .all()
    )

    daily_map = {}
    for row in rows:
        daily_map[row.date] = abs(_to_float(row.total_amount))

    if group_by == "month":
        grouped = {}
        current = date(start_date.year, start_date.month, 1)
        end_month = date(end_date.year, end_date.month, 1)

        while current <= end_month:
            key = current.strftime("%Y-%m")
            grouped[key] = 0

            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)

        for row_date, amount in daily_map.items():
            key = row_date.strftime("%Y-%m")
            grouped[key] = grouped.get(key, 0) + amount

        return [
            {
                "label": key,
                "amount": round(amount, 2),
            }
            for key, amount in grouped.items()
        ]

    if group_by == "week":
        grouped = {}
        current = start_date

        while current <= end_date:
            iso = current.isocalendar()
            key = f"{iso.year}-W{iso.week:02d}"
            grouped.setdefault(key, 0)
            current += timedelta(days=1)

        for row_date, amount in daily_map.items():
            iso = row_date.isocalendar()
            key = f"{iso.year}-W{iso.week:02d}"
            grouped[key] = grouped.get(key, 0) + amount

        return [
            {
                "label": key,
                "amount": round(amount, 2),
            }
            for key, amount in grouped.items()
        ]

    # Default: day
    result = []
    current = start_date

    while current <= end_date:
        result.append({
            "label": current.strftime("%m-%d"),
            "amount": daily_map.get(current, 0),
        })
        current += timedelta(days=1)

    return result


def _build_insights(summary, category_breakdown, trend, previous_total, currency):
    messages = []

    top_category = category_breakdown[0] if category_breakdown else None

    if top_category:
        messages.append({
            "type": "primary",
            "title": "Biggest spending category",
            "text": (
                f"{top_category['category_name']} used {top_category['share']}% "
                f"of your expenses ({_money(top_category['amount'], currency)})."
            ),
        })

        if top_category["share"] >= 30:
            messages.append({
                "type": "warning",
                "title": "High category concentration",
                "text": (
                    f"{top_category['category_name']} is taking a big share. "
                    f"Try reducing it by 10–15% first."
                ),
            })

    top_3_share = round(sum(row["share"] for row in category_breakdown[:3]), 2)

    if top_3_share >= 70:
        messages.append({
            "type": "info",
            "title": "Top 3 categories control most spending",
            "text": (
                f"Your top 3 categories make up {top_3_share}% of expenses. "
                f"Small changes there will create the biggest result."
            ),
        })

    if trend:
        highest = max(trend, key=lambda item: item["amount"])

        if highest["amount"] > 0:
            messages.append({
                "type": "secondary",
                "title": "Highest spending period",
                "text": (
                    f"Your highest spending point was {highest['label']} "
                    f"with {_money(highest['amount'], currency)}."
                ),
            })

    compare_percent = summary.get("compare_previous_percent", 0)

    if previous_total > 0:
        if compare_percent > 15:
            messages.append({
                "type": "danger",
                "title": "Spending increased",
                "text": (
                    f"Your spending is {compare_percent}% higher than the previous "
                    f"same-length period."
                ),
            })
        elif compare_percent < 0:
            messages.append({
                "type": "success",
                "title": "Spending improved",
                "text": (
                    f"Your spending is {abs(compare_percent)}% lower than the previous "
                    f"same-length period."
                ),
            })

    if top_category and top_category["amount"] > 0:
        possible_save = top_category["amount"] * 0.15

        messages.append({
            "type": "success",
            "title": "Possible saving target",
            "text": (
                f"Reducing {top_category['category_name']} by 15% could save "
                f"about {_money(possible_save, currency)}."
            ),
        })

    if not messages:
        messages.append({
            "type": "light",
            "title": "No strong pattern yet",
            "text": "There is not enough expense data for strong advice in this period.",
        })

    return {
        "top_3_share": top_3_share,
        "messages": messages,
    }


# --------------------------
# API
# --------------------------
@main.route("/api/expense-analysis")
@login_required
def api_expense_analysis():
    today = date.today()
    default_start = today.replace(day=1)
    default_end = today

    currency = request.args.get("currency", "KRW").upper()

    if currency not in ("KRW", "BDT"):
        currency = "KRW"

    start_date = _parse_date(request.args.get("start_date"), default_start)
    end_date = _parse_date(request.args.get("end_date"), default_end)

    if end_date < start_date:
        start_date, end_date = end_date, start_date

    account_id = request.args.get("account_id", type=int)
    category_id = request.args.get("category_id", type=int)
    group_by = request.args.get("group_by", "day")

    if group_by not in ("day", "week", "month"):
        group_by = "day"

    include_pending = _parse_bool(request.args.get("include_pending"), False)
    include_fees = _parse_bool(request.args.get("include_fees"), True)
    include_transfers = _parse_bool(request.args.get("include_transfers"), False)
    include_settlements = _parse_bool(request.args.get("include_settlements"), False)

    txn_model = TransactionKRW if currency == "KRW" else TransactionBDT

    base_query = _base_query(
        txn_model=txn_model,
        start_date=start_date,
        end_date=end_date,
        account_id=account_id,
        category_id=category_id,
        include_pending=include_pending,
    )

    expense_query = _apply_expense_rules(
        query=base_query,
        txn_model=txn_model,
        include_fees=include_fees,
        include_transfers=include_transfers,
        include_settlements=include_settlements,
    )

    period_days = max((end_date - start_date).days + 1, 1)

    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=period_days - 1)

    previous_base_query = _base_query(
        txn_model=txn_model,
        start_date=prev_start,
        end_date=prev_end,
        account_id=account_id,
        category_id=category_id,
        include_pending=include_pending,
    )

    previous_expense_query = _apply_expense_rules(
        query=previous_base_query,
        txn_model=txn_model,
        include_fees=include_fees,
        include_transfers=include_transfers,
        include_settlements=include_settlements,
    )

    total_expense = _sum_abs(expense_query, txn_model)
    previous_total = _sum_abs(previous_expense_query, txn_model)

    income_query = _apply_income_rules(base_query, txn_model)
    total_income = _sum_positive(income_query, txn_model)
    net_cashflow = round(total_income - total_expense, 2)

    if previous_total > 0:
        compare_previous_percent = round(
            ((total_expense - previous_total) / previous_total) * 100,
            2,
        )
    else:
        compare_previous_percent = 0

    if compare_previous_percent > 0:
        compare_previous_label = f"+{compare_previous_percent}%"
    elif compare_previous_percent < 0:
        compare_previous_label = f"{compare_previous_percent}%"
    else:
        compare_previous_label = "0%"

    category_breakdown = _build_category_breakdown(expense_query, txn_model, total_expense)
    account_breakdown = _build_account_breakdown(expense_query, txn_model, total_expense)
    recipient_breakdown = _build_recipient_breakdown(expense_query, txn_model, total_expense)
    trend = _build_trend(expense_query, txn_model, start_date, end_date, group_by)

    top_category = category_breakdown[0]["category_name"] if category_breakdown else "-"
    top_category_amount = category_breakdown[0]["amount"] if category_breakdown else 0

    highest_trend = max(trend, key=lambda item: item["amount"]) if trend else {"label": "-", "amount": 0}

    if highest_trend["amount"] <= 0:
        highest_spending_label = "-"
        highest_spending_amount = 0
    else:
        highest_spending_label = highest_trend["label"]
        highest_spending_amount = highest_trend["amount"]

    summary = {
        "currency": currency,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "period_days": period_days,
        "total_expense": total_expense,
        "total_income": total_income,
        "net_cashflow": net_cashflow,
        "avg_daily_expense": round(total_expense / period_days, 2),
        "transaction_count": _count(expense_query, txn_model),
        "top_category": top_category,
        "top_category_amount": top_category_amount,
        "highest_spending_label": highest_spending_label,
        "highest_spending_amount": highest_spending_amount,
        "compare_previous": compare_previous_label,
        "compare_previous_percent": compare_previous_percent,
        "previous_total_expense": previous_total,
    }

    insights = _build_insights(
        summary=summary,
        category_breakdown=category_breakdown,
        trend=trend,
        previous_total=previous_total,
        currency=currency,
    )

    return jsonify({
        "filters": {
            "accounts": _account_options(currency),
            "categories": _category_options(),
        },
        "summary": summary,
        "category_breakdown": category_breakdown,
        "account_breakdown": account_breakdown,
        "recipient_breakdown": recipient_breakdown,
        "trend": trend,
        "insights": insights,
    })

@main.route("/api/expense-analysis/category-transactions")
@login_required
def api_expense_analysis_category_transactions():
    try:
        today = date.today()
        default_start = today.replace(day=1)
        default_end = today

        currency = request.args.get("currency", "KRW").upper()
        if currency not in ("KRW", "BDT"):
            currency = "KRW"

        start_date = _parse_date(request.args.get("start_date"), default_start)
        end_date = _parse_date(request.args.get("end_date"), default_end)

        if end_date < start_date:
            start_date, end_date = end_date, start_date

        account_id = request.args.get("account_id", type=int)
        category_id = request.args.get("category_id", type=int)
        uncategorized = _parse_bool(request.args.get("uncategorized"), False)

        include_pending = _parse_bool(request.args.get("include_pending"), False)
        include_fees = _parse_bool(request.args.get("include_fees"), True)
        include_transfers = _parse_bool(request.args.get("include_transfers"), False)
        include_settlements = _parse_bool(request.args.get("include_settlements"), False)

        txn_model = TransactionKRW if currency == "KRW" else TransactionBDT

        base_query = _base_query(
            txn_model=txn_model,
            start_date=start_date,
            end_date=end_date,
            account_id=account_id,
            category_id=None,
            include_pending=include_pending,
        )

        if uncategorized:
            base_query = base_query.filter(txn_model.category_id.is_(None))
        elif category_id:
            category_ids = _child_category_ids(category_id)
            base_query = base_query.filter(txn_model.category_id.in_(category_ids))
        else:
            return jsonify({
                "ok": True,
                "transactions": [],
                "summary": {
                    "total_amount": 0,
                    "transaction_count": 0,
                }
            })

        expense_query = _apply_expense_rules(
            query=base_query,
            txn_model=txn_model,
            include_fees=include_fees,
            include_transfers=include_transfers,
            include_settlements=include_settlements,
        )

        rows = (
            expense_query
            .outerjoin(Account, Account.id == txn_model.account_id)
            .with_entities(
                txn_model.id.label("id"),
                txn_model.date.label("date"),
                txn_model.amount.label("amount"),
                txn_model.category_name.label("category_name"),
                txn_model.note.label("note"),
                txn_model.recipient_name.label("recipient_name"),
                txn_model.method.label("method"),
                Account.name.label("account_name"),
            )
            .order_by(txn_model.date.desc(), txn_model.id.desc())
            .limit(500)
            .all()
        )

        transactions = []
        total_amount = 0

        for row in rows:
            item = row._mapping

            amount = abs(_to_float(item["amount"]))
            total_amount += amount

            method_value = item["method"]
            method_value = getattr(method_value, "value", method_value)

            row_date = item["date"]
            row_date = row_date.isoformat() if row_date else ""

            transactions.append({
                "id": item["id"],
                "date": row_date,
                "amount": amount,
                "category_name": item["category_name"] or "Uncategorized",
                "account_name": item["account_name"] or "-",
                "recipient_name": item["recipient_name"] or "-",
                "method": method_value or "-",
                "note": item["note"] or "",
            })

        return jsonify({
            "ok": True,
            "transactions": transactions,
            "summary": {
                "total_amount": round(total_amount, 2),
                "transaction_count": len(transactions),
            }
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "transactions": [],
            "summary": {
                "total_amount": 0,
                "transaction_count": 0,
            }
        }), 500
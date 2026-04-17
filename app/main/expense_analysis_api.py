from datetime import date
import calendar
from sqlalchemy import not_, and_, or_
from flask import request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func, extract

from . import main
from ..models import TransactionKRW, TransactionBDT, Account, Category
def build_category_map(rows):
    result = {}
    for row in rows:
        name = row.category_name or "Uncategorized"
        result[name] = abs(float(row.total_amount or 0))
    return result
def apply_real_expense_filters(query, txn_model):
    settlement_filter = and_(
        txn_model.note.isnot(None),
        txn_model.note.ilike('%credit%'),
        txn_model.note.ilike('%card%'),
        txn_model.note.ilike('%settlement%')
    )

    self_transfer_filter = and_(
        txn_model.recipient_name.isnot(None),
        func.lower(txn_model.recipient_name).in_(['self', 'myself'])
    )

    internal_transfer_filter = or_(
        txn_model.note.ilike('%internal transfer%'),
        txn_model.note.ilike('%own account transfer%'),
        txn_model.note.ilike('%transfer to self%')
    )

    return query.filter(
        txn_model.amount < 0,
        not_(settlement_filter),
        not_(self_transfer_filter),
        not_(internal_transfer_filter)
    )
@main.route('/api/expense-analysis')
@login_required
def api_expense_analysis():
    currency = request.args.get('currency', 'KRW').upper()
    month = request.args.get('month', type=int)
    year = request.args.get('year', type=int)
    account_id = request.args.get('account_id', type=int)
    category_id = request.args.get('category_id', type=int)

    today = date.today()
    month = month or today.month
    year = year or today.year

    txn_model = TransactionKRW if currency == 'KRW' else TransactionBDT

    accounts = (
        Account.query
        .filter(
            Account.user_id == current_user.id,
            Account.is_active.is_(True)
        )
        .order_by(Account.display_order.asc(), Account.name.asc())
        .all()
    )

    account_options = []
    for acc in accounts:
        acc_currency = getattr(acc.currency, "value", acc.currency)
        if acc_currency == currency:
            account_options.append({
                "id": acc.id,
                "name": acc.name
            })



    base_query = txn_model.query.filter(
        txn_model.user_id == current_user.id,
        txn_model.is_deleted.is_(False),
        extract('month', txn_model.date) == month,
        extract('year', txn_model.date) == year,
    )

    base_query = apply_real_expense_filters(base_query, txn_model)

    if account_id:
        base_query = base_query.filter(txn_model.account_id == account_id)

    if category_id:
        base_query = base_query.filter(txn_model.category_id == category_id)

    total_expense_raw = base_query.with_entities(
        func.coalesce(func.sum(txn_model.amount), 0)
    ).scalar()

    total_expense = abs(float(total_expense_raw or 0))

    category_rows = (
        base_query.with_entities(
            txn_model.category_id,
            txn_model.category_name,
            func.coalesce(func.sum(txn_model.amount), 0).label('total_amount')
        )
        .group_by(txn_model.category_id, txn_model.category_name)
        .all()
    )

    category_breakdown = []
    for row in category_rows:
        amount_abs = abs(float(row.total_amount or 0))
        share = round((amount_abs / total_expense * 100), 2) if total_expense > 0 else 0

        category_breakdown.append({
            "category_id": row.category_id,
            "category_name": row.category_name or "Uncategorized",
            "amount": amount_abs,
            "share": share
        })

    category_breakdown.sort(key=lambda x: x["amount"], reverse=True)

    top_category = category_breakdown[0]["category_name"] if category_breakdown else "-"
    top_category_amount = category_breakdown[0]["amount"] if category_breakdown else 0

    days_in_month = calendar.monthrange(year, month)[1]
    avg_daily_expense = round(total_expense / days_in_month, 2) if days_in_month else 0

    if month == 1:
        prev_month = 12
        prev_year = year - 1
    else:
        prev_month = month - 1
        prev_year = year

    prev_query = txn_model.query.filter(
        txn_model.user_id == current_user.id,
        txn_model.is_deleted.is_(False),
        extract('month', txn_model.date) == prev_month,
        extract('year', txn_model.date) == prev_year,
    )

    prev_query = apply_real_expense_filters(prev_query, txn_model)

    if account_id:
        prev_query = prev_query.filter(txn_model.account_id == account_id)

    if category_id:
        prev_query = prev_query.filter(txn_model.category_id == category_id)

    prev_total_raw = prev_query.with_entities(
        func.coalesce(func.sum(txn_model.amount), 0)
    ).scalar()

    prev_total = abs(float(prev_total_raw or 0))

    if prev_total > 0:
        compare_percent = round(((total_expense - prev_total) / prev_total) * 100, 2)
    else:
        compare_percent = 0

    if compare_percent > 0:
        compare_label = f"+{compare_percent}%"
    elif compare_percent < 0:
        compare_label = f"{compare_percent}%"
    else:
        compare_label = "0%"

    categories = (
        Category.query
        .filter(Category.user_id == current_user.id)
        .order_by(Category.name.asc())
        .all()
    )

    category_options = [
        {
            "id": cat.id,
            "name": cat.name,
            "parent_id": cat.parent_id
        }
        for cat in categories
    ]
    # -----------------------------------
    # Daily expense trend for selected month
    # -----------------------------------
    daily_rows = (
        base_query.with_entities(
            extract('day', txn_model.date).label('day'),
            func.coalesce(func.sum(txn_model.amount), 0).label('total_amount')
        )
        .group_by(extract('day', txn_model.date))
        .order_by(extract('day', txn_model.date))
        .all()
    )

    days_in_month = calendar.monthrange(year, month)[1]
    daily_map = {int(row.day): abs(float(row.total_amount or 0)) for row in daily_rows}

    daily_expense_trend = [
        {
            "day": day,
            "amount": daily_map.get(day, 0)
        }
        for day in range(1, days_in_month + 1)
    ]
        # -----------------------------------
    # Monthly expense trend for selected year
    # -----------------------------------
    yearly_base_query = txn_model.query.filter(
        txn_model.user_id == current_user.id,
        txn_model.is_deleted.is_(False),
        extract('year', txn_model.date) == year,
    )

    yearly_base_query = apply_real_expense_filters(yearly_base_query, txn_model)

    if account_id:
        yearly_base_query = yearly_base_query.filter(txn_model.account_id == account_id)

    if category_id:
        yearly_base_query = yearly_base_query.filter(txn_model.category_id == category_id)

    monthly_rows = (
        yearly_base_query.with_entities(
            extract('month', txn_model.date).label('month'),
            func.coalesce(func.sum(txn_model.amount), 0).label('total_amount')
        )
        .group_by(extract('month', txn_model.date))
        .order_by(extract('month', txn_model.date))
        .all()
    )

    monthly_map = {int(row.month): abs(float(row.total_amount or 0)) for row in monthly_rows}

    monthly_expense_trend = [
        {
            "month": m,
            "amount": monthly_map.get(m, 0)
        }
        for m in range(1, 13)
    ]
        # -----------------------------------
    # Small expense insight
    # -----------------------------------
    SMALL_EXPENSE_THRESHOLD = 10000 if currency == "KRW" else 1000

    small_expense_rows = (
        base_query.with_entities(
            txn_model.id,
            txn_model.amount
        )
        .filter(func.abs(txn_model.amount) < SMALL_EXPENSE_THRESHOLD)
        .all()
    )

    small_expense_count = len(small_expense_rows)
    small_expense_total = round(
        sum(abs(float(row.amount or 0)) for row in small_expense_rows), 2
    )

    # -----------------------------------
    # Highest spending day
    # -----------------------------------
    highest_spending_day = None
    highest_spending_day_amount = 0

    if daily_expense_trend:
        highest_day_row = max(daily_expense_trend, key=lambda x: x["amount"])
        if highest_day_row["amount"] > 0:
            highest_spending_day = highest_day_row["day"]
            highest_spending_day_amount = highest_day_row["amount"]

    # -----------------------------------
    # Top 3 category concentration
    # -----------------------------------
    top_3_total = round(sum(row["amount"] for row in category_breakdown[:3]), 2)
    top_3_share = round(sum(row["share"] for row in category_breakdown[:3]), 2)

    # -----------------------------------
    # Biggest category growth vs previous month
    # -----------------------------------
    prev_category_rows = (
        prev_query.with_entities(
            txn_model.category_id,
            txn_model.category_name,
            func.coalesce(func.sum(txn_model.amount), 0).label('total_amount')
        )
        .group_by(txn_model.category_id, txn_model.category_name)
        .all()
    )

    current_category_map = build_category_map(category_rows)
    prev_category_map = build_category_map(prev_category_rows)

    biggest_growth_category = None
    biggest_growth_amount = 0
    biggest_growth_percent = 0

    for category_name, current_amount in current_category_map.items():
        previous_amount = prev_category_map.get(category_name, 0)

        growth_amount = current_amount - previous_amount

        if previous_amount > 0:
            growth_percent = round((growth_amount / previous_amount) * 100, 2)
        elif current_amount > 0:
            growth_percent = 100.0
        else:
            growth_percent = 0

        if growth_amount > biggest_growth_amount:
            biggest_growth_amount = round(growth_amount, 2)
            biggest_growth_percent = growth_percent
            biggest_growth_category = category_name
    return jsonify({
        "filters": {
            "accounts": account_options,
            "categories": category_options
        },
        "summary": {
            "total_expense": total_expense,
            "top_category": top_category,
            "top_category_amount": top_category_amount,
            "avg_daily_expense": avg_daily_expense,
            "compare_last_month": compare_label,
            "compare_last_month_percent": compare_percent
        },
        "category_breakdown": category_breakdown,
        "daily_expense_trend": daily_expense_trend,
        "monthly_expense_trend": monthly_expense_trend,
        "insights": {
            "small_expense_threshold": SMALL_EXPENSE_THRESHOLD,
            "small_expense_count": small_expense_count,
            "small_expense_total": small_expense_total,
            "highest_spending_day": highest_spending_day,
            "highest_spending_day_amount": highest_spending_day_amount,
            "top_3_total": top_3_total,
            "top_3_share": top_3_share,
            "biggest_growth_category": biggest_growth_category,
            "biggest_growth_amount": biggest_growth_amount,
            "biggest_growth_percent": biggest_growth_percent
        }
    })
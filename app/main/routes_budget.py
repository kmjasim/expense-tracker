# routes_budget.py
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import request, render_template, redirect, url_for
from flask_login import login_required, current_user

from ..main import main
from ..extensions import db
from ..models import Budget, BudgetType, Category, TxnType
from ..services.budgeting import compute_budget_page, month_income_total


TYPE_ROW_ID = "type:transfer_international"  # must match services
TYPE_ENUM   = TxnType.transfer_international


@main.route("/budget", methods=["GET"], endpoint="budget_page")
@login_required
def budget_page():
    today = date.today()
    year  = request.args.get("year", type=int)  or today.year
    month = request.args.get("month", type=int) or today.month

    # Force KRW per requirement (we ignore any ?currency=)
    currency = "KRW"

    # Build data (includes the type pseudo-row)
    data = compute_budget_page(current_user.id, currency, year, month)
    income_total_val = month_income_total(current_user.id, currency, year, month)

    # ---------- Fallback injection from previous month (categories + type) ----------
    # Do we have any budgets (either category or type) this month?
    have_any_curr = (
        db.session.query(Budget.id)
        .filter(Budget.user_id == current_user.id, Budget.year == year, Budget.month == month)
        .first() is not None
    ) or (
        db.session.query(BudgetType.id)
        .filter(BudgetType.user_id == current_user.id, BudgetType.year == year, BudgetType.month == month)
        .first() is not None
    )

    data["used_fallback"] = False
    if not have_any_curr:
        prev_y, prev_m = (year - 1, 12) if month == 1 else (year, month - 1)

        # map of previous month parent-category budgets
        prev_cat_map = {
            cid: amt
            for cid, amt in db.session.query(Budget.category_id, Budget.amount)
            .filter(Budget.user_id == current_user.id,
                    Budget.year == prev_y, Budget.month == prev_m)
            .all()
            if cid is not None
        }

        # previous month type budget for transfer_international
        prev_type_amt = (db.session.query(BudgetType.amount)
                         .filter(BudgetType.user_id == current_user.id,
                                 BudgetType.year == prev_y, BudgetType.month == prev_m,
                                 BudgetType.txn_type == TYPE_ENUM)
                         .scalar())

        # Inject into current data.table (do not overwrite non-zero values)
        for row in (data.get("table") or []):
            rid = row.get("id")
            if rid == TYPE_ROW_ID:
                if (row.get("budget") in (None, 0, "0", "")) and prev_type_amt:
                    row["budget"] = float(prev_type_amt or 0)
            else:
                if (row.get("budget") in (None, 0, "0", "")) and (rid in prev_cat_map):
                    row["budget"] = float(prev_cat_map[rid])

        data["used_fallback"] = bool(prev_cat_map or prev_type_amt)
    # -----------------------------------------------------------------------------

    # Yearly lines (KRW-only)
    line_labels = [f"{m:02d}" for m in range(1, 13)]
    line_budget, line_spent = [], []
    for m in range(1, 13):
        d = compute_budget_page(current_user.id, currency, year, m)
        line_budget.append(d["totals"]["budget"])
        line_spent.append(d["totals"]["spent"])

    return render_template(
        "budget.html",
        page_title="Budget",
        year=year, month=month, currency=currency,
        data=data,
        line_chart={"labels": line_labels, "budget": line_budget, "spent": line_spent},
        income_total=float(income_total_val),
    )


@main.route("/budget/set", methods=["POST"], endpoint="budget_set")
@login_required
def budget_set():
    year  = request.form.get("year", type=int)
    month = request.form.get("month", type=int)
    currency = "KRW"  # forced

    # ----- 1) Save category budgets (parents only) -----
    parent_ids = {
        cid for (cid,) in db.session.query(Category.id)
        .filter(Category.user_id == current_user.id, Category.parent_id.is_(None))
        .all()
    }

    posted_cat = {k: v for k, v in request.form.items() if k.startswith("budget[")}
    for key, val in posted_cat.items():
        # key is like budget[<id>]
        cid_raw = key[7:-1]
        try:
            cid = int(cid_raw)
        except ValueError:
            continue
        if cid not in parent_ids:
            continue

        val = (val or "").strip()
        if val == "":
            (Budget.query
                .filter_by(user_id=current_user.id, category_id=cid, year=year, month=month)
                .delete(synchronize_session=False))
            continue

        try:
            amt = Decimal(val)
        except InvalidOperation:
            continue

        row = (Budget.query
               .filter_by(user_id=current_user.id, category_id=cid, year=year, month=month)
               .first())
        if row:
            row.amount = amt
        else:
            db.session.add(Budget(
                user_id=current_user.id, category_id=cid,
                year=year, month=month, amount=amt
            ))

    # ----- 2) Save type budgets (transfer_international) -----
    # Expect an input named: budget_type[transfer_international]
    posted_type = {k: v for k, v in request.form.items() if k.startswith("budget_type[")}
    for key, val in posted_type.items():
        tname = key[len("budget_type["):-1]  # "transfer_international"
        try:
            t_enum = TxnType[tname]
        except KeyError:
            continue

        val = (val or "").strip()
        if val == "":
            (BudgetType.query
                .filter_by(user_id=current_user.id, year=year, month=month, txn_type=t_enum)
                .delete(synchronize_session=False))
            continue

        try:
            amt = Decimal(val)
        except InvalidOperation:
            continue

        row = (BudgetType.query
               .filter_by(user_id=current_user.id, year=year, month=month, txn_type=t_enum)
               .first())
        if row:
            row.amount = amt
        else:
            db.session.add(BudgetType(
                user_id=current_user.id, year=year, month=month, txn_type=t_enum, amount=amt
            ))

    db.session.commit()
    return redirect(url_for("main.budget_page", year=year, month=month, currency=currency, success="Budgets saved"))


@main.route("/budget/reset", methods=["POST"], endpoint="budget_reset")
@login_required
def budget_reset():
    year  = request.form.get("year", type=int)
    month = request.form.get("month", type=int)
    currency = "KRW"  # forced

    (Budget.query
        .filter_by(user_id=current_user.id, year=year, month=month)
        .delete(synchronize_session=False))
    (BudgetType.query
        .filter_by(user_id=current_user.id, year=year, month=month)
        .delete(synchronize_session=False))

    db.session.commit()
    return redirect(url_for("main.budget_page", year=year, month=month, currency=currency, success="Budgets cleared"))

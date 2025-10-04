# routes_budget.py (or inside your main routes file)
from datetime import date
from flask import request, render_template, redirect, url_for
from flask_login import login_required, current_user
from ..services.budgeting import compute_budget_page, month_income_total
from ..main import main
from ..extensions import db
from ..models import Budget, Category
# routes_budget.py
from decimal import Decimal, InvalidOperation

@main.route("/budget", methods=["GET"], endpoint="budget_page")
@login_required
def budget_page():
    today = date.today()
    year  = request.args.get("year", type=int)  or today.year
    month = request.args.get("month", type=int) or today.month
    currency = request.args.get("currency") or "KRW"   # or "BDT" as needed

    data = compute_budget_page(current_user.id, currency, year, month)
    income_total_val = month_income_total(current_user.id, currency, year, month)  # <- CALL IT
    # If you want yearly line chart: compute 12 months quickly here (optional)
    # line_labels = [f"{m:02d}" for m in range(1,13)]
    # line_budget = []
    # line_spent  = []
    # for m in range(1,13):
    #     d = compute_budget_page(current_user.id, currency, year, m)
    #     line_budget.append(d["totals"]["budget"])
    #     line_spent.append(d["totals"]["spent"])
    # inside budget_page()
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


# routes_budget.py (budget_set)
@main.route("/budget/set", methods=["POST"], endpoint="budget_set")
@login_required
def budget_set():
    year  = request.form.get("year", type=int)
    month = request.form.get("month", type=int)
    currency = request.form.get("currency") or "KRW"

    # parents for this user only
    parent_ids = {
        cid for (cid,) in db.session.query(Category.id)
        .filter(Category.user_id == current_user.id, Category.parent_id.is_(None)).all()
    }

    posted = {k: v for k, v in request.form.items() if k.startswith("budget[")}
    for key, val in posted.items():
        cid = int(key[7:-1])
        if cid not in parent_ids:
            continue  # ignore children budgets

        val = (val or "").strip()
        if val == "":
            Budget.query.filter_by(user_id=current_user.id, category_id=cid, year=year, month=month).delete()
            continue

        from decimal import Decimal, InvalidOperation
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
    db.session.commit()
    return redirect(url_for("main.budget_page", year=year, month=month, currency=currency, success="Budgets saved"))

@main.route("/budget/reset", methods=["POST"], endpoint="budget_reset")
@login_required
def budget_reset():
    year  = request.form.get("year", type=int)
    month = request.form.get("month", type=int)
    currency = request.form.get("currency") or "KRW"

    (Budget.query
        .filter_by(user_id=current_user.id, year=year, month=month)
        .delete(synchronize_session=False))
    db.session.commit()
    return redirect(url_for("main.budget_page", year=year, month=month, currency=currency, success="Budgets cleared"))

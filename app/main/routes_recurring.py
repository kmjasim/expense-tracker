# routes_recurring.py (or inside your existing blueprint file)
from ..main import main
from datetime import date
from decimal import Decimal, InvalidOperation
from flask import request, redirect, url_for, render_template
from flask_login import login_required, current_user
from collections import defaultdict
from ..models import Category  # adjust import path

from ..extensions import db
from ..models import Account, TxnType, RecurringRule, RecurringFrequency
from ..services.recurring import run_due_rules_for_user
def _category_options_breadcrumb(user_id: int):
    """Return list of dicts: {'id': int, 'path': 'Parent / Child / Subchild'}."""
    cats = Category.query.filter_by(user_id=user_id).all()
    by_id = {c.id: c for c in cats}

    def path(c):
        parts = []
        cur = c
        # assuming Category has parent_id; adjust if your field differs
        while cur:
            parts.append(cur.name)
            cur = by_id.get(cur.parent_id) if getattr(cur, "parent_id", None) else None
        return " / ".join(reversed(parts))

    out = [{"id": c.id, "path": path(c)} for c in cats]
    # Sort by breadcrumb for nicer UX
    out.sort(key=lambda x: x["path"].lower())
    return out
@main.route("/recurring", methods=["GET"], endpoint="recurring_page")
@login_required
def recurring_page():
    rules = (RecurringRule.query
             .filter_by(user_id=current_user.id)
             .order_by(RecurringRule.is_enabled.desc(), RecurringRule.next_run.asc())
             .all())
    accounts = Account.query.filter_by(user_id=current_user.id, is_active=True).all()
    categories = _category_options_breadcrumb(current_user.id)
    return render_template(
        "recurring.html",
        page_title="Recurring Payments",
        rules=rules,
        accounts=accounts,
        categories=categories,   # <-- pass to template
    )

@main.route("/recurring/new", methods=["POST"], endpoint="recurring_create")
@login_required
def recurring_create():
    try:
        account_id = request.form.get("account_id", type=int)
        ttype_str  = request.form.get("type", "expense")
        amount_str = (request.form.get("amount") or "").strip()
        category_id = request.form.get("category_id", type=int)
        note = (request.form.get("note") or "").strip()

        freq_str   = request.form.get("frequency", "monthly")
        every_n    = request.form.get("every_n", type=int) or 1
        start_str  = request.form.get("start_date")
        end_str    = request.form.get("end_date")  # optional
        weekday    = request.form.get("weekday", type=int)  # optional 0..6
        dom        = request.form.get("day_of_month", type=int)  # optional 1..31

        if not account_id or not amount_str or not start_str:
            return redirect(url_for("main.recurring_page", warning="Missing required fields"))

        try:
            amount = Decimal(amount_str)
        except InvalidOperation:
            return redirect(url_for("main.recurring_page", warning="Invalid amount"))
        if amount <= 0:
            return redirect(url_for("main.recurring_page", warning="Amount must be > 0"))

        try:
            ttype = TxnType(ttype_str)
        except ValueError:
            return redirect(url_for("main.recurring_page", warning="Invalid transaction type"))

        try:
            start_date = date.fromisoformat(start_str)
        except Exception:
            return redirect(url_for("main.recurring_page", warning="Invalid start date"))

        end_date = None
        if end_str:
            try:
                end_date = date.fromisoformat(end_str)
            except Exception:
                return redirect(url_for("main.recurring_page", warning="Invalid end date"))

        account = db.session.get(Account, account_id)
        if not account or account.user_id != current_user.id:
            return redirect(url_for("main.recurring_page", warning="Invalid account"))

        try:
            freq = RecurringFrequency(freq_str)
        except ValueError:
            return redirect(url_for("main.recurring_page", warning="Invalid frequency"))

        rule = RecurringRule(
            user_id=current_user.id,
            account_id=account.id,
            type=ttype,
            amount=amount,
            category_id=category_id,
            note=note,

            frequency=freq,
            every_n=every_n,
            start_date=start_date,
            next_run=start_date,
            end_date=end_date,
            weekday=weekday if freq == RecurringFrequency.weekly else None,
            day_of_month=dom if freq == RecurringFrequency.monthly else None,
            is_enabled=True,
        )
        db.session.add(rule)
        db.session.commit()
        return redirect(url_for("main.recurring_page", success="Recurring rule created"))
    except Exception as e:
        db.session.rollback()
        return redirect(url_for("main.recurring_page", warning="Failed to create"))

@main.route("/recurring/<int:rid>/toggle", methods=["POST"], endpoint="recurring_toggle")
@login_required
def recurring_toggle(rid):
    rule = db.session.get(RecurringRule, rid)
    if not rule or rule.user_id != current_user.id:
        return redirect(url_for("main.recurring_page", warning="Rule not found"))
    rule.is_enabled = not bool(rule.is_enabled)
    db.session.commit()
    return redirect(url_for("main.recurring_page", success="Updated"))

@main.route("/recurring/<int:rid>/delete", methods=["POST"], endpoint="recurring_delete")
@login_required
def recurring_delete(rid):
    rule = db.session.get(RecurringRule, rid)
    if not rule or rule.user_id != current_user.id:
        return redirect(url_for("main.recurring_page", warning="Rule not found"))
    db.session.delete(rule)
    db.session.commit()
    return redirect(url_for("main.recurring_page", success="Deleted"))

# routes_recurring.py
@main.route("/recurring/run-now", methods=["POST"], endpoint="recurring_run_now")
@login_required
def recurring_run_now():
    from datetime import date
    today = date.today()
    # Only for the current user? or all users?
    # If you want ALL users, call the scheduler helper instead (requires app context).
    summary = run_due_rules_for_user(current_user.id, today=today)
    msg = f"Created {summary['created']}, Errors {len(summary['errors'])}"
    return redirect(url_for("main.recurring_page", success=msg))

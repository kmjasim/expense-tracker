# app/routes/debt.py
from datetime import date, datetime
from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from flask_login import login_required, current_user
from decimal import Decimal
from ..extensions import db
from ..models import DebtItem, DebtTxn, DebtDirection, Recipient, Currency
from . import main
from sqlalchemy import func, case, desc 
def _uid():
    return current_user.id

# ---------- helpers ----------
def _sum_or_zero(v):
    return v or Decimal("0")
def _apply_repayment_to_item(item, amount, when, note):
    """
    Apply up to `amount` to a single DebtItem.
    Returns the remaining amount (if payment was larger than outstanding).
    """
    amount = Decimal(amount)
    if amount <= 0:
        return Decimal("0")

    pay = min(amount, item.outstanding_principal)
    if pay <= 0:
        return amount

    # Update item
    new_out = item.outstanding_principal - pay
    item.outstanding_principal = new_out
    if new_out == 0:
        item.status = "settled"

    # Create txn
    txn = DebtTxn(
        user_id=_uid(),
        item_id=item.id,
        action="repayment",
        date=when,
        amount=pay,
        principal_portion=pay,
        interest_portion=Decimal("0"),
        fee_portion=Decimal("0"),
        note=note,
    )
    db.session.add(txn)

    return amount - pay

def debt_totals(user_id):
    # cards: totals + progress for both directions
    rows = (
        db.session.query(DebtItem.direction,
                         db.func.sum(DebtItem.original_principal),
                         db.func.sum(DebtItem.outstanding_principal))
        .filter(DebtItem.user_id == user_id, DebtItem.status == "active")
        .group_by(DebtItem.direction)
        .all()
    )
    data = {
        "owe": {"original": Decimal("0"), "outstanding": Decimal("0")},
        "lend": {"original": Decimal("0"), "outstanding": Decimal("0")},
    }
    for d, orig, out in rows:
        key = d.value if isinstance(d, DebtDirection) else d
        data[key]["original"] = _sum_or_zero(orig)
        data[key]["outstanding"] = _sum_or_zero(out)
    for key in data:
        orig = data[key]["original"]
        out = data[key]["outstanding"]
        paid = (orig - out) if orig else Decimal("0")
        pct = (paid / orig * 100) if orig else Decimal("0")
        data[key]["paid"] = paid
        data[key]["pct"] = round(pct, 2)
    return data

def list_txns(user_id, t_filter=None, a_filter=None, q=None):
    qy = (
        db.session.query(DebtTxn, DebtItem, Recipient)
        .join(DebtItem, DebtTxn.item_id == DebtItem.id)
        .join(Recipient, DebtItem.recipient_id == Recipient.id)
        .filter(DebtTxn.user_id == user_id)
        .order_by(DebtTxn.date.desc(), DebtTxn.id.desc())
    )
    if t_filter in ("owe", "lend"):
        qy = qy.filter(DebtItem.direction == DebtDirection(t_filter))
    if a_filter in ("add", "repayment"):
        qy = qy.filter(DebtTxn.action == a_filter)
    if q:
        like = f"%{q.strip()}%"
        qy = qy.filter(Recipient.name.ilike(like))
    return qy.limit(200).all()  # simple cap for now

# app/routes/debt.py
def list_by_person(user_id, t_filter=None):
    # True only if EVERY item in the group is settled
    is_paid_expr = (
        (func.min(case((DebtItem.status == "settled", 1), else_=0)) == 1)
    ).label("is_paid")

    qy = (
        db.session.query(
            DebtItem.direction,
            Recipient.name,
            func.count(DebtItem.id).label("count"),
            func.sum(DebtItem.original_principal).label("original"),
            func.sum(DebtItem.original_principal - DebtItem.outstanding_principal).label("paid"),
            func.sum(DebtItem.outstanding_principal).label("outstanding"),
            is_paid_expr,
        )
        .join(Recipient, DebtItem.recipient_id == Recipient.id)
        .filter(DebtItem.user_id == user_id)
        .group_by(DebtItem.direction, Recipient.name)
        .order_by(desc(func.sum(DebtItem.outstanding_principal)))
    )
    if t_filter in ("owe", "lend"):
        qy = qy.filter(DebtItem.direction == DebtDirection(t_filter))
    return qy.all()
# ---------- pages ----------
@main.route("/debts", methods=["GET"])
@login_required
def debts_page():
    t_filter = request.args.get("type")         # 'owe' | 'lend' | None
    a_filter = request.args.get("action")       # 'add' | 'repayment' | None
    q = request.args.get("q")

    cards = debt_totals(_uid())
    txns = list_txns(_uid(), t_filter, a_filter, q)
    per_person = list_by_person(_uid(), t_filter)
    recipients = Recipient.query.filter_by(user_id=_uid()).order_by(Recipient.name).all()
    # inside debts_page()
    open_items = (
        DebtItem.query
        .filter_by(user_id=_uid(), status="active")
        .filter(DebtItem.outstanding_principal > 0)
        .order_by(DebtItem.start_date.desc())
        .all()
    )
    return render_template(
        "debts.html",
        page_title="Debt Tracker",
        cards=cards,
        txns=txns,
        per_person=per_person,
        recipients=recipients,
        t_filter=t_filter,
        a_filter=a_filter,
        q=q or "",
        page_slug="debts",  # loads css/debts.css if present
        open_items=open_items,
    )

# ---------- actions: add / repay / edit / delete ----------
@main.route("/debts/add", methods=["POST"])
@login_required
def debts_add():
    direction = DebtDirection(request.form["direction"])
    recipient_id = int(request.form["recipient_id"])
    currency = Currency(request.form["currency"])
    amount = Decimal(request.form["amount"])
    note = request.form.get("note") or ""

    start_date_str = request.form.get("start_date")
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date() if start_date_str else date.today()

    item = DebtItem(
        user_id=_uid(),
        direction=direction,
        currency=currency,
        recipient_id=recipient_id,
        original_principal=amount,
        outstanding_principal=amount,
        start_date=start_date,   # ✅ correct type now
        note=note,
        status="active",
    )
    db.session.add(item)
    db.session.flush()

    txn = DebtTxn(
        user_id=_uid(),
        item_id=item.id,
        action="add",
        date=start_date,
        amount=amount,
        principal_portion=amount,
        note="Open" if not note else note,
    )
    db.session.add(txn)
    db.session.commit()
    return redirect(url_for("main.debts_page"))


@main.route("/debts/repay", methods=["POST"])
@login_required
def debts_repay():
    item_id = int(request.form["item_id"])
    amount = Decimal(request.form["amount"])
    date_str = request.form.get("date")
    when = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else date.today()
    note = request.form.get("note") or ""

    item = db.session.get(DebtItem, item_id)
    if not item or item.user_id != _uid():
        return redirect(url_for("main.debts_page"))

    # Use the helper for this single item
    _apply_repayment_to_item(item, amount, when, note)

    db.session.commit()
    return redirect(url_for("main.debts_page"))

@main.route("/debts/repay_person", methods=["POST"])
@login_required
def debts_repay_person():
    """
    Repay an amount to a person (recipient) at once.
    The payment will be allocated across all open items for that person
    in the given direction (owe / lend), oldest first.
    """
    recipient_id = int(request.form["recipient_id"])
    direction = DebtDirection(request.form["direction"])  # 'owe' or 'lend'
    amount = Decimal(request.form["amount"])
    date_str = request.form.get("date")
    when = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else date.today()
    note = request.form.get("note") or ""

    # Get all open items for this recipient & direction
    items = (
        DebtItem.query
        .filter_by(
            user_id=_uid(),
            recipient_id=recipient_id,
            direction=direction,
            status="active",
        )
        .filter(DebtItem.outstanding_principal > 0)
        .order_by(DebtItem.start_date.asc(), DebtItem.id.asc())  # pay oldest first
        .all()
    )

    remaining = amount

    for item in items:
        if remaining <= 0:
            break
        remaining = _apply_repayment_to_item(item, remaining, when, note)

    # If remaining > 0 here, it means the user tried to pay more than outstanding.
    # You can ignore the leftover, or later show a message. For now we just cap at total outstanding.

    db.session.commit()
    return redirect(url_for("main.debts_page"))


@main.route("/debts/tx/<int:txid>/delete", methods=["POST"])
@login_required
def debts_tx_delete(txid):
    txn = db.session.get(DebtTxn, txid)
    if not txn or txn.user_id != _uid():
        return jsonify(ok=False), 404
    item = txn.item

    # reverse effect if needed
    if txn.action == "add":
        # removing opening/top-up -> reduce item principals
        item.original_principal -= txn.principal_portion
        item.outstanding_principal = max(Decimal("0"), item.outstanding_principal - txn.principal_portion)
        if item.original_principal <= 0:
            db.session.delete(item)
    else:
        # repayment -> restore outstanding
        item.outstanding_principal += txn.principal_portion
        if item.status == "settled" and item.outstanding_principal > 0:
            item.status = "active"

    db.session.delete(txn)
    db.session.commit()
    return jsonify(ok=True)

@main.route("/debts/tx/<int:txid>/edit", methods=["POST"])
@login_required
def debts_tx_edit(txid):
    # minimal: only note edit for MVP (safe). You can extend later.
    txn = db.session.get(DebtTxn, txid)
    if not txn or txn.user_id != _uid():
        return jsonify(ok=False), 404
    txn.note = request.form.get("note") or ""
    db.session.commit()
    return jsonify(ok=True, note=txn.note)

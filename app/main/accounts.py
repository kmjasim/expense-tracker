# app/main/accounts.py
# ---------------------------------
# Create / Update / Delete / Reorder accounts. All actions scoped to the owner.

from flask import request, redirect, url_for
from flask_login import login_required, current_user
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from sqlalchemy import case,func
from ..extensions import db
from ..models import TransactionKRW, TransactionBDT, Currency, AccountType, Account
from . import main
# ---------------------------
# Accounts (CREATE) — input is "desired current balance"
# ---------------------------

@main.route("/accounts/new", methods=["POST"], endpoint="account_create")
@login_required
def account_create():
    name      = (request.form.get("name") or "").strip()
    currency  = (request.form.get("currency") or "KRW").strip().upper()
    acc_type  = (request.form.get("type") or "bank").strip().lower()
    init_str  = (request.form.get("initial_balance") or "0").strip()
    limit_str = (request.form.get("credit_limit") or "").strip()  # from the credit UI
    is_active = bool(request.form.get("is_active"))

    if not name:
        return redirect(url_for("main.payments_page", warning="Account name required"))

    # Validate enums
    try:
        currency_enum = Currency(currency)
        type_enum     = AccountType(acc_type)
    except ValueError:
        return redirect(url_for("main.payments_page", warning="Invalid type/currency"))

    # Parse numbers
    try:
        desired_balance = Decimal(init_str or "0").quantize(Decimal("0.01"))
    except InvalidOperation:
        desired_balance = Decimal("0.00")

    try:
        desired_limit = Decimal(limit_str or "0").quantize(Decimal("0.01"))
    except InvalidOperation:
        desired_limit = Decimal("0.00")

    # Create the account first
    acc = Account(
        user_id=current_user.id,
        name=name,
        currency=currency_enum,
        type=type_enum,
        initial_balance=Decimal("0.00"),
        credit_limit=None,
        is_active=is_active,
    )
    db.session.add(acc)
    db.session.commit()  # acc.id available

    # Credit cards: set available limit now; no balance math
    if type_enum == AccountType.credit:
        acc.credit_limit = desired_limit
        db.session.commit()
        return redirect(url_for("main.payments_page"))

    # Non-credit: set initial_balance so displayed == desired
    # displayed = initial_balance + sum(txns)
    if currency_enum == Currency.KRW:
        txn_sum = (
            db.session.query(func.coalesce(func.sum(TransactionKRW.amount), 0))
            .filter(
                TransactionKRW.user_id == current_user.id,
                TransactionKRW.account_id == acc.id,
                TransactionKRW.is_deleted.is_(False),
            )
            .scalar()
        )
    else:
        txn_sum = (
            db.session.query(func.coalesce(func.sum(TransactionBDT.amount), 0))
            .filter(
                TransactionBDT.user_id == current_user.id,
                TransactionBDT.account_id == acc.id,
                TransactionBDT.is_deleted.is_(False),
            )
            .scalar()
        )

    txn_sum = Decimal(txn_sum or 0).quantize(Decimal("0.01"))
    acc.initial_balance = (desired_balance - txn_sum).quantize(Decimal("0.01"))
    db.session.commit()

    return redirect(url_for("main.payments_page"))

# ---------------------------
# Accounts (UPDATE)
# ---------------------------
# app/services/... or your accounts routes module

@main.route("/accounts/update", methods=["POST"], endpoint="account_update")
@login_required
def account_update():
    acc_id = request.form.get("id", type=int)
    acc = db.session.get(Account, acc_id)
    if not acc or acc.user_id != current_user.id:
        return redirect(url_for("main.payments_page", warning="Account not found"))

    # Basic editable fields
    name = (request.form.get("name") or "").strip()
    currency = request.form.get("currency") or getattr(acc.currency, "value", "KRW")
    acc_type = request.form.get("type") or getattr(acc.type, "value", "bank")
    init_str = (request.form.get("initial_balance") or "").strip()
    is_active = bool(request.form.get("is_active"))

    if name:
        acc.name = name
    acc.currency = Currency(currency)
    acc.type = AccountType(acc_type)
    acc.is_active = is_active

    # ----- Key behavior: treat input as the desired final balance -----
    # If user provided a value (even "0"), we make displayed balance EXACTLY that.
    if init_str != "":
        try:
            desired = Decimal(init_str).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            # ignore bad input; keep previous initial_balance
            db.session.commit()
            return redirect(url_for("main.payments_page", warning="Invalid initial balance"))

        # Sum current (non-deleted) transactions for THIS account/user
        curr_code = getattr(acc.currency, "value", str(acc.currency))
        if curr_code == "KRW":
            txn_sum = (
                db.session.query(func.coalesce(func.sum(TransactionKRW.amount), 0))
                .filter(
                    TransactionKRW.user_id == current_user.id,
                    TransactionKRW.account_id == acc.id,
                    TransactionKRW.is_deleted.is_(False),
                )
                .scalar()
            )
        else:
            txn_sum = (
                db.session.query(func.coalesce(func.sum(TransactionBDT.amount), 0))
                .filter(
                    TransactionBDT.user_id == current_user.id,
                    TransactionBDT.account_id == acc.id,
                    TransactionBDT.is_deleted.is_(False),
                )
                .scalar()
            )

        txn_sum = Decimal(txn_sum or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Set stored initial_balance so that: initial_balance + txn_sum == desired
        acc.initial_balance = (desired - txn_sum).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    db.session.commit()
    return redirect(url_for("main.payments_page"))


# ---------------------------
# Accounts (DELETE)
# ---------------------------
@main.route("/accounts/delete", methods=["POST"], endpoint="account_delete")
@login_required
def account_delete():
    acc_id = request.form.get("id", type=int)
    acc = db.session.get(Account, acc_id)
    if acc and acc.user_id == current_user.id:
        db.session.delete(acc)
        db.session.commit()
    return redirect(url_for("main.payments_page"))


# ---------------------------
# Accounts (REORDER)
# ---------------------------
@main.route("/accounts/reorder", methods=["POST"], endpoint="accounts_reorder")
@login_required
def accounts_reorder():
    payload = request.get_json(silent=True) or {}
    order = payload.get("order")
    if not isinstance(order, list) or not order:
        return {"ok": False, "error": "bad payload"}, 400

    # Only reorder THIS user's accounts
    rows = (
        db.session.query(Account.id)
        .filter(Account.user_id == current_user.id, Account.id.in_(order))
        .all()
    )
    if not rows:
        return {"ok": False, "error": "no accounts matched"}, 404

    # Map: id -> position (10,20,30…)
    values = {acc_id: (idx + 1) * 10 for idx, acc_id in enumerate(order)}

    stmt = (
        Account.__table__.update()
        .where(Account.user_id == current_user.id, Account.id.in_(order))
        .values(display_order=case(values, value=Account.id, else_=Account.display_order))
    )
    db.session.execute(stmt)
    db.session.commit()
    return {"ok": True}

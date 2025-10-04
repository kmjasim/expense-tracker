# app/main/transactions_actions.py
from decimal import Decimal, InvalidOperation
from flask import request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from datetime import date
from ..main import main
from ..extensions import db
from ..models import Account, AccountType, TransactionKRW, TransactionBDT, TxnType
from decimal import Decimal

def _adjust_account_balance(acc: Account, delta: Decimal):
    """Adjusts balance or credit limit depending on account type."""
    if not acc:
        return
    if acc.type == AccountType.credit:
        acc.credit_limit = (acc.credit_limit or Decimal("0")) + delta
    else:
        acc.initial_balance = (acc.initial_balance or Decimal("0")) + delta

def _model_for(cur: str):
    cur = (cur or "").upper()
    if cur == "KRW": return TransactionKRW
    if cur == "BDT": return TransactionBDT
    abort(404)

def _get_tx_or_404(cur: str, txid: int):
    Model = _model_for(cur)
    tx = db.session.get(Model, txid)
    if not tx or tx.user_id != current_user.id:
        abort(404)
    return tx

def _safe_decimal(s: str | None):
    if s is None or s == "":
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None

@main.route("/transactions/<cur>/<int:txid>/delete", methods=["POST"], endpoint="tx_delete")
@login_required
def tx_delete(cur, txid):
    tx = _get_tx_or_404(cur, txid)
    if tx.is_deleted:
        flash("Already deleted.", "info")
    else:
        acc = db.session.get(Account, tx.account_id)
        if acc:
            # reverse effect of this txn
            _adjust_account_balance(acc, -tx.amount)
        tx.is_deleted = True
        db.session.commit()
        flash("Transaction deleted.", "success")
    return redirect(request.referrer or url_for("main.transactions_page"))

@main.route("/transactions/<cur>/<int:txid>/restore", methods=["POST"], endpoint="tx_restore")
@login_required
def tx_restore(cur, txid):
    tx = _get_tx_or_404(cur, txid)
    if not tx.is_deleted:
        flash("Already active.", "info")
    else:
        acc = db.session.get(Account, tx.account_id)
        if acc:
            # re-apply effect
            _adjust_account_balance(acc, tx.amount)
        tx.is_deleted = False
        db.session.commit()
        flash("Transaction restored.", "success")
    return redirect(request.referrer or url_for("main.transactions_page"))



@main.route("/transactions/<cur>/<int:txid>/edit", methods=["POST"], endpoint="tx_edit")
@login_required
def tx_edit(cur, txid):
    tx = _get_tx_or_404(cur, txid)

    old_acc_id = tx.account_id
    old_amt    = tx.amount

    # --- parse form ---
    d = request.form.get("date")
    if d:
        try:
            y, m, dd = d.split("-")
            tx.date = date(int(y), int(m), int(dd))
        except Exception:
            flash("Invalid date.", "danger")
            return redirect(request.referrer or url_for("main.transactions_page"))

    new_acc_id  = request.form.get("account_id", type=int) or tx.account_id
    tx.category_id = request.form.get("category_id", type=int)

    type_val = request.form.get("type")
    if type_val:
        try:
            tx.type = TxnType(type_val)
        except ValueError:
            flash("Invalid type.", "danger")
            return redirect(request.referrer or url_for("main.transactions_page"))

    new_amt = _safe_decimal(request.form.get("amount")) or tx.amount
    tx.account_id = new_acc_id
    tx.amount     = new_amt
    tx.note       = (request.form.get("note") or None)
    tx.is_pending = bool(request.form.get("is_pending"))

    # --- adjust balances only if txn is active ---
    if not tx.is_deleted:
        if old_acc_id == new_acc_id:
            acc = db.session.get(Account, new_acc_id)
            if acc:
                # same account: adjust by delta
                _adjust_account_balance(acc, (new_amt - old_amt))
        else:
            # different accounts: reverse old, apply new
            old_acc = db.session.get(Account, old_acc_id)
            new_acc = db.session.get(Account, new_acc_id)
            if old_acc: _adjust_account_balance(old_acc, -old_amt)
            if new_acc: _adjust_account_balance(new_acc, new_amt)

    db.session.commit()
    flash("Transaction updated.", "success")
    return redirect(request.referrer or url_for("main.transactions_page"))

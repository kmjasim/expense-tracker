from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from flask import render_template, request, redirect, url_for
from flask_login import login_required, current_user

from ..main import main
from ..extensions import db
from ..models import (
    Account, AccountType, Currency, Recipient,
    TransactionKRW, TransactionBDT, TxnType
)
from app.utils.helpers import get_page_title
# --------------------------
# Helpers
# --------------------------

def apply_delta(account: Account, delta: Decimal):
    """Mutate live balance (non-credit only)."""
    if account.type.name == "credit":
        return  # transfers shouldn't hit credit anyway
    account.initial_balance = Decimal(account.initial_balance or 0) + Decimal(delta)

def reverse_transfer_effect(txn):
    """
    Given an existing domestic txn row, compute how to roll its effect back out of accounts.
    Use when deleting/reversing.
    Negative amounts were outflows; positive were inflows.
    """
    return -Decimal(txn.amount or 0)

def _parse_amount(s: str) -> Decimal:
    try:
        amt = Decimal((s or "").strip())
    except InvalidOperation:
        raise ValueError("Invalid amount")
    if amt <= 0:
        raise ValueError("Amount must be greater than 0")
    return amt

def _parse_date(s: str) -> date:
    if not s:
        raise ValueError("Date is required")
    return datetime.strptime(s, "%Y-%m-%d").date()

def _get_acc(uid: int, acc_id: int) -> Account | None:
    if not acc_id:
        return None
    return db.session.query(Account).filter_by(id=acc_id, user_id=uid, is_active=True).first()

def _txn_model_for(curr: Currency):
    return TransactionKRW if curr == Currency.KRW else TransactionBDT

def _find_or_create_external(uid: int, curr: Currency) -> Account:
    name = f"External ({curr.value})"
    acc = db.session.query(Account).filter_by(user_id=uid, currency=curr, name=name).first()
    if acc:
        return acc
    acc = Account(
        user_id=uid, name=name, currency=curr,
        type=AccountType.bank, initial_balance=Decimal("0.00"),
        is_active=False, display_order=9999,
    )
    db.session.add(acc); db.session.flush()
    return acc


# --------------------------
# GET: Transfers page
# --------------------------
@main.route("/transfers", methods=["GET"], endpoint="transfers_page")
@login_required
def transfers_page():
    uid = current_user.id
    accounts = (
        db.session.query(Account)
        .filter(Account.user_id == uid, Account.is_active.is_(True))
        .order_by(Account.currency.asc(), Account.display_order.asc(), Account.name.asc())
        .all()
    )
    krw_accounts = [a for a in accounts if a.currency == Currency.KRW and a.type != AccountType.credit]
    bdt_accounts = [a for a in accounts if a.currency == Currency.BDT and a.type != AccountType.credit]

    recipients = (
        db.session.query(Recipient)
        .filter(Recipient.user_id == uid)
        .order_by(Recipient.is_favorite.desc(), Recipient.name.asc())
        .all()
    )
    recipients = (
        db.session.query(Recipient)
        .filter(Recipient.user_id == current_user.id)
        .order_by(Recipient.is_favorite.desc(), Recipient.name.asc())
        .all()
    )
    return render_template(
        "transfers.html",
        krw_accounts=krw_accounts,
        bdt_accounts=bdt_accounts,
        recipients=recipients,
        current_date=date.today().isoformat(),
        page_title=get_page_title("Transfers")
    )


# --------------------------
# POST: Domestic (same currency)
# --------------------------
@main.route("/transfers/domestic", methods=["POST"], endpoint="transfers_domestic")
@login_required
def transfers_domestic():
    uid = current_user.id
    from_id   = request.form.get("from_account_id", type=int)
    to_id     = request.form.get("to_account_id", type=int)
    direction = (request.form.get("direction") or "out").strip().lower()  # "out" | "in"
    note      = (request.form.get("note") or "").strip()

    try:
        tx_date = _parse_date(request.form.get("date"))
        amount  = _parse_amount(request.form.get("amount"))
    except ValueError as e:
        return redirect(url_for("main.transfers_page", warning=str(e)))

    # Load accounts
    src = _get_acc(uid, from_id)
    dst = _get_acc(uid, to_id) if to_id else None

    if direction == "out" and not src:
        return redirect(url_for("main.transfers_page", warning="Select a source account"))
    if direction == "in" and not (dst or src):
        return redirect(url_for("main.transfers_page", warning="Select a destination account"))

    # No credit accounts allowed for transfers (matches UI)
    for acc in filter(None, [src, dst]):
        if acc.type == AccountType.credit:
            return redirect(url_for("main.transfers_page", warning="Credit cards cannot be used for transfers"))

    # Infer currency and model; if both given, they must match
    base_acc = src or dst
    currency = base_acc.currency
    if src and dst and src.currency != dst.currency:
        return redirect(url_for("main.transfers_page", warning="Accounts must be same currency"))
    Txn = _txn_model_for(currency)

    # Recipient snapshot (optional)
    rec_id = request.form.get("recipient_id", type=int)
    rec_name_override = (request.form.get("recipient_name") or "").strip() or None
    rec = db.session.query(Recipient).filter_by(user_id=uid, id=rec_id).first() if rec_id else None
    rec_id_final = rec.id if rec else None
    rec_name_final = rec.name if rec else rec_name_override

    gid = str(uuid4())

    # ---- Balance updates + history rows ----
    if direction == "out":
        # overdraft guard on source
        if Decimal(src.initial_balance or 0) < amount:
            return redirect(url_for("main.transfers_page", warning="Insufficient funds in source account"))

        # 1) deduct from source
        src.initial_balance = Decimal(src.initial_balance or 0) - amount
        db.session.add(Txn(
            user_id=uid, account_id=src.id, date=tx_date,
            type=TxnType.transfer_domestic, amount=-amount,
            recipient_id=rec_id_final, recipient_name=rec_name_final,
            note=note, transfer_group_id=gid
        ))

        # 2) if internal destination, credit it
        if dst:
            dst.initial_balance = Decimal(dst.initial_balance or 0) + amount
            db.session.add(Txn(
                user_id=uid, account_id=dst.id, date=tx_date,
                type=TxnType.transfer_domestic, amount=amount,
                recipient_id=rec_id_final, recipient_name=rec_name_final,
                note=note, transfer_group_id=gid
            ))

    else:  # direction == "in"
        target = dst or src
        target.initial_balance = Decimal(target.initial_balance or 0) + amount
        db.session.add(Txn(
            user_id=uid, account_id=target.id, date=tx_date,
            type=TxnType.transfer_domestic, amount=amount,
            recipient_id=rec_id_final, recipient_name=rec_name_final,
            note=note, transfer_group_id=gid
        ))

    db.session.commit()
    return redirect(url_for("main.transfers_page", success="Domestic transfer saved"))


# --------------------------
# POST: International (KRW -> BDT)
# --------------------------
@main.route("/transfers/international", methods=["POST"], endpoint="transfers_international")
@login_required
def transfers_international():
    uid = current_user.id

    try:
        tx_date    = _parse_date(request.form.get("date"))
        sent_krw   = _parse_amount(request.form.get("amount_sent_krw"))
        recv_bdt   = _parse_amount(request.form.get("amount_received_bdt"))
    except ValueError as e:
        return redirect(url_for("main.transfers_page", warning=str(e)))

    from_id = request.form.get("from_account_id", type=int)        # KRW source
    to_id   = request.form.get("to_account_id_bdt", type=int)      # BDT destination if self
    is_self = (request.form.get("recipient_is_self") == "on")

    # Source (must be KRW, non-credit)
    src = _get_acc(uid, from_id)
    if not src or src.currency != Currency.KRW or src.type == AccountType.credit:
        return redirect(url_for("main.transfers_page", warning="Select a valid KRW source (no credit cards)"))

    # Destination
    if is_self:
        dst = _get_acc(uid, to_id)
        if not dst or dst.currency != Currency.BDT or dst.type == AccountType.credit:
            return redirect(url_for("main.transfers_page", warning="Select a valid BDT receiving account"))
    else:
        dst = _find_or_create_external(uid, Currency.BDT)

    # Recipient snapshot (optional)
    rec_id = request.form.get("recipient_id", type=int)
    rec_name_override = (request.form.get("recipient_name") or "").strip() or None
    rec = db.session.query(Recipient).filter_by(user_id=uid, id=rec_id).first() if rec_id else None
    rec_id_final = rec.id if rec else None
    rec_name_final = rec.name if rec else rec_name_override
    service_name = (request.form.get("service_name") or "").strip() or None
    note = (request.form.get("note") or "").strip()

    gid = str(uuid4())

    # ---- Balance updates + history rows ----
    # Deduct KRW from source (overdraft guard)
    if Decimal(src.initial_balance or 0) < sent_krw:
        return redirect(url_for("main.transfers_page", warning="Insufficient funds in KRW source"))
    src.initial_balance = Decimal(src.initial_balance or 0) - sent_krw

    # KRW outflow row
    db.session.add(TransactionKRW(
        user_id=uid, account_id=src.id, date=tx_date,
        type=TxnType.transfer_international, amount=-sent_krw,
        recipient_id=rec_id_final, recipient_name=rec_name_final,
        service_name=service_name,
        amount_sent_krw=sent_krw, amount_received_bdt=recv_bdt,
        note=note, transfer_group_id=gid
    ))

    # BDT inflow row (+ balance only if self)
    if is_self:
        dst.initial_balance = Decimal(dst.initial_balance or 0) + recv_bdt

    db.session.add(TransactionBDT(
        user_id=uid, account_id=dst.id, date=tx_date,
        type=TxnType.transfer_international, amount=recv_bdt,
        recipient_id=rec_id_final, recipient_name=rec_name_final,
        service_name=service_name,
        amount_sent_krw=sent_krw, amount_received_bdt=recv_bdt,
        note=note, transfer_group_id=gid
    ))

    db.session.commit()
    return redirect(url_for("main.transfers_page", success="International transfer saved"))



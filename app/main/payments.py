# app/main/payments.py
# ---------------------------------
# Payments page (GET) + Payments create (POST)
# Scoped to the logged-in user. Uses KRW/BDT-specific txn models.

from datetime import date, datetime  # ✅ use class imports only (no 'import datetime' module)
from flask import jsonify, render_template, request, redirect, url_for
from sqlalchemy import func, asc
from sqlalchemy.orm import selectinload
from flask_login import login_required, current_user
from decimal import Decimal, InvalidOperation
from uuid import uuid4
from app.services.finance_score import get_finance_score
from . import main
from ..extensions import db
from ..models import (
    AccountType,
    Currency,
    TransactionKRW,
    TransactionBDT,
    TxnType,
    Category,
    Account,
)
from app.utils.helpers import get_page_title


# ---------------------------
# Helpers
# ---------------------------
def _parse_form_date(s: str) -> date:
    """Parse various date formats from the form robustly."""
    s = (s or "").strip()
    if not s:
        raise ValueError("Empty date")

    # Most browsers send YYYY-MM-DD for <input type="date">
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass

    # Fallbacks
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue

    raise ValueError(f"Unrecognized date: {s!r}")

# ---------------------------
# Payments (READ)
# ---------------------------
@main.route("/payments", methods=["GET"])
@login_required
def payments_page():
    active_id = request.args.get("category_id", type=int)

    # Left-side categories (ONLY this user's)
    tops = (
        db.session.query(Category)
        .filter(Category.user_id == current_user.id, Category.parent_id.is_(None))
        .options(selectinload(Category.children))
        .order_by(Category.name.asc())
        .all()
    )

    # Accounts (ALL this user's — active + inactive)
    accounts = (
        db.session.query(Account)
        .filter(Account.user_id == current_user.id)
        .order_by(func.coalesce(Account.display_order, 10_000).asc(), Account.name.asc())
        .all()
    )

    # JSON for right-side selects
    cat_json = [
        {
            "id": p.id,
            "name": p.name,
            "children": [{"id": c.id, "name": c.name} for c in sorted(p.children, key=lambda x: x.name or "")],
        }
        for p in tops
    ]

    # -------------------------------
    # Balances + pending (CREDIT ONLY)
    # -------------------------------
    balances = {}
    pending_totals = {}

    for a in accounts:
        # Non-credit: show the authoritative field directly
        if a.type != AccountType.credit:
            balances[a.id] = Decimal(a.initial_balance or 0)
            continue

        # Credit: show available limit as the main number
        balances[a.id] = Decimal(a.credit_limit or 0)

        # Pending for credit cards (sum of pending expense/fee)
        curr_code = getattr(a.currency, "value", str(a.currency))
        if curr_code == "KRW":
            pend = (
                db.session.query(func.coalesce(func.sum(TransactionKRW.amount), 0))
                .filter(
                    TransactionKRW.user_id == current_user.id,
                    TransactionKRW.account_id == a.id,
                    TransactionKRW.is_pending.is_(True),
                    TransactionKRW.is_deleted.is_(False),
                    TransactionKRW.type.in_([TxnType.expense, TxnType.fee]),
                )
                .scalar()
            )
        else:
            pend = (
                db.session.query(func.coalesce(func.sum(TransactionBDT.amount), 0))
                .filter(
                    TransactionBDT.user_id == current_user.id,
                    TransactionBDT.account_id == a.id,
                    TransactionBDT.is_pending.is_(True),
                    TransactionBDT.is_deleted.is_(False),
                    TransactionBDT.type.in_([TxnType.expense, TxnType.fee]),
                )
                .scalar()
            )
        # Store pending as a positive number for display
        pending_totals[a.id] = Decimal(-(pend or 0))

    return render_template(
        "payments.html",
        top_categories=tops,
        flat_categories=tops,
        active_category_id=active_id,
        accounts=accounts,
        balances=balances,              # 👈 non-credit = initial_balance; credit = credit_limit
        pending_totals=pending_totals,  # 👈 for credit card UI
        today=date.today().isoformat(),
        warning_message=request.args.get("warning"),
        cat_json=cat_json,
        selected_parent_id=None,
        selected_child_id=None,
        debug_user_id=current_user.id,
        debug_accounts_len=len(accounts),
        page_title=get_page_title(),
        AccountType=AccountType,
    )

# ---------------------------
# Payments (CREATE txn)
# ---------------------------
@main.route("/payments/new", methods=["POST"], endpoint="payments_create")
@login_required
def payments_create():
    account_id = request.form.get("account_id", type=int)
    ttype_str  = request.form.get("type", "expense")
    date_str   = request.form.get("date")
    note       = (request.form.get("note") or "").strip()
    cat_id     = request.form.get("category_id", type=int)
    amt_str    = (request.form.get("amount") or "").strip()

    if not account_id or not date_str or not amt_str:
        return redirect(url_for("main.payments_page", warning="Missing required fields"))

    # amount (must be positive)
    try:
        amount = Decimal(amt_str)
    except InvalidOperation:
        return redirect(url_for("main.payments_page", warning="Invalid amount"))
    if amount <= 0:
        return redirect(url_for("main.payments_page", warning="Amount must be > 0"))

    # tx type (enum)
    try:
        ttype = TxnType(ttype_str)
    except ValueError:
        return redirect(url_for("main.payments_page", warning="Invalid type"))

    # date
    try:
        tx_date = _parse_form_date(date_str)
    except Exception:
        return redirect(url_for("main.payments_page", warning="Invalid date"))

    # account
    account = db.session.get(Account, account_id)
    if not account or account.user_id != current_user.id:
        return redirect(url_for("main.payments_page", warning="Invalid account"))
    if not account.is_active:
        return redirect(url_for("main.payments_page", warning="This account is inactive. Edit and activate it first."))

    is_pending = False

    if account.type == AccountType.credit:
        # Credit card: reduce available limit and mark txn pending
        if account.credit_limit is None:
            return redirect(url_for("main.payments_page", warning="Credit limit not set"))
        if amount > account.credit_limit:
            return redirect(url_for("main.payments_page", warning="Insufficient credit limit"))
        account.credit_limit -= amount
        is_pending = True
    else:
        # Non-credit accounts: use initial_balance as CURRENT balance
        curr = Decimal(account.initial_balance or 0)
        if ttype in {TxnType.expense, TxnType.fee}:
            # overdraft block
            if amount > curr:
                return redirect(url_for("main.payments_page", warning="Insufficient funds"))
            account.initial_balance = curr - amount
        elif ttype == TxnType.income:
            account.initial_balance = curr + amount
        else:
            # If other types appear, treat as no-op on balance (or handle as needed)
            pass

    # choose table by currency
    txn_cls = TransactionKRW if account.currency == Currency.KRW else TransactionBDT

    # record history row (sign convention preserved)
    txn = txn_cls(
        user_id=current_user.id,
        account_id=account.id,
        date=tx_date,
        type=ttype,
        amount=(-amount if ttype in {TxnType.expense, TxnType.fee} else amount),
        category_id=cat_id,
        note=note,
        is_pending=is_pending,
    )

    db.session.add(txn)
    db.session.commit()
    return redirect(url_for("main.payments_page", success="Payment recorded"))


@main.route("/accounts/set_limit", methods=["POST"])
@login_required
def accounts_set_limit():
    account_id = request.form.get("account_id", type=int)
    limit_str  = (request.form.get("credit_limit") or "").strip()

    if not account_id or limit_str == "":
        return redirect(url_for("main.payments_page", warning="Missing account or limit"))

    # Parse limit (>= 0) and round to cents
    try:
        new_limit = Decimal(limit_str).quantize(Decimal("0.01"))
        if new_limit < 0:
            raise InvalidOperation
    except InvalidOperation:
        return redirect(url_for("main.payments_page", warning="Invalid limit"))

    acc = db.session.get(Account, account_id)
    if not acc or acc.user_id != current_user.id:
        return redirect(url_for("main.payments_page", warning="Account not found"))

    if acc.type != AccountType.credit:
        return redirect(url_for("main.payments_page", warning="Selected account is not a credit card"))

    # Overwrite AVAILABLE limit to the new value (does not change history)
    acc.credit_limit = new_limit
    db.session.commit()

    return redirect(url_for("main.payments_page", success=f"Updated limit for {acc.name}"))


# app/main/payments.py (add below payments_create)


@main.route("/payments/settle", methods=["POST"], endpoint="payments_settle")
@login_required
def payments_settle():
    card_id     = request.form.get("card_id", type=int)
    from_acc_id = request.form.get("from_account_id", type=int)
    amt_str     = (request.form.get("amount") or "").strip()

    # Parse & validate amount
    try:
        amount = Decimal(amt_str)
    except InvalidOperation:
        return redirect(url_for("main.payments_page", warning="Invalid amount"))
    if amount <= 0:
        return redirect(url_for("main.payments_page", warning="Amount must be greater than 0"))

    # Load accounts and validate ownership
    card = db.session.get(Account, card_id)
    pay  = db.session.get(Account, from_acc_id)
    if not card or not pay or card.user_id != current_user.id or pay.user_id != current_user.id:
        return redirect(url_for("main.payments_page", warning="Invalid accounts"))

    # Types and currency checks
    if card.type != AccountType.credit:
        return redirect(url_for("main.payments_page", warning="Selected card is not a credit account"))
    if pay.type == AccountType.credit:
        return redirect(url_for("main.payments_page", warning="Pay-from account cannot be a credit card"))
    if card.currency != pay.currency:
        return redirect(url_for("main.payments_page", warning="Currency mismatch"))
    if not pay.is_active:
        return redirect(url_for("main.payments_page", warning="Pay-from account is inactive"))

    # Resolve txn class by currency
    cls_card = TransactionKRW if card.currency == Currency.KRW else TransactionBDT
    cls_pay  = TransactionKRW if pay.currency  == Currency.KRW  else TransactionBDT

    # Sum total pending on the card (positive number)
    pend_sum = (
        db.session.query(func.coalesce(func.sum(cls_card.amount), 0))
        .filter(
            cls_card.user_id == current_user.id,
            cls_card.account_id == card.id,
            cls_card.is_pending.is_(True),
            cls_card.is_deleted.is_(False),
            cls_card.type.in_([TxnType.expense, TxnType.fee]),
        )
        .scalar()
    )
    # expenses/fees are negative in storage; flip sign
    pending_total = Decimal(-(pend_sum or 0))
    if pending_total <= 0:
        return redirect(url_for("main.payments_page", warning="No pending transactions"))
    if amount > pending_total:
        return redirect(url_for("main.payments_page", warning="Amount exceeds pending total"))

    # Check paying account live balance (you treat initial_balance as live)
    current_balance = Decimal(pay.initial_balance or 0)
    if amount > current_balance:
        return redirect(url_for("main.payments_page", warning="Insufficient funds in paying account"))

    # Fetch pending txns FIFO (oldest first)
    pending_txns = (
        db.session.query(cls_card)
        .filter(
            cls_card.user_id == current_user.id,
            cls_card.account_id == card.id,
            cls_card.is_pending.is_(True),
            cls_card.is_deleted.is_(False),
            cls_card.type.in_([TxnType.expense, TxnType.fee]),
        )
        .order_by(asc(cls_card.date), asc(cls_card.id))
        .with_for_update()  # lock rows during settlement to avoid races
        .all()
    )

    gid = str(uuid4())
    remaining = amount

    # 1) Paying account: deduct balance and record an expense row
    pay.initial_balance = current_balance - amount
    db.session.add(cls_pay(
        user_id=current_user.id,
        account_id=pay.id,
        date=date.today(),
        type=TxnType.expense,
        amount=-amount,
        note=f"Credit card settlement → {card.name}",
        transfer_group_id=gid,
    ))

    # 2) Card side: record settlement credit (positive)
    db.session.add(cls_card(
        user_id=current_user.id,
        account_id=card.id,
        date=date.today(),
        type=TxnType.refund,  # or adjustment if you prefer
        amount=amount,
        note=f"Settlement from {pay.name}",
        transfer_group_id=gid,
        is_pending=False,
    ))

    # 3) Restore available limit
    card.credit_limit = (card.credit_limit or 0) + amount

    # 4) Apply settlement to pending txns FIFO, allowing PARTIAL split on a single txn
    for t in pending_txns:
        if remaining <= 0:
            break

        txn_abs = -Decimal(t.amount or 0)  # stored negative -> make positive
        if txn_abs <= 0:
            continue

        if remaining >= txn_abs:
            # fully cover this txn: mark it paid
            t.is_pending = False
            remaining -= txn_abs
        else:
            # PARTIAL COVER: split this txn
            # (a) insert a non-pending "paid portion" row mirroring the txn
            paid_portion = cls_card(
                user_id=current_user.id,
                account_id=card.id,
                date=t.date,
                type=t.type,
                amount=Decimal(-remaining),  # negative (expense)
                category_id=getattr(t, "category_id", None),
                note=(t.note or "") + " [partial paid]",
                transfer_group_id=gid,
                is_pending=False,
            )
            db.session.add(paid_portion)

            # (b) reduce the original pending txn amount by the paid chunk
            # original amount is negative; add the paid (positive) toward zero
            t.amount = Decimal(t.amount) + Decimal(remaining)  # e.g., -100 + 30 => -70 remains pending
            # keep is_pending=True
            remaining = Decimal("0")

    db.session.commit()

    # Craft feedback
    if amount < pending_total:
        return redirect(url_for("main.payments_page", success="Partial settlement recorded"))
    return redirect(url_for("main.payments_page", success="Card settled"))


@main.route("/accounts/set_balance_exact", methods=["POST"])
@login_required
def accounts_set_balance_exact():
    acc_id = request.form.get("account_id", type=int)
    target_str = (request.form.get("target_balance") or "").strip()

    if not acc_id or target_str == "":
        return redirect(url_for("main.payments_page", warning="Missing fields"))

    from decimal import Decimal, InvalidOperation
    try:
        target = Decimal(target_str).quantize(Decimal("0.01"))
    except InvalidOperation:
        return redirect(url_for("main.payments_page", warning="Invalid amount"))

    acc = db.session.get(Account, acc_id)
    if not acc or acc.user_id != current_user.id:
        return redirect(url_for("main.payments_page", warning="Account not found"))

    if acc.type == AccountType.credit:
        # Credit cards: this sets AVAILABLE limit right now
        acc.credit_limit = target
    else:
        # Non-credit: this is your **live balance**. No transaction math.
        acc.initial_balance = target

    db.session.commit()
    return redirect(url_for("main.payments_page", success="Balance updated"))

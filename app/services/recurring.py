# app/services/recurring.py  (replace run_due_rules_for_user with this catch-up version)

from datetime import date
from decimal import Decimal
from sqlalchemy import text
from ..extensions import db
from ..models import (
    RecurringRule, RecurringFrequency,
    Account, AccountType, Currency,
    TransactionKRW, TransactionBDT,
    TxnType
)
from calendar import monthrange
from datetime import timedelta

def _clamp_day(y: int, m: int, desired_day: int) -> int:
    return min(desired_day, monthrange(y, m)[1])

def _add_months(d: date, n: int, pinned_day: int | None) -> date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    day = pinned_day or d.day
    return date(y, m, _clamp_day(y, m, day))

def compute_next_run(prev: date, rule: RecurringRule) -> date:
    if rule.frequency == RecurringFrequency.daily:
        return prev + timedelta(days=rule.every_n)
    if rule.frequency == RecurringFrequency.weekly:
        nxt = prev + timedelta(weeks=rule.every_n)
        if rule.weekday is not None:
            diff = (rule.weekday - nxt.weekday()) % 7
            nxt = nxt + timedelta(days=diff)
        return nxt
    return _add_months(prev, rule.every_n, rule.day_of_month)

def _txn_model_for_account(acct: Account):
    return TransactionKRW if acct.currency == Currency.KRW else TransactionBDT

def _apply_account_side_effects(account: Account, ttype: TxnType, amount: Decimal):
    is_pending = False
    if account.type == AccountType.credit:
        if account.credit_limit is None:
            return False, "Credit limit not set", is_pending
        if amount > account.credit_limit:
            return False, "Insufficient credit limit", is_pending
        account.credit_limit -= amount
        is_pending = True
        return True, None, is_pending

    curr = Decimal(account.initial_balance or 0)
    if ttype in {TxnType.expense, TxnType.fee}:
        if amount > curr:
            return False, "Insufficient funds", is_pending
        account.initial_balance = curr - amount
    elif ttype == TxnType.income:
        account.initial_balance = curr + amount
    return True, None, is_pending

def _create_history_row(user_id: int, account: Account, ttype: TxnType, amount: Decimal,
                        category_id: int | None, note: str | None, run_date: date, is_pending: bool):
    Txn = _txn_model_for_account(account)
    signed = (-amount if ttype in {TxnType.expense, TxnType.fee} else amount)
    return Txn(
        user_id=user_id,
        account_id=account.id,
        date=run_date,
        type=ttype,
        amount=signed,
        category_id=category_id,
        note=note or "",
        is_pending=is_pending,
    )

def run_due_rules_for_user(user_id: int, today: date | None = None) -> dict:
    """
    Execute all enabled recurring rules whose next_run <= today.
    Catches up MULTIPLE missed occurrences by looping until next_run > today.
    """
    today = today or date.today()
    rules = (RecurringRule.query
             .filter(RecurringRule.user_id == user_id,
                     RecurringRule.is_enabled.is_(True),
                     RecurringRule.next_run <= today)
             .all())

    created = 0
    skipped = 0
    errors  = []

    for r in rules:
        acct = r.account
        if not acct or acct.user_id != user_id or not acct.is_active:
            errors.append((r.id, "Invalid or inactive account"))
            continue

        amount = Decimal(r.amount or 0)
        if amount <= 0:
            errors.append((r.id, "Amount must be > 0"))
            continue

        # Loop to catch up all missed runs (e.g., app was down)
        runs_this_rule = 0
        safety_cap = 100  # prevent infinite loop in case of corrupt data
        while r.next_run <= today and (not r.end_date or r.next_run <= r.end_date):
            ok, err, is_pending = _apply_account_side_effects(acct, r.type, amount)
            if not ok:
                # If funds/limit insufficient, stop trying further occurrences today
                errors.append((r.id, err or "Account update failed"))
                break

            txn = _create_history_row(
                user_id=user_id,
                account=acct,
                ttype=r.type,
                amount=amount,
                category_id=r.category_id,
                note=r.note,
                run_date=r.next_run,
                is_pending=is_pending,
            )
            db.session.add(txn)

            r.last_run = r.next_run
            r.next_run = compute_next_run(r.next_run, r)

            created += 1
            runs_this_rule += 1
            safety_cap -= 1
            if safety_cap <= 0:
                errors.append((r.id, "Aborted: too many catch-up iterations"))
                break

        if runs_this_rule == 0:
            skipped += 1

    db.session.commit()
    return {"created": created, "skipped": skipped, "errors": errors}

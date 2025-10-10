# app/services/finance_score.py
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Dict, Any, Tuple, List

from sqlalchemy import func, case, and_
from app.extensions import db
from app.models import (
    TransactionKRW, TransactionBDT, TxnType, Currency
)

EPS = Decimal("0.01")

@dataclass
class FinanceInputs:
    inflow: Decimal           # positive amounts (income, refunds, positive adjustments)
    outflow: Decimal          # absolute value of non-pending negative amounts (expense, fee)
    pending_outflow: Decimal  # absolute value of pending negative amounts

@dataclass
class FinanceScore:
    score: int
    label: str
    details: Dict[str, Any]   # raw metrics for UI

def _month_range(year: int, month: int) -> Tuple:
    from datetime import date
    start = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1)
    return start, end

def _sum_components(model, user_id: int, start, end) -> FinanceInputs:
    # Consider only current user's, not deleted, within period
# --------------------------
# inside _sum_components()
# --------------------------

    q = (
        db.session.query(
            # inflow: positive amounts (income, refund, positive adjustment)
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(model.amount > 0,
                                model.type.in_([TxnType.income, TxnType.refund, TxnType.adjustment])),
                            model.amount
                        ),
                        else_=0
                    )
                ), 0
            ).label("inflow"),

            # outflow: negative amounts that are NOT pending
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                model.amount < 0,
                                model.is_pending.is_(False),
                                model.type.in_([
                                    TxnType.expense,
                                    TxnType.fee,
                                    TxnType.transfer_international,   # 👈 added
                                ])
                            ),
                            -model.amount
                        ),
                        else_=0
                    )
                ), 0
            ).label("outflow"),

            # pending_outflow: negative amounts that ARE pending
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                model.amount < 0,
                                model.is_pending.is_(True),
                                model.type.in_([
                                    TxnType.expense,
                                    TxnType.fee,
                                    TxnType.transfer_international,  # 👈 added
                                ])
                            ),
                            -model.amount
                        ),
                        else_=0
                    )
                ), 0
            ).label("pending_outflow"),
        )
        .filter(
            model.user_id == user_id,
            model.is_deleted.is_(False),
            model.date >= start,
            model.date < end,
        )
    )

    row = q.one()
    return FinanceInputs(
        inflow=Decimal(row.inflow or 0),
        outflow=Decimal(row.outflow or 0),
        pending_outflow=Decimal(row.pending_outflow or 0),
    )

def _combine(inputs: List[FinanceInputs]) -> FinanceInputs:
    inflow = sum((i.inflow for i in inputs), Decimal(0))
    outflow = sum((i.outflow for i in inputs), Decimal(0))
    pending = sum((i.pending_outflow for i in inputs), Decimal(0))
    return FinanceInputs(inflow=inflow, outflow=outflow, pending_outflow=pending)

def _grade(score_num: int) -> str:
    if score_num >= 85: return "Excellent"
    if score_num >= 70: return "Good"
    if score_num >= 50: return "Fair"
    return "Poor"

def _clamp(x: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    return max(lo, min(hi, x))

def _score_from(inputs: FinanceInputs) -> FinanceScore:
    inflow  = inputs.inflow
    outflow = inputs.outflow
    pending = inputs.pending_outflow

    # avoid divide-by-zero; treat no-income month as worst case
    base = inflow if inflow > 0 else EPS

    spent_ratio   = _clamp(outflow / base, Decimal(0), Decimal(1))        # 0.00–1.00
    pending_ratio = _clamp(pending / base, Decimal(0), Decimal(1))        # 0.00–1.00

    # Core idea: score is the inverse of spending %
    # 100% spend -> 0, 0% spend -> 100
    # Nonlinear: curve exponent (>1 tightens the drop when spent_ratio is high)
    core = Decimal(100) * (Decimal(1) - spent_ratio) ** Decimal("1.20")


    # Optional: small penalty for pending (unsettled) outflow, capped at 20 points
    # e.g., 10% pending -> -10 points, 30% pending -> -20 (cap)
    pending_penalty = min(Decimal(20), pending_ratio * Decimal(100))

    score = core - pending_penalty
    score = _clamp(score, Decimal(0), Decimal(100))
    score_int = int(round(float(score)))

    # Helpful details for your UI
    return FinanceScore(
        score=score_int,
        label=_grade(score_int),
        details={
            "inflow":          str(inflow),
            "outflow":         str(outflow),
            "pending_outflow": str(pending),
            "spent_ratio":     float(spent_ratio),      # 0..1
            "pending_ratio":   float(pending_ratio),    # 0..1
            "core_before_penalty": float(core),
            "pending_penalty": float(pending_penalty),
        },
    )


def get_finance_score(
    user_id: int,
    year: Optional[int] = None,
    month: Optional[int] = None,
    currency: Optional[str] = None,  # "KRW" | "BDT" | None (both)
) -> FinanceScore:
    """
    By default: current month, both currencies combined.
    """
    from datetime import date
    today = date.today()
    year = year or today.year
    month = month or today.month
    start, end = _month_range(year, month)

    inputs = []

    # Per-currency switch
    if currency == Currency.KRW.value:
        inputs.append(_sum_components(TransactionKRW, user_id, start, end))
    elif currency == Currency.BDT.value:
        inputs.append(_sum_components(TransactionBDT, user_id, start, end))
    else:
        inputs.append(_sum_components(TransactionKRW, user_id, start, end))
        inputs.append(_sum_components(TransactionBDT, user_id, start, end))

    return _score_from(_combine(inputs))

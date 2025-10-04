from datetime import date, timedelta, datetime
from io import BytesIO, StringIO
import csv

from flask import (
    abort, jsonify, redirect, render_template, request, Response,
    stream_with_context, url_for, flash
)
from flask_login import login_required, current_user

from sqlalchemy import (
    and_, or_, not_,
    func, extract, case, literal,
    String, cast
)
from sqlalchemy.orm import selectinload

from ..main import main
from ..extensions import db
from ..models import (
    Recipient, RecipientType, TransactionKRW, TransactionBDT, Account,
    Category, Currency, TxnType
)

# -------- Helpers (BDT) --------
def _period_filters_bdt(year: int, month: int | None):
    flt = [
        TransactionBDT.user_id == current_user.id,
        TransactionBDT.is_deleted.is_(False),
        extract("year", TransactionBDT.date) == year,
    ]
    if month:
        flt.append(extract("month", TransactionBDT.date) == month)
    return flt

def _is_expense_bdt():
    # enum path only; if you kept cast/like fallbacks elsewhere, not needed here
    return TransactionBDT.type == TxnType.expense

def _is_transfer_bdt():
    return TransactionBDT.type.in_([TxnType.transfer_domestic, TxnType.transfer_international])

def _is_sent_type_bdt():
    return _is_expense_bdt() | _is_transfer_bdt()

def _is_self_transfer_bdt():
    """
    Treat as 'self' only when it is actually a transfer AND looks self-ish.
    This is intentionally NARROW so we don't exclude everything.

    Conditions:
      - Transfer type
      - recipient_name = 'self' (case/space-insensitive)
        OR recipient_name is NULL/empty AND recipient_id is NULL
    """
    name_is_self = func.coalesce(func.lower(func.trim(TransactionBDT.recipient_name)), '') == 'self'
    name_empty_and_id_null = (func.coalesce(func.nullif(func.trim(TransactionBDT.recipient_name), ''), literal(None)) == None) & (TransactionBDT.recipient_id.is_(None))  # noqa: E711
    return _is_transfer_bdt() & (name_is_self | name_empty_and_id_null)

def _exclude_all_self_transfers_bdt():
    return ~_is_self_transfer_bdt()

def _sum_abs_bdt():
    return func.coalesce(func.sum(func.abs(TransactionBDT.amount)), 0)


# ----------------------------
# EXPORT (CSV / PDF)
# ----------------------------
# ----------------------------
# EXPORT (CSV / PDF) — Helpers
# ----------------------------
from io import BytesIO, StringIO
from datetime import datetime

from flask import Response, redirect, url_for, abort, request, flash
from sqlalchemy.orm import selectinload

# Optional PDF deps (guarded)
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False

def _is_transfer(row) -> bool:
    t = row.type.value if hasattr(row.type, "value") else str(row.type)
    return t in ("transfer_domestic", "transfer_international")

def _recipient_label_for_bdt(row) -> str:
    # Fallback "Self" as you use in the UI
    return (row.recipient_name or "Self")

def _is_bdt_transfer_to_others(row) -> bool:
    # BDT “others” = any transfer where recipient is NOT Self
    if not _is_transfer(row):
        return False
    return _recipient_label_for_bdt(row) != "Self"

# ----- Formatting helpers -----
def _pick_pdf_font() -> str:
    """
    Try to register a Unicode TTF (for KR/BD text). If it fails, fallback to Helvetica.
    Place a font at static/fonts/NotoSans-Regular.ttf if you want full Unicode.
    """
    if not REPORTLAB_OK:
        return "Helvetica"
    try:
        pdfmetrics.registerFont(TTFont("NotoSans", "static/fonts/NotoSans-Regular.ttf"))
        return "NotoSans"
    except Exception:
        return "Helvetica"


def _fmt_amt(cur: str, amount_float: float) -> str:
    sym = "KRW" if cur == "KRW" else "BDT"
    return f"{sym}{amount_float:,.2f}"


def _cat_or_recipient(cur: str, row) -> str:
    """Show 'Recipient' for transfer types (with BDT Self fallback), Settlement label, else Category."""
    ttype = row.type.value if hasattr(row.type, "value") else str(row.type)
    note = (row.note or "")
    if "credit card settlement" in note.lower():
        return "Settlement"
    if ttype in ("transfer_domestic", "transfer_international"):
        return (row.recipient_name or ("Self" if cur == "BDT" else "")) or "—"
    return (getattr(row, "category_name", None) or "Uncategorized")


def _page_decor(canvas, doc, FONT_NAME: str):
    """Footer with page number + generated timestamp; subtle header/footer lines."""
    canvas.saveState()

    # Header rule
    y_top = doc.height + doc.topMargin + 2 * mm
    canvas.setStrokeColor(colors.HexColor("#e6e6e6"))
    canvas.setLineWidth(0.6)
    canvas.line(15 * mm, y_top, doc.width + doc.leftMargin, y_top)

    # Footer rule
    canvas.line(15 * mm, 15 * mm, doc.width + doc.leftMargin, 15 * mm)

    # Page number (left)
    canvas.setFont(FONT_NAME, 8)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.drawString(15 * mm, 11 * mm, f"Page {canvas.getPageNumber()}")

    # Timestamp (right)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    w = canvas.stringWidth(ts, FONT_NAME, 8)
    canvas.drawString(doc.width + doc.leftMargin - w, 11 * mm, ts)

    canvas.restoreState()

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from io import BytesIO
from datetime import datetime

def build_transaction_report_pdf(cur: str, rows, from_str: str, to_str: str):
    """
    Multi-page PDF:
    - Header bar "Transaction Report"
    - Period + total count
    - Cards:
        * KRW: Income / Expense / Net
        * BDT: ONE card: Total Transferred to Others
    - Table: Date | Category/Recipient | Note | Amount
    """
    # --- font ---
    try:
        pdfmetrics.registerFont(TTFont("NotoSans", "static/fonts/NotoSans-Regular.ttf"))
        FONT_NAME = "NotoSans"
    except Exception:
        FONT_NAME = "Helvetica"

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm,
        topMargin=22*mm, bottomMargin=20*mm
    )

    styles = getSampleStyleSheet()
    # add new styles only
    styles.add(ParagraphStyle(name="HeaderBar", fontName=FONT_NAME, fontSize=16,
                              textColor=colors.white, alignment=1))
    styles.add(ParagraphStyle(name="Muted", fontName=FONT_NAME, fontSize=8,
                              textColor=colors.HexColor("#666666")))
    styles.add(ParagraphStyle(name="TH", fontName=FONT_NAME, fontSize=9,
                              textColor=colors.white, alignment=1))
    normal = styles["Normal"]
    normal.fontName = FONT_NAME
    normal.fontSize = 9

    story = []

    # Header bar
    header_tbl = Table([[Paragraph("Transaction Report", styles["HeaderBar"])]],
                       colWidths=[doc.width])
    header_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#1f2937")),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
    ]))
    story.append(header_tbl)
    story.append(Spacer(0, 6))

    # Period & count
    story.append(Paragraph(f"Period: {from_str or 'Start'} → {to_str or 'Today'}", normal))
    story.append(Paragraph(f"Total Transactions: {len(rows)}", normal))
    story.append(Spacer(0, 8))

    # ----- Cards section -----
    if cur == "BDT":
        total_sent = 0.0
        total_received = 0.0

        for t in rows:
            ttype = t.type.value if hasattr(t.type, "value") else str(t.type)
            amt = float(t.amount)
            recipient_label = (t.recipient_name or "Self")

            # Card 1: all expenses/outflows except domestic transfers
            if amt < 0 and ttype != "transfer_domestic":
                total_sent += abs(amt)

            # Card 2: international transfers received to self
            if ttype == "transfer_international" and recipient_label == "Self" and amt > 0:
                total_received += amt

        # Card 1 – Sent (NO full-width colWidths here)
        card1 = Table(
            [[Paragraph("Total Expenses)", normal),
            Paragraph(f"<font color='#dc3545'><b>{_fmt_amt(cur, total_sent)}</b></font>", normal)]]
        )
        card1.setStyle(TableStyle([
            ("BOX", (0,0), (-1,-1), 0.8, colors.HexColor("#d1d5db")),
            ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#f3f4f6")),
            ("ALIGN", (1,0), (1,0), "RIGHT"),
            ("TOPPADDING", (0,0), (-1,-1), 8),
            ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ]))

        # Card 2 – Received (NO full-width colWidths here)
        card2 = Table(
            [[Paragraph("Total Received", normal),
            Paragraph(f"<font color='#198754'><b>{_fmt_amt(cur, total_received)}</b></font>", normal)]]
        )
        card2.setStyle(TableStyle([
            ("BOX", (0,0), (-1,-1), 0.8, colors.HexColor("#d1d5db")),
            ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#f3f4f6")),
            ("ALIGN", (1,0), (1,0), "RIGHT"),
            ("TOPPADDING", (0,0), (-1,-1), 8),
            ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ]))

        # Outer row table controls final layout
        cards_row = Table([[card1, card2]], colWidths=[doc.width/2, doc.width/2])
        cards_row.setStyle(TableStyle([
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ("LEFTPADDING", (0,0), (-1,-1), 6),
            ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ]))

        story.append(cards_row)
        story.append(Spacer(0, 12))


    else:
        # KRW (or default): Income / Expense / Net (3 cards)
        income = sum(float(t.amount) for t in rows if float(t.amount) > 0.0)
        expense_raw = sum(float(t.amount) for t in rows if float(t.amount) < 0.0)  # negative
        expense = abs(expense_raw)
        net = income - expense

        def _mini_card(title, value_html):
            return Table([[Paragraph(title, normal), Paragraph(value_html, normal)]],
                         colWidths=[doc.width/3*0.5, doc.width/3*0.5],
                         style=TableStyle([
                             ("BOX", (0,0), (-1,-1), 0.8, colors.HexColor("#d1d5db")),
                             ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#f3f4f6")),
                             ("ALIGN", (1,0), (1,0), "RIGHT"),
                             ("TOPPADDING", (0,0), (-1,-1), 8),
                             ("BOTTOMPADDING", (0,0), (-1,-1), 8),
                         ]))

        income_card  = _mini_card("Income",  f"<font color='#198754'><b>{_fmt_amt(cur, income)}</b></font>")
        expense_card = _mini_card("Expense", f"<font color='#dc3545'><b>{_fmt_amt(cur, expense)}</b></font>")
        net_color = "#198754" if net >= 0 else "#dc3545"
        net_card    = _mini_card("Net",     f"<font color='{net_color}'><b>{_fmt_amt(cur, net)}</b></font>")

        cards_row = Table([[income_card, expense_card, net_card]],
                          colWidths=[doc.width/3, doc.width/3, doc.width/3])
        story.append(cards_row)

    story.append(Spacer(0, 12))

    # ----- Table: Date | Category/Recipient | Note | Amount -----
    head = [Paragraph("Date", styles["TH"]),
            Paragraph("Category / Recipient", styles["TH"]),
            Paragraph("Note", styles["TH"]),
            Paragraph("Amount", styles["TH"])]
    data = [head]

    # Category/Recipient selection consistent with your rules
    def _cat_or_recipient(cur_: str, row) -> str:
        ttype = row.type.value if hasattr(row.type, "value") else str(row.type)
        note = (row.note or "")
        if "credit card settlement" in note.lower():
            return "Settlement"
        if ttype in ("transfer_domestic", "transfer_international"):
            # BDT uses Self fallback, others keep just name if present
           return row.recipient_name or "Self"
        return (getattr(row, "category_name", None) or "Uncategorized")

    for t in rows:
        date_str = t.date.isoformat()
        label = _cat_or_recipient(cur, t)
        note_para = Paragraph(t.note or "—", styles["Muted"])

        amt = float(t.amount)
        color = "#198754" if amt > 0 else "#dc3545" if amt < 0 else "#0d6efd"
        amt_html = f"<font color='{color}'><b>{_fmt_amt(cur, amt)}</b></font>"
        amt_para = Paragraph(amt_html, normal)

        data.append([
            Paragraph(date_str, normal),
            Paragraph(label, normal),
            note_para,
            amt_para
        ])

    tbl = Table(
        data,
        colWidths=[28*mm, 60*mm, 82*mm, 30*mm],
        repeatRows=1, splitByRow=True
    )
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#111111")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), FONT_NAME),
        ("VALIGN",     (0,0), (-1,0), "MIDDLE"),
        ("BOTTOMPADDING", (0,0), (-1,0), 6),

        ("FONTNAME",   (0,1), (-1,-1), FONT_NAME),
        ("FONTSIZE",   (0,1), (-1,-1), 9),
        ("VALIGN",     (0,1), (-1,-1), "TOP"),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.whitesmoke, colors.HexColor("#f5f5f5")]),
        ("GRID",       (0,0), (-1,-1), 0.25, colors.HexColor("#d9d9d9")),
        ("ALIGN",      (-1,1), (-1,-1), "RIGHT"),
    ]))
    story.append(tbl)

    # Footer (page number + timestamp)
    def _page_decor(canvas, doc, FONT_NAME_):
        canvas.saveState()
        # footer line
        canvas.setStrokeColor(colors.HexColor("#e6e6e6"))
        canvas.setLineWidth(0.6)
        canvas.line(15*mm, 15*mm, doc.width + doc.leftMargin, 15*mm)
        # page number
        canvas.setFont(FONT_NAME_, 8)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(15*mm, 11*mm, f"Page {canvas.getPageNumber()}")
        # timestamp
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        w = canvas.stringWidth(ts, FONT_NAME_, 8)
        canvas.drawString(doc.width + doc.leftMargin - w, 11*mm, ts)
        canvas.restoreState()

    def _on_page(c, d): _page_decor(c, d, FONT_NAME)
    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)

    pdf = buf.getvalue()
    buf.close()
    filename = f"{cur}_transactions_{from_str or 'start'}_{to_str or 'today'}.pdf"
    return pdf, filename


def _date_range(from_str: str | None, to_str: str | None):
    """Return [start, end) dates. Default = current month (1st → today inclusive)."""
    today = date.today()
    if not from_str and not to_str:
        start = today.replace(day=1)
        end = today + timedelta(days=1)
        return start, end

    start = date(2000, 1, 1)
    end   = today + timedelta(days=1)
    if from_str:
        start = date.fromisoformat(from_str)
    if to_str:
        end = date.fromisoformat(to_str) + timedelta(days=1)  # inclusive 'to'
    return start, end

def _base(model, start, end, q, category_id, account_id):
    qset = (db.session.query(model)
            .filter(model.user_id == current_user.id,
                    model.is_deleted.is_(False),
                    model.date >= start, model.date < end)
            .options(selectinload(model.account)))
    if q:
        like = f"%{q.strip()}%"
        qset = qset.filter(
            func.lower(func.coalesce(model.note, "")).like(func.lower(like)) |
            func.lower(func.coalesce(model.recipient_name, "")).like(func.lower(like))
        )
    if category_id:
        qset = qset.filter(model.category_id == category_id)
    if account_id:
        qset = qset.filter(model.account_id == account_id)
    return qset.order_by(model.date.desc(), model.id.desc())
# use this for the UI tables (KRW/BDT)

def _base_for_table(model, start, end, q, category_id, account_id):
    qset = (
        db.session.query(model)
        .filter(
            model.user_id == current_user.id,
            model.is_deleted.is_(False),
            model.date >= start, model.date < end,
            model.type != TxnType.refund,  # (your UI rule)
        )
        .options(
            selectinload(model.account),
            selectinload(model.recipient),   # 👈 add this
        )
        .order_by(model.date.desc(), model.id.desc())
    )
    if q:
        like = f"%{q.strip()}%"
        qset = qset.filter(
            func.lower(func.coalesce(model.note, "")).like(func.lower(like)) |
            func.lower(func.coalesce(model.recipient_name, "")).like(func.lower(like))
        )
    if category_id:
        qset = qset.filter(model.category_id == category_id)
    if account_id:
        qset = qset.filter(model.account_id == account_id)
    return qset






SETTLEMENT_PHRASE = "credit card settlement"  # lower-case match

def _kpis(model, start, end, q, category_id, account_id):
    # Build same filtered set as table BUT do NOT exclude any types here.
    qset = (
        db.session.query(model)
        .filter(
            model.user_id == current_user.id,
            model.is_deleted.is_(False),
            model.date >= start, model.date < end,
        )
    )
    if q:
        like = f"%{q.strip()}%"
        qset = qset.filter(
            func.lower(func.coalesce(model.note, "")).like(func.lower(like)) |
            func.lower(func.coalesce(model.recipient_name, "")).like(func.lower(like))
        )
    if category_id:
        qset = qset.filter(model.category_id == category_id)
    if account_id:
        qset = qset.filter(model.account_id == account_id)

    # Subquery to avoid accidental cross-joins
    sq = qset.with_entities(
        model.amount.label("amount"),
        model.type.label("type"),
        func.lower(func.coalesce(model.note, "")).label("note_lc"),
    ).subquery()

    # IN: include positives EXCEPT refunds
    inflow_expr = case(
        (and_(sq.c.amount > 0, sq.c.type != TxnType.refund), sq.c.amount),
        else_=0,
    )

    # OUT: include negatives EXCEPT settlements whose note contains "credit card settlement"
    outflow_expr = case(
        (and_(sq.c.amount < 0, not_(sq.c.note_lc.like(f"%{SETTLEMENT_PHRASE}%"))), -sq.c.amount),
        else_=0,
    )

    # NET: leave as total sum (change if you want to ignore refunds/settlements as well)
    net_expr = func.sum(sq.c.amount)

    i, o, n = db.session.query(
        func.coalesce(func.sum(inflow_expr), 0),
        func.coalesce(func.sum(outflow_expr), 0),
        func.coalesce(net_expr, 0),
    ).first()

    sym = "원" if model.__tablename__ == "transactions_krw" else "৳"
    fmt = lambda v: f"{v:,.0f} {sym}"
    return {
        "inflow": i, "outflow": o, "net": n,
        "inflow_fmt": fmt(i), "outflow_fmt": fmt(o), "net_fmt": fmt(n),
    }

from sqlalchemy import func, case, and_, not_
from ..models import TransactionBDT, TxnType

def _kpis_bdt(start, end, q, category_id, account_id):
    qset = (
        db.session.query(TransactionBDT)
        .filter(
            TransactionBDT.user_id == current_user.id,
            TransactionBDT.is_deleted.is_(False),
            TransactionBDT.date >= start, TransactionBDT.date < end,
        )
    )
    if q:
        like = f"%{q.strip()}%"
        qset = qset.filter(
            func.lower(func.coalesce(TransactionBDT.note, "")).like(func.lower(like)) |
            func.lower(func.coalesce(TransactionBDT.recipient_name, "")).like(func.lower(like))
        )
    if category_id:
        qset = qset.filter(TransactionBDT.category_id == category_id)
    if account_id:
        qset = qset.filter(TransactionBDT.account_id == account_id)

    sq = qset.with_entities(
        TransactionBDT.amount.label("amount"),
        TransactionBDT.type.label("type"),
        TransactionBDT.recipient_name.label("rcpt_name"),
    ).subquery()

    rcpt_is_self      = (func.coalesce(sq.c.rcpt_name, "Self") == "Self")
    has_recipient     = (func.coalesce(sq.c.rcpt_name, "") != "")
    is_intl           = (sq.c.type == TxnType.transfer_international)
    is_dom            = (sq.c.type == TxnType.transfer_domestic)
    is_external_intl  = and_(is_intl, not_(rcpt_is_self))
    dom_to_other      = and_(is_dom, has_recipient)   # domestic with recipient present

    # --- IN ---
    # 1) normal positives (not refund, not domestic, not external intl)
    normal_pos_in = case(
        (
            and_(
                sq.c.amount > 0,
                sq.c.type != TxnType.refund,
                or_(
                    not_(is_dom),                   # not domestic
                    and_(is_external_intl, rcpt_is_self) # international but to self
                )
            ),
            sq.c.amount
        ),
        else_=0
    )

    # 2) domestic to others should DEDUCT from IN (subtract |amount|)
    dom_deduct = case((dom_to_other, func.abs(sq.c.amount)), else_=0)

    inflow_expr = normal_pos_in - dom_deduct

    # --- OUT ---
    outflow_expr = case(
        (is_external_intl, func.abs(sq.c.amount)),   # external intl counts fully to OUT
        (dom_to_other,    func.abs(sq.c.amount)),    # domestic to others counts to OUT
        (sq.c.amount < 0, -sq.c.amount),             # otherwise normal negatives
        else_=0
    )

    i, o, n = db.session.query(
        func.coalesce(func.sum(inflow_expr), 0),
        func.coalesce(func.sum(outflow_expr), 0),
        func.coalesce(func.sum(sq.c.amount), 0),
    ).first()

    fmt = lambda v: f"{v:,.0f} ৳"
    return {
        "inflow": i, "outflow": o, "net": n,
        "inflow_fmt": fmt(i), "outflow_fmt": fmt(o), "net_fmt": fmt(n),
    }
# ... after computing krw_from_str/to etc.
from urllib.parse import urlencode

def _export_url(cur, fmt, q, category_id, account_id, from_str, to_str):
    base = url_for("main.transactions_export", cur=cur, fmt=fmt)
    qs = urlencode({
        "q": q or "",
        "category_id": category_id or "",
        "account_id": account_id or "",
        "from": from_str or "",
        "to": to_str or "",
    })
    return f"{base}?{qs}"
def _base_period_filters(year: int, month: int | None):
    flt = [
        TransactionBDT.user_id == current_user.id,
        extract("year", TransactionBDT.date) == year,
    ]
    if month:
        flt.append(extract("month", TransactionBDT.date) == month)
    return flt

def _is_sent_type():
    # Count as "sent" if it's an expense or any transfer
    return TransactionBDT.type.in_([
        TxnType.expense,
        TxnType.transfer_international,
        TxnType.transfer_domestic,
    ])

def _exclude_domestic_self():
    # Exclude domestic transfer to self (common patterns)
    return ~and_(
        TransactionBDT.type == TxnType.transfer_domestic,
        or_(
            TransactionBDT.recipient_name == "Self",
            TransactionBDT.recipient_id.is_(None)  # adjust if you store explicit self id
        )
    )

def _sent_amount_expr():
    """
    Treat outflows as positive contributions to 'sent':
    - If amount < 0  -> count -amount (absolute)
    - Else           -> count 0
    This avoids accidentally counting inflows/deposits.
    """
    return func.coalesce(
        func.sum(
            case(
                (TransactionBDT.amount < 0, -TransactionBDT.amount),
                else_=0
            )
        ),
        0
    ) 
@main.route("/transactions", methods=["GET"], endpoint="transactions_page")
@login_required
def transactions_page():
    # ---------------- KRW filters ----------------
    krw_q           = request.args.get("krw_q") or ""
    krw_category_id = request.args.get("krw_category_id", type=int)
    krw_account_id  = request.args.get("krw_account_id", type=int)
    krw_from        = request.args.get("krw_from")   # YYYY-MM-DD
    krw_to          = request.args.get("krw_to")     # YYYY-MM-DD
    krw_start, krw_end = _date_range(krw_from, krw_to)
    krw_from_str = krw_from or krw_start.isoformat()
    krw_to_str   = krw_to   or (krw_end - timedelta(days=1)).isoformat()

    # ---------------- BDT filters ----------------
    bdt_q           = request.args.get("bdt_q") or ""
    bdt_category_id = request.args.get("bdt_category_id", type=int)
    bdt_account_id  = request.args.get("bdt_account_id", type=int)
    bdt_from        = request.args.get("bdt_from")
    bdt_to          = request.args.get("bdt_to")
    bdt_start, bdt_end = _date_range(bdt_from, bdt_to)
    bdt_from_str = bdt_from or bdt_start.isoformat()
    bdt_to_str   = bdt_to   or (bdt_end - timedelta(days=1)).isoformat()

    # ---------------- dropdown data ----------------
    categories = (db.session.query(Category)
                  .filter(Category.user_id == current_user.id, Category.parent_id.is_(None))
                  .options(selectinload(Category.children))
                  .order_by(Category.name.asc()).all())
    accounts_krw = (db.session.query(Account)
                    .filter(Account.user_id == current_user.id,
                            Account.currency == Currency.KRW,
                            Account.is_active.is_(True))
                    .order_by(Account.display_order.asc()).all())
    accounts_bdt = (db.session.query(Account)
                    .filter(Account.user_id == current_user.id,
                            Account.currency == Currency.BDT,
                            Account.is_active.is_(True))
                    .order_by(Account.display_order.asc()).all())

    # ---------------- Table rows ----------------
    # (hide refunds in UI; show settlements)
    krw_page = request.args.get("krw_page", 1, type=int)
    krw_per_page = 10

    krw_rows = (
        _base_for_table(TransactionKRW, krw_start, krw_end,
                        krw_q, krw_category_id, krw_account_id)
        .offset((krw_page - 1) * krw_per_page)
        .limit(krw_per_page)
        .all()
    )

    krw_total = (
        _base_for_table(TransactionKRW, krw_start, krw_end,
                        krw_q, krw_category_id, krw_account_id)
        .count()
    )

    bdt_page = request.args.get("bdt_page", 1, type=int)
    bdt_per_page = 10

    bdt_rows = (
        _base_for_table(TransactionBDT, bdt_start, bdt_end,
                        bdt_q, bdt_category_id, bdt_account_id)
        .offset((bdt_page - 1) * bdt_per_page)
        .limit(bdt_per_page)
        .all()
    )

    bdt_total = (
        _base_for_table(TransactionBDT, bdt_start, bdt_end,
                        bdt_q, bdt_category_id, bdt_account_id)
        .count()
    )

    # ---------------- KPIs ----------------
    # (exclude refunds from IN; exclude “credit card settlement” from OUT)
    kpi_krw = _kpis(TransactionKRW, krw_start, krw_end,
                    krw_q, krw_category_id, krw_account_id)
    kpi_bdt = _kpis_bdt(bdt_start, bdt_end, bdt_q, bdt_category_id, bdt_account_id)


    # export URLs
    krw_csv_url = _export_url("KRW", "csv", krw_q, krw_category_id, krw_account_id, krw_from_str, krw_to_str)
    krw_pdf_url = _export_url("KRW", "pdf", krw_q, krw_category_id, krw_account_id, krw_from_str, krw_to_str)
    bdt_csv_url = _export_url("BDT", "csv", bdt_q, bdt_category_id, bdt_account_id, bdt_from_str, bdt_to_str)
    bdt_pdf_url = _export_url("BDT", "pdf", bdt_q, bdt_category_id, bdt_account_id, bdt_from_str, bdt_to_str)

    return render_template(
        "transactions.html",
        page_title="Transactions", page_slug="transactions",

        # KRW
        krw_q=krw_q, krw_category_id=krw_category_id, krw_account_id=krw_account_id,
        krw_from_str=krw_from_str, krw_to_str=krw_to_str,
        accounts_krw=accounts_krw, krw_rows=krw_rows, kpi_krw=kpi_krw,
        krw_page=krw_page, krw_per_page=krw_per_page, krw_total=krw_total,
        # BDT
        bdt_q=bdt_q, bdt_category_id=bdt_category_id, bdt_account_id=bdt_account_id,
        bdt_from_str=bdt_from_str, bdt_to_str=bdt_to_str,
        accounts_bdt=accounts_bdt, bdt_rows=bdt_rows, kpi_bdt=kpi_bdt,
        bdt_page=bdt_page, bdt_per_page=bdt_per_page, bdt_total=bdt_total,
        # shared
        categories=categories,
        # export URLs
        krw_csv_url=krw_csv_url, krw_pdf_url=krw_pdf_url,
        bdt_csv_url=bdt_csv_url, bdt_pdf_url=bdt_pdf_url,
    )
    start, end, _ = _date_range(ym)

    model = TransactionKRW if cur == "KRW" else TransactionBDT
    qset = _base(model, start, end, q, category_id, account_id).order_by(model.date.asc())

    def gen():
        yield "currency,date,account,type,amount,category,note,status\n"
        for t in qset:
            status = "deleted" if t.is_deleted else ("pending" if t.is_pending else "completed")
            acct = t.account.name if t.account else ""
            cat  = getattr(t, "category_name", "") or ""
            note = (t.note or "").replace('"','""')
            yield f'{cur},{t.date.isoformat()},"{acct}",{t.type.value},{t.amount},"{cat}","{note}",{status}\n'

    fname = f"transactions_{cur}_{start.isoformat()}_{end.isoformat()}.csv"
    return Response(stream_with_context(gen()),
                    mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ----------------------------
# EXPORT route
# ----------------------------
@main.route("/transactions/export/<cur>.<fmt>", methods=["GET"], endpoint="transactions_export")
@login_required
def transactions_export(cur, fmt):
    cur = cur.upper()
    if cur not in ("KRW", "BDT"):
        abort(404)
    if fmt not in ("csv", "pdf"):
        abort(404)

    # Parse filters (mirror your page)
    q           = request.args.get("q") or ""
    category_id = request.args.get("category_id", type=int)
    account_id  = request.args.get("account_id", type=int)
    from_str    = request.args.get("from")  # YYYY-MM-DD
    to_str      = request.args.get("to")    # YYYY-MM-DD
    start, end  = _date_range(from_str, to_str)

    # Model & query (same logic as UI table: refunds hidden, settlements shown)
    Model = TransactionKRW if cur == "KRW" else TransactionBDT
    qset = (_base_for_table(Model, start, end, q, category_id, account_id)
            .options(selectinload(Model.account), selectinload(Model.recipient))
            .order_by(Model.date.desc(), Model.id.desc()))
    rows = qset.all()

    # CSV
    if fmt == "csv":
        si = StringIO()
        writer = csv.writer(si)
        writer.writerow(["Date", "Type", "Account", "Recipient/Category", "Note", "Amount", "Pending", "Deleted"])
        for t in rows:
            label = _cat_or_recipient(cur, t)
            writer.writerow([
                t.date.isoformat(),
                (t.type.value if hasattr(t.type, "value") else str(t.type)),
                (t.account.name if t.account else ""),
                label,
                (t.note or ""),
                f"{t.amount}",
                "yes" if t.is_pending else "no",
                "yes" if t.is_deleted else "no",
            ])
        output = si.getvalue().encode("utf-8")
        filename = f"{cur}_transactions_{from_str or 'start'}_{to_str or 'today'}.csv"
        return Response(output, mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={filename}"})

    # PDF
    if not REPORTLAB_OK:
        flash("PDF export requires 'reportlab'. Please install it: pip install reportlab", "danger")
        # fallback to CSV preserving filters
        return redirect(
            url_for("main.transactions_export", cur=cur, fmt="csv") +
            (("?" + request.query_string.decode("utf-8")) if request.query_string else "")
        )

    pdf_bytes, filename = build_transaction_report_pdf(cur, rows, from_str, to_str)
    return Response(pdf_bytes, mimetype="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


# -------- API --------

@main.route("/api/send_overview", methods=["GET"])
@login_required
def send_overview():
    now = datetime.utcnow()
    year  = request.args.get("year", type=int, default=now.year)
    month = request.args.get("month", type=int)  # optional

    # ---------- Base filters ----------
    base_filters = [
        TransactionBDT.user_id == current_user.id,
        TransactionBDT.is_deleted.is_(False),
        extract("year", TransactionBDT.date) == year,
    ]
    if month:
        base_filters.append(extract("month", TransactionBDT.date) == month)

    # ---------- Type & self rules ----------
    is_expense   = (TransactionBDT.type == TxnType.expense)
    is_transfer  = TransactionBDT.type.in_([TxnType.transfer_domestic, TxnType.transfer_international])

    # Only treat explicit "self" text as self; do NOT exclude NULL/blank (to avoid dropping legit data)
    name_lower = func.lower(func.trim(TransactionBDT.recipient_name))
    is_self    = (name_lower == 'self')

    # ---------- Labels ----------
    # For breakdown we use recipient_name first (transfers) otherwise fall back to category
    name_expr = func.coalesce(
        TransactionBDT.recipient_name,
        Category.name,
        literal("Uncategorized"),
    ).label("name")

    month_expr = extract("month", TransactionBDT.date).label("m")

    # ---------- What counts as "sent" ----------
    # 1) Expenses → include ABS(amount)
    # 2) Transfers that are NOT self → include ABS(amount)
    # 3) Everything else → 0
    sent_amount = case(
        (is_expense, func.abs(TransactionBDT.amount)),
        (and_(is_transfer, ~is_self), func.abs(TransactionBDT.amount)),
        else_=0,
    ).label("sent_amount")

    subq = (
        db.session.query(
            name_expr,        # label for grouping
            month_expr,       # month number 1..12
            sent_amount,      # numeric
        )
        .select_from(TransactionBDT)
        .outerjoin(Category, Category.id == TransactionBDT.category_id)
        .filter(*base_filters)
    ).subquery()

    # ---------- KPI total for the selected period ----------
    kpi_q = db.session.query(func.coalesce(func.sum(subq.c.sent_amount), 0))
    total_sent = float(kpi_q.scalar() or 0)

    # ---------- Monthly totals for the whole selected year ----------
    # (subq already filtered to the selected month if provided, so monthly will reflect that)
    monthly_rows = (
        db.session.query(
            subq.c.m,
            func.coalesce(func.sum(subq.c.sent_amount), 0),
        )
        .group_by(subq.c.m)
        .all()
    )
    monthly = {int(m): float(t) for m, t in monthly_rows if m is not None}

    # ---------- Per recipient/category for the selected period ----------
    per_rows = (
        db.session.query(
            subq.c.name,
            func.coalesce(func.sum(subq.c.sent_amount), 0),
        )
        .group_by(subq.c.name)
        .order_by(func.sum(subq.c.sent_amount).desc())
        .all()
    )
    recipients = [{"name": n, "total": float(t)} for n, t in per_rows]

    return jsonify({
        "year": year,
        "month": month,
        "total_sent": total_sent,
        "monthly": monthly,
        "recipients": recipients,
    })
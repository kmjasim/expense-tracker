import calendar
from dateutil.relativedelta import relativedelta
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import Table, TableStyle
from reportlab.lib.pagesizes import A4
from datetime import date, timedelta


def _month_days(year: int, month: int):
    start = date(year, month, 1)
    last = calendar.monthrange(year, month)[1]

    days = []
    d = start
    for _ in range(last):
        days.append(d)
        d += timedelta(days=1)

    return days

def _money(n):
    return f"{int(round(float(n))):,} KRW"

def _draw_card(c, x, y, w, h, title=None):
    c.setFillColor(colors.white)
    c.setStrokeColor(colors.Color(0.85, 0.85, 0.85))
    c.roundRect(x, y, w, h, 8, stroke=1, fill=1)
    if title:
        c.setFont("Helvetica-Bold", 10)
        c.setFillColor(colors.HexColor("#333333"))
        c.drawString(x + 10, y + h - 18, title)

def draw_payslip_page(c, user, year, month, summary, deduction_items=None):
    deduction_items = deduction_items or []
    W, H = A4
    m = 18 * mm

    month_name = calendar.month_name[month]

    # Header
    c.setFillColor(colors.HexColor("#111827"))
    c.setFont("Helvetica-Bold", 20)
    c.drawString(m, H - m - 8, "Payslip")

    c.setFillColor(colors.HexColor("#6B7280"))
    c.setFont("Helvetica", 12)
    c.drawString(m, H - m - 28, f"For the month: {month_name} {year}")

    # Cards
    card_gap = 10
    card_h = 90
    card_y = H - m - 28 - 18 - card_h
    card_w = (W - 2 * m - card_gap) / 2

    # Employee info card
    x1 = m
    _draw_card(c, x1, card_y, card_w, card_h, "Employee Information")

    employee_name = getattr(user, "name", None) or "Employee"
    designation = getattr(user, "designation", None) or "Employee"

    last_day = calendar.monthrange(year, month)[1]
    pay_period = f"{year}-{month:02d}-01 to {year}-{month:02d}-{last_day}"
    pay_date = (date(year, month, 1) + relativedelta(months=1)).replace(day=10)
    pay_date = pay_date.isoformat()

    c.setFont("Helvetica", 10)
    lines = [
        ("Name", employee_name),
        ("Designation", designation),
        ("Pay period", pay_period),
        ("Pay date", pay_date),
    ]
    yy = card_y + card_h - 34
    for k, v in lines:
        c.setFillColor(colors.HexColor("#6B7280"))
        c.drawString(x1 + 10, yy, f"{k}:")
        c.setFillColor(colors.HexColor("#111827"))
        c.drawString(x1 + 90, yy, str(v))
        yy -= 14

    # Net salary card
    x2 = m + card_w + card_gap
    _draw_card(c, x2, card_y, card_w, card_h, "Net Salary")
    c.setFont("Helvetica-Bold", 22)
    c.setFillColor(colors.HexColor("#6FDA60"))
    c.drawString(x2 + 10, card_y + 28, _money(summary["net"]))
    c.setFont("Helvetica", 9)
    c.setFillColor(colors.HexColor("#6FDA60"))
    c.drawString(x2 + 10, card_y + 14, "Net amount to be paid")

    # Build earnings/deductions table
    earnings = [
        ("Base Salary", summary["base_salary"]),
        ("Weekday Overtime", summary["overtime_pay"]),
        ("Weekend/Holiday", summary["weekend_holiday_pay"]),
        ("Allowance", summary["allowance_total"]),
    ]

    # ✅ Detailed deductions (Insurance, Tax, etc.)
    deductions = [(name, amt) for (name, amt) in deduction_items]

    # ✅ Always include Leave Deduction as its own line (if any)
    if float(summary["leave_deduction"]) > 0:
        deductions.append(("Leave Deduction", summary["leave_deduction"]))


    table_top = card_y - 18
    table_x = m
    table_w = W - 2 * m

    rows = max(len(earnings), len(deductions))
    body = []
    for i in range(rows):
        e = earnings[i] if i < len(earnings) else ("", "")
        d = deductions[i] if i < len(deductions) else ("", "")
        body.append([e[0], _money(e[1]) if e[0] else "", d[0], _money(d[1]) if d[0] else ""])

    tdata = [["Earnings", "Amount", "Deductions", "Amount"]] + body + [["", "", "", ""]]
    gross = sum(float(v) for _, v in earnings)
    total_ded = sum(float(v) for _, v in deductions)
    tdata.append(["Gross Earnings", _money(gross), "Total Deductions", _money(total_ded)])
    tdata.append([
        "Total OT (hrs)",
        summary.get("overtime_hours_str", "0.0h"),
        "Weekend/Holiday(days)",
        str(summary.get("weekend_holiday_days", 0)),
    ])

    col_w = [table_w * 0.30, table_w * 0.20, table_w * 0.30, table_w * 0.20]
    t = Table(tdata, colWidths=col_w)
    row_h = 50

    t = Table(
        tdata,
        colWidths=col_w,
        rowHeights=[row_h] * len(tdata),
    )
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#111827")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("ALIGN", (1,1), (1,-1), "RIGHT"),
        ("ALIGN", (3,1), (3,-1), "RIGHT"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("TEXTCOLOR", (3,1), (3,-2), colors.HexColor("#B91C1C")),
        ("FONTNAME", (3,1), (3,-2), "Helvetica-Bold"),

        ("TEXTCOLOR", (2,1), (2,-2), colors.HexColor("#6B7280")),

        ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#D1D5DB")),
        ("ROWBACKGROUNDS", (0,1), (-1,-3), [colors.white, colors.HexColor("#F9FAFB")]),
        # Gross/Total row (second last)
        ("BACKGROUND", (0,-2), (-1,-2), colors.HexColor("#F3F4F6")),
        ("FONTNAME", (0,-2), (-1,-2), "Helvetica-Bold"),

        # New OT/Weekend row (last)
        ("BACKGROUND", (0,-2), (-1,-1), colors.HexColor("#F3F4F6")),
        ("TEXTCOLOR", (0,-1), (-1,-1), colors.HexColor("#374151")),
        ("FONTNAME", (0,-2), (-1,-2), "Helvetica-Bold"),

    ]))

    tw, th = t.wrapOn(c, table_w, 0)
    t.drawOn(c, table_x, table_top - th)

    c.setFont("Helvetica", 8)
    c.setFillColor(colors.HexColor("#6B7280"))
    c.drawString(m, 12 * mm, "Generated by Expense Tracker. Values are based on recorded logs and settings.")


def draw_logs_page(c, user, year, month, summary):
    W, H = A4
    m = 18 * mm

    month_name = calendar.month_name[month]
    c.setFillColor(colors.HexColor("#111827"))
    c.setFont("Helvetica-Bold", 18)
    c.drawString(m, H - m - 10, f"Daily Logs - {month_name} {year}")

    # ---- build full month rows ----
    logs = summary["logs"]
    log_by_date = {l.work_date: l for l in logs}

    days = _month_days(year, month)

    head = ["Date", "Day", "IN", "OUT", "Lunch", "Worked", "Regular", "OT", "Leave", "Note"]
    rows = [head]

    for d in days:
        l = log_by_date.get(d)

        # values (blank if no log)
        in_s = l.in_time.strftime("%H:%M") if l and l.in_time else "—"
        out_s = l.out_time.strftime("%H:%M") if l and l.out_time else "—"
        lunch = str(l.lunch_minutes or 0) if l else "0"
        worked = f"{((l.worked_minutes or 0)/60):.1f}" if l else "0.0"
        reg = f"{((l.regular_minutes or 0)/60):.1f}" if l else "0.0"
        ot = f"{((l.overtime_minutes or 0)/60):.1f}" if l else "0.0"
        leave = "Yes" if (l and l.is_full_day_leave) else "No"
        note = ((l.note or "")[:26] if l else "")

        rows.append([
            d.strftime("%Y-%m-%d"),
            d.strftime("%a"),
            in_s,
            out_s,
            lunch,
            worked,
            reg,
            ot,
            leave,
            note
        ])

    table_w = W - 2 * m
    col_w = [
        table_w * 0.12,  # Date
        table_w * 0.1,  # Day
        table_w * 0.1,  # IN
        table_w * 0.1,  # OUT
        table_w * 0.1,  # Lunch
        table_w * 0.1,  # Worked
        table_w * 0.1,  # Regular
        table_w * 0.1,  # OT
        table_w * 0.1,  # Leave
        table_w * 0.18,  # Note
    ]

    t = Table(rows, colWidths=col_w, repeatRows=1)

    # ---- base style ----
    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),

        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 1), (8, -1), "CENTER"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F9FAFB")]),
    ])

    # ---- highlight weekends (full calendar) ----
    # Body rows start at index 1
    for i, d in enumerate(days, start=1):
        if d.weekday() >= 5:  # Sat/Sun
            style.add("BACKGROUND", (0, i), (-1, i), colors.HexColor("#EFF6FF"))  # light blue

    # If you later want holiday highlighting too, we can add it from DB (like your web page)

    t.setStyle(style)

    tw, th = t.wrapOn(c, table_w, H)
    t.drawOn(c, m, H - m - 40 - th)

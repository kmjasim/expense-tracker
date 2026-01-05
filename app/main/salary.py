# app/routes/salary.py
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from flask import render_template, request, redirect, url_for,jsonify
from flask_login import login_required, current_user
from sqlalchemy import func
from ..extensions import db
from ..models import SalarySettings, WorkLog, Holiday, SalaryAdjust
from . import main
from app.utils.helpers import get_page_title
import calendar
import os
import tempfile
from flask import send_file, request
from . import main
from app.utils.payslip_pdf import build_payslip_pdf

def _uid():
    return current_user.id


# ---------- formatting helpers ----------
def _d(v) -> Decimal:
    return v if isinstance(v, Decimal) else (Decimal(str(v)) if v is not None else Decimal("0"))


def fmt_krw(v: Decimal) -> str:
    try:
        return f"{int(v):,}₩"
    except Exception:
        return "0₩"


def fmt_hours_from_minutes(mins: int) -> str:
    h = Decimal(mins) / Decimal(60)
    # show 0.0, 1.5, 12.0 style
    return f"{h:.1f}"


# ---------- settings ----------
def get_or_create_settings(user_id: int) -> SalarySettings:
    s = (
        SalarySettings.query
        .filter_by(user_id=user_id)
        .order_by(SalarySettings.effective_from.desc(), SalarySettings.id.desc())
        .first()
    )
    if not s:
        s = SalarySettings(user_id=user_id)  # uses your defaults
        db.session.add(s)
        db.session.commit()
    return s


# ---------- date helpers ----------
def month_bounds(year: int, month: int):
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    return start, end


def month_start(d: date) -> date:
    return date(d.year, d.month, 1)

def get_current_settings(user_id: int) -> SalarySettings:
    today = month_start(date.today())
    s = (
        SalarySettings.query
        .filter(SalarySettings.user_id == user_id,
                SalarySettings.effective_from <= today)
        .order_by(SalarySettings.effective_from.desc(), SalarySettings.id.desc())
        .first()
    )
    if not s:
        s = SalarySettings(user_id=user_id, effective_from=today)
        db.session.add(s)
        db.session.commit()
    return s

def get_settings_for_month(user_id: int, year: int, month: int) -> SalarySettings:
    target = date(year, month, 1)
    s = (
        SalarySettings.query
        .filter(SalarySettings.user_id == user_id,
                SalarySettings.effective_from <= target)
        .order_by(SalarySettings.effective_from.desc(), SalarySettings.id.desc())
        .first()
    )
    if not s:
        # fallback: create default effective from target month start
        s = SalarySettings(user_id=user_id, effective_from=target)
        db.session.add(s)
        db.session.commit()
    return s


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5  # Sat=5, Sun=6


def is_holiday(user_id: int, d: date) -> bool:
    return (
        db.session.query(func.count(Holiday.id))
        .filter(Holiday.user_id == user_id, Holiday.holiday_date == d)
        .scalar()
        > 0
    )


def week_start_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


# ---------- work minutes calc ----------
def compute_minutes_for_day(user_id: int, s: SalarySettings, d: date, in_t, out_t, lunch_min: int, full_leave: bool):
    """
    Your rules:
    - If weekend OR holiday: all worked minutes are overtime
    - Else (normal weekday): first 8h regular, extra overtime
    - Full-day leave: minutes = 0 (weekly deduction handled separately)
    """
    if full_leave:
        return 0, 0, 0

    if not in_t or not out_t:
        return 0, 0, 0

    # basic same-day shift only (if you later need overnight, we'll add a toggle)
    dt_in = datetime.combine(d, in_t)
    dt_out = datetime.combine(d, out_t)
    if dt_out <= dt_in:
        # invalid (or overnight) -> treat as 0 for now
        return 0, 0, 0

    total_minutes = int((dt_out - dt_in).total_seconds() // 60)
    total_minutes = max(0, total_minutes - int(lunch_min or 0))

    if total_minutes <= 0:
        return 0, 0, 0

    weekend_or_holiday = is_weekend(d) or is_holiday(user_id, d)

    if weekend_or_holiday:
        return total_minutes, 0, total_minutes

    # weekday: 8h regular cap
    regular_cap = int(Decimal(s.hours_per_day) * 60)  # 8h -> 480 mins
    regular_minutes = min(total_minutes, regular_cap)
    overtime_minutes = max(0, total_minutes - regular_minutes)
    return total_minutes, regular_minutes, overtime_minutes


# ---------- monthly summary ----------
def calc_month_summary(user_id: int, year: int, month: int):
    s = get_settings_for_month(user_id, year, month)
    start, end = month_bounds(year, month)

    logs = (
        WorkLog.query
        .filter(WorkLog.user_id == user_id, WorkLog.work_date >= start, WorkLog.work_date < end)
        .order_by(WorkLog.work_date.desc())
        .all()
    )

    # overtime totals (minutes)
    # overtime totals (minutes) - ONLY weekday overtime (Mon–Fri, non-holiday)
    total_ot_minutes = 0
    for l in logs:
        d = l.work_date
        if is_weekend(d) or is_holiday(user_id, d):
            continue
        if l.is_full_day_leave:
            continue
        total_ot_minutes += int(l.overtime_minutes or 0)


    # weekend/holiday days count (worked > 0 on weekend/holiday)
    weekend_holiday_days = 0
    weekend_holiday_minutes = 0
    for l in logs:
        if (l.worked_minutes or 0) > 0 and (is_weekend(l.work_date) or is_holiday(user_id, l.work_date)):
            weekend_holiday_days += 1
            weekend_holiday_minutes += int(l.worked_minutes or 0)

    # weekly leave + penalty (Mon–Sun, workdays Mon–Fri; skip weekends/holidays)
    # penalty applies ONLY for full-day leave
    leave_days = 0
    penalty_days = 0

    # group leave days by week_start
    week_map = {}
    for l in logs:
        d = l.work_date
        if is_weekend(d) or is_holiday(user_id, d):
            continue
        if l.is_full_day_leave:
            ws = week_start_monday(d)
            week_map.setdefault(ws, 0)
            week_map[ws] += 1

    for ws, L in week_map.items():
        if L > 0:
            leave_days += L
            penalty_days += 1  # your rule: +1 per week when any full-day leave exists

    # money math
    hourly = _d(s.hourly_rate)
    ot_rate = hourly * _d(s.overtime_multiplier)

    overtime_pay = (Decimal(total_ot_minutes) / Decimal(60)) * ot_rate

    # leave deduction = (leave_days + penalty_days) * hours/day * hourly_rate
    hours_per_day = _d(s.hours_per_day)
    leave_deduction = Decimal(leave_days + penalty_days) * hours_per_day * hourly
    # weekday short-hours deduction (worked < 8h)
    short_minutes = 0

    regular_cap = int(Decimal(s.hours_per_day) * 60)  # 8h -> 480 mins

    for l in logs:
        d = l.work_date

        # only weekdays, non-holiday, not full-day leave
        if is_weekend(d) or is_holiday(user_id, d):
            continue
        if l.is_full_day_leave:
            continue

        # must have time entry (otherwise we don't assume anything)
        if not l.in_time or not l.out_time:
            continue

        worked = int(l.worked_minutes or 0)
        if worked < regular_cap:
            short_minutes += (regular_cap - worked)

    short_hours_deduction = (Decimal(short_minutes) / Decimal(60)) * hourly

    # monthly adjustments
    allowance_total = (
        db.session.query(func.coalesce(func.sum(SalaryAdjust.amount), 0))
        .filter_by(user_id=user_id, year=year, month=month, kind="allowance")
        .scalar()
    )
    deduction_total = (
        db.session.query(func.coalesce(func.sum(SalaryAdjust.amount), 0))
        .filter_by(user_id=user_id, year=year, month=month, kind="deduction")
        .scalar()
    )

    allowance_total = _d(allowance_total)
    deduction_total = _d(deduction_total)

    base_salary = _d(s.base_salary)
    weekend_holiday_pay = (Decimal(weekend_holiday_minutes) / Decimal(60)) * ot_rate
    # net = base + OT + allowance - (deductions + leave_deduction)
    gross= base_salary + weekend_holiday_pay + overtime_pay + allowance_total
    net = base_salary + weekend_holiday_pay + overtime_pay + allowance_total - (deduction_total + leave_deduction + short_hours_deduction)


    # For your cards:
    return {
        "settings": s,
        "logs": logs,

        "base_salary": base_salary,

        "overtime_minutes": total_ot_minutes,
        "overtime_hours_str": fmt_hours_from_minutes(total_ot_minutes),
        "overtime_pay": overtime_pay,

        "weekend_holiday_days": weekend_holiday_days,
        "weekend_holiday_minutes": weekend_holiday_minutes,
        "weekend_holiday_hours_str": fmt_hours_from_minutes(weekend_holiday_minutes),
        "weekend_holiday_pay": (Decimal(weekend_holiday_minutes) / Decimal(60)) * ot_rate,  # info card

        "allowance_total": allowance_total,
        "deduction_total": deduction_total,

        "leave_days": leave_days,
        "penalty_days": penalty_days,
        "leave_deduction": leave_deduction + short_hours_deduction,
        "short_minutes": short_minutes,
        "short_hours_deduction": short_hours_deduction,
        "net": net,
        "gross": gross,
    }


# ---------- pages ----------
@main.route("/salary", methods=["GET"])
@login_required
def salary_page():
    today = date.today()
    year = int(request.args.get("year", today.year))
    month = int(request.args.get("month", today.month))

    summary = calc_month_summary(_uid(), year, month)

    start, end = month_bounds(year, month)


    s = summary["settings"]
    adjustments = (
    SalaryAdjust.query
    .filter_by(user_id=_uid(), year=year, month=month)
    .order_by(SalaryAdjust.kind.asc(), SalaryAdjust.id.desc())
    .all()
    )

    gross_additions = summary["overtime_pay"] + summary["allowance_total"] + summary["weekend_holiday_pay"]
    total_deductions = summary["deduction_total"] + summary["leave_deduction"]

    # 1) all days of month
    days = []
    d = start
    while d < end:
        days.append(d)
        d += timedelta(days=1)

    # 2) existing logs -> dict by date
    log_by_date = {l.work_date: l for l in summary["logs"]}

    # 3) holidays as ISO strings (so your template logic stays same)
    holiday_dates = set(
        hd.isoformat() for (hd,) in db.session.query(Holiday.holiday_date)
            .filter(Holiday.user_id == _uid(),
                    Holiday.holiday_date >= start,
                    Holiday.holiday_date < end)
            .all()
    )


    return render_template(
        "salary.html",
        page_title=get_page_title(),
        year=year,
        month=month,
        today=date.today(),
        holiday_dates=holiday_dates,
        gross_additions_str=fmt_krw(gross_additions),
        total_deductions_str=fmt_krw(total_deductions),
        adjustments=adjustments,

        # settings defaults for the form
        default_lunch_minutes=int(s.default_lunch_minutes or 0),

        # card values (formatted)
        base_salary_str=fmt_krw(summary["base_salary"]),
        overtime_pay_str=fmt_krw(summary["overtime_pay"]),
        overtime_hours_str=summary["overtime_hours_str"],

        weekend_holiday_pay_str=fmt_krw(summary["weekend_holiday_pay"]),
        weekend_holiday_days=summary["weekend_holiday_days"],
        weekend_holiday_hours_str=summary["weekend_holiday_hours_str"],

        allowance_str=fmt_krw(summary["allowance_total"]),
        deductions_str=fmt_krw(summary["deduction_total"]),

        leave_total_deduction_str=fmt_krw(summary["leave_deduction"]),
        leave_days=summary["leave_days"],
        penalty_days=summary["penalty_days"],
        short_minutes=summary["short_minutes"],
        short_hours_deduction=summary["short_hours_deduction"],
        net_salary_str=fmt_krw(summary["net"]),
        gross_salary_str=fmt_krw(summary["gross"]),
        days=days,
        log_by_date=log_by_date,
        # table
        logs=summary["logs"],
    )



@main.route("/holidays", methods=["GET"])
@login_required
def holidays_page():
    year = request.args.get("year", type=int) or date.today().year

    start = date(year, 1, 1)
    end = date(year + 1, 1, 1)

    holidays = (
        Holiday.query
        .filter_by(user_id=_uid(), year=year)
        .order_by(Holiday.holiday_date.asc())
        .all()
    )

    return render_template(
        "holidays.html",
        page_title=get_page_title(),
        year=year,
        holidays=holidays,
        today=date.today(),
    )

@main.route("/holidays/add", methods=["POST"])
@login_required
def holiday_add():
    dstr = request.form.get("holiday_date")
    name = (request.form.get("name") or "").strip()
    kind = (request.form.get("kind") or "public").strip()  # 'public'|'company'

    if not dstr or not name:
        return redirect(url_for("main.holidays_page"))

    hdate = datetime.strptime(dstr, "%Y-%m-%d").date()
    yr = hdate.year

    # duplicate check (your unique constraint will also protect, but this avoids errors)
    exists = Holiday.query.filter_by(user_id=_uid(), holiday_date=hdate).first()
    if exists:
        return redirect(url_for("main.holidays_page", year=yr))

    h = Holiday(
        user_id=_uid(),
        holiday_date=hdate,
        name=name,
        kind=kind if kind in ("public", "company") else "public",
        year=yr,
    )
    db.session.add(h)
    db.session.commit()

    return redirect(url_for("main.holidays_page", year=yr))



@main.route("/holidays/<int:hid>/delete", methods=["POST"])
@login_required
def holiday_delete(hid):
    h = db.session.get(Holiday, hid)
    if not h or h.user_id != _uid():
        return redirect(url_for("main.holidays_page"))

    yr = h.holiday_date.year
    db.session.delete(h)
    db.session.commit()
    return redirect(url_for("main.holidays_page", year=yr))



from decimal import Decimal

@main.route("/salary/settings", methods=["GET", "POST"])
@login_required
def salary_settings_page():
    if request.method == "POST":
        def to_int(x, default=0):
            try:
                return int(str(x).strip())
            except Exception:
                return default

        def to_decimal(x, default="0"):
            try:
                return Decimal(str(x).strip())
            except Exception:
                return Decimal(default)

        eff_str = request.form.get("effective_from") or ""
        eff = datetime.strptime(eff_str, "%Y-%m-%d").date() if eff_str else date.today()
        eff = date(eff.year, eff.month, 1)  # force month-start

        base_salary = to_int(request.form.get("base_salary"), 0)
        hourly_rate = to_decimal(request.form.get("hourly_rate"), 0)
        hours_per_day = to_decimal(request.form.get("hours_per_day"), "8")
        overtime_multiplier = to_decimal(request.form.get("overtime_multiplier"), "1.5")
        default_lunch_minutes = to_int(request.form.get("default_lunch_minutes"), 0)

        # clamps
        if base_salary < 0: base_salary = 0
        if hourly_rate < 0: hourly_rate = Decimal("0")
        if hours_per_day <= 0: hours_per_day = Decimal("8")
        if overtime_multiplier <= 0: overtime_multiplier = Decimal("1.5")
        if default_lunch_minutes < 0: default_lunch_minutes = 0

        # ✅ upsert by (user_id, effective_from)
        row = (
            SalarySettings.query
            .filter_by(user_id=_uid(), effective_from=eff)
            .order_by(SalarySettings.id.desc())
            .first()
        )
        if not row:
            row = SalarySettings(user_id=_uid(), effective_from=eff)
            db.session.add(row)

        row.base_salary = base_salary
        row.hourly_rate = hourly_rate
        row.hours_per_day = hours_per_day
        row.overtime_multiplier = overtime_multiplier
        row.default_lunch_minutes = default_lunch_minutes

        db.session.commit()
        return redirect(url_for("main.salary_settings_page"))

    # GET
    current = get_current_settings(_uid())
    history = (
        SalarySettings.query
        .filter_by(user_id=_uid())
        .order_by(SalarySettings.effective_from.desc(), SalarySettings.id.desc())
        .all()
    )

    return render_template(
        "settings.html",
        page_title=get_page_title(),
        settings=current,     # current effective settings
        history=history,      # all versions
        today=date.today(),
    )


# ---------- actions ----------
@main.route("/salary/log", methods=["POST"])
@login_required
def salary_log_save():
    # required date
    d = datetime.strptime(request.form["work_date"], "%Y-%m-%d").date()

    # optional in/out
    in_str = (request.form.get("in_time") or "").strip()
    out_str = (request.form.get("out_time") or "").strip()

    in_t = datetime.strptime(in_str, "%H:%M").time() if in_str else None
    out_t = datetime.strptime(out_str, "%H:%M").time() if out_str else None

    lunch_min = int(request.form.get("lunch_minutes") or 0)
    full_leave = (request.form.get("is_full_day_leave") == "on")
    note = request.form.get("note") or ""

    s = get_or_create_settings(_uid())

    worked, regular, overtime = compute_minutes_for_day(_uid(), s, d, in_t, out_t, lunch_min, full_leave)

    log = WorkLog.query.filter_by(user_id=_uid(), work_date=d).first()
    if not log:
        log = WorkLog(user_id=_uid(), work_date=d)
        db.session.add(log)

    log.in_time = in_t
    log.out_time = out_t
    log.lunch_minutes = lunch_min
    log.is_full_day_leave = full_leave
    log.note = note

    log.worked_minutes = worked
    log.regular_minutes = regular
    log.overtime_minutes = overtime

    db.session.commit()
    return redirect(url_for("main.salary_page", year=d.year, month=d.month))


@main.route("/salary/adjust/add", methods=["POST"])
@login_required
def salary_adjust_add():
    year = int(request.form["year"])
    month = int(request.form["month"])

    kind = (request.form.get("kind") or "").strip()  # allowance|deduction
    label = (request.form.get("label") or "").strip()
    amount_raw = (request.form.get("amount") or "0").strip()

    # basic validation (no flash)
    if kind not in ("allowance", "deduction"):
        return redirect(url_for("main.salary_page", year=year, month=month))

    if not label:
        return redirect(url_for("main.salary_page", year=year, month=month))

    try:
        amount = Decimal(amount_raw)
    except Exception:
        amount = Decimal("0")

    if amount < 0:
        amount = abs(amount)

    adj = SalaryAdjust(
        user_id=_uid(),
        year=year,
        month=month,
        kind=kind,
        label=label,
        amount=amount,
    )
    db.session.add(adj)
    db.session.commit()

    return redirect(url_for("main.salary_page", year=year, month=month))


@main.route("/salary/adjust/<int:adj_id>/delete", methods=["POST"])
@login_required
def salary_adjust_delete(adj_id):
    year = int(request.form["year"])
    month = int(request.form["month"])

    adj = db.session.get(SalaryAdjust, adj_id)
    if not adj or adj.user_id != _uid():
        return redirect(url_for("main.salary_page", year=year, month=month))

    db.session.delete(adj)
    db.session.commit()
    return redirect(url_for("main.salary_page", year=year, month=month))


@main.route("/salary/adjust/copy_prev", methods=["POST"])
@login_required
def salary_adjust_copy_prev():
    year = int(request.form["year"])
    month = int(request.form["month"])

    # previous month calculation
    if month == 1:
        p_year, p_month = year - 1, 12
    else:
        p_year, p_month = year, month - 1

    # get previous month lines
    prev = (
        SalaryAdjust.query
        .filter_by(user_id=_uid(), year=p_year, month=p_month)
        .all()
    )
    if not prev:
        return redirect(url_for("main.salary_page", year=year, month=month))

    # current month existing keys (avoid duplicates)
    existing = (
        SalaryAdjust.query
        .filter_by(user_id=_uid(), year=year, month=month)
        .with_entities(SalaryAdjust.kind, SalaryAdjust.label)
        .all()
    )
    existing_keys = {(k, (lbl or "").strip().lower()) for k, lbl in existing}

    # copy only missing
    created = 0
    for a in prev:
        key = (a.kind, (a.label or "").strip().lower())
        if key in existing_keys:
            continue

        db.session.add(SalaryAdjust(
            user_id=_uid(),
            year=year,
            month=month,
            kind=a.kind,
            label=a.label,
            amount=a.amount,
        ))
        created += 1

    if created:
        db.session.commit()

    return redirect(url_for("main.salary_page", year=year, month=month))
@main.route("/salary/adjust/<int:adj_id>/edit", methods=["POST"])
@login_required
def salary_adjust_edit(adj_id):
    adj = db.session.get(SalaryAdjust, adj_id)
    if not adj or adj.user_id != _uid():
        return {"ok": False}, 404

    kind = (request.form.get("kind") or "").strip()
    label = (request.form.get("label") or "").strip()
    amount_raw = (request.form.get("amount") or "0").strip()

    if kind not in ("allowance", "deduction"):
        return {"ok": False, "error": "invalid kind"}, 400
    if not label:
        return {"ok": False, "error": "empty label"}, 400

    try:
        amount = Decimal(amount_raw)
    except Exception:
        amount = Decimal("0")

    if amount < 0:
        amount = abs(amount)

    adj.kind = kind
    adj.label = label
    adj.amount = amount
    db.session.commit()

    # return formatted values for UI
    return {
        "ok": True,
        "kind": adj.kind,
        "label": adj.label,
        "amount": int(adj.amount),
    }


@main.route("/salary/leave/apply", methods=["POST"])
@login_required
def salary_long_leave_apply():
    start = datetime.strptime(request.form["start_date"], "%Y-%m-%d").date()
    end = datetime.strptime(request.form["end_date"], "%Y-%m-%d").date()
    note = (request.form.get("note") or "").strip()
    overwrite = (request.form.get("overwrite") == "on")

    # safety: swap if user inputs reversed
    if end < start:
        start, end = end, start

    s = get_or_create_settings(_uid())

    # preload holiday dates in range (fast)
    holiday_rows = (
        db.session.query(Holiday.holiday_date)
        .filter(Holiday.user_id == _uid(),
                Holiday.holiday_date >= start,
                Holiday.holiday_date <= end)
        .all()
    )
    holiday_set = {d[0] for d in holiday_rows}

    created = 0
    updated = 0
    skipped = 0

    d = start
    while d <= end:
        # skip weekends
        if d.weekday() >= 5:
            d += timedelta(days=1)
            continue

        # skip holidays
        if d in holiday_set:
            d += timedelta(days=1)
            continue

        log = WorkLog.query.filter_by(user_id=_uid(), work_date=d).first()

        if log and not overwrite:
            # If there is already any record (work or leave), skip
            skipped += 1
            d += timedelta(days=1)
            continue

        if not log:
            log = WorkLog(user_id=_uid(), work_date=d)
            db.session.add(log)
            created += 1
        else:
            updated += 1

        # Mark as full day leave
        log.is_full_day_leave = True
        log.note = note

        # clear times (this is leave)
        log.in_time = None
        log.out_time = None
        log.lunch_minutes = int(s.default_lunch_minutes or 0)

        # computed minutes are zero
        log.worked_minutes = 0
        log.regular_minutes = 0
        log.overtime_minutes = 0

        d += timedelta(days=1)

    db.session.commit()

    # go to month of start (or end) — we'll use start
    return redirect(url_for("main.salary_page", year=start.year, month=start.month,today=date.today()))

@main.route("/salary/leave/remove", methods=["POST"])
@login_required
def salary_long_leave_remove():
    start = datetime.strptime(request.form["start_date"], "%Y-%m-%d").date()
    end = datetime.strptime(request.form["end_date"], "%Y-%m-%d").date()

    if end < start:
        start, end = end, start

    # Only target logs that are marked as full-day leave
    qy = (
        WorkLog.query
        .filter(
            WorkLog.user_id == _uid(),
            WorkLog.work_date >= start,
            WorkLog.work_date <= end,
            WorkLog.is_full_day_leave.is_(True),
        )
    )

    # safest: delete only "pure leave rows" (no in/out and 0 minutes)
    logs = qy.all()
    for log in logs:
        pure_leave = (log.in_time is None and log.out_time is None and (log.worked_minutes or 0) == 0)
        if pure_leave:
            db.session.delete(log)
        else:
            # if somehow mixed data exists, just unmark leave
            log.is_full_day_leave = False

    db.session.commit()
    return redirect(url_for("main.salary_page", year=start.year, month=start.month))



@main.route("/salary/payslip/pdf", methods=["GET"])
@login_required
def salary_payslip_pdf():
    year = request.args.get("year", type=int) or date.today().year
    month = request.args.get("month", type=int) or date.today().month

    summary = calc_month_summary(_uid(), year, month)

    month_name = calendar.month_name[month]
    pay_period = f"{year}-{month:02d}-01 to {year}-{month:02d}-{calendar.monthrange(year, month)[1]}"
    pay_date = f"{year}-{month:02d}-10"  # you can change later if needed

    # Build earnings/deductions for the table
    earnings = [
        ("Base Salary", float(summary["base_salary"])),
        ("Weekday Overtime", float(summary["overtime_pay"])),
        ("Weekend/Holiday", float(summary["weekend_holiday_pay"])),
        ("Allowance", float(summary["allowance_total"])),
    ]
    deductions = [
        ("Deductions", float(summary["deduction_total"])),
        ("Leave Deduction", float(summary["leave_deduction"])),
    ]

    payload = {
        "month_name": month_name,
        "year": year,
        "employee_name": current_user.name if hasattr(current_user, "name") and current_user.name else "Employee",
        "designation": "Employee",  # later we can store in settings/profile
        "pay_period": pay_period,
        "pay_date": pay_date,
        "net_salary": float(summary["net"]),
        "earnings": earnings,
        "deductions": deductions,
    }

    fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    build_payslip_pdf(tmp_path, payload)

    filename = f"payslip_{year}_{month:02d}.pdf"
    return send_file(tmp_path, as_attachment=True, download_name=filename, mimetype="application/pdf")




@main.route("/salary/summary-data", methods=["GET"])
@login_required
def salary_summary_data():
    year = request.args.get("year", type=int) or date.today().year
    month = request.args.get("month", type=int)  # optional

    # Year series: gross & net for each month
    labels = []
    gross_series = []
    net_series = []

    for m in range(1, 13):
        s = calc_month_summary(_uid(), year, m)

        gross = (
            _d(s["base_salary"])
            + _d(s["overtime_pay"])
            + _d(s["weekend_holiday_pay"])
            + _d(s["allowance_total"])
        )
        net = _d(s["net"])

        labels.append(m)
        gross_series.append(float(gross))
        net_series.append(float(net))

    # Selected month summary OR whole year summary
    if month and 1 <= month <= 12:
        s = calc_month_summary(_uid(), year, month)
        gross = (
            _d(s["base_salary"])
            + _d(s["overtime_pay"])
            + _d(s["weekend_holiday_pay"])
            + _d(s["allowance_total"])
        )
        net = _d(s["net"])

        payload = {
            "mode": "month",
            "year": year,
            "month": month,
            "gross": float(gross),
            "net": float(net),
        }
    else:
        payload = {
            "mode": "year",
            "year": year,
            "gross": float(sum(gross_series)),
            "net": float(sum(net_series)),
        }

    return jsonify(
        ok=True,
        year=year,
        labels=labels,
        gross_series=gross_series,
        net_series=net_series,
        summary=payload,
    )


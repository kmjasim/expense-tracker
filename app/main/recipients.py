# app/main/recipients.py
from flask import request, redirect, url_for, jsonify
from flask_login import login_required, current_user
from decimal import Decimal
from ..extensions import db
from ..main import main
from ..models import Recipient, Method, RecipientType, TransactionKRW, TransactionBDT

def _get_recipient_owned(rid: int):
    r = db.session.get(Recipient, rid)
    return r if (r and r.user_id == current_user.id) else None

def _recipient_usage_count(rid: int) -> int:
    # how many txns reference this recipient in both ledgers
    c1 = db.session.query(TransactionKRW).filter(
        TransactionKRW.user_id == current_user.id,
        TransactionKRW.recipient_id == rid
    ).count()
    c2 = db.session.query(TransactionBDT).filter(
        TransactionBDT.user_id == current_user.id,
        TransactionBDT.recipient_id == rid
    ).count()
    return c1 + c2

@main.route("/recipients/new", methods=["POST"], endpoint="recipients_create")
@login_required
def recipients_create():
    uid = current_user.id
    name = (request.form.get("name") or "").strip()
    if not name:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(ok=False, error="Name is required"), 400
        return redirect(url_for("main.transfers_page", warning="Recipient name required"))

    # Enums (safe fallbacks)
    type_str = (request.form.get("type") or "person").strip()
    try:
        rtype = RecipientType(type_str if type_str != "self" else "self_")
    except Exception:
        rtype = RecipientType.person

    method_str = (request.form.get("default_method") or "").strip() or None
    try:
        dmethod = Method(method_str) if method_str else None
    except Exception:
        dmethod = None

    r = Recipient(
        user_id=uid,
        name=name,
        type=rtype,
        country=(request.form.get("country") or "").strip() or None,
        default_method=dmethod,
        default_service_name=(request.form.get("default_service_name") or "").strip() or None,
        default_account_no_masked=(request.form.get("default_account_no_masked") or "").strip() or None,
        notes=(request.form.get("notes") or "").strip() or None,
        is_favorite=bool(request.form.get("is_favorite")),
    )
    db.session.add(r)
    db.session.commit()

    # AJAX?
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(ok=True, id=r.id, name=r.name, acct=r.default_account_no_masked or "")

    # Non-AJAX fallback
    return redirect(url_for("main.transfers_page", success="Recipient added"))


@main.route("/recipients/<int:rid>/update", methods=["POST"], endpoint="recipients_update")
@login_required
def recipients_update(rid):
    r = _get_recipient_owned(rid)
    if not r:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(ok=False, error="Recipient not found"), 404
        return redirect(url_for("main.transfers_page", warning="Recipient not found"))

    name = (request.form.get("name") or "").strip()
    if not name:
        msg = "Name is required"
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(ok=False, error=msg), 400
        return redirect(url_for("main.transfers_page", warning=msg))

    # enums with safe fallbacks
    type_str = (request.form.get("type") or "person").strip()
    try:
        r.type = RecipientType(type_str if type_str != "self" else "self_")
    except Exception:
        r.type = RecipientType.person

    method_str = (request.form.get("default_method") or "").strip() or None
    try:
        r.default_method = Method(method_str) if method_str else None
    except Exception:
        r.default_method = None

    r.name = name
    r.country = (request.form.get("country") or "").strip() or None
    r.default_service_name = (request.form.get("default_service_name") or "").strip() or None
    r.default_account_no_masked = (request.form.get("default_account_no_masked") or "").strip() or None
    r.notes = (request.form.get("notes") or "").strip() or None
    r.is_favorite = bool(request.form.get("is_favorite"))

    db.session.commit()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(ok=True, id=r.id, name=r.name, acct=r.default_account_no_masked or "")
    return redirect(url_for("main.transfers_page", success="Recipient updated"))

@main.route("/recipients/<int:rid>/delete", methods=["POST"], endpoint="recipients_delete")
@login_required
def recipients_delete(rid):
    r = _get_recipient_owned(rid)
    if not r:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(ok=False, error="Recipient not found"), 404
        return redirect(url_for("main.transfers_page", warning="Recipient not found"))

    used = _recipient_usage_count(rid)
    if used > 0:
        msg = "Cannot delete: recipient is used in transactions."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(ok=False, error=msg), 400
        return redirect(url_for("main.transfers_page", warning=msg))

    db.session.delete(r)
    db.session.commit()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(ok=True, id=rid)
    return redirect(url_for("main.transfers_page", success="Recipient deleted"))

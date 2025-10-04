# app/main/categories.py
# ---------------------------------
# Create & delete categories. Ownership checks are enforced.

from flask import request, redirect, url_for
from flask_login import login_required, current_user
from sqlalchemy import func
from urllib.parse import urlencode

from . import main
from ..extensions import db
from ..models import Category

# ---------------------------
# Categories (CREATE)
# ---------------------------
@main.route("/categories/new", methods=["POST"], endpoint="create_category")
@login_required
def create_category():
    name_raw = (request.form.get("name") or "").strip()
    parent_id = request.form.get("parent_id", type=int)

    if not name_raw:
        return redirect(url_for("main.payments_page"))

    # Case-insensitive duplicate check within same parent FOR THIS USER
    exists = (
        db.session.query(Category.id)
        .filter(
            Category.user_id == current_user.id,
            (Category.parent_id == (parent_id or None)),
            func.lower(Category.name) == func.lower(name_raw),
        )
        .first()
    )
    if exists:
        qs = urlencode({"error": "duplicate", "dup_name": name_raw})
        return redirect(f"{url_for('main.payments_page')}?{qs}")

    cat = Category(user_id=current_user.id, name=name_raw, parent_id=parent_id or None)
    db.session.add(cat)
    db.session.commit()
    return redirect(url_for("main.payments_page", category_id=cat.id))


# ---------------------------
# Categories (DELETE)
# ---------------------------
@main.route("/categories/<int:category_id>/delete", methods=["POST"], endpoint="delete_category")
@login_required
def delete_category(category_id: int):
    cat = db.session.get(Category, category_id)
    # owner check
    if not cat or cat.user_id != current_user.id:
        return redirect(url_for("main.payments_page"))

    if cat.children and len(cat.children) > 0:
        # Optional: return a message to UI that child categories exist
        return redirect(url_for("main.payments_page", category_id=category_id))

    # TODO: handle transactions linked to this category if you add FK
    db.session.delete(cat)
    db.session.commit()
    return redirect(url_for("main.payments_page"))

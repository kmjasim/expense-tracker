from flask import render_template, request, redirect, url_for
from flask_login import login_required, current_user, logout_user
from ..extensions import db
from ..models import User
from . import settings



@settings.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """
    Single page handling 3 forms:
    - form=profile   -> update name
    - form=password  -> change password
    - form=delete    -> delete account
    """
    success = {}
    errors = {"profile": {}, "password": {}, "delete": {}}

    if request.method == "POST":
        form_type = request.form.get("form")

        # 1) Update name
        if form_type == "profile":
            name = request.form.get("name", "").strip()
            if not name:
                errors["profile"]["name"] = "Name is required."
            elif len(name) < 2:
                errors["profile"]["name"] = "Name is too short."
            if not errors["profile"]:
                current_user.name = name
                db.session.commit()
                success["profile"] = "Your profile has been updated."

        # 2) Change password
        elif form_type == "password":
            current_pw = request.form.get("current_password", "")
            new_pw = request.form.get("new_password", "")
            new_pw2 = request.form.get("new_password_confirm", "")

            if not current_user.check_password(current_pw):
                errors["password"]["current_password"] = "Current password is incorrect."
            if not new_pw:
                errors["password"]["new_password"] = "New password is required."
            elif len(new_pw) < 6:
                errors["password"]["new_password"] = "Use at least 6 characters."
            if not new_pw2:
                errors["password"]["new_password_confirm"] = "Please confirm the new password."
            elif new_pw != new_pw2:
                errors["password"]["new_password_confirm"] = "Passwords do not match."

            if not errors["password"]:
                current_user.set_password(new_pw)
                db.session.commit()
                success["password"] = "Your password has been changed."

        # 3) Delete account
        elif form_type == "delete":
            confirm = request.form.get("confirm_text", "").strip().lower()
            expected = current_user.email.lower()
            if confirm != expected:
                errors["delete"]["confirm_text"] = "Type your account email exactly to confirm."
            if not errors["delete"]:
                # Remove the user account
                user = User.query.get(current_user.id)
                logout_user()
                db.session.delete(user)
                db.session.commit()
                return redirect(url_for("auth.register"))

    # GET or POST fall-through re-renders the page with messages
    return render_template(
        "settings/profile.html",
        page_title="Settings",
        errors=errors,
        success=success,
    )

# app/main/finance_score_routes.py
from flask import request, jsonify, current_app
from flask_login import login_required, current_user
from app.main import main
from app.services.finance_score import get_finance_score

@main.route("/api/finance-score", methods=["GET"])
@login_required
def api_finance_score():
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    currency = request.args.get("currency")

    # TEMP DEBUG (you can remove later)
    current_app.logger.info(f"[finance-score] user={current_user.id} year={year} month={month} currency={currency}")

    fs = get_finance_score(current_user.id, year=year, month=month, currency=currency)
    return jsonify({"score": fs.score, "label": fs.label, "details": fs.details})

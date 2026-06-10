# app/routes/lotto.py
from datetime import datetime

from flask import render_template, request
from sqlalchemy import func
from sqlalchemy.orm import aliased

from ..extensions import db
from ..main import main
from ..models import LottoDraw, LottoDrawNumber, LottoDrawStats, LottoGame
from app.services.lotto_service import add_draw_from_form, generate_smart_picks
from app.utils.helpers import get_page_title


@main.route("/lotto-analyzer", methods=["GET", "POST"])
def lotto_analyzer():
    message = None
    ok = None
    generated_picks = []

    gen_window_selected = 100
    gen_count_selected = 1

    if request.method == "POST":
        action = (request.form.get("action") or request.form.get("submit") or "").strip()

        # 1) Smart generator
        if action == "generate":
            try:
                gen_count_selected = int(request.form.get("gen_count", "1"))
                gen_window_selected = int(request.form.get("gen_window", "100"))

                # Keep the generator safe from accidental huge requests.
                gen_count_selected = max(1, min(gen_count_selected, 20))
                gen_window_selected = max(10, min(gen_window_selected, 5000))

                generated_picks = generate_smart_picks(
                    game_name="Korea Lotto 6/45",
                    window=gen_window_selected,
                    count=gen_count_selected,
                )
                ok = True
                message = f"Generated {len(generated_picks)} pick set(s)."
            except Exception as e:
                ok = False
                message = f"Generator error: {e}"

        # 2) Manual add draw
        else:
            try:
                round_no = int(request.form.get("round_no", "").strip())
                draw_date = datetime.strptime(
                    request.form.get("draw_date", "").strip(),
                    "%Y-%m-%d",
                ).date()

                numbers_raw = request.form.get("numbers", "")
                bonus_raw = request.form.get("bonus", "").strip()
                bonus = int(bonus_raw) if bonus_raw else None

                res = add_draw_from_form(
                    round_no=round_no,
                    draw_date=draw_date,
                    numbers_raw=numbers_raw,
                    bonus=bonus,
                    game_name="Korea Lotto 6/45",
                )

                ok = res.ok
                message = res.message

            except Exception as e:
                ok = False
                message = f"Invalid input: {e}"

    game = LottoGame.query.filter_by(name="Korea Lotto 6/45").first()

    # --- Quick Analysis Window ---
    selected_window = request.args.get("window", "50").strip()
    try:
        selected_window = int(selected_window)
    except Exception:
        selected_window = 50

    selected_window = max(10, min(selected_window, 5000))

    hot_numbers = []
    cold_numbers = []
    odd_even_dist = {}
    low_high_dist = {}
    top_pairs = []
    analysis_draw_ids = []
    analysis_draw_count = 0
    total_draws = 0
    latest_draw = None

    if game:
        total_draws = (
            db.session.query(func.count(LottoDraw.id))
            .filter(LottoDraw.game_id == game.id)
            .scalar()
            or 0
        )

        latest_draw = (
            LottoDraw.query
            .filter(LottoDraw.game_id == game.id)
            .order_by(LottoDraw.round_no.desc())
            .first()
        )

        analysis_draw_ids = [
            x[0]
            for x in (
                db.session.query(LottoDraw.id)
                .filter(LottoDraw.game_id == game.id)
                .order_by(LottoDraw.round_no.desc())
                .limit(selected_window)
                .all()
            )
        ]
        analysis_draw_count = len(analysis_draw_ids)

    if analysis_draw_ids:
        # Hot numbers: top 10 from main numbers only, excluding bonus.
        hot_numbers = (
            db.session.query(
                LottoDrawNumber.num.label("num"),
                func.count(LottoDrawNumber.num).label("cnt"),
            )
            .filter(
                LottoDrawNumber.draw_id.in_(analysis_draw_ids),
                LottoDrawNumber.is_bonus.is_(False),
            )
            .group_by(LottoDrawNumber.num)
            .order_by(func.count(LottoDrawNumber.num).desc(), LottoDrawNumber.num.asc())
            .limit(10)
            .all()
        )

        # Cold numbers: bottom 10 from main numbers only, excluding bonus.
        cold_numbers = (
            db.session.query(
                LottoDrawNumber.num.label("num"),
                func.count(LottoDrawNumber.num).label("cnt"),
            )
            .filter(
                LottoDrawNumber.draw_id.in_(analysis_draw_ids),
                LottoDrawNumber.is_bonus.is_(False),
            )
            .group_by(LottoDrawNumber.num)
            .order_by(func.count(LottoDrawNumber.num).asc(), LottoDrawNumber.num.asc())
            .limit(10)
            .all()
        )

        # Top pairs: main numbers only, within selected window.
        n1 = aliased(LottoDrawNumber)
        n2 = aliased(LottoDrawNumber)

        top_pairs = (
            db.session.query(
                n1.num.label("a"),
                n2.num.label("b"),
                func.count(func.distinct(n1.draw_id)).label("cnt"),
            )
            .filter(
                n1.draw_id.in_(analysis_draw_ids),
                n2.draw_id.in_(analysis_draw_ids),
                n1.draw_id == n2.draw_id,
                n1.is_bonus.is_(False),
                n2.is_bonus.is_(False),
                n1.num < n2.num,
            )
            .group_by(n1.num, n2.num)
            .order_by(
                func.count(func.distinct(n1.draw_id)).desc(),
                n1.num.asc(),
                n2.num.asc(),
            )
            .limit(20)
            .all()
        )

        stats_rows = (
            db.session.query(LottoDrawStats)
            .filter(LottoDrawStats.draw_id.in_(analysis_draw_ids))
            .all()
        )

        for s in stats_rows:
            odd_even_key = f"{s.odd_count}/{s.even_count}"
            odd_even_dist[odd_even_key] = odd_even_dist.get(odd_even_key, 0) + 1

            low_high_key = f"{s.low_count}/{s.high_count}"
            low_high_dist[low_high_key] = low_high_dist.get(low_high_key, 0) + 1

    # Sort distribution dictionaries by highest count first for a cleaner UI.
    odd_even_dist = dict(sorted(odd_even_dist.items(), key=lambda item: item[1], reverse=True))
    low_high_dist = dict(sorted(low_high_dist.items(), key=lambda item: item[1], reverse=True))

    draws = []
    if game:
        draws = (
            db.session.query(LottoDraw, LottoDrawStats)
            .outerjoin(LottoDrawStats, LottoDrawStats.draw_id == LottoDraw.id)
            .filter(LottoDraw.game_id == game.id)
            .order_by(LottoDraw.round_no.desc())
            .limit(30)
            .all()
        )

    draw_ids = [draw.id for draw, _ in draws]
    numbers_map = {draw_id: [] for draw_id in draw_ids}
    bonus_map = {draw_id: None for draw_id in draw_ids}

    if draw_ids:
        rows = (
            LottoDrawNumber.query
            .filter(LottoDrawNumber.draw_id.in_(draw_ids))
            .order_by(
                LottoDrawNumber.draw_id.asc(),
                LottoDrawNumber.is_bonus.asc(),
                LottoDrawNumber.num.asc(),
            )
            .all()
        )

        for row in rows:
            if row.is_bonus:
                bonus_map[row.draw_id] = row.num
            else:
                numbers_map[row.draw_id].append(row.num)

    return render_template(
        "lotto/lotto_analyzer.html",
        ok=ok,
        message=message,
        draws=draws,
        numbers_map=numbers_map,
        bonus_map=bonus_map,
        top_pairs=top_pairs,
        selected_window=selected_window,
        hot_numbers=hot_numbers,
        cold_numbers=cold_numbers,
        odd_even_dist=odd_even_dist,
        low_high_dist=low_high_dist,
        generated_picks=generated_picks,
        gen_window_selected=gen_window_selected,
        gen_count_selected=gen_count_selected,
        total_draws=total_draws,
        latest_draw=latest_draw,
        analysis_draw_count=analysis_draw_count,
        page_title=get_page_title(),
    )

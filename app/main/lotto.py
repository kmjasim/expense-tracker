# app/routes/lotto.py
from sqlalchemy import func
from sqlalchemy.orm import aliased
from datetime import datetime
from flask import render_template, request
from ..extensions import db
from ..models import LottoDraw, LottoDrawStats, LottoDrawNumber, LottoGame
from app.services.lotto_service import add_draw_from_form, generate_smart_picks
from ..main import main
from app.utils.helpers import get_page_title
@main.route("/lotto-analyzer", methods=["GET", "POST"])
def lotto_analyzer():
    message = None
    ok = None
    generated_picks = []

    if request.method == "POST":
        action = (request.form.get("action") or request.form.get("submit") or "").strip()


        # 1) Generator
        if action == "generate":
            try:
                gen_count = int(request.form.get("gen_count", "1"))
                gen_window = int(request.form.get("gen_window", "100"))
                generated_picks = generate_smart_picks(
                    game_name="Korea Lotto 6/45",
                    window=gen_window,
                    count=gen_count,
                )
                ok = True
                message = f"Generated {len(generated_picks)} pick set(s)."
            except Exception as e:
                ok = False
                message = f"Generator error: {e}"

        # 2) Manual add draw (your existing logic)
        else:
            try:
                round_no = int(request.form.get("round_no", "").strip())
                draw_date = datetime.strptime(
                    request.form.get("draw_date", "").strip(),
                    "%Y-%m-%d"
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
        # --- Quick Analysis (Hot/Cold + Distributions) ---
    selected_window = request.args.get("window", "50").strip()
    try:
        selected_window = int(selected_window)
    except:
        selected_window = 50

    # clamp to sane values
    if selected_window < 10:
        selected_window = 10
    if selected_window > 5000:
        selected_window = 5000

    hot_numbers = []
    cold_numbers = []
    odd_even_dist = {}
    low_high_dist = {}
    top_pairs = []

    analysis_draw_ids = []
    if game:
        analysis_draw_ids = [
            x[0] for x in (
                db.session.query(LottoDraw.id)
                .filter(LottoDraw.game_id == game.id)
                .order_by(LottoDraw.round_no.desc())
                .limit(selected_window)
                .all()
            )
        ]

    if analysis_draw_ids:
        # Hot numbers (top 10) from main numbers only (exclude bonus)
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

        # Cold numbers (bottom 10)
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
                # Top Pairs (main numbers only, within selected window)
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

                # ensure pair is unique (a < b)
                n1.num < n2.num,
            )
            .group_by(n1.num, n2.num)
            .order_by(func.count(func.distinct(n1.draw_id)).desc(), n1.num.asc(), n2.num.asc())
            .limit(20)
            .all()
        )

        # Distributions from stats
        stats_rows = (
            db.session.query(LottoDrawStats)
            .filter(LottoDrawStats.draw_id.in_(analysis_draw_ids))
            .all()
        )

        for s in stats_rows:
            k1 = f"{s.odd_count}/{s.even_count}"
            odd_even_dist[k1] = odd_even_dist.get(k1, 0) + 1

            k2 = f"{s.low_count}/{s.high_count}"
            low_high_dist[k2] = low_high_dist.get(k2, 0) + 1

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

    draw_ids = [d.id for d, _ in draws]
    numbers_map = {did: [] for did in draw_ids}
    bonus_map = {did: None for did in draw_ids}

    if draw_ids:
        rows = (
            LottoDrawNumber.query
            .filter(LottoDrawNumber.draw_id.in_(draw_ids))
            .order_by(
                LottoDrawNumber.draw_id.asc(),
                LottoDrawNumber.is_bonus.asc(),
                LottoDrawNumber.num.asc()
            )
            .all()
        )

        for r in rows:
            if r.is_bonus:
                bonus_map[r.draw_id] = r.num
            else:
                numbers_map[r.draw_id].append(r.num)

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

        page_title=get_page_title(),

    )

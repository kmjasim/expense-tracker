# seed_lotto_from_csv.py

from __future__ import annotations
import os

# Force DB url for this process (optional; safest for scripts)
# If you already set DATABASE_URL in PowerShell, you can skip this.
# os.environ["DATABASE_URL"] = "postgresql://... ?sslmode=require"

# Force environment selection used by your create_app (if you use it)
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("FLASK_ENV", "development")


import csv
from datetime import date, timedelta, datetime
from typing import List, Optional, Tuple

from app import create_app, db
from app.models import LottoGame, LottoDraw, LottoDrawNumber, LottoDrawStats
from app.services.lotto_service import compute_draw_stats


# ✅ Put CSV in your project root and set this filename
CSV_PATH = "로또 회차별 당첨번호_20260104234503 1(로또 회차별 당첨번호).csv"

GAME_NAME = "Korea Lotto 6/45"
ROUND1_DATE = date(2002, 12, 7)   # Korea Lotto round 1 date (Saturday)
COMMIT_EVERY = 50


def round_to_date(round_no: int) -> date:
    return ROUND1_DATE + timedelta(days=(round_no - 1) * 7)


def _to_int(x) -> int:
    # Handles "  1", "1.0", etc.
    s = str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return int(s)


def _pick(row: dict, keys: List[str]) -> Optional[str]:
    """Return first existing key value (non-empty) from row."""
    for k in keys:
        if k in row and str(row[k]).strip() != "":
            return row[k]
    return None


def _read_rows() -> List[Tuple[int, List[int], int]]:
    """
    Robust CSV reader for Korean exports (often CP949/EUC-KR).
    Expected column order usually:
      0: 회차
      1: 추첨일 (optional)
      2..7: 6 winning numbers
      8: bonus
    We will not depend on header names; we will use column positions.
    """

    encodings_to_try = ["utf-8-sig", "cp949", "euc-kr"]

    last_err = None
    for enc in encodings_to_try:
        try:
            with open(CSV_PATH, "r", encoding=enc, newline="") as f:
                reader = csv.reader(f)
                header = next(reader)  # skip header row
                # print(header)  # you can uncomment once to see it

                rows: List[Tuple[int, List[int], int]] = []
                for line_no, row in enumerate(reader, start=2):
                    # skip empty lines
                    if not row or all(str(x).strip() == "" for x in row):
                        continue

                    # Make sure row has enough columns
                    # We need at least: 0(round) + 6 nums + bonus(8) -> 9 columns
                    if len(row) < 9:
                        raise RuntimeError(f"Row {line_no} has too few columns: {len(row)} -> {row}")

                    round_no = _to_int(row[0])

                    # numbers are usually at indices 2..7
                    nums = [_to_int(row[2]), _to_int(row[3]), _to_int(row[4]), _to_int(row[5]), _to_int(row[6]), _to_int(row[7])]
                    bonus = _to_int(row[8])

                    rows.append((round_no, nums, bonus))

                return rows

        except Exception as e:
            last_err = e

    raise RuntimeError(f"Failed to read CSV with encodings {encodings_to_try}. Last error: {last_err}")

def _ensure_game() -> LottoGame:
    game = LottoGame.query.filter_by(name=GAME_NAME).first()
    if game:
        return game

    game = LottoGame(
        name=GAME_NAME,
        numbers_per_draw=6,
        min_num=1,
        max_num=45,
        has_bonus=True,
        low_high_split=22,
    )
    db.session.add(game)
    db.session.commit()
    print(f"✅ Created game row: {GAME_NAME}")
    return game


def seed():
    app = create_app()
    with app.app_context():
        game = _ensure_game()

        data = _read_rows()
        data.sort(key=lambda x: x[0])  # oldest → newest (important)

        total = len(data)
        print(f"📥 Loaded {total} draws from CSV. Seeding into DB...")

        inserted = 0
        skipped = 0

        for i, (round_no, numbers, bonus) in enumerate(data, start=1):
            # skip if already exists
            existing = LottoDraw.query.filter_by(game_id=game.id, round_no=round_no).first()
            if existing:
                skipped += 1
                continue

            draw = LottoDraw(
                game_id=game.id,
                round_no=round_no,
                draw_date=round_to_date(round_no),  # generated
                bonus=bonus,
                source="seed_csv",
            )
            db.session.add(draw)
            db.session.flush()  # get draw.id

            nums_sorted = sorted(numbers)

            # Insert 6 main numbers
            for pos, n in enumerate(nums_sorted, start=1):
                db.session.add(
                    LottoDrawNumber(
                        draw_id=draw.id,
                        num=n,
                        position=pos,
                        is_bonus=False,
                    )
                )

            # Insert bonus as normalized row
            db.session.add(
                LottoDrawNumber(
                    draw_id=draw.id,
                    num=bonus,
                    position=None,
                    is_bonus=True,
                )
            )

            # Previous draws for repeat metrics (prev1, prev2)
            prev_draws = (
                LottoDraw.query
                .filter(LottoDraw.game_id == game.id, LottoDraw.round_no < round_no)
                .order_by(LottoDraw.round_no.desc())
                .limit(2)
                .all()
            )

            prev1 = None
            prev2 = None
            if len(prev_draws) > 0:
                prev1 = [x.num for x in LottoDrawNumber.query.filter_by(draw_id=prev_draws[0].id, is_bonus=False).all()]
            if len(prev_draws) > 1:
                prev2 = [x.num for x in LottoDrawNumber.query.filter_by(draw_id=prev_draws[1].id, is_bonus=False).all()]

            stats_data = compute_draw_stats(nums_sorted, game, prev1=prev1, prev2=prev2)
            db.session.add(LottoDrawStats(draw_id=draw.id, **stats_data))

            inserted += 1

            if inserted > 0 and inserted % COMMIT_EVERY == 0:
                db.session.commit()
                print(f"✅ Inserted {inserted} draws... ({i}/{total} processed)")

        db.session.commit()
        print(f"🎉 Done. Inserted={inserted}, Skipped(existing)={skipped}, Total={total}")


if __name__ == "__main__":
    seed()

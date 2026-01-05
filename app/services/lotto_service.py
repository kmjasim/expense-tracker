# app/services/lotto_service.py
from __future__ import annotations
import random

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple
from datetime import date

from sqlalchemy import and_, func
from ..extensions import db
from ..models import LottoGame, LottoDraw, LottoDrawNumber, LottoDrawStats


@dataclass
class LottoAddResult:
    ok: bool
    message: str
    draw_id: Optional[int] = None


def _parse_numbers_csv(raw: str) -> List[int]:
    # Accept: "1,2,3,4,5,6" or "1 2 3 4 5 6"
    raw = (raw or "").strip()
    if not raw:
        return []
    raw = raw.replace("\n", " ").replace("\t", " ")
    parts = []
    for chunk in raw.split(","):
        parts.extend(chunk.split())
    nums = []
    for p in parts:
        if p.strip():
            nums.append(int(p.strip()))
    return nums


def _validate_numbers(numbers: Sequence[int], bonus: Optional[int], game: LottoGame) -> Tuple[bool, str]:
    if len(numbers) != game.numbers_per_draw:
        return False, f"Need exactly {game.numbers_per_draw} numbers."

    if len(set(numbers)) != len(numbers):
        return False, "Numbers must be unique."

    for n in numbers:
        if n < game.min_num or n > game.max_num:
            return False, f"All numbers must be between {game.min_num} and {game.max_num}."

    if game.has_bonus:
        if bonus is None:
            return False, "Bonus is required for this game."
        if bonus < game.min_num or bonus > game.max_num:
            return False, f"Bonus must be between {game.min_num} and {game.max_num}."
        if bonus in set(numbers):
            return False, "Bonus must be different from the 6 numbers."
    return True, "OK"


def _consecutive_metrics(sorted_nums: List[int]) -> Tuple[int, int]:
    """
    Returns:
      consecutive_pairs_count: count of i where nums[i+1] == nums[i] + 1
      max_consecutive_run: length of longest consecutive run (e.g. 3 for 21,22,23)
    """
    pairs = 0
    max_run = 1
    current_run = 1
    for i in range(len(sorted_nums) - 1):
        if sorted_nums[i + 1] == sorted_nums[i] + 1:
            pairs += 1
            current_run += 1
            max_run = max(max_run, current_run)
        else:
            current_run = 1
    return pairs, max_run


def _gap_metrics(sorted_nums: List[int]) -> Tuple[float, int, int]:
    gaps = [sorted_nums[i + 1] - sorted_nums[i] for i in range(len(sorted_nums) - 1)]
    if not gaps:
        return 0.0, 0, 0
    avg_gap = sum(gaps) / len(gaps)
    return float(avg_gap), min(gaps), max(gaps)


def compute_draw_stats(
    numbers: Sequence[int],
    game: LottoGame,
    prev1: Optional[Sequence[int]] = None,
    prev2: Optional[Sequence[int]] = None,
) -> dict:
    nums = sorted(numbers)
    sum_total = sum(nums)
    odd_count = sum(1 for n in nums if n % 2 == 1)
    even_count = len(nums) - odd_count

    low_count = sum(1 for n in nums if n <= game.low_high_split)
    high_count = len(nums) - low_count

    range_span = nums[-1] - nums[0]
    avg_gap, min_gap, max_gap = _gap_metrics(nums)
    consecutive_pairs_count, max_consecutive_run = _consecutive_metrics(nums)

    repeat_from_prev1 = None
    repeat_from_prev2 = None
    s = set(nums)
    if prev1:
        repeat_from_prev1 = len(s.intersection(set(prev1)))
    if prev2:
        repeat_from_prev2 = len(s.intersection(set(prev2)))

    return dict(
        sum_total=sum_total,
        odd_count=odd_count,
        even_count=even_count,
        low_count=low_count,
        high_count=high_count,
        range_span=range_span,
        avg_gap=avg_gap,
        min_gap=min_gap,
        max_gap=max_gap,
        consecutive_pairs_count=consecutive_pairs_count,
        max_consecutive_run=max_consecutive_run,
        repeat_from_prev1=repeat_from_prev1,
        repeat_from_prev2=repeat_from_prev2,
    )


def _get_prev_draw_numbers(game_id: int, round_no: int, k: int = 2) -> List[List[int]]:
    """
    Returns up to k previous draws' main numbers as list of lists, most recent first.
    """
    prev_draws = (
        LottoDraw.query
        .filter(and_(LottoDraw.game_id == game_id, LottoDraw.round_no < round_no))
        .order_by(LottoDraw.round_no.desc())
        .limit(k)
        .all()
    )

    results: List[List[int]] = []
    for d in prev_draws:
        nums = (
            LottoDrawNumber.query
            .filter_by(draw_id=d.id, is_bonus=False)
            .order_by(LottoDrawNumber.position.asc().nulls_last(), LottoDrawNumber.num.asc())
            .all()
        )
        results.append([x.num for x in nums])
    return results


def add_draw_from_form(
    round_no: int,
    draw_date: date,
    numbers_raw: str,
    bonus: Optional[int],
    game_name: str = "Korea Lotto 6/45",
) -> LottoAddResult:
    game = LottoGame.query.filter_by(name=game_name).first()
    if not game:
        return LottoAddResult(False, f"Game '{game_name}' not found. Seed it in lotto_game first.")

    numbers = _parse_numbers_csv(numbers_raw)
    ok, msg = _validate_numbers(numbers, bonus, game)
    if not ok:
        return LottoAddResult(False, msg)

    existing = LottoDraw.query.filter_by(game_id=game.id, round_no=round_no).first()
    if existing:
        return LottoAddResult(False, f"Round {round_no} already exists.")

    # Insert draw
    draw = LottoDraw(
        game_id=game.id,
        round_no=round_no,
        draw_date=draw_date,
        bonus=bonus,
        source="manual",
    )
    db.session.add(draw)
    db.session.flush()  # get draw.id without committing yet

    # Insert main numbers
    nums_sorted = sorted(numbers)
    for idx, n in enumerate(nums_sorted, start=1):
        db.session.add(LottoDrawNumber(draw_id=draw.id, num=n, position=idx, is_bonus=False))

    # Insert bonus row (normalized) if game has bonus
    if game.has_bonus and bonus is not None:
        db.session.add(LottoDrawNumber(draw_id=draw.id, num=int(bonus), position=None, is_bonus=True))

    # Compute stats using previous draws
    prevs = _get_prev_draw_numbers(game.id, round_no, k=2)
    prev1 = prevs[0] if len(prevs) > 0 else None
    prev2 = prevs[1] if len(prevs) > 1 else None

    stats_dict = compute_draw_stats(nums_sorted, game, prev1=prev1, prev2=prev2)
    stats = LottoDrawStats(draw_id=draw.id, **stats_dict)
    db.session.add(stats)

    db.session.commit()
    return LottoAddResult(True, "Saved successfully.", draw_id=draw.id)


def _latest_draw_ids(game_id: int, window: int) -> list[int]:
    rows = (db.session.query(LottoDraw.id)
            .filter(LottoDraw.game_id == game_id)
            .order_by(LottoDraw.round_no.desc())
            .limit(window)
            .all())
    return [r[0] for r in rows]

def _num_counts(draw_ids: list[int]) -> dict[int, int]:
    # counts for main numbers only
    rows = (db.session.query(LottoDrawNumber.num, func.count(LottoDrawNumber.num))
            .filter(LottoDrawNumber.draw_id.in_(draw_ids),
                    LottoDrawNumber.is_bonus.is_(False))
            .group_by(LottoDrawNumber.num)
            .all())
    return {int(n): int(c) for n, c in rows}

def _pair_counts(draw_ids: list[int]) -> dict[tuple[int, int], int]:
    # compute pair counts in python to keep code simple and reliable
    # (window max ~200 keeps it fast)
    nums_by_draw = {}
    rows = (db.session.query(LottoDrawNumber.draw_id, LottoDrawNumber.num)
            .filter(LottoDrawNumber.draw_id.in_(draw_ids),
                    LottoDrawNumber.is_bonus.is_(False))
            .all())
    for did, n in rows:
        nums_by_draw.setdefault(did, []).append(int(n))

    pair_cnt = {}
    for nums in nums_by_draw.values():
        nums = sorted(nums)
        for i in range(len(nums)):
            for j in range(i + 1, len(nums)):
                pair = (nums[i], nums[j])
                pair_cnt[pair] = pair_cnt.get(pair, 0) + 1
    return pair_cnt

def _stats_targets(draw_ids: list[int]) -> dict:
    # derive typical ranges from stats for the window
    stats = (db.session.query(LottoDrawStats)
             .filter(LottoDrawStats.draw_id.in_(draw_ids))
             .all())
    if not stats:
        # sane defaults
        return {
            "sum_min": 90, "sum_max": 180,
            "max_consecutive_run_max": 2,
            "consecutive_pairs_max": 1,
            "odd_even_allowed": {(3,3), (2,4), (4,2)},
            "low_high_allowed": {(3,3), (2,4), (4,2)},
        }

    sums = sorted([s.sum_total for s in stats])
    # use percentile-ish band (avoid extremes)
    lo = sums[int(len(sums) * 0.15)]
    hi = sums[int(len(sums) * 0.85)]

    odd_even = {}
    low_high = {}
    for s in stats:
        odd_even[(s.odd_count, s.even_count)] = odd_even.get((s.odd_count, s.even_count), 0) + 1
        low_high[(s.low_count, s.high_count)] = low_high.get((s.low_count, s.high_count), 0) + 1

    # take top 3 most common patterns
    oe_allowed = set([k for k, _ in sorted(odd_even.items(), key=lambda x: x[1], reverse=True)[:3]])
    lh_allowed = set([k for k, _ in sorted(low_high.items(), key=lambda x: x[1], reverse=True)[:3]])

    return {
        "sum_min": int(lo),
        "sum_max": int(hi),
        "max_consecutive_run_max": 2,      # typical
        "consecutive_pairs_max": 1,        # typical
        "odd_even_allowed": oe_allowed,
        "low_high_allowed": lh_allowed,
    }

def _compute_basic_features(nums: list[int], low_split: int = 22) -> dict:
    nums = sorted(nums)
    s = sum(nums)
    odd = sum(1 for x in nums if x % 2 == 1)
    even = 6 - odd
    low = sum(1 for x in nums if x <= low_split)
    high = 6 - low

    # consecutive pairs + max run
    consec_pairs = 0
    max_run = 1
    run = 1
    for i in range(1, len(nums)):
        if nums[i] == nums[i-1] + 1:
            consec_pairs += 1
            run += 1
            max_run = max(max_run, run)
        else:
            run = 1

    return {
        "sum_total": s,
        "odd": odd, "even": even,
        "low": low, "high": high,
        "consec_pairs": consec_pairs,
        "max_run": max_run,
    }

def _weighted_sample_without_replacement(items: list[int], weights: list[float], k: int) -> list[int]:
    # simple roulette wheel sampling without replacement
    chosen = []
    pool = items[:]
    w = weights[:]
    for _ in range(k):
        total = sum(w)
        r = random.uniform(0, total)
        upto = 0.0
        idx = 0
        for i, wi in enumerate(w):
            upto += wi
            if upto >= r:
                idx = i
                break
        chosen.append(pool[idx])
        pool.pop(idx)
        w.pop(idx)
    return chosen

def generate_smart_picks(game_name: str, window: int = 100, count: int = 3, seed: int | None = None) -> list[dict]:
    """
    Returns list of dicts:
      {"numbers":[...], "score": float, "explain": {...}}
    """
    if seed is not None:
        random.seed(seed)

    game = LottoGame.query.filter_by(name=game_name).first()
    if not game:
        raise RuntimeError(f"Game not found: {game_name}")

    count = max(1, min(int(count), 5))
    window = max(20, min(int(window), 500))

    draw_ids = _latest_draw_ids(game.id, window)
    if not draw_ids:
        return []

    num_cnt = _num_counts(draw_ids)
    pair_cnt = _pair_counts(draw_ids)
    targets = _stats_targets(draw_ids)

    # Build weights for 1..45
    nums = list(range(game.min_num, game.max_num + 1))
    # base weight = 1
    weights = []
    max_c = max(num_cnt.values()) if num_cnt else 1

    for n in nums:
        c = num_cnt.get(n, 0)
        # hotness weight: 1.0 .. ~2.5
        hot_w = 1.0 + (c / max_c) * 1.5
        # tiny bonus for mid numbers (avoid extreme bias) (optional)
        weights.append(hot_w)

    results = []
    attempts = 0

    while len(results) < count and attempts < 2000:
        attempts += 1

        pick = sorted(_weighted_sample_without_replacement(nums, weights, 6))
        feat = _compute_basic_features(pick, low_split=game.low_high_split)

        # Hard rules from window stats
        if not (targets["sum_min"] <= feat["sum_total"] <= targets["sum_max"]):
            continue
        if (feat["odd"], feat["even"]) not in targets["odd_even_allowed"]:
            continue
        if (feat["low"], feat["high"]) not in targets["low_high_allowed"]:
            continue
        if feat["max_run"] > targets["max_consecutive_run_max"]:
            continue
        if feat["consec_pairs"] > targets["consecutive_pairs_max"]:
            continue

        # Scoring: hotness + pair synergy
        hot_score = sum(num_cnt.get(n, 0) for n in pick) / (window * 6)  # normalized-ish
        synergy = 0
        for i in range(6):
            for j in range(i+1, 6):
                synergy += pair_cnt.get((pick[i], pick[j]), 0)

        score = (hot_score * 10.0) + (synergy / max(1, window))  # simple combined score

        results.append({
            "numbers": pick,
            "score": round(float(score), 4),
            "explain": {
                "sum": feat["sum_total"],
                "odd_even": f"{feat['odd']}/{feat['even']}",
                "low_high": f"{feat['low']}/{feat['high']}",
                "synergy": int(synergy),
                "window": window
            }
        })

    # sort best first
    results.sort(key=lambda x: x["score"], reverse=True)
    return results

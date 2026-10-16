"""Projection model: what each player is likely to score in this league over the next
three seasons, and how far above a replaceable player that is.

Pieces (all fit from nflverse history, so they follow the data as seasons are added):
  age curves   — year-over-year change in points per game by position and age
                 (delta method), shrunk toward a sensible prior where samples are thin
  draft prior  — what players drafted in a pick range typically score in NFL years 1-3,
                 used to fill in for rookies and players with little history
  talent       — recent points per game (blended with expected points), age-adjusted,
                 weighted by recency and games, with the draft prior as a pseudo-sample
  model value  — sum over rest-of-season + next 3 seasons of games x expected PPG above
                 replacement (projection treated as a range, wider for young players and
                 further-out seasons, so upside counts), discounted by year

Writes: age_curve, draft_prior, player_projection.
"""

from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from .store import Store

log = logging.getLogger(__name__)

POSITIONS = ("QB", "RB", "WR", "TE")
AGE_MIN, AGE_MAX = 21, 38

# Prior year-over-year PPG multiplier from age a to a+1 (common dynasty aging shapes).
PRIOR_CURVE = {
    "QB": [(24, 1.08), (27, 1.04), (32, 1.00), (35, 0.95), (99, 0.88)],
    "RB": [(22, 1.10), (24, 1.03), (25, 1.00), (26, 0.96), (27, 0.92), (28, 0.88), (99, 0.82)],
    "WR": [(22, 1.12), (24, 1.08), (26, 1.02), (28, 0.98), (29, 0.94), (30, 0.91), (99, 0.86)],
    "TE": [(23, 1.15), (25, 1.10), (27, 1.03), (29, 0.98), (30, 0.93), (99, 0.88)],
}
PICK_BUCKETS = [(12, "1-12"), (32, "13-32"), (64, "33-64"), (100, "65-100"), (160, "101-160"), (999, "161+")]


@dataclass
class ModelConfig:
    min_games_curve: int = 6
    curve_shrink_games: float = 60.0      # games of evidence needed to move halfway off the prior
    recency: tuple = (1.0, 0.75, 0.5)     # weights for current, last, 2 seasons ago
    actual_vs_expected: float = 0.75      # PPG blend: actual vs expected fantasy points
    prior_games: float = 6.0              # pseudo-games given to the draft prior
    availability: float = 0.85            # share of games a player is expected to play
    season_games: int = 17
    discounts: tuple = (1.0, 0.9, 0.75, 0.6)  # rest-of-season, +1, +2, +3 seasons
    replacement_depth: float = 1.5        # replacement = starters x this, per position
    teams: int = 12
    extra: dict = field(default_factory=dict)


def prior_multiplier(pos: str, age: int) -> float:
    for upto, m in PRIOR_CURVE.get(pos, [(99, 1.0)]):
        if age <= upto:
            return m
    return 1.0


def pick_bucket(pick: int | None) -> str:
    if not pick:
        return "UDFA"
    for upto, name in PICK_BUCKETS:
        if pick <= upto:
            return name
    return "UDFA"


def _num(v):
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _date(v) -> date | None:
    if not v:
        return None
    try:
        y, m, d = (int(x) for x in str(v)[:10].split("-"))
        return date(y, m, d)
    except ValueError:
        return None


def age_on(bd: date | None, season: int) -> float | None:
    if not bd:
        return None
    return (date(season, 9, 1) - bd).days / 365.25


def _exists(store: Store, t: str) -> bool:
    return bool(store.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)))


def _cols(store: Store, t: str) -> set[str]:
    return {r["name"] for r in store.query(f"PRAGMA table_info({t})")}


# ---------------------------------------------------------------------------
# Age curves
# ---------------------------------------------------------------------------
def fit_age_curves(seasons: list[dict], birth: dict, current: int, cfg: ModelConfig) -> dict:
    """{pos: {age: multiplier for age -> age+1}} via the delta method, shrunk to the prior."""
    by_player = defaultdict(dict)
    for s in seasons:
        by_player[s["gsis_id"]][s["season"]] = s
    sums = defaultdict(lambda: [0.0, 0.0])  # (pos, age) -> [weighted log ratio, weight]
    for g, ss in by_player.items():
        bd = birth.get(g)
        for yr, s0 in ss.items():
            s1 = ss.get(yr + 1)
            if not s1 or yr + 1 >= current:  # current season is incomplete
                continue
            if s0["games"] < cfg.min_games_curve or s1["games"] < cfg.min_games_curve:
                continue
            pos = s0["position"]
            a = age_on(bd, yr)
            if pos not in POSITIONS or a is None:
                continue
            lr = math.log(max(s1["ppg"], 2.0) / max(s0["ppg"], 2.0))
            w = min(s0["games"], s1["games"])
            k = (pos, int(a))
            sums[k][0] += w * lr
            sums[k][1] += w
    curves = {}
    for pos in POSITIONS:
        c = {}
        for age in range(AGE_MIN - 3, AGE_MAX + 1):
            prior = math.log(prior_multiplier(pos, age))
            tot, w = sums.get((pos, age), (0.0, 0.0))
            est = (tot + cfg.curve_shrink_games * prior) / (w + cfg.curve_shrink_games)
            c[age] = est
        # light smoothing across neighbouring ages
        sm = {}
        for age in c:
            nb = [c[a] for a in (age - 1, age, age + 1) if a in c]
            sm[age] = math.exp(0.25 * nb[0] + 0.5 * c[age] + 0.25 * nb[-1]) if len(nb) == 3 else math.exp(c[age])
        curves[pos] = sm
    return curves


def age_factor(curves: dict, pos: str, from_age: float, to_age: float) -> float:
    """Product of yearly multipliers moving a player from one age to another."""
    c = curves.get(pos)
    if not c or from_age is None or to_age is None:
        return 1.0
    f = 1.0
    a = from_age
    step = 1 if to_age >= from_age else -1
    while (to_age - a) * step >= 0.5:
        age = int(a) if step == 1 else int(a) - 1
        age = min(max(age, AGE_MIN - 3), AGE_MAX)
        m = c.get(age, 1.0)
        f = f * m if step == 1 else f / m
        a += step
    return f


# ---------------------------------------------------------------------------
# Draft prior
# ---------------------------------------------------------------------------
def fit_draft_prior(picks: list[dict], seasons: list[dict], current: int) -> dict:
    """{(pos, bucket, nfl_year): expected points per game (missed games count as 0)}."""
    pts = {(s["gsis_id"], s["season"]): s["pts"] for s in seasons}
    acc = defaultdict(list)
    for p in picks:
        pos, yr = p["position"], p["year"]
        if pos not in POSITIONS or yr is None:
            continue
        b = pick_bucket(p["pick"])
        for n in (1, 2, 3):
            s = yr + n - 1
            if s >= current:
                continue
            acc[(pos, b, n)].append(pts.get((p["gsis_id"], s), 0.0) / 17.0)
    prior = {}
    for k, v in acc.items():
        if len(v) >= 3:
            prior[k] = round(sum(v) / len(v), 3)
    # fill gaps: a missing year uses the nearest year available for that bucket
    for pos in POSITIONS:
        for _, b in PICK_BUCKETS + [(0, "UDFA")]:
            for n in (1, 2, 3):
                if (pos, b, n) not in prior:
                    alt = [prior[(pos, b, m)] for m in (n - 1, n + 1, 3, 2, 1) if (pos, b, m) in prior]
                    if alt:
                        prior[(pos, b, n)] = alt[0]
    return prior


# ---------------------------------------------------------------------------
# Upside: expected points above replacement under uncertainty
# ---------------------------------------------------------------------------
def uncertainty(games_history: float) -> float:
    """Coefficient of variation of a PPG projection: ~0.62 with no NFL games, ~0.24 for veterans."""
    return 0.22 + 0.40 * math.exp(-(games_history or 0) / 25.0)


def expected_excess(mu: float, sigma: float, repl: float) -> float:
    """E[max(0, X - repl)] for X ~ Normal(mu, sigma). Gives uncertain players option value."""
    if sigma <= 1e-9:
        return max(0.0, mu - repl)
    d = (mu - repl) / sigma
    pdf = math.exp(-0.5 * d * d) / math.sqrt(2 * math.pi)
    cdf = 0.5 * (1 + math.erf(d / math.sqrt(2)))
    return max(0.0, (mu - repl) * cdf + sigma * pdf)


# ---------------------------------------------------------------------------
# Replacement level from the league's starting lineup
# ---------------------------------------------------------------------------
FLEX_ELIGIBLE = {
    "FLEX": ("RB", "WR", "TE"),
    "SUPER_FLEX": ("QB", "RB", "WR", "TE"),
    "REC_FLEX": ("WR", "TE"),
    "WRRB_FLEX": ("WR", "RB"),
}


def replacement_levels(proj: dict[str, tuple[str, float]], roster_positions: list[str],
                       teams: int, depth: float) -> tuple[dict, dict]:
    """proj: player -> (pos, ppg). Returns ({pos: replacement ppg}, {pos: starters})."""
    slots = defaultdict(int)
    flex = []
    for s in roster_positions:
        if s in POSITIONS:
            slots[s] += 1
        elif s in FLEX_ELIGIBLE:
            flex.append(FLEX_ELIGIBLE[s])
    ranked = {pos: sorted((v for p, (ps, v) in proj.items() if ps == pos), reverse=True) for pos in POSITIONS}
    starters = {pos: slots[pos] * teams for pos in POSITIONS}
    used = dict(starters)
    for elig in flex:
        for _ in range(teams):
            best = max(
                (pos for pos in elig if used[pos] < len(ranked[pos])),
                key=lambda pos: ranked[pos][used[pos]],
                default=None,
            )
            if best:
                used[best] += 1
    repl = {}
    for pos in POSITIONS:
        idx = int(round(used[pos] * depth))
        lst = ranked[pos]
        repl[pos] = lst[idx] if idx < len(lst) else (lst[-1] if lst else 0.0)
    return repl, used


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------
def build(store: Store, cfg: ModelConfig | None = None) -> dict:
    cfg = cfg or ModelConfig()
    if not _exists(store, "player_season"):
        log.warning("No player_season table; run production first")
        return {}
    lg = store.query("SELECT * FROM league_season WHERE is_current=1")[0]
    current = int(lg["season"])
    cfg.teams = lg["total_rosters"] or cfg.teams
    roster_positions = json.loads(lg["roster_positions"] or "[]")
    last_leg = lg["last_scored_leg"] or 0
    st = store.query("SELECT body FROM raw_snapshot WHERE endpoint='state/nfl'")
    state = json.loads(st[0]["body"]) if st and st[0]["body"] else {}
    if str(state.get("season")) == str(current) and state.get("season_type") == "regular":
        last_leg = max(last_leg, int(state.get("week") or 0) - 1)
    remaining = max(0, cfg.season_games - last_leg) if state.get("season_type") != "post" else 0

    seasons = [dict(r) for r in store.query("SELECT * FROM player_season")]

    # birthdates: nflverse rosters first, then Sleeper via the id map
    birth: dict[str, date] = {}
    if _exists(store, "nfl_rosters"):
        rc = _cols(store, "nfl_rosters")
        bcol = next((c for c in ("birth_date", "birthdate") if c in rc), None)
        if bcol and "gsis_id" in rc:
            for r in store.query(f'SELECT gsis_id, "{bcol}" b FROM nfl_rosters WHERE "{bcol}" IS NOT NULL'):
                d = _date(r["b"])
                if d:
                    birth[str(r["gsis_id"])] = d
    sleeper_birth = {}
    for r in store.query(
        "SELECT x.gsis_id, p.player_id, p.birth_date FROM player p LEFT JOIN player_xref x ON x.player_id=p.player_id"
    ):
        d = _date(r["birth_date"])
        if d:
            sleeper_birth[r["player_id"]] = d
            if r["gsis_id"]:
                birth.setdefault(r["gsis_id"], d)

    # draft picks: nflverse, plus FantasyCalc draft info for players nflverse lacks (rookies)
    picks = []
    draft_by_gsis = {}
    if _exists(store, "nfl_draft_picks"):
        dc = _cols(store, "nfl_draft_picks")
        gcol = next((c for c in ("gsis_id", "player_id") if c in dc), None)
        pcol = "position" if "position" in dc else ("pos" if "pos" in dc else None)
        if gcol and pcol and "pick" in dc:
            for r in store.query(f'SELECT season, pick, "{gcol}" g, "{pcol}" pos FROM nfl_draft_picks'):
                rec = {"gsis_id": str(r["g"]) if r["g"] else None, "year": int(r["season"]),
                       "pick": int(r["pick"]) if r["pick"] else None, "position": r["pos"]}
                if rec["gsis_id"]:
                    draft_by_gsis[rec["gsis_id"]] = rec
                picks.append(rec)
    draft_by_sleeper = {}
    if _exists(store, "market_value"):
        for r in store.query(
            "SELECT player_id, extra FROM market_value WHERE source='fantasycalc' AND player_id IS NOT NULL "
            "AND snapshot_date=(SELECT MAX(snapshot_date) FROM market_value WHERE source='fantasycalc')"
        ):
            info = (json.loads(r["extra"] or "{}") or {}).get("draft") or {}
            if info.get("year") and info.get("pick") is not None:
                rnd = info.get("round") or 1
                draft_by_sleeper[r["player_id"]] = {"year": int(info["year"]),
                                                    "pick": (rnd - 1) * 32 + int(info["pick"])}

    curves = fit_age_curves(seasons, birth, current, cfg)
    prior = fit_draft_prior(picks, seasons, current)

    store.write_frame("age_curve", [
        {"position": pos, "age": age, "multiplier": round(m, 4), "prior": prior_multiplier(pos, age)}
        for pos, c in curves.items() for age, m in sorted(c.items())
    ])
    store.write_frame("draft_prior", [
        {"position": k[0], "bucket": k[1], "nfl_year": k[2], "ppg": v} for k, v in sorted(prior.items())
    ])

    hist = defaultdict(dict)
    for s in seasons:
        hist[s["gsis_id"]][s["season"]] = s

    players = store.query(
        """SELECT p.player_id, p.full_name, p.position, p.years_exp, p.status, p.team, x.gsis_id,
                  EXISTS(SELECT 1 FROM roster_snapshot rs WHERE rs.player_id=p.player_id) rostered
           FROM player p LEFT JOIN player_xref x ON x.player_id=p.player_id
           WHERE p.position IN ('QB','RB','WR','TE')"""
    )

    proj = {}
    for p in players:
        pid, pos, g = p["player_id"], p["position"], p["gsis_id"]
        active = p["status"] == "Active" or p["team"] or p["rostered"]
        if not active:
            continue
        bd = birth.get(g) if g else None
        bd = bd or sleeper_birth.get(pid)
        age_now = age_on(bd, current)
        ss = hist.get(g, {}) if g else {}

        # draft prior for this player's NFL year
        d = draft_by_gsis.get(g) if g else None
        d = d or draft_by_sleeper.get(pid)
        nfl_year = (current - d["year"] + 1) if d else ((p["years_exp"] or 0) + 1)
        bucket = pick_bucket(d["pick"]) if d else "UDFA"
        prior_ppg = prior.get((pos, bucket, min(max(nfl_year, 1), 3)))
        if prior_ppg is None:
            prior_ppg = 0.0

        num = den = 0.0
        games_hist = 0
        for i, w in enumerate(cfg.recency):
            s = ss.get(current - i)
            if not s or not s["games"]:
                continue
            ppg = s["ppg"]
            if s.get("xppg") is not None:
                ppg = cfg.actual_vs_expected * ppg + (1 - cfg.actual_vs_expected) * s["xppg"]
            a_then = age_on(bd, current - i)
            ppg *= age_factor(curves, pos, a_then, age_now) if a_then is not None else 1.0
            num += w * s["games"] * ppg
            den += w * s["games"]
            games_hist += s["games"]
        young = nfl_year <= 3
        pw = cfg.prior_games * (1.0 if young else 0.4)
        talent = (num + pw * prior_ppg) / (den + pw) if (den + pw) else 0.0

        def growth(n_from: int, n_to: int) -> float:
            """Typical PPG change for this draft range between NFL years (years 1-3 only)."""
            a = prior.get((pos, bucket, min(max(n_from, 1), 3)))
            b = prior.get((pos, bucket, min(max(n_to, 1), 3)))
            if not a or not b or a < 0.5:
                return 1.0
            return min(max(b / a, 0.8), 2.5)

        def at(season_offset: int) -> float:
            # Early career: follow the draft class's year-1 -> year-3 trajectory instead of
            # the age curve (it already contains the development jump); after year 3, age curve.
            if nfl_year <= 3:
                dev_years = min(3, nfl_year + season_offset) - nfl_year
                base = talent * growth(nfl_year, nfl_year + dev_years)
                rest = season_offset - dev_years
                if rest <= 0 or age_now is None:
                    return base
                start = age_now + dev_years
                return base * age_factor(curves, pos, start, start + rest)
            if age_now is None:
                return talent
            return talent * age_factor(curves, pos, age_now, age_now + season_offset)

        proj[pid] = {
            "player_id": pid, "gsis_id": g, "position": pos, "name": p["full_name"],
            "age": round(age_now, 1) if age_now else None, "nfl_year": nfl_year,
            "draft_bucket": bucket, "games_history": games_hist,
            "prior_ppg": round(prior_ppg, 2), "talent_ppg": round(talent, 2),
            "ppg_ros": round(talent, 2), "ppg_y1": round(at(1), 2),
            "ppg_y2": round(at(2), 2), "ppg_y3": round(at(3), 2),
        }

    repl, starters = replacement_levels(
        {k: (v["position"], v["ppg_y1"]) for k, v in proj.items()},
        roster_positions, cfg.teams, cfg.replacement_depth,
    )
    games_full = cfg.season_games * cfg.availability
    games_ros = remaining * cfg.availability
    for v in proj.values():
        r = repl.get(v["position"], 0.0)
        cv0 = uncertainty(v["games_history"])
        parts = [
            cfg.discounts[0] * games_ros * expected_excess(v["ppg_ros"], cv0 * v["ppg_ros"], r),
            cfg.discounts[1] * games_full * expected_excess(v["ppg_y1"], (cv0 + 0.06) * v["ppg_y1"], r),
            cfg.discounts[2] * games_full * expected_excess(v["ppg_y2"], (cv0 + 0.12) * v["ppg_y2"], r),
            cfg.discounts[3] * games_full * expected_excess(v["ppg_y3"], (cv0 + 0.18) * v["ppg_y3"], r),
        ]
        v["repl_ppg"] = round(r, 2)
        v["model_value"] = round(sum(parts), 1)
        v["model_value_now"] = round(parts[0] + parts[1], 1)

    store.write_frame("player_projection", list(proj.values()))
    store.commit()
    return {
        "projected": len(proj),
        "with_value": sum(1 for v in proj.values() if v["model_value"] > 0),
        "replacement_ppg": {k: round(v, 1) for k, v in repl.items()},
        "starters_counted": starters,
        "rest_of_season_weeks": remaining,
    }

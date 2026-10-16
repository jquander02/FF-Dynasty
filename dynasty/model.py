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
from dataclasses import dataclass, field, replace
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
    """Defaults tuned on the backtest (dynasty/backtest.py, offseasons 2015-2024): first for
    next-season PPG error, then for how well model value ranks what players went on to produce
    over three seasons (all positions together), with each position's value calibrated so a
    point of value means the same production at QB, RB, WR and TE."""
    # aging curves
    min_games_curve: int = 6
    curve_min_games_next: int = 6         # games needed in the following season for an aging pair
    curve_basis: str = "ppg"              # "ppg" or "blend" (actual/expected mix)
    curve_weight: str = "min"             # pair weight: "min" games or "harmonic" mean
    curve_shrink_games: float = 200.0     # games of evidence needed to move halfway off the prior shape
    # talent estimate
    recency: tuple = (1.0, 0.5, 0.25)     # weights for the latest season, one before, two before
    actual_vs_expected: float = 1.0       # PPG blend: actual vs expected fantasy points
    regress_games: float = 2.0            # pseudo-games at the position's average PPG (pulls extremes in)
    late_weight: float = 0.0              # weight on the last 6 games of the latest season (tested: no help)
    # draft prior (rookies and NFL years 1-3)
    prior_curve: bool = True              # smooth curve in log(pick) rather than pick ranges
    prior_per_game: bool = True           # points per game played; how often draftees play feeds availability
    prior_games: float = 1.0              # pseudo-games given to the draft prior in NFL years 1-3
    vet_prior_share: float = 0.0          # share of prior_games kept after NFL year 3
    growth_cap: float = 1.8               # max year-1 -> year-3 PPG growth from the draft-class trajectory
    # per-position overrides of the settings above (e.g. {"TE": {"recency": (1, .4, .2)}}). Left empty:
    # per-position tuning beat the shared settings in-sample but not on held-out seasons.
    by_position: dict = field(default_factory=dict)
    # value
    availability: float = 0.8             # typical share of games played (cap for veterans)
    play_from_history: bool = True        # expected games from each player's own playing time (QBs: snaps)
    play_shrink: float = 0.25             # pseudo-seasons at the typical availability
    play_cap: bool = True                 # never above `availability`
    uncertainty_scale: float = 0.4        # width of the projection range used for upside value
    young_uncertainty_scale: float = 0.2  # same, for rookies and players with under a season of games
    position_scale: dict = field(default_factory=lambda: {"QB": 0.854, "RB": 1.152, "WR": 1.108, "TE": 0.886})
    season_games: int = 17
    discounts: tuple = (1.0, 0.9, 0.75, 0.6)  # rest-of-season, +1, +2, +3 seasons
    replacement_depth: float | dict = 1.5  # replacement = starters x this (a number, or one per position)
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
def fit_age_curves(seasons: list[dict], birth: dict, current: int, cfg: ModelConfig,
                   current_complete: bool = False) -> dict:
    """{pos: {age: multiplier for age -> age+1}} via the delta method, shrunk to the prior.
    The current season is left out while it's in progress (current_complete=False)."""
    last = current + 1 if current_complete else current
    by_player = defaultdict(dict)
    for s in seasons:
        by_player[s["gsis_id"]][s["season"]] = s
    sums = defaultdict(lambda: [0.0, 0.0])  # (pos, age) -> [weighted log ratio, weight]
    for g, ss in by_player.items():
        bd = birth.get(g)
        for yr, s0 in ss.items():
            s1 = ss.get(yr + 1)
            if not s1 or yr + 1 >= last:  # current season is incomplete
                continue
            if s0["games"] < cfg.min_games_curve or s1["games"] < cfg.curve_min_games_next:
                continue
            pos = s0["position"]
            a = age_on(bd, yr)
            if pos not in POSITIONS or a is None:
                continue

            def level(srow):
                if cfg.curve_basis == "blend" and srow.get("xppg") is not None:
                    return cfg.actual_vs_expected * srow["ppg"] + (1 - cfg.actual_vs_expected) * srow["xppg"]
                return srow["ppg"]
            lr = math.log(max(level(s1), 2.0) / max(level(s0), 2.0))
            w = 2 * s0["games"] * s1["games"] / (s0["games"] + s1["games"]) if cfg.curve_weight == "harmonic" \
                else min(s0["games"], s1["games"])
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
def fit_draft_prior(picks: list[dict], seasons: list[dict], current: int, current_complete: bool = False,
                    per_game: bool = True, share: dict | None = None) -> dict:
    """{(pos, bucket, nfl_year): points per game for players drafted in that range}.

    per_game=True: points per game played (games-weighted), the same footing as a player's own
    PPG; how often draftees actually play goes into `share` ({key: games / 17}) instead.
    per_game=False (old behaviour): season points / 17, so missed games count as zeros.
    Only seasons inside the stats window count; earlier seasons aren't zeros, they're unknown."""
    last = current + 1 if current_complete else current
    first = min((s["season"] for s in seasons), default=current)
    rows = {(s["gsis_id"], s["season"]): s for s in seasons}
    acc = defaultdict(lambda: [0.0, 0.0, 0])   # pts, games, players
    obs = defaultdict(list)                    # (pos, n) -> [(log pick, ppg, games)]
    for p in picks:
        pos, yr = p["position"], p["year"]
        if pos not in POSITIONS or yr is None:
            continue
        b = pick_bucket(p["pick"])
        for n in (1, 2, 3):
            s = yr + n - 1
            if s >= last or s < first:
                continue
            r = rows.get((p["gsis_id"], s))
            obs[(pos, n)].append((math.log(p["pick"] or 300), (r["pts"] / r["games"]) if r and r["games"] else None,
                                  r["games"] if r else 0))
            a = acc[(pos, b, n)]
            a[0] += r["pts"] if r else 0.0
            a[1] += r["games"] if r else 0
            a[2] += 1
    # Pooled trajectory over picks 1-100 per position: a stable year 1 -> 3 growth curve
    # (single buckets like first-round TEs have too few players to trust their shape).
    for (pos, b, n), a in list(acc.items()):
        if b in ("1-12", "13-32", "33-64", "65-100"):
            pa = acc[(pos, "POOLED", n)]
            for i in range(3):
                pa[i] += a[i]
    prior = {}
    for k, (pts, games, n) in acc.items():
        if n < 3:
            continue
        if per_game:
            if games >= 17:
                prior[k] = round(pts / games, 3)
        else:
            prior[k] = round(pts / 17.0 / n, 3)
        if share is not None:
            share[k] = round(games / 17.0 / n, 3)
    # smooth draft-capital curves: PPG (games-weighted) and share of games played vs log(pick)
    for (pos, n), ob in obs.items():
        pts = [(x, y, g) for x, y, g in ob if y is not None and g > 0]
        fit = _wls([x for x, _, _ in pts], [y for _, y, _ in pts], [g for _, _, g in pts])
        sfit = _wls([x for x, _, _ in ob], [g / 17.0 for _, _, g in ob], [1.0] * len(ob))
        if fit and len(pts) >= 15:
            prior[(pos, "CURVE", n)] = fit
        if sfit and len(ob) >= 15 and share is not None:
            share[(pos, "CURVE", n)] = sfit
    # fill gaps: a bucket with no players borrows the average of its neighbouring buckets
    order = [b for _, b in PICK_BUCKETS]
    for pos in POSITIONS:
        for n in (1, 2, 3):
            for i, b in enumerate(order):
                if (pos, b, n) in prior:
                    continue
                nb = [prior[(pos, order[j], n)] for j in (i - 1, i + 1) if 0 <= j < len(order) and (pos, order[j], n) in prior]
                if nb:
                    prior[(pos, b, n)] = round(sum(nb) / len(nb), 3)
    # still empty (very little draft history): the position's pooled picks 1-100
    for pos in POSITIONS:
        for n in (1, 2, 3):
            for b in order:
                if (pos, b, n) not in prior and (pos, "POOLED", n) in prior:
                    prior[(pos, b, n)] = prior[(pos, "POOLED", n)]
    # fill gaps: a missing year uses the nearest year available for that bucket
    for pos in POSITIONS:
        for _, b in PICK_BUCKETS + [(0, "UDFA")]:
            for n in (1, 2, 3):
                if (pos, b, n) not in prior:
                    alt = [prior[(pos, b, m)] for m in (n - 1, n + 1, 3, 2, 1) if (pos, b, m) in prior]
                    if alt:
                        prior[(pos, b, n)] = alt[0]
    return prior


def _wls(xs: list[float], ys: list[float], ws: list[float]):
    """Weighted least squares line y = a + b x. Returns (a, b) or None."""
    sw = sum(ws)
    if sw <= 0 or len(xs) < 3:
        return None
    mx = sum(w * x for w, x in zip(ws, xs)) / sw
    my = sum(w * y for w, y in zip(ws, ys)) / sw
    sxx = sum(w * (x - mx) ** 2 for w, x in zip(ws, xs))
    if sxx <= 0:
        return None
    b = sum(w * (x - mx) * (y - my) for w, x, y in zip(ws, xs, ys)) / sxx
    return (round(my - b * mx, 4), round(b, 4))


def curve_at(fit, pick, lo: float = 0.0, hi: float | None = None) -> float:
    v = fit[0] + fit[1] * math.log(pick or 300)
    return max(lo, min(hi, v) if hi is not None else v)


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
                       teams: int, depth: float | dict) -> tuple[dict, dict]:
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
        d = depth.get(pos, 1.5) if isinstance(depth, dict) else depth
        idx = int(round(used[pos] * d))
        lst = ranked[pos]
        repl[pos] = lst[idx] if idx < len(lst) else (lst[-1] if lst else 0.0)
    return repl, used


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------
def project(players: list[dict], seasons: list[dict], curves: dict, prior: dict, current: int,
            cfg: ModelConfig, current_games: int | None = None) -> dict:
    """Points-per-game projections from data through season `current`.

    players: {key, gsis_id, position, name, years_exp, birth (date), draft ({year, pick}) or None}.
    Returns key -> {ppg_ros (current season talent), ppg_y1, ppg_y2, ppg_y3, ...}. Used by the live
    model (current = this season, in progress) and by the backtest (current = last finished season,
    so ppg_y1 is the season being predicted)."""
    hist = defaultdict(dict)
    for srow in seasons:
        if srow["season"] <= current:
            hist[srow["gsis_id"]][srow["season"]] = srow
    # regression target: average PPG of regular contributors at each position, last two seasons
    pool = defaultdict(list)
    for srow in seasons:
        if current - 1 <= srow["season"] <= current and srow["games"] >= 6 and srow["position"] in POSITIONS:
            pool[srow["position"]].append(srow["ppg"])
    pos_mean = {k: sum(v) / len(v) for k, v in pool.items() if v}
    proj = {}
    for p in players:
        pid, pos, g = p["key"], p["position"], p["gsis_id"]
        pc = replace(cfg, **cfg.by_position[pos]) if cfg.by_position and pos in cfg.by_position else cfg
        bd = p.get("birth")
        age_now = age_on(bd, current)
        ss = hist.get(g, {}) if g else {}

        # draft prior for this player's NFL year
        d = p.get("draft")
        nfl_year = (current - d["year"] + 1) if d else ((p.get("years_exp") or 0) + 1)
        bucket = pick_bucket(d["pick"]) if d else "UDFA"
        n_now = min(max(nfl_year, 1), 3)
        prior_ppg = prior.get((pos, bucket, n_now))
        if cfg.prior_curve and (pos, "CURVE", n_now) in prior:
            prior_ppg = curve_at(prior[(pos, "CURVE", n_now)], d["pick"] if d else None, lo=1.0)
        if prior_ppg is None:
            prior_ppg = 0.0

        num = den = 0.0
        games_hist = 0
        # playing time: weighted share of games played (QBs: games x snap share, so cameo
        # appearances by backups don't count as starts), seasons since his debut that he missed count as 0
        pt_num = pt_den = 0.0
        debut = min(ss) if ss else None
        for i, w in enumerate(pc.recency):
            yr = current - i
            if debut is None or yr < debut:
                continue
            srow = ss.get(yr)
            g_eff = 0.0
            if srow and srow["games"]:
                g_eff = srow["games"]
                if pos == "QB" and srow.get("snap_pct") is not None:
                    g_eff *= min(1.0, srow["snap_pct"] / 0.9)
            possible = cfg.season_games
            if yr == current and current_games is not None:
                # season in progress: weeks so far, less a bye once teams have had them
                possible = max(1, min(cfg.season_games, current_games - (1 if current_games >= 9 else 0)))
                if current_games < 4:
                    continue                                              # too early to say anything
            pt_num += w * min(g_eff, possible)
            pt_den += w * possible
        for i, w in enumerate(pc.recency):
            srow = ss.get(current - i)
            if not srow or not srow["games"]:
                continue
            ppg = srow["ppg"]
            if srow.get("xppg") is not None:
                ppg = pc.actual_vs_expected * ppg + (1 - pc.actual_vs_expected) * srow["xppg"]
            if i == 0 and pc.late_weight and srow.get("ppg_late") is not None:
                ppg = (1 - pc.late_weight) * ppg + pc.late_weight * srow["ppg_late"]   # current role
            a_then = age_on(bd, current - i)
            ppg *= age_factor(curves, pos, a_then, age_now) if a_then is not None else 1.0
            num += w * srow["games"] * ppg
            den += w * srow["games"]
            games_hist += srow["games"]
        young = nfl_year <= 3
        pw = pc.prior_games * (1.0 if young else pc.vet_prior_share)
        rw = pc.regress_games if den > 0 else 0.0
        target = pos_mean.get(pos, prior_ppg)
        talent = (num + pw * prior_ppg + rw * target) / (den + pw + rw) if (den + pw + rw) else 0.0

        def growth(n_from: int, n_to: int) -> float:
            """Typical PPG change for this draft range between NFL years (years 1-3 only)."""
            key = "POOLED" if (pos, "POOLED", 1) in prior else bucket
            a = prior.get((pos, key, min(max(n_from, 1), 3)))
            b = prior.get((pos, key, min(max(n_to, 1), 3)))
            if not a or not b or a < 0.5:
                return 1.0
            return min(max(b / a, 0.8), cfg.growth_cap)

        def at(season_offset: int) -> float:
            # Early career: follow the draft class's year-1 -> year-3 trajectory instead of
            # the age curve (it already contains the development jump); after year 3, age curve.
            if nfl_year <= 3:
                dev_years = min(3, nfl_year + season_offset) - max(nfl_year, 0)
                dev_years = max(dev_years, 0)
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
            "player_id": pid, "gsis_id": g, "position": pos, "name": p.get("name"),
            "age": round(age_now, 1) if age_now else None, "nfl_year": nfl_year,
            "draft_bucket": bucket, "draft_pick": d["pick"] if d else None, "games_history": games_hist,
            "prior_ppg": round(prior_ppg, 2), "talent_ppg": round(talent, 2),
            "play_share": round((pt_num + cfg.play_shrink * cfg.season_games * cfg.availability)
                                / (pt_den + cfg.play_shrink * cfg.season_games), 3),
            "ppg_ros": round(talent, 2), "ppg_y1": round(at(1), 2),
            "ppg_y2": round(at(2), 2), "ppg_y3": round(at(3), 2),
        }
    return proj


def availability(v: dict, offset: int, share: dict, cfg: ModelConfig) -> float:
    """Share of games a player is expected to play. Young players with little NFL history play as
    often as their draft range typically does (busts and depth players play less)."""
    if cfg.play_from_history and v.get("play_share") is not None and v["games_history"] >= 10:
        return min(cfg.availability, v["play_share"]) if cfg.play_cap else v["play_share"]
    if not cfg.prior_per_game or v["nfl_year"] > 3 or v["games_history"] >= 10:
        return cfg.availability
    n = min(max(v["nfl_year"] + offset, 1), 3)
    sh = share.get((v["position"], v["draft_bucket"], n))
    if cfg.prior_curve and (v["position"], "CURVE", n) in share:
        sh = curve_at(share[(v["position"], "CURVE", n)], v.get("draft_pick"), lo=0.02, hi=1.0)
    return min(cfg.availability, sh) if sh is not None else cfg.availability


def value_parts(v: dict, repl: float, share: dict, cfg: ModelConfig, games_left: int) -> list[float]:
    """Expected points above replacement, undiscounted: [rest of this season, +1, +2, +3 seasons].
    The projection is treated as a range (wider for young players and later seasons), so a player
    near replacement level still has upside value."""
    scale = cfg.young_uncertainty_scale if (v["nfl_year"] <= 1 and v["games_history"] < 17) else cfg.uncertainty_scale
    cv0 = uncertainty(v["games_history"]) * scale
    keys = ("ppg_ros", "ppg_y1", "ppg_y2", "ppg_y3")
    games = (games_left,) + (cfg.season_games,) * 3
    k_pos = (cfg.position_scale or {}).get(v["position"], 1.0)
    return [k_pos * games[i] * availability(v, i, share, cfg)
            * expected_excess(v[k], (cv0 + 0.06 * i * scale) * v[k], repl) for i, k in enumerate(keys)]


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
    share: dict = {}
    prior = fit_draft_prior(picks, seasons, current, per_game=cfg.prior_per_game, share=share)

    store.write_frame("age_curve", [
        {"position": pos, "age": age, "multiplier": round(m, 4), "prior": prior_multiplier(pos, age)}
        for pos, c in curves.items() for age, m in sorted(c.items())
    ])
    store.write_frame("draft_prior", [
        {"position": k[0], "bucket": k[1], "nfl_year": k[2],
         "ppg": v if not isinstance(v, tuple) else None,
         "curve": json.dumps(v) if isinstance(v, tuple) else None} for k, v in sorted(prior.items())
    ])

    players = []
    for p in store.query(
        """SELECT p.player_id, p.full_name, p.position, p.years_exp, p.status, p.team, x.gsis_id,
                  EXISTS(SELECT 1 FROM roster_snapshot rs WHERE rs.player_id=p.player_id) rostered
           FROM player p LEFT JOIN player_xref x ON x.player_id=p.player_id
           WHERE p.position IN ('QB','RB','WR','TE')"""
    ):
        if not (p["status"] == "Active" or p["team"] or p["rostered"]):
            continue
        g, pid = p["gsis_id"], p["player_id"]
        players.append({
            "key": pid, "gsis_id": g, "position": p["position"], "name": p["full_name"],
            "years_exp": p["years_exp"],
            "birth": (birth.get(g) if g else None) or sleeper_birth.get(pid),
            "draft": (draft_by_gsis.get(g) if g else None) or draft_by_sleeper.get(pid),
        })
    proj = project(players, seasons, curves, prior, current, cfg,
                   current_games=last_leg if state.get("season_type") == "regular" else None)

    repl, starters = replacement_levels(
        {k: (v["position"], v["ppg_y1"]) for k, v in proj.items()},
        roster_positions, cfg.teams, cfg.replacement_depth,
    )
    for v in proj.values():
        parts = value_parts(v, repl.get(v["position"], 0.0), share, cfg, remaining)
        v["repl_ppg"] = round(repl.get(v["position"], 0.0), 2)
        v["model_value"] = round(sum(cfg.discounts[i] * x for i, x in enumerate(parts)), 1)
        v["model_value_now"] = round(cfg.discounts[0] * parts[0] + cfg.discounts[1] * parts[1], 1)

    store.write_frame("player_projection", list(proj.values()))
    store.commit()
    return {
        "projected": len(proj),
        "with_value": sum(1 for v in proj.values() if v["model_value"] > 0),
        "replacement_ppg": {k: round(v, 1) for k, v in repl.items()},
        "starters_counted": starters,
        "rest_of_season_weeks": remaining,
    }

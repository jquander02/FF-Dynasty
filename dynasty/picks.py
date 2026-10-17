"""Season simulation, rookie draft order odds, and pick values.

Draft order rule: two candidates are checked against the league's last rookie draft and
the better one is used: worst record picks first, or non-playoff teams by reverse record
then playoff teams by playoff finish (champion picks last). The refresh summary reports
how many slots each explains, so a lottery or custom order shows up.

Odds:
  next draft      Monte Carlo of the rest of this regular season on Sleeper's schedule.
                  Each team's weekly score ~ Normal(mean, sd); the mean blends points
                  scored so far with the projected best lineup.
  the year after  team strength next season plus wide noise
  two years out   team strength in two seasons plus wider noise

Pick value (same 60/40 lean as players):
  model   value now of the players this league has taken at each overall pick in its
          rookie drafts (a fitted decay curve), discounted per year until the draft
  market  FantasyCalc / DynastyProcess pick values (Early / Mid / Late, by round)
  The slot odds are applied to the slot values: value = sum of P(slot) x value(slot).

Writes season_sim, pick_value; adds pick capital to team_window.
"""

from __future__ import annotations

import json
import logging
import math
import random
import re
import statistics
from collections import defaultdict

from .store import Store

log = logging.getLogger(__name__)
SIMS = 10000
TIERS = ("Early", "Mid", "Late")


def _has(store: Store, t: str) -> bool:
    return bool(store.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)))


# ---------------------------------------------------------------------------
# Season simulation
# ---------------------------------------------------------------------------
def team_sd(store: Store) -> float:
    """Typical week-to-week spread of a team's score, from this league's matchups."""
    rows = store.query("SELECT league_id, roster_id, points FROM matchup WHERE points > 0")
    by = defaultdict(list)
    for r in rows:
        by[(r["league_id"], r["roster_id"])].append(r["points"])
    resid = []
    for pts in by.values():
        if len(pts) >= 3:
            m = sum(pts) / len(pts)
            resid += [p - m for p in pts]
    if len(resid) < 30:
        return 22.0
    return min(35.0, max(15.0, statistics.pstdev(resid)))


def kdef_weekly(store: Store, league_id: str) -> float:
    r = store.query(
        """SELECT SUM(mp.points) s, COUNT(DISTINCT mp.week || '-' || mp.roster_id) n
           FROM matchup_player mp JOIN player p ON p.player_id = mp.player_id
           WHERE mp.league_id=? AND mp.is_starter=1 AND p.position IN ('K','DEF')""",
        (league_id,),
    )
    if r and r[0]["n"]:
        return r[0]["s"] / r[0]["n"]
    return 15.0


def playoff_places(seeds: list, mean: dict, sd: float, rng: random.Random) -> list:
    """Single-elimination playoff (top seeds get byes). Returns teams in finishing order 1..k.
    Supports 4, 6 or 8 teams; places 5+ go to first-round losers by seed (6-team: 5th-place game)."""
    k = len(seeds)

    def game(a, b):
        return (a, b) if rng.gauss(mean[a], sd) >= rng.gauss(mean[b], sd) else (b, a)

    if k == 6:
        w1, l1 = game(seeds[2], seeds[5])
        w2, l2 = game(seeds[3], seeds[4])
        low, high = sorted([w1, w2], key=seeds.index)[::-1], None
        sw1, sl1 = game(seeds[0], low[0])
        sw2, sl2 = game(seeds[1], low[1])
        champ, runner = game(sw1, sw2)
        third, fourth = game(sl1, sl2)
        fifth, sixth = game(l1, l2)
        return [champ, runner, third, fourth, fifth, sixth]
    if k in (4, 8):
        rnd = list(seeds)
        losers_by_round = []
        while len(rnd) > 2:
            nxt, lost = [], []
            for i in range(len(rnd) // 2):
                w, l = game(rnd[i], rnd[-1 - i])
                nxt.append(w)
                lost.append(l)
            losers_by_round.append(sorted(lost, key=seeds.index))
            rnd = sorted(nxt, key=seeds.index)
        champ, runner = game(rnd[0], rnd[1])
        semis = losers_by_round[-1]
        third, fourth = game(semis[0], semis[1])
        rest = [t for grp in reversed(losers_by_round[:-1]) for t in grp]
        return [champ, runner, third, fourth] + rest
    return list(seeds)


def simulate_season(teams: dict, schedule: dict, weeks: list[int], sd: float, playoff_teams: int,
                    sims: int = SIMS, seed: int = 7, rule: str = "reverse_standings",
                    fixed: dict | None = None) -> dict:
    """teams: rid -> {wins, ties, pf, mean}. schedule: week -> [(a, b), ...].
    rule: 'reverse_standings' (worst record picks 1st) or 'playoff_finish' (non-playoff teams by
    reverse record, then playoff teams by finish, champion last).
    fixed: week -> {rid: (mean, sd)} for a week already projected lineup by lineup (this week).
    Returns rid -> {slots (draft slot counts), playoffs, titles, wins (total over sims)}."""
    rng = random.Random(seed)
    rids = list(teams)
    n = len(rids)
    out = {rid: {"slots": [0] * (n + 1), "playoffs": 0, "titles": 0, "wins": 0.0} for rid in rids}
    for _ in range(sims):
        wins = {rid: teams[rid]["wins"] + 0.5 * teams[rid]["ties"] for rid in rids}
        pf = {rid: teams[rid]["pf"] for rid in rids}
        for w in weeks:
            pairs = schedule.get(w)
            if not pairs:
                order = rids[:]
                rng.shuffle(order)
                pairs = [(order[i], order[i + 1]) for i in range(0, n - 1, 2)]
            fx = (fixed or {}).get(w) or {}
            for a, b in pairs:
                ma, sda = fx.get(a, (teams[a]["mean"], sd))
                mb, sdb = fx.get(b, (teams[b]["mean"], sd))
                sa = rng.gauss(ma, sda)
                sb = rng.gauss(mb, sdb)
                pf[a] += sa
                pf[b] += sb
                if sa > sb:
                    wins[a] += 1
                elif sb > sa:
                    wins[b] += 1
        standing = sorted(rids, key=lambda r: (wins[r], pf[r]), reverse=True)
        means = {r: teams[r]["mean"] for r in rids}
        if 0 < playoff_teams < n:
            places = playoff_places(standing[:playoff_teams], means, sd, rng)
            out[places[0]]["titles"] += 1
        else:
            places = standing
        if rule == "playoff_finish" and 0 < playoff_teams < n:
            order = places + standing[playoff_teams:]
        else:
            order = standing
        for i, rid in enumerate(order):
            out[rid]["slots"][n - i] += 1  # last in the order picks 1st
        for i, rid in enumerate(standing):
            if i < playoff_teams:
                out[rid]["playoffs"] += 1
            out[rid]["wins"] += wins[rid]
    return out


def strength_slots(strength: dict, noise: float, sims: int = SIMS, seed: int = 11) -> dict:
    """Slot odds for a future season from projected strength plus noise (weakest picks 1st)."""
    rng = random.Random(seed)
    rids = list(strength)
    n = len(rids)
    out = {rid: [0] * (n + 1) for rid in rids}
    for _ in range(sims):
        draw = {r: strength[r] + rng.gauss(0, noise) for r in rids}
        for i, rid in enumerate(sorted(rids, key=lambda r: draw[r])):
            out[rid][i + 1] += 1
    return out


def bracket_places(store: Store, league_id: str) -> dict:
    """roster_id -> final playoff place, from the winners bracket's placement games."""
    places = {}
    for r in store.query("SELECT winner, loser, place FROM bracket WHERE league_id=? AND bracket='winners' "
                         "AND place IS NOT NULL", (league_id,)):
        if r["winner"]:
            places[r["winner"]] = r["place"]
        if r["loser"]:
            places[r["loser"]] = r["place"] + 1
    return places


def draft_rule_check(store: Store) -> dict:
    """Which draft-order rule explains the league's last rookie draft best."""
    d = store.query(
        "SELECT * FROM draft WHERE player_type=1 AND status='complete' ORDER BY season DESC LIMIT 1"
    )
    if not d:
        return {}
    d = d[0]
    prev = store.query("SELECT league_id, playoff_teams FROM league_season WHERE CAST(season AS INTEGER)=?",
                       (int(d["season"]) - 1,))
    order = json.loads(d["draft_order"] or "{}")
    if not prev or not order:
        return {}
    plid, pt = prev[0]["league_id"], prev[0]["playoff_teams"] or 0
    rows = store.query("SELECT roster_id, owner_id, wins, ties, fpts FROM roster WHERE league_id=?", (plid,))
    worst_first = sorted(rows, key=lambda r: ((r["wins"] or 0) + 0.5 * (r["ties"] or 0), r["fpts"] or 0))
    n = len(rows)
    a = {r["owner_id"]: i + 1 for i, r in enumerate(worst_first)}
    places = bracket_places(store, plid)
    b = {}
    non_playoff = worst_first[: n - pt] if pt else worst_first
    for i, r in enumerate(non_playoff):
        b[r["owner_id"]] = i + 1
    for r in rows:
        if r["roster_id"] in places:
            b[r["owner_id"]] = n - places[r["roster_id"]] + 1
    ma = sum(1 for uid, slot in order.items() if a.get(uid) == slot)
    mb = sum(1 for uid, slot in order.items() if b.get(uid) == slot) if places else -1
    best = "playoff_finish" if mb > ma else "reverse_standings"
    return {"draft_season": d["season"], "slots": len(order), "matches_reverse_standings": ma,
            "matches_playoff_finish": mb, "rule": best}


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------
def league_pick_curve(store: Store) -> tuple[float, float] | None:
    """Fit value(overall pick) = exp(a + b*pick) - 100 to this league's rookie drafts."""
    if not _has(store, "player_value"):
        return None
    vd = store.query("SELECT MAX(snapshot_date) d FROM player_value")[0]["d"]
    val = {r["player_id"]: r["value"] for r in store.query(
        "SELECT player_id, value FROM player_value WHERE snapshot_date=?", (vd,))}
    pts = []
    for r in store.query(
        """SELECT pm.pick_no, pm.player_id FROM draft_pick_made pm JOIN draft d ON d.draft_id=pm.draft_id
           WHERE d.player_type=1 AND d.status='complete'"""
    ):
        pts.append((r["pick_no"], math.log((val.get(r["player_id"]) or 0.0) + 100.0)))
    if len(pts) < 12:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    return (a, min(b, 0.0))


def curve_value(curve, overall: int) -> float:
    a, b = curve
    return max(0.0, math.exp(a + b * overall) - 100.0)


FC_PICK = re.compile(r"(\d{4}) (\d)(?:st|nd|rd|th)(?: \((Early|Mid|Late)\))?")
DP_PICK = re.compile(r"(\d{4}) (?:(Early|Mid|Late) )?(\d)(?:st|nd|rd|th)")
DP_EXACT = re.compile(r"(\d{4}) Pick (\d)\.(\d{2})")


def market_picks(store: Store) -> dict:
    """{source: {(season, round, tier|None): value}, 'exact': {(season, round, slot): value}}"""
    out = {"fantasycalc": {}, "dynastyprocess": {}, "exact": {}}
    if not _has(store, "market_value"):
        return out
    for src in ("fantasycalc", "dynastyprocess"):
        for r in store.query(
            """SELECT name, value FROM market_value WHERE source=? AND position='PICK'
               AND snapshot_date=(SELECT MAX(snapshot_date) FROM market_value WHERE source=?)""",
            (src, src),
        ):
            name, v = r["name"] or "", r["value"] or 0.0
            m = DP_EXACT.match(name)
            if m and src == "dynastyprocess":
                out["exact"][(m.group(1), int(m.group(2)), int(m.group(3)))] = v
                continue
            if src == "fantasycalc":
                m = FC_PICK.match(name)
                if m:
                    out[src][(m.group(1), int(m.group(2)), m.group(3))] = v
            else:
                m = DP_PICK.match(name)
                if m:
                    out[src][(m.group(1), int(m.group(3)), m.group(2))] = v
    return out


def tier_value(table: dict, season: str, rnd: int, slot: int, n: int) -> float | None:
    """Interpolate Early/Mid/Late values (centred at n/6, n/2, 5n/6) for a slot."""
    vals = [table.get((season, rnd, t)) for t in TIERS]
    if all(v is None for v in vals):
        return table.get((season, rnd, None))
    centers = [n / 6 + 0.5, n / 2 + 0.5, 5 * n / 6 + 0.5]
    pts = [(c, v) for c, v in zip(centers, vals) if v is not None]
    if len(pts) == 1:
        return pts[0][1]
    if slot <= pts[0][0]:
        (c0, v0), (c1, v1) = pts[0], pts[1]
    elif slot >= pts[-1][0]:
        (c0, v0), (c1, v1) = pts[-2], pts[-1]
    else:
        for (c0, v0), (c1, v1) in zip(pts, pts[1:]):
            if c0 <= slot <= c1:
                break
    v = v0 + (v1 - v0) * (slot - c0) / (c1 - c0)
    lo, hi = min(p[1] for p in pts), max(p[1] for p in pts)
    return max(lo * 0.8, min(hi * 1.15, v))


def build(store: Store, weights: dict | None = None) -> dict:
    w = {"model": 0.6, "fantasycalc": 0.3, "dynastyprocess": 0.1}
    w.update(weights or {})
    lg = store.query("SELECT * FROM league_season WHERE is_current=1")[0]
    lid, season = lg["league_id"], int(lg["season"])
    st = json.loads(lg["settings"] or "{}")
    n = lg["total_rosters"] or 12
    playoff_teams = lg["playoff_teams"] or 6
    scored = int(lg["last_scored_leg"] or 0)
    last_reg = int(lg["playoff_week_start"] or 15) - 1

    tw = {r["roster_id"]: dict(r) for r in store.query("SELECT * FROM team_window")}
    rosters = {r["roster_id"]: r for r in store.query("SELECT * FROM roster WHERE league_id=?", (lid,))}
    sd = team_sd(store)
    kdef = kdef_weekly(store, lid)

    teams = {}
    for rid, r in rosters.items():
        played = (r["wins"] or 0) + (r["losses"] or 0) + (r["ties"] or 0)
        actual = (r["fpts"] or 0.0) / played if played else None
        proj = (tw.get(rid, {}).get("strength_now") or 0.0) + kdef
        wgt = played / (played + 6.0)
        mean = wgt * actual + (1 - wgt) * proj if actual is not None else proj
        teams[rid] = {"wins": r["wins"] or 0, "ties": r["ties"] or 0, "pf": r["fpts"] or 0.0, "mean": mean}

    schedule = defaultdict(list)
    seen = set()
    for r in store.query("SELECT week, roster_id, matchup_id FROM schedule WHERE league_id=? ORDER BY week, matchup_id",
                         (lid,)):
        key = (r["week"], r["matchup_id"])
        if key in seen:
            continue
        mates = [x["roster_id"] for x in store.query(
            "SELECT roster_id FROM schedule WHERE league_id=? AND week=? AND matchup_id=?",
            (lid, r["week"], r["matchup_id"]))]
        if len(mates) == 2:
            schedule[r["week"]].append((mates[0], mates[1]))
        seen.add(key)
    weeks = list(range(scored + 1, last_reg + 1))
    check = draft_rule_check(store)
    rule = check.get("rule", "playoff_finish")
    # this week's lineups, injuries, byes and points already scored (dynasty/weekly.py)
    fixed = defaultdict(dict)
    if _has(store, "week_team"):
        for r in store.query("SELECT roster_id, week, mean, sd FROM week_team"):
            if r["week"] in weeks and r["mean"] is not None:
                fixed[r["week"]][r["roster_id"]] = (r["mean"], max(r["sd"] or 0.0, 0.5))
    sim = simulate_season(teams, schedule, weeks, sd, playoff_teams, rule=rule, fixed=fixed) if teams else {}

    sims = sum(sim[next(iter(sim))]["slots"]) if sim else 1
    store.write_frame("season_sim", [
        {"roster_id": rid, "mean_weekly": round(teams[rid]["mean"], 1),
         "exp_wins": round(s["wins"] / sims, 2), "p_playoffs": round(s["playoffs"] / sims, 3),
         "p_title": round(s["titles"] / sims, 3),
         "p_first_pick": round(s["slots"][1] / sims, 3), "p_top3": round(sum(s["slots"][1:4]) / sims, 3),
         "exp_slot": round(sum(i * c for i, c in enumerate(s["slots"])) / sims, 2)}
        for rid, s in sim.items()
    ])

    # slot odds per draft season
    owned = store.query("SELECT * FROM draft_pick_owned ORDER BY season, round, original_roster_id")
    seasons = sorted({r["season"] for r in owned})
    odds: dict[str, dict[int, list[float]]] = {}
    for i, yr in enumerate(seasons):
        if int(yr) == season + 1 and sim:
            odds[yr] = {rid: [c / sims for c in s["slots"]] for rid, s in sim.items()}
        else:
            key = "strength_next" if int(yr) == season + 2 else "strength_later"
            strength = {rid: (t.get(key) or 0.0) for rid, t in tw.items()}
            spread = statistics.pstdev(strength.values()) if len(strength) > 1 else 1.0
            noise = max(1.0, spread * (1.0 if key == "strength_next" else 1.5))
            ss = strength_slots(strength, noise)
            tot = sum(next(iter(ss.values()))) or 1
            odds[yr] = {rid: [c / tot for c in s] for rid, s in ss.items()}

    curve = league_pick_curve(store)
    mk = market_picks(store)
    # put DynastyProcess pick values on FantasyCalc's scale via players valued by both
    k_dp = 1.0
    if _has(store, "player_value"):
        vd = store.query("SELECT MAX(snapshot_date) d FROM player_value")[0]["d"]
        ratios = [r["fc_value"] / r["dp_scaled"] for r in store.query(
            "SELECT fc_value, dp_scaled FROM player_value WHERE snapshot_date=? AND fc_value>500 AND dp_scaled>500",
            (vd,))]
        if ratios:
            k_dp = statistics.median(ratios)

    def slot_value(yr: str, rnd: int, slot: int, years_out: int) -> tuple[float, dict]:
        parts, detail = [], {}
        if curve:
            mv = curve_value(curve, (rnd - 1) * n + slot) * (0.9 ** years_out)
            parts.append((w["model"], mv))
            detail["model"] = mv
        fc = tier_value(mk["fantasycalc"], yr, rnd, slot, n)
        if fc is not None:
            parts.append((w["fantasycalc"], fc))
            detail["fc"] = fc
        dp = mk["exact"].get((yr, rnd, slot)) or tier_value(mk["dynastyprocess"], yr, rnd, slot, n)
        if dp is not None:
            parts.append((w["dynastyprocess"], dp * k_dp))
            detail["dp"] = dp * k_dp
        tot = sum(a for a, _ in parts)
        return (sum(a * b for a, b in parts) / tot if tot else 0.0), detail

    rows = []
    first = int(seasons[0]) if seasons else season + 1
    for r in owned:
        yr, rnd, orig = r["season"], r["round"], r["original_roster_id"]
        dist = odds.get(yr, {}).get(orig) or ([0.0] + [1.0 / n] * n)
        value = model_v = market_v = 0.0
        for slot in range(1, n + 1):
            p = dist[slot] if slot < len(dist) else 0.0
            if p <= 0:
                continue
            v, det = slot_value(yr, rnd, slot, int(yr) - first)
            value += p * v
            model_v += p * det.get("model", 0.0)
            market_v += p * det.get("fc", det.get("dp", 0.0))
        exp_slot = sum(i * p for i, p in enumerate(dist))
        rows.append({
            "season": yr, "round": rnd, "original_roster_id": orig, "owner_roster_id": r["owner_roster_id"],
            "exp_slot": round(exp_slot, 2),
            "p_slot1": round(dist[1], 3) if len(dist) > 1 else None,
            "p_top3": round(sum(dist[1:4]), 3),
            "value": round(value), "model_value": round(model_v), "market_value": round(market_v),
            "slot_odds": json.dumps([round(x, 3) for x in dist[1:]]),
        })
    store.write_frame("pick_value", rows)

    # pick capital on team_window
    cap = defaultdict(float)
    for r in rows:
        cap[r["owner_roster_id"]] += r["value"]
    for rid, t in tw.items():
        t["pick_value"] = round(cap.get(rid, 0.0))
        t["total_assets"] = round((t.get("player_value") or 0) + t["pick_value"])
    store.write_frame("team_window", list(tw.values()))
    store.commit()
    return {
        "simulated_weeks": len(weeks), "schedule_weeks": len(schedule), "team_sd": round(sd, 1),
        "kdef_weekly": round(kdef, 1), "pick_curve": bool(curve), "picks_valued": len(rows),
        "market_pick_rows": sum(len(v) for v in mk.values()),
        "draft_rule": check,
    }

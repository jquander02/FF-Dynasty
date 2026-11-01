"""Schedule tab: every team's weekly matchups and strength of schedule.

Played games (weeks already scored):
  result, score, and how hard the week was: the opponent's points that week against the league's
  average score.
  SOS played     opponents' season scoring average (points per game across all their games)
                 against the league average. Higher = tougher opponents.
  Points against per game, against the league average: how hot opponents were against you.
  All-play       the record a team would have if it played every other team every week. The gap
                 to the real record is schedule luck.

Games left (rest of the regular season, plus this week if it isn't over):
  opponent strength = his projected weekly score from the season simulation (season_sim.mean_weekly:
  this season's scoring blended with the projected best lineup); this week uses the Week tab's
  lineup-level projection (week_team) when there is one.
  SOS remaining  average opponent strength against the league average.
  Win odds per game from both teams' strengths and the league's week-to-week spread (team_sd), so
  expected wins left = the sum of those odds.

The current week counts as played once it's scored; while it's in progress it counts as left.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from collections import defaultdict

from .picks import team_sd
from .store import Store

log = logging.getLogger(__name__)


def _has(store: Store, t: str) -> bool:
    return bool(store.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)))


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _pairs(rows) -> dict:
    """week -> {roster: opponent} from (week, roster_id, matchup_id) rows."""
    by = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["matchup_id"] is not None:
            by[r["week"]][r["matchup_id"]].append(r["roster_id"])
    out = defaultdict(dict)
    for w, ms in by.items():
        for rids in ms.values():
            if len(rids) == 2:
                a, b = rids
                out[w][a], out[w][b] = b, a
    return out


def build(store: Store) -> dict:
    lgs = store.query("SELECT * FROM league_season WHERE is_current=1")
    if not lgs:
        return {}
    lg = lgs[0]
    lid = lg["league_id"]
    last_reg = int(lg["playoff_week_start"] or 15) - 1
    st = store.query("SELECT body FROM raw_snapshot WHERE endpoint='state/nfl'")
    state = json.loads(st[0]["body"]) if st and st[0]["body"] else {}
    in_season = str(state.get("season")) == str(lg["season"]) and state.get("season_type") == "regular"
    cur_week = int(state.get("week") or 0) if in_season else 0
    scored = int(lg["last_scored_leg"] or 0)
    if in_season:
        scored = max(scored, cur_week - 1)
    elif state.get("season_type") == "post" or lg["status"] == "complete":
        scored = last_reg

    rosters = [r["roster_id"] for r in store.query("SELECT roster_id FROM roster WHERE league_id=?", (lid,))]
    if not rosters:
        return {}
    played = _pairs(store.query("SELECT week, roster_id, matchup_id FROM matchup WHERE league_id=? AND week<=?",
                                (lid, last_reg)))
    future = _pairs(store.query("SELECT week, roster_id, matchup_id FROM schedule WHERE league_id=? AND week<=?",
                                (lid, last_reg)))
    pts = {(r["week"], r["roster_id"]): r["points"] or 0.0 for r in store.query(
        "SELECT week, roster_id, points FROM matchup WHERE league_id=? AND week<=?", (lid, scored))}

    # ---- played weeks
    done_weeks = sorted(w for w in played if w <= scored)
    week_avg = {w: statistics.mean([pts.get((w, r), 0.0) for r in rosters]) for w in done_weeks}
    league_avg = statistics.mean(week_avg.values()) if week_avg else None
    season_ppg = {}
    for r in rosters:
        xs = [pts[(w, r)] for w in done_weeks if (w, r) in pts]
        season_ppg[r] = statistics.mean(xs) if xs else None

    # ---- strength going forward
    sd = team_sd(store)
    strength = {}
    if _has(store, "season_sim"):
        for r in store.query("SELECT roster_id, mean_weekly FROM season_sim"):
            if r["mean_weekly"] is not None:
                strength[r["roster_id"]] = r["mean_weekly"]
    this_week = {}
    if _has(store, "week_team") and cur_week and cur_week > scored:
        for r in store.query("SELECT roster_id, week, mean, sd FROM week_team WHERE week=?", (cur_week,)):
            this_week[r["roster_id"]] = (r["mean"], r["sd"])
    if not strength:
        strength = {r: (season_ppg.get(r) or league_avg or 100.0) for r in rosters}
    avg_strength = statistics.mean(strength.values())

    teams, games = {}, {}
    for r in rosters:
        rows = []
        wins = losses = ties = 0
        ap_w = ap_l = 0.0
        opp_ppg, against = [], []
        left_strength, exp_left = [], 0.0
        for w in range(1, last_reg + 1):
            if w <= scored and w in played and r in played[w]:
                o = played[w][r]
                pf, pa = pts.get((w, r), 0.0), pts.get((w, o), 0.0)
                res = "W" if pf > pa else "L" if pf < pa else "T"
                wins += res == "W"
                losses += res == "L"
                ties += res == "T"
                others = [pts.get((w, x), 0.0) for x in rosters if x != r]
                ap_w += sum(pf > x for x in others) + 0.5 * sum(pf == x for x in others)
                ap_l += sum(pf < x for x in others) + 0.5 * sum(pf == x for x in others)
                if season_ppg.get(o) is not None:
                    opp_ppg.append(season_ppg[o])
                against.append(pa)
                rows.append({"week": w, "opp": o, "st": "final", "pf": round(pf, 2), "pa": round(pa, 2), "res": res,
                             "opp_avg": round(season_ppg[o], 1) if season_ppg.get(o) is not None else None,
                             "hard": round(pa - week_avg[w], 1)})
                continue
            o = (future.get(w) or played.get(w) or {}).get(r)
            if o is None:
                rows.append({"week": w, "opp": None, "st": "none"})
                continue
            if w == cur_week and r in this_week and o in this_week:
                (m1, s1), (m2, s2) = this_week[r], this_week[o]
                p = _phi((m1 - m2) / math.sqrt(max(1e-6, s1 * s1 + s2 * s2)))
                opp_str = m2
            else:
                m1, m2 = strength.get(r, avg_strength), strength.get(o, avg_strength)
                p = _phi((m1 - m2) / (sd * math.sqrt(2)))
                opp_str = m2
            left_strength.append(opp_str)
            exp_left += p
            rows.append({"week": w, "opp": o, "st": "live" if w == cur_week and cur_week > scored else "next",
                         "opp_proj": round(opp_str, 1), "win": round(p, 3),
                         "now": round(pts.get((w, r), 0.0), 2) if w == cur_week else None})
        n = wins + losses + ties
        teams[r] = {
            "record": [wins, losses, ties],
            "allplay": [round(ap_w, 1), round(ap_l, 1)],
            "allplay_pct": round(ap_w / (ap_w + ap_l), 3) if ap_w + ap_l else None,
            # wins above what the all-play rate would give over the same games: schedule luck
            "luck": round((wins + 0.5 * ties) - n * ap_w / (ap_w + ap_l), 2) if ap_w + ap_l else None,
            "sos_played": round(statistics.mean(opp_ppg) - league_avg, 1) if opp_ppg and league_avg else None,
            "opp_ppg": round(statistics.mean(opp_ppg), 1) if opp_ppg else None,
            "pa_pg": round(statistics.mean(against), 1) if against else None,
            "pa_vs_avg": round(statistics.mean(against) - league_avg, 1) if against and league_avg else None,
            "sos_left": round(statistics.mean(left_strength) - avg_strength, 1) if left_strength else None,
            "opp_left": round(statistics.mean(left_strength), 1) if left_strength else None,
            "games_left": len(left_strength),
            "exp_wins_left": round(exp_left, 2),
            "strength": round(strength.get(r, avg_strength), 1),
            "ppg": round(season_ppg[r], 1) if season_ppg.get(r) is not None else None,
        }
        games[r] = rows

    # ranks: 1 = toughest
    for key in ("sos_played", "sos_left"):
        order = sorted((t[key], r) for r, t in teams.items() if t[key] is not None)
        for i, (_, r) in enumerate(reversed(order), 1):
            teams[r][f"{key}_rank"] = i

    report = {"week": cur_week or None, "scored": scored, "last_regular_week": last_reg,
              "playoff_week_start": last_reg + 1, "league_avg": round(league_avg, 1) if league_avg else None,
              "avg_strength": round(avg_strength, 1), "team_sd": round(sd, 1),
              "teams": {str(r): t for r, t in teams.items()}, "games": {str(r): g for r, g in games.items()}}
    store.snapshot("schedule_report", report)
    store.commit()
    hardest = min(teams, key=lambda r: teams[r].get("sos_left_rank") or 99)
    return {"teams": len(teams), "weeks_played": len(done_weeks), "league_avg": report["league_avg"],
            "toughest_left": hardest}


def report(store: Store) -> dict | None:
    r = store.query("SELECT body FROM raw_snapshot WHERE endpoint='schedule_report'")
    return json.loads(r[0]["body"]) if r and r[0]["body"] else None

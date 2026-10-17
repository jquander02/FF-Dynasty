"""This week in the league: a projection for every player, lineup checks, matchup win odds and
waiver-wire targets with FAAB bids.

Weekly projection, QB/RB/WR/TE:
    the model's rest-of-season points per game
    x (team's implied points this week / its season-average implied points) ** 0.5
    x chance he plays (injury designation); 0 on a bye.
The 0.5 exponent was fit on 2015-20 player-weeks and checked on 2021-25: weekly RMSE 7.28 -> 7.26.
Bigger exponents, the raw (not team-relative) implied total and the opponent's points allowed to the
position all made it worse or added nothing. Kickers use their league-scored points per game, defenses
this league's points for them, both adjusted by the line (defenses by the opponent's implied total).

Spread of one player's score, given he plays: a + b * projection, fit by position on 2018-25
player-weeks. Teammates' misses barely correlate (0.04), so a team's variance is the sum.

Games already over count their actual points; games in progress count points so far plus the
unplayed share of the projection; started players are locked in place, like on Sleeper.

Waivers: for every team and free agent, the points he'd add to that team's best lineup in each
remaining week (playoff weeks weighted by the team's playoff odds), plus his model value beyond
this season. The bid pays only for what he adds over the next-best free agent (who costs nothing):
a share of the team's remaining budget that grows with that edge, held to just above what the
most interested rival should pay.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .model import FLEX_ELIGIBLE
from .store import Store

log = logging.getLogger(__name__)

VEGAS_BETA = 0.5          # skill players and kickers: own team's implied total
DEF_BETA = 1.0            # defenses: opponent's implied total (inverse)
SD_FIT = {"QB": (6.96, 0.078), "RB": (3.18, 0.346), "WR": (2.89, 0.394), "TE": (2.13, 0.45),
          "K": (4.5, 0.0), "DEF": (6.0, 0.0)}
PLAY = {"Questionable": 0.8, "Doubtful": 0.2, "Out": 0.0, "IR": 0.0, "PUP": 0.0, "Sus": 0.0,
        "NA": 0.0, "COV": 0.0, "DNR": 0.0}
LONG_OUT = {"IR", "PUP", "Sus", "NA"}
GAME_MINUTES = 190        # kickoff to final whistle, typical
FINAL_AFTER = 225         # treat as final after this many minutes
TEAM_CODE = {"LA": "LAR", "STL": "LAR", "SD": "LAC", "OAK": "LV"}  # nflverse -> Sleeper
SLOTS = {"QB", "RB", "WR", "TE", "K", "DEF", *FLEX_ELIGIBLE}
WORTH_HALF = 150.0        # worth (points) at which a team should spend half its remaining FAAB
MAX_SHARE = 0.75
FUTURE_FALLBACK = 0.7     # share of model value that's beyond this season, when not stored


def _has(store: Store, table: str) -> bool:
    return bool(store.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)))


def _cols(store: Store, table: str) -> set[str]:
    return {r["name"] for r in store.query(f"PRAGMA table_info({table})")}


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def win_prob(m1: float, v1: float, m2: float, v2: float) -> float:
    s = math.sqrt(max(v1 + v2, 0.0))
    if s < 1e-6:
        return 1.0 if m1 > m2 else 0.0 if m1 < m2 else 0.5
    return phi((m1 - m2) / s)


def _kickoff(day: str | None, time: str | None) -> datetime | None:
    """nflverse gameday/gametime are US Eastern."""
    if not day:
        return None
    try:
        hh, mm = (time or "13:00").split(":")[:2]
        naive = datetime.fromisoformat(str(day)[:10]).replace(hour=int(hh), minute=int(mm))
    except ValueError:
        return None
    try:
        from zoneinfo import ZoneInfo
        return naive.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
    except Exception:  # noqa: BLE001 - no tz database: daylight time covers most of the season
        return (naive + timedelta(hours=4)).replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------- games
def load_games(store: Store, season: int) -> tuple[dict, dict, float]:
    """(week, team) -> game; team -> season-average implied points (shrunk); league average."""
    if not _has(store, "nfl_schedules"):
        return {}, {}, 22.5
    cols = _cols(store, "nfl_schedules")
    need = {"season", "week", "home_team", "away_team"}
    if not need <= cols:
        return {}, {}, 22.5
    pick = [c for c in ("game_type", "gameday", "gametime", "spread_line", "total_line", "result") if c in cols]
    rows = store.query(f"SELECT season, week, home_team, away_team{''.join(', ' + c for c in pick)} "
                       "FROM nfl_schedules WHERE CAST(season AS INTEGER)=?", (season,))
    games, imps = {}, defaultdict(list)
    for r in rows:
        r = dict(r)
        if r.get("game_type") not in (None, "REG", "WC", "DIV", "CON", "SB"):
            continue
        wk = int(r["week"])
        home, away = TEAM_CODE.get(r["home_team"], r["home_team"]), TEAM_CODE.get(r["away_team"], r["away_team"])
        tot, spr = _f(r.get("total_line")), _f(r.get("spread_line"))
        ih = ia = None
        if tot is not None and spr is not None:
            ih, ia = (tot + spr) / 2, (tot - spr) / 2      # spread_line > 0: home favoured
            imps[home].append(ih)
            imps[away].append(ia)
        kick = _kickoff(r.get("gameday"), r.get("gametime"))
        done = r.get("result") not in (None, "")
        games[(wk, home)] = {"opp": away, "home": True, "kick": kick, "implied": ih, "done": done}
        games[(wk, away)] = {"opp": home, "home": False, "kick": kick, "implied": ia, "done": done}
    allv = [x for v in imps.values() for x in v]
    league = sum(allv) / len(allv) if allv else 22.5
    avg = {t: (sum(v) + 4 * league) / (len(v) + 4) for t, v in imps.items()}
    return games, avg, league


def game_status(g: dict | None, now: datetime) -> tuple[str, float]:
    """('none'|'later'|'live'|'final', share of the game still to play)."""
    if not g:
        return "none", 0.0
    if g.get("done"):
        return "final", 0.0
    k = g.get("kick")
    if k is None or now < k:
        return "later", 1.0
    mins = (now - k).total_seconds() / 60
    if mins >= FINAL_AFTER:
        return "final", 0.0
    return "live", max(0.03, 1.0 - mins / GAME_MINUTES)


# ---------------------------------------------------------------------------- lineups
def slot_order(slots: list[str]) -> list[int]:
    """Fill single-position slots first, then flex slots from narrowest to widest."""
    return sorted(range(len(slots)), key=lambda i: (slots[i] in FLEX_ELIGIBLE,
                                                    len(FLEX_ELIGIBLE.get(slots[i], ())), i))


def eligible(slot: str) -> tuple:
    return FLEX_ELIGIBLE.get(slot, (slot,))


def best_lineup(slots: list[str], pool: list[tuple[str, str, float]], fixed: dict[int, str] | None = None,
                order: list[int] | None = None) -> list[str | None]:
    """pool: (pid, pos, points). Greedy fill (optimal for nested flex slots). fixed: slot -> pid."""
    fixed = fixed or {}
    out: list[str | None] = [None] * len(slots)
    used = set(fixed.values())
    for i, pid in fixed.items():
        out[i] = pid
    by_pos = defaultdict(list)
    for pid, pos, pts in pool:
        if pid not in used:
            by_pos[pos].append((pts, pid))
    for lst in by_pos.values():
        lst.sort(reverse=True)
    ptr = defaultdict(int)
    for i in order or slot_order(slots):
        if i in fixed:
            continue
        best = None
        for pos in eligible(slots[i]):
            lst, j = by_pos.get(pos, []), ptr[pos]
            while j < len(lst) and lst[j][1] in used:
                j += 1
            ptr[pos] = j
            if j < len(lst) and (best is None or lst[j][0] > best[0]):
                best = (lst[j][0], lst[j][1], pos)
        if best:
            out[i] = best[1]
            used.add(best[1])
            ptr[best[2]] += 1
    return out


def lineup_points(slots: list[str], by_pos: dict[str, list[float]], order: list[int]) -> float:
    """Best total from sorted-descending points lists per position (no ids: fast what-ifs)."""
    ptr = defaultdict(int)
    total = 0.0
    for i in order:
        best, bp = None, None
        for pos in eligible(slots[i]):
            lst, j = by_pos.get(pos), ptr[pos]
            if lst and j < len(lst) and (best is None or lst[j] > best):
                best, bp = lst[j], pos
        if bp is not None:
            total += best
            ptr[bp] += 1
    return total


# ---------------------------------------------------------------------------- projections
class Projector:
    def __init__(self, store: Store, season: int, week: int, games: dict, avg_imp: dict, league_imp: float,
                 lid: str):
        self.week, self.games, self.avg_imp, self.league_imp = week, games, avg_imp, league_imp
        self.info = {r["player_id"]: dict(r) for r in store.query(
            "SELECT player_id, full_name, position, team, injury_status, status, age FROM player "
            "WHERE position IN ('QB','RB','WR','TE','K','DEF')")}
        self.proj = {}
        if _has(store, "player_projection"):
            pc = _cols(store, "player_projection")
            extra = ", ".join(c for c in ("ros_share", "value_future", "model_value") if c in pc)
            for r in store.query(f"SELECT player_id, ppg_ros{', ' + extra if extra else ''} FROM player_projection"):
                self.proj[r["player_id"]] = dict(r)
        self.kicker = self._kickers(store, season)
        self.defense, self.def_mean = self._defenses(store, lid)

    def _kickers(self, store: Store, season: int) -> dict:
        if not _has(store, "player_week"):
            return {}
        rows = store.query("SELECT player_id, season, pts FROM player_week WHERE position='K' AND season>=? "
                           "AND player_id IS NOT NULL", (season - 1,))
        cur, last = defaultdict(list), defaultdict(list)
        for r in rows:
            (cur if int(r["season"]) == season else last)[r["player_id"]].append(r["pts"] or 0.0)
        allp = [x for v in list(cur.values()) + list(last.values()) for x in v]
        mean = sum(allp) / len(allp) if allp else 8.0
        out = {}
        for pid in set(cur) | set(last):
            c, lp = cur.get(pid, []), last.get(pid, [])
            prior = (sum(lp) + 4 * mean) / (len(lp) + 4)
            out[pid] = (sum(c) + 4 * prior) / (len(c) + 4)
        out["_mean"] = mean
        return out

    def _defenses(self, store: Store, lid: str) -> tuple[dict, float]:
        rows = store.query(
            """SELECT mp.player_id, mp.week, MAX(mp.points) pts FROM matchup_player mp
               JOIN player p ON p.player_id=mp.player_id
               WHERE mp.league_id=? AND p.position='DEF' AND mp.week<? GROUP BY mp.player_id, mp.week""",
            (lid, self.week))
        by = defaultdict(list)
        for r in rows:
            by[r["player_id"]].append(r["pts"] or 0.0)
        allp = [x for v in by.values() for x in v]
        mean = sum(allp) / len(allp) if len(allp) >= 10 else 7.0
        return {pid: (sum(v) + 6 * mean) / (len(v) + 6) for pid, v in by.items()}, mean

    def name(self, pid):
        return (self.info.get(pid) or {}).get("full_name") or pid

    def pos(self, pid):
        return (self.info.get(pid) or {}).get("position")

    def team(self, pid):
        i = self.info.get(pid) or {}
        return pid if i.get("position") == "DEF" else i.get("team")

    def base(self, pid) -> float | None:
        pos = self.pos(pid)
        if pos == "K":
            return self.kicker.get(pid, self.kicker.get("_mean", 8.0) * 0.9)
        if pos == "DEF":
            return self.defense.get(pid, self.def_mean)
        p = self.proj.get(pid)
        return p["ppg_ros"] if p and p.get("ppg_ros") is not None else None

    def future_value(self, pid) -> float:
        p = self.proj.get(pid)
        if not p:
            return 0.0
        if p.get("value_future") is not None:
            return max(0.0, p["value_future"])
        return max(0.0, (p.get("model_value") or 0.0) * FUTURE_FALLBACK)

    def play_prob(self, pid, w) -> float:
        i = self.info.get(pid) or {}
        pos, inj = i.get("position"), i.get("injury_status") or ""
        if pos == "DEF":
            return 1.0
        ahead = w - self.week
        if ahead == 0:
            return PLAY.get(inj, 1.0)
        share = (self.proj.get(pid) or {}).get("ros_share")
        share = 0.9 if share is None else min(1.0, max(0.0, share))
        if pos == "K":
            share = 0.97
        if inj in LONG_OUT and ahead < 4:
            return 0.0
        if inj == "Out" and ahead == 1:
            return share * 0.6
        return share

    def dist(self, pid, w) -> tuple[float, float]:
        """(mean, variance) of his points in week w, before any are scored."""
        team = self.team(pid)
        g = self.games.get((w, team)) if team else None
        if not g:
            # no schedule loaded: assume he plays; with a schedule, no game means a bye
            if self.games or not team:
                return 0.0, 0.0
            g = {}
        b = self.base(pid)
        if not b or b <= 0:
            return 0.0, 0.0
        pos = self.pos(pid)
        fac = 1.0
        if pos == "DEF":
            opp = self.games.get((w, g.get("opp")), {}) if g else {}
            if opp.get("implied"):
                fac = (self.league_imp / opp["implied"]) ** DEF_BETA
        elif g.get("implied") and self.avg_imp.get(team):
            fac = (g["implied"] / self.avg_imp[team]) ** VEGAS_BETA
        m = b * fac
        a, k = SD_FIT.get(pos, (5.0, 0.3))
        sd = a + k * m
        p = self.play_prob(pid, w)
        mean = p * m
        return mean, max(0.0, p * (sd * sd + m * m) - mean * mean)


# ---------------------------------------------------------------------------- build
def _playoff_weeks(lg) -> list[int]:
    start = int(lg["playoff_week_start"] or 15)
    teams = int(lg["playoff_teams"] or 6)
    rounds = max(1, math.ceil(math.log2(max(2, teams))))
    return list(range(start, start + rounds))


def build(store: Store, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    lgs = store.query("SELECT * FROM league_season WHERE is_current=1")
    if not lgs:
        return {}
    lg = lgs[0]
    lid, season = lg["league_id"], int(lg["season"])
    st = store.query("SELECT body FROM raw_snapshot WHERE endpoint='state/nfl'")
    state = json.loads(st[0]["body"]) if st and st[0]["body"] else {}
    week = int(state.get("week") or 0)
    playoff_weeks = _playoff_weeks(lg)
    last_reg = playoff_weeks[0] - 1
    in_season = (str(state.get("season")) == str(season) and state.get("season_type") in ("regular", "post")
                 and 1 <= week <= playoff_weeks[-1])
    if not in_season:
        store.snapshot("weekly", {"week": None})
        store.conn.execute("DROP TABLE IF EXISTS week_team")
        return {"week": None}

    roster_positions = json.loads(lg["roster_positions"] or "[]")
    slots = [s for s in roster_positions if s in SLOTS]
    order = slot_order(slots)
    games, avg_imp, league_imp = load_games(store, season)
    P = Projector(store, season, week, games, avg_imp, league_imp, lid)

    # rosters: latest snapshot; ordered starters from the raw rosters response
    snap = store.query("SELECT MAX(snapshot_date) d FROM roster_snapshot WHERE league_id=?", (lid,))[0]["d"]
    roster, slot_of = defaultdict(list), {}
    for r in store.query("SELECT roster_id, player_id, slot FROM roster_snapshot WHERE league_id=? AND snapshot_date=?",
                         (lid, snap)):
        roster[r["roster_id"]].append(r["player_id"])
        slot_of[r["player_id"]] = r["slot"]
    starters = {}
    raw = store.query("SELECT body FROM raw_snapshot WHERE endpoint=?", (f"league/{lid}/rosters",))
    for r in (json.loads(raw[0]["body"]) if raw and raw[0]["body"] else []) or []:
        s = list(r.get("starters") or [])
        if len(s) == len(slots):
            starters[r["roster_id"]] = [x if x and x != "0" else None for x in s]
    for rid, pids in roster.items():
        if rid not in starters:     # no ordered lineup: place the starters as Sleeper would
            on = [(p, P.pos(p), 1.0) for p in pids if slot_of.get(p) == "starter"]
            starters[rid] = best_lineup(slots, on, order=order)

    # this week's scoring so far
    matchup_of, live_pts = {}, {}
    for r in store.query("SELECT roster_id, matchup_id FROM matchup WHERE league_id=? AND week=?", (lid, week)):
        matchup_of[r["roster_id"]] = r["matchup_id"]
    if not matchup_of:
        for r in store.query("SELECT roster_id, matchup_id FROM schedule WHERE league_id=? AND week=?", (lid, week)):
            matchup_of[r["roster_id"]] = r["matchup_id"]
    for r in store.query("SELECT player_id, points FROM matchup_player WHERE league_id=? AND week=?", (lid, week)):
        live_pts[r["player_id"]] = r["points"] or 0.0

    status_cache = {}

    def status(pid):
        if pid not in status_cache:
            t = P.team(pid)
            g = games.get((week, t)) if t else None
            if not games:                   # no schedule loaded: nothing is known to have started
                status_cache[pid] = ("later", 1.0)
            else:
                status_cache[pid] = game_status(g, now) if g else ("bye", 0.0)
        return status_cache[pid]

    def now_dist(pid) -> tuple[float, float, float]:
        """(expected final points, variance, points so far) for this week."""
        stt, left = status(pid)
        so_far = live_pts.get(pid, 0.0)
        m, v = P.dist(pid, week)
        if stt == "final":
            return so_far, 0.0, so_far
        if stt == "live":
            return so_far + left * m, left * v, so_far
        return m, v, 0.0

    def locked(pid) -> bool:
        return status(pid)[0] in ("live", "final")

    active = {rid: [p for p in pids if slot_of.get(p) in ("starter", "bench")] for rid, pids in roster.items()}

    # ---- lineups and matchups
    team_out = {}
    for rid in roster:
        cur = starters[rid]
        set_mean = set_var = so_far = 0.0
        for pid in cur:
            if pid:
                m, v, s = now_dist(pid)
                set_mean += m
                set_var += v
                so_far += s
        fixed = {i: p for i, p in enumerate(cur) if p and locked(p)}
        pool = [(p, P.pos(p), now_dist(p)[0]) for p in active[rid] if not locked(p)]
        best = best_lineup(slots, pool, fixed, order)
        best_mean = best_var = 0.0
        for pid in best:
            if pid:
                m, v, _ = now_dist(pid)
                best_mean += m
                best_var += v
        start = [p for p in best if p and p not in cur]
        sit = [p for p in cur if p and p not in best]
        issues = []
        for i, pid in enumerate(cur):
            if pid is None:
                issues.append({"slot": slots[i], "pid": None, "why": "Empty slot"})
                continue
            stt = status(pid)[0]
            inj = (P.info.get(pid) or {}).get("injury_status") or ""
            if stt == "bye":
                issues.append({"slot": slots[i], "pid": pid, "why": "On bye"})
            elif not locked(pid) and PLAY.get(inj, 1.0) == 0.0:
                issues.append({"slot": slots[i], "pid": pid, "why": inj})
            elif not locked(pid) and inj in ("Doubtful", "Questionable"):
                issues.append({"slot": slots[i], "pid": pid, "why": inj, "minor": True})
        team_out[rid] = {
            "matchup": matchup_of.get(rid), "slots": [{"slot": s, "pid": p} for s, p in zip(slots, cur)],
            "mean": round(set_mean, 1), "sd": round(math.sqrt(set_var), 1), "now": round(so_far, 2),
            "best": {"mean": round(best_mean, 1), "sd": round(math.sqrt(best_var), 1),
                     "gain": round(best_mean - set_mean, 1), "start": start, "sit": sit,
                     "slots": [{"slot": s, "pid": p} for s, p in zip(slots, best)]},
            "issues": issues,
        }

    pairs, by_m = [], defaultdict(list)
    for rid, m in matchup_of.items():
        if m is not None and rid in team_out:
            by_m[m].append(rid)
    for m, rids in sorted(by_m.items()):
        if len(rids) != 2:
            continue
        a, b = rids
        A, B = team_out[a], team_out[b]
        va, vb = A["sd"] ** 2, B["sd"] ** 2
        A["opp"], B["opp"] = b, a
        A["win"] = round(win_prob(A["mean"], va, B["mean"], vb), 3)
        B["win"] = round(1 - A["win"], 3)
        A["best"]["win"] = round(win_prob(A["best"]["mean"], A["best"]["sd"] ** 2, B["mean"], vb), 3)
        B["best"]["win"] = round(win_prob(B["best"]["mean"], B["best"]["sd"] ** 2, A["mean"], va), 3)
        pairs.append([a, b])
    store.write_frame("week_team", [{"roster_id": rid, "week": week, "mean": t["mean"], "sd": t["sd"],
                                     "now": t["now"]} for rid, t in team_out.items()])

    # ---- waivers
    rostered = {p for pids in roster.values() for p in pids}
    weeks_left = [w for w in range(week, playoff_weeks[-1] + 1)]
    p_po = {r["roster_id"]: r["p_playoffs"] for r in store.query("SELECT roster_id, p_playoffs FROM season_sim")} \
        if _has(store, "season_sim") else {}

    def weight(rid, w):
        if w <= last_reg:
            return 1.0
        p = p_po.get(rid)
        return 0.7 * (0.5 if p is None else p)

    start_pos = {pos for s in slots for pos in eligible(s)}
    candidates = []
    for pid, i in P.info.items():
        if pid in rostered or i["position"] not in start_pos:
            continue
        if not P.team(pid):
            continue
        if i["position"] != "DEF" and i.get("status") not in (None, "Active") and P.future_value(pid) < 10:
            continue
        b = P.base(pid)
        if (not b or b < 2.0) and P.future_value(pid) < 10:
            continue
        candidates.append(pid)
    # this-week and later-week points for every player involved
    wk_pts = {}

    def pts(pid, w):
        k = (pid, w)
        if k not in wk_pts:
            if w == week:
                wk_pts[k] = 0.0 if (pid not in rostered and locked(pid)) else now_dist(pid)[0]
            else:
                wk_pts[k] = P.dist(pid, w)[0]
        return wk_pts[k]

    # free agents worth a look: best weekly projection across the rest of the season
    candidates.sort(key=lambda p: -(max(pts(p, w) for w in weeks_left[:4]) + P.future_value(p) / 10))
    candidates = candidates[:250]

    budget = lg["waiver_budget"] or 0
    left = {r["roster_id"]: budget - (r["waiver_budget_used"] or 0)
            for r in store.query("SELECT roster_id, waiver_budget_used FROM roster WHERE league_id=?", (lid,))}
    roster_cap = sum(1 for s in roster_positions if s in SLOTS or s == "BN")

    gains = defaultdict(dict)          # pid -> rid -> points added this season
    drops = {}
    for rid, pids in active.items():
        # this week, started players are locked: only the open slots and unlocked players can change
        open_now = [s for s, p in zip(slots, starters[rid]) if not (p and locked(p))]
        shape = {w: (open_now, slot_order(open_now)) if w == week else (slots, order) for w in weeks_left}
        base_lists, base_val = {}, {}
        for w in weeks_left:
            lists = defaultdict(list)
            for p in pids:
                if w == week and locked(p):
                    continue
                lists[P.pos(p)].append(pts(p, w))
            for lst in lists.values():
                lst.sort(reverse=True)
            base_lists[w] = lists
            base_val[w] = lineup_points(shape[w][0], lists, shape[w][1])

        def with_extra(pos, x, w):
            lists = base_lists[w]
            new = dict(lists)
            lst = list(lists.get(pos, []))
            lst.append(x)
            lst.sort(reverse=True)
            new[pos] = lst
            return lineup_points(shape[w][0], new, shape[w][1]) - base_val[w]

        for p in candidates:
            pos = P.pos(p)
            g = 0.0
            for w in weeks_left:
                x = pts(p, w)
                if x > 0:
                    g += weight(rid, w) * with_extra(pos, x, w)
            gains[p][rid] = g
        # drop candidate: the active player whose loss hurts least (lineup this season + future value)
        best_drop = None
        for q in pids:
            if q in starters[rid] and locked(q):
                continue
            pos = P.pos(q)
            loss = 0.0
            for w in weeks_left:
                if w == week and locked(q):
                    continue
                lists = base_lists[w]
                lst = list(lists.get(pos, []))
                x = pts(q, w)
                if x in lst:
                    lst.remove(x)
                new = dict(lists)
                new[pos] = lst
                loss += weight(rid, w) * (base_val[w] - lineup_points(shape[w][0], new, shape[w][1]))
            loss += P.future_value(q)
            if best_drop is None or loss < best_drop[1]:
                best_drop = (q, loss)
        drops[rid] = {"pid": best_drop[0], "loss": round(best_drop[1], 1),
                      "open": len(pids) < roster_cap} if best_drop else None

    def worth(rid, score):
        if not budget or score <= 0:
            return 0
        frac = min(MAX_SHARE, score / (score + WORTH_HALF))
        return int(round(max(0, left.get(rid, 0)) * frac))

    # FAAB pays only for what a player gives over the next-best free agent, who costs nothing:
    # lineup gain over the best other free agent at his position, future value over the best
    # other stash. Ten similar defenses are worth a $0 bid; the one breakout back is not.
    fut_of = {p: P.future_value(p) for p in candidates}
    fut_top = sorted(((v, p) for p, v in fut_of.items()), reverse=True)[:2]
    gain_top = {}
    for rid in active:
        by = defaultdict(list)
        for p in candidates:
            by[P.pos(p)].append((gains[p].get(rid, 0.0), p))
        gain_top[rid] = {pos: sorted(v, reverse=True)[:2] for pos, v in by.items()}

    def best_other(top, p):
        return next((v for v, q in top if q != p), 0.0)

    def excess(rid, p):
        g = gains[p].get(rid, 0.0) - best_other(gain_top[rid].get(P.pos(p), []), p)
        f = fut_of[p] - best_other(fut_top, p)
        return max(0.0, g) + max(0.0, f)

    waivers = {}
    for rid in active:
        recs = []
        d = drops.get(rid) or {}
        for p in candidates:
            fut = fut_of[p]
            score = gains[p].get(rid, 0.0) + fut
            net = score - (0.0 if d.get("open") else d.get("loss", 0.0))
            if score < 2.0 or net <= 1.0:
                continue
            ex = excess(rid, p)
            mine = worth(rid, ex)
            rivals = sorted(((worth(o, excess(o, p)), o) for o in active if o != rid), reverse=True)
            rivals = [(wv, o) for wv, o in rivals if wv > 0][:2]
            top = rivals[0][0] if rivals else 0
            if not budget:
                bid = None
            elif top >= mine:
                bid = mine
            else:
                bid = min(mine, max(top + 1, int(round(mine * 0.5))))
            recs.append({"pid": p, "gain": round(gains[p].get(rid, 0.0), 1), "future": round(fut, 1),
                         "score": round(score, 1), "excess": round(ex, 1), "net": round(net, 1),
                         "worth": mine, "bid": bid,
                         "rivals": [{"rid": o, "worth": wv} for wv, o in rivals],
                         "drop": None if d.get("open") else d.get("pid")})
        recs.sort(key=lambda r: -r["net"])
        waivers[rid] = recs[:12]

    # best free agents this week by position (streaming)
    available = {}
    for pos in sorted(start_pos):
        ranked = sorted((p for p in candidates if P.pos(p) == pos and pts(p, week) > 0),
                        key=lambda p: -pts(p, week))
        available[pos] = ranked[:8]

    bids = []
    if _has(store, "txn"):
        for r in store.query(
            """SELECT t.week, m.roster_id, m.player_id, m.amount FROM txn t JOIN txn_move m ON m.transaction_id=t.transaction_id
               WHERE t.league_id=? AND t.type='waiver' AND t.status='complete' AND m.kind='add' AND m.amount>0
               ORDER BY m.amount DESC LIMIT 8""", (lid,)):
            bids.append({"pid": r["player_id"], "rid": r["roster_id"], "amount": r["amount"], "week": r["week"]})
    nz = [r["amount"] for r in store.query(
        """SELECT m.amount FROM txn t JOIN txn_move m ON m.transaction_id=t.transaction_id
           WHERE t.league_id=? AND t.type='waiver' AND t.status='complete' AND m.kind='add' AND m.amount>0""",
        (lid,))] if _has(store, "txn") else []

    # ---- players referenced anywhere in the report
    ref = set()
    for t in team_out.values():
        ref |= {s["pid"] for s in t["slots"] if s["pid"]} | {s["pid"] for s in t["best"]["slots"] if s["pid"]}
    for rid in active:
        ref |= set(active[rid])
        if drops.get(rid):
            ref.add(drops[rid]["pid"])
    for recs in waivers.values():
        ref |= {r["pid"] for r in recs}
    for lst in available.values():
        ref |= set(lst)
    for b in bids:
        ref.add(b["pid"])
    players = {}
    for pid in ref:
        if not pid:
            continue
        i = P.info.get(pid) or {}
        t = P.team(pid)
        g = games.get((week, t)) if t else None
        stt, _ = status(pid)
        m, v, s = now_dist(pid)
        players[pid] = {
            "name": P.name(pid), "pos": P.pos(pid), "team": t, "inj": i.get("injury_status") or None,
            "opp": (("" if g["home"] else "@") + g["opp"]) if g else None,
            "kick": g["kick"].isoformat() if g and g.get("kick") else None,
            "st": stt, "proj": round(m, 1), "sd": round(math.sqrt(v), 1),
            "pts": round(s, 2) if stt in ("live", "final") else None,
            "ppg": round(P.base(pid), 1) if P.base(pid) else None,
            "rid": next((r for r, pids in roster.items() if pid in pids), None),
        }

    report = {
        "week": week, "season": season, "phase": "playoffs" if week > last_reg else "regular",
        "made": now.isoformat(timespec="seconds"), "last_regular_week": last_reg,
        "lines": bool(avg_imp), "budget": budget, "faab_left": left,
        "matchups": pairs, "teams": {str(k): v for k, v in team_out.items()},
        "waivers": {str(k): v for k, v in waivers.items()},
        "drop": {str(k): v for k, v in drops.items()},
        "available": available, "bids": bids,
        "bid_median": statistics.median(nz) if nz else None, "bid_count": len(nz),
        "players": players,
    }
    store.snapshot("weekly", report)
    store.commit()
    return {"week": week, "matchups": len(pairs), "free_agents": len(candidates),
            "lineup_gains": sum(1 for t in team_out.values() if t["best"]["gain"] >= 0.5),
            "lines": bool(avg_imp)}


def report(store: Store) -> dict | None:
    r = store.query("SELECT body FROM raw_snapshot WHERE endpoint='weekly'")
    return json.loads(r[0]["body"]) if r and r[0]["body"] else None

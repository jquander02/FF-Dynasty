"""Team planner: the best way forward for every team, and a plan for each of the next three seasons.

Strategy comes from this season's playoff and title odds and each team's projected best-lineup
strength this season, next season and the one after (ranked against the league):

  Stay on top             contending now and still a top lineup two seasons out
  Push for the title      contending now, but the lineup fades by two seasons out
  Build for next season   in the playoff hunt now, much stronger later: don't spend the future
  Decide at the deadline  on the bubble now, no stronger later: buy only if the odds climb
  Reload                  out of the race now, close to a top lineup within a season or two
  Rebuild                 out of the race now and short of a top lineup in both later seasons

Each strategy has a target season, the one the team aims to win. From it:
  buy    players on other teams who'd start for this team in the target season, held by teams
         that won't start them in *their* target season (so they're realistically available)
  sell   builders: players who won't help in the target season (age cliff, falling projection,
         or not good enough to start by then), with the teams they'd start for.
         contenders: "trade chips", bench depth and future picks to spend on upgrades
  keep   the players the target season is built on
  years  2026 (rest of season), 2027 and 2028: lineup rank as the roster stands, weak and strong
         spots against the league's median starter at each slot, players aging out and rising,
         draft picks that year, and the moves for that season
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict

from .model import FLEX_ELIGIBLE, POSITIONS
from .store import Store
from .teams import OUT_STATUSES

CLIFF = {"QB": 32, "RB": 26, "WR": 28, "TE": 29}
KEYS = ("ppg_ros", "ppg_y1", "ppg_y2")
YEARS = 3

STRATEGY = {
    "sustain": "Stay on top",
    "allin": "Push for the title",
    "climb": "Compete now, peak in {target}",
    "run": "Make a run, then retool",
    "next": "Build for {target}",
    "deadline": "Decide at the deadline",
    "reload": "Reload for {target}",
    "rebuild": "Rebuild for {target}",
}
CONTENDING = ("sustain", "allin", "climb", "run")


def lineup_slots(players: list[tuple], roster_positions: list[str]) -> list[dict]:
    """players: (player_id, pos, ppg). Best lineup with a label per slot (QB1, RB1, RB2, FLEX...).
    Same fill order as teams.best_lineup: fixed slots first, then flex slots take the best left."""
    by_pos = defaultdict(list)
    for pid, pos, ppg in players:
        if pos in POSITIONS and ppg is not None:
            by_pos[pos].append((ppg, pid))
    for lst in by_pos.values():
        lst.sort(reverse=True)
    used, out, count = set(), [], Counter()
    for s in roster_positions:
        if s not in POSITIONS:
            continue
        count[s] += 1
        hit = next(((ppg, pid) for ppg, pid in by_pos[s] if pid not in used), None)
        if hit:
            used.add(hit[1])
        out.append({"label": f"{s}{count[s]}", "type": s, "pid": hit[1] if hit else None,
                    "ppg": hit[0] if hit else 0.0, "pos": s})
    nflex = 0
    for s in roster_positions:
        if s not in FLEX_ELIGIBLE:
            continue
        nflex += 1
        best = None
        for pos in FLEX_ELIGIBLE[s]:
            for ppg, pid in by_pos[pos]:
                if pid not in used:
                    if best is None or ppg > best[0]:
                        best = (ppg, pid, pos)
                    break
        if best:
            used.add(best[1])
        label = ("SFLEX" if s == "SUPER_FLEX" else "FLEX") + ("" if nflex == 1 else str(nflex))
        out.append({"label": label, "type": s, "pid": best[1] if best else None,
                    "ppg": best[0] if best else 0.0, "pos": best[2] if best else None})
    return out


def _eligible(slot: dict, pos: str) -> bool:
    return slot["type"] == pos or pos in FLEX_ELIGIBLE.get(slot["type"], ())


class League:
    """Everything the planner needs, loaded once for all teams."""

    def __init__(self, store: Store):
        lg = store.query("SELECT * FROM league_season WHERE is_current=1")[0]
        self.lid = lid = lg["league_id"]
        self.season = int(lg["season"])
        self.positions = json.loads(lg["roster_positions"] or "[]")
        self.deadline = lg["trade_deadline"]
        st = store.query("SELECT body FROM raw_snapshot WHERE endpoint='state/nfl'")
        self.week = (json.loads(st[0]["body"]) if st and st[0]["body"] else {}).get("week") or 0
        self.weeks_to_deadline = max(0, (self.deadline or 0) - self.week) if self.deadline else None

        self.tw = {r["roster_id"]: dict(r) for r in store.query("SELECT * FROM team_window")}
        self.sim = {r["roster_id"]: dict(r) for r in store.query("SELECT * FROM season_sim")}
        self.names = {
            r["roster_id"]: (r["n"] or "").strip() for r in store.query(
                """SELECT r.roster_id, COALESCE(m.team_name, o.display_name) n FROM roster r
                   LEFT JOIN owner o ON o.user_id=r.owner_id
                   LEFT JOIN league_member m ON m.league_id=r.league_id AND m.user_id=r.owner_id
                   WHERE r.league_id=?""", (lid,))
        }
        self.record = {r["roster_id"]: dict(r) for r in store.query(
            "SELECT roster_id, wins, losses, ties FROM roster WHERE league_id=?", (lid,))}
        vd = store.query("SELECT MAX(snapshot_date) d FROM player_value")[0]["d"]
        self.val = {r["player_id"]: dict(r) for r in store.query("SELECT * FROM player_value WHERE snapshot_date=?", (vd,))}
        self.proj = {r["player_id"]: dict(r) for r in store.query("SELECT * FROM player_projection")}
        self.info = {r["player_id"]: dict(r) for r in store.query(
            "SELECT player_id, full_name, team, injury_status FROM player")}
        snap = store.query("SELECT MAX(snapshot_date) d FROM roster_snapshot WHERE league_id=?", (lid,))[0]["d"]
        self.rosters, self.owner_of, self.hurt = defaultdict(list), {}, set()
        for r in store.query("SELECT roster_id, player_id, slot FROM roster_snapshot WHERE league_id=? AND snapshot_date=?",
                             (lid, snap)):
            self.rosters[r["roster_id"]].append(r["player_id"])
            self.owner_of[r["player_id"]] = r["roster_id"]
            if r["slot"] == "reserve" or (self.info.get(r["player_id"], {}).get("injury_status") in OUT_STATUSES):
                self.hurt.add(r["player_id"])
        self.picks = [dict(r) for r in store.query("SELECT * FROM pick_value ORDER BY season, round, exp_slot")]
        self.rids = sorted(self.rosters)
        self.n = len(self.rids) or 12

        # lineups for each team and season, league medians per slot, ranks
        self.lineups = {rid: [self._lineup(rid, y) for y in range(YEARS)] for rid in self.rids}
        self.strength = {rid: [round(sum(s["ppg"] for s in lu), 1) for lu in self.lineups[rid]] for rid in self.rids}
        self.rank = {rid: [0] * YEARS for rid in self.rids}
        for y in range(YEARS):
            for i, rid in enumerate(sorted(self.rids, key=lambda r: -self.strength[r][y]), 1):
                self.rank[rid][y] = i
        self.median = []
        for y in range(YEARS):
            by_label = defaultdict(list)
            for rid in self.rids:
                for s in self.lineups[rid][y]:
                    by_label[s["label"]].append(s["ppg"])
            self.median.append({k: statistics.median(v) for k, v in by_label.items()})
        self.median_strength = [statistics.median([self.strength[r][y] for r in self.rids]) for y in range(YEARS)]
        assets = {rid: (self.tw.get(rid, {}).get("total_assets") or 0) for rid in self.rids}
        self.asset_rank = {rid: i for i, rid in enumerate(sorted(self.rids, key=lambda r: -assets[r]), 1)}
        self.strategy = {rid: self._strategy(rid) for rid in self.rids}

    # -- players --------------------------------------------------------------------
    def ppg(self, pid: str, y: int):
        p = self.proj.get(pid)
        return p.get(KEYS[y]) if p else None

    def age(self, pid: str, y: int = 0):
        p = self.proj.get(pid)
        return p["age"] + y if p and p.get("age") is not None else None

    def pos(self, pid: str):
        p = self.proj.get(pid)
        return p["position"] if p else None

    def value(self, pid: str) -> float:
        return (self.val.get(pid) or {}).get("value") or 0.0

    def past_cliff(self, pid: str, y: int) -> bool:
        a, pos = self.age(pid, y), self.pos(pid)
        return a is not None and pos in CLIFF and a >= CLIFF[pos] - 0.5

    def card(self, pid: str) -> dict:
        p, v, i = self.proj.get(pid, {}), self.val.get(pid, {}), self.info.get(pid, {})
        mkt = v.get("fc_value") if v.get("fc_value") is not None else v.get("dp_scaled")
        return {
            "id": pid, "name": i.get("full_name") or p.get("name") or pid, "pos": p.get("position"),
            "nfl": i.get("team"), "age": p.get("age"), "val": round(v.get("value") or 0),
            "model": round(v["model_scaled"]) if v.get("model_scaled") is not None else None,
            "market": round(mkt) if mkt is not None else None,
            "ppg_now": p.get("ppg_ros"), "ppg_next": p.get("ppg_y1"), "ppg_2y": p.get("ppg_y2"),
        }

    # -- lineups --------------------------------------------------------------------
    def _lineup(self, rid: int, y: int) -> list[dict]:
        rows = []
        for pid in self.rosters[rid]:
            if y == 0 and pid in self.hurt:
                continue
            ppg = self.ppg(pid, y)
            if ppg is not None:
                rows.append((pid, self.pos(pid), ppg))
        return lineup_slots(rows, self.positions)

    def starts(self, rid: int, y: int, pid: str) -> dict | None:
        return next((s for s in self.lineups[rid][y] if s["pid"] == pid), None)

    def upgrade(self, rid: int, y: int, pid: str) -> tuple[float, dict | None]:
        """PPG added if `pid` replaced the weakest starter he's eligible to replace on `rid` in season y."""
        pos, ppg = self.pos(pid), self.ppg(pid, y)
        if pos is None or ppg is None or (y == 0 and pid in self.hurt):
            return 0.0, None
        slots = [s for s in self.lineups[rid][y] if _eligible(s, pos)]
        if not slots:
            return 0.0, None
        weakest = min(slots, key=lambda s: s["ppg"])
        return ppg - weakest["ppg"], weakest

    # -- strategy -------------------------------------------------------------------
    def _strategy(self, rid: int) -> dict:
        r0, r1, r2 = self.rank[rid]
        sim = self.sim.get(rid, {})
        p, pt = sim.get("p_playoffs") or 0.0, sim.get("p_title") or 0.0
        top = max(1, round(self.n / 3))
        contending = p >= 0.6 or (p >= 0.4 and r0 <= 3)
        alive = p >= 0.2
        future = min(r1, r2)
        later_y = 1 if (r1 <= top + 1 or r1 <= r2) else 2
        if contending:
            if r0 <= top:
                key, target = ("sustain" if r2 <= top + 1 else "allin"), 0
            elif future <= top + 1:
                key, target = "climb", later_y
            else:
                key, target = "run", 0
        elif alive:
            key, target = ("next", later_y) if (future <= top and future < r0) else ("deadline", 0)
        elif future <= top + 2:
            key, target = "reload", later_y
        else:
            key, target = "rebuild", 2
        return {"key": key, "target": target, "p": p, "pt": pt}

    def target(self, rid: int) -> int:
        return self.strategy[rid]["target"]

    def available(self, rid: int, pid: str) -> bool:
        """Would team `rid` realistically move `pid`? Sellers (reload, rebuild) move veterans and
        anyone outside their target-season lineup; everyone else moves only bench players who don't
        start now or in their target season."""
        st = self.strategy[rid]
        age, T = self.age(pid), st["target"]
        young_core = age is not None and age <= 24 and self.value(pid) >= 800
        if st["key"] == "rebuild":
            return not young_core and ((age is not None and age >= 26) or self.starts(rid, T, pid) is None)
        if st["key"] in ("reload", "next", "climb"):
            return not young_core and (self.starts(rid, T, pid) is None or self.past_cliff(pid, T))
        return self.starts(rid, 0, pid) is None and self.starts(rid, T, pid) is None


def build_all(store: Store) -> dict:
    """A plan for every team, keyed by roster id."""
    lg = League(store)
    return {rid: _plan(lg, rid) for rid in lg.rids}


def build(store: Store, roster_id: int | None = None) -> dict:
    """Plan for one team (defaults to the configured user's team)."""
    lg = League(store)
    rid = roster_id
    if rid is None:
        me = store.query("SELECT user_id FROM owner WHERE is_me=1")
        if me:
            r = store.query("SELECT roster_id FROM roster WHERE league_id=? AND (owner_id=? OR co_owners LIKE ?)",
                            (lg.lid, me[0]["user_id"], f'%"{me[0]["user_id"]}"%'))
            rid = r[0]["roster_id"] if r else None
    if rid is None or rid not in lg.rosters:
        return {}
    return _plan(lg, rid)


def _ord(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _plan(lg: League, rid: int) -> dict:
    st = lg.strategy[rid]
    key, T = st["key"], st["target"]
    season = lg.season
    tseason = season + T
    r = lg.rank[rid]
    tw, sim = lg.tw.get(rid, {}), lg.sim.get(rid, {})
    dl = lg.deadline
    open_deadline = bool(lg.weeks_to_deadline)
    young_val = tw.get("value_young") or 0
    pval = tw.get("player_value") or 0

    title = STRATEGY[key].format(target=tseason)
    summary = {
        "sustain": f"A top lineup now and still one in {season + 2}. Add for this year's title with bench depth and "
                   f"distant picks, and keep the young core.",
        "allin": f"A top lineup now that fades by {season + 2}. Spend future picks and young bench players on starters "
                 f"for this year and next, then retool.",
        "climb": f"Winning now with a lineup that ranks {_ord(r[0])}, and better by {tseason}. Make cheap moves for "
                 f"this year's playoffs, but keep picks and young players for {tseason}.",
        "run": f"Winning now with a lineup that ranks {_ord(r[0])} and fades fast. Add cheap veterans for the playoff run "
               f"without paying in firsts, then sell aging starters in the offseason.",
        "next": f"In the playoff hunt, but the lineup is much stronger in {tseason}. Don't spend picks or young players "
                f"chasing this year; add starters for {tseason}.",
        "deadline": f"On the playoff bubble with no stronger season ahead as the roster stands. Buy cheap upgrades only "
                    f"if the playoff odds climb past 50% by week {dl}; otherwise sell veterans to contenders.",
        "reload": f"Out of the race this year but close to a top lineup in {tseason}. Sell veterans who won't matter by "
                  f"then, keep the young core, and add starters for {tseason}.",
        "rebuild": f"Out of the race and short of a top lineup in the next two seasons. Turn veterans into picks and "
                   f"young players and build toward {tseason}.",
    }[key]
    why = [
        f"{round(st['p'] * 100)}% playoff odds and {round(st['pt'] * 100)}% title odds this season",
        f"Best lineup ranks {_ord(r[0])} now, {_ord(r[1])} in {season + 1} and {_ord(r[2])} in {season + 2} of {lg.n}",
        f"Total value ranks {_ord(lg.asset_rank[rid])}"
        + (f", {round(100 * young_val / pval)}% of player value in players under 25" if pval else ""),
    ]

    # ---- buy: starters for the target season from teams that don't need them then ----------
    buy = []
    for o in lg.rids:
        if o == rid:
            continue
        for pid in lg.rosters[o]:
            v = lg.value(pid)
            if v < 500 or not lg.available(o, pid):
                continue
            if T > 0 and lg.past_cliff(pid, T):
                continue
            up, slot = lg.upgrade(rid, T, pid)
            if up < 1.0 or slot is None:
                continue
            ok = lg.strategy[o]
            if ok["key"] in ("reload", "rebuild"):
                why_avail = f"{lg.names.get(o)} is building for {season + ok['target']}"
            else:
                why_avail = f"on {lg.names.get(o)}'s bench"
            c = lg.card(pid)
            c.update(roster_id=o, team=lg.names.get(o), owner_strategy=STRATEGY[ok["key"]].format(target=season + ok["target"]),
                     upgrade_ppg=round(up, 1), season=season + T, slot=slot["label"],
                     reason=f"Would start at {slot['label']} in {season + T} (+{up:.1f} ppg); {why_avail}",
                     score=up * 1000 / max(v, 400))
            buy.append(c)
    buy.sort(key=lambda c: -c["score"])
    per_owner, kept = Counter(), []
    for c in buy:
        if per_owner[c["roster_id"]] >= 2:
            continue
        per_owner[c["roster_id"]] += 1
        kept.append(c)
    buy = kept[:12]

    # ---- sell (builders) or trade chips (contenders) ----------------------------------------
    contending = key in CONTENDING
    sell = []
    for pid in lg.rosters[rid]:
        v = lg.value(pid)
        if v < 500 or lg.pos(pid) is None:
            continue
        in_t = lg.starts(rid, T, pid)
        nxt = lg.starts(rid, min(T + 1, YEARS - 1), pid)
        age, now_ppg, t_ppg = lg.age(pid), lg.ppg(pid, 0) or 0.0, lg.ppg(pid, T) or 0.0
        young_core = age is not None and age <= 24 and v >= 800
        if contending:
            if lg.starts(rid, 0, pid) or (key in ("sustain", "climb") and (nxt or in_t)):
                continue
            reason = "Bench depth: doesn't start this season" + ("" if nxt else " or next")
            rank_reason = 0
        elif key == "deadline":
            if age is None or age < 27:
                continue
            reason = "Veteran to sell if the playoff odds don't climb by the deadline"
            if lg.past_cliff(pid, 1):
                reason = f"Past his age cliff by {season + 1}: sell if the playoff odds don't climb"
            rank_reason = 0 if lg.past_cliff(pid, 1) else 1
        else:
            if young_core:
                continue
            if in_t:
                continue
            rising = age is not None and age <= 24 and (lg.ppg(pid, 2) or 0) > now_ppg
            if rising and nxt:
                continue  # young and on the way up: part of the plan
            if lg.past_cliff(pid, T):
                reason, rank_reason = f"Past his age cliff by {tseason}", 0
            elif now_ppg > 0 and t_ppg < 0.85 * now_ppg:
                reason, rank_reason = f"Projected to fall from {now_ppg:.1f} to {t_ppg:.1f} ppg by {tseason}", 1
            else:
                reason, rank_reason = f"Won't make your best lineup in {tseason}", 2
        buyers = []
        for b in lg.rids:
            if b == rid:
                continue
            tb = lg.target(b)
            if tb == 0 and pid in lg.hurt:
                tb = 1  # out now: a contender would be buying him for next season
            if tb > 0 and lg.past_cliff(pid, tb):
                continue
            up, slot = lg.upgrade(b, tb, pid)
            if up >= 0.8 and slot is not None:
                buyers.append({"roster_id": b, "team": lg.names.get(b), "upgrade_ppg": round(up, 1),
                               "season": season + tb})
        buyers.sort(key=lambda x: (x["season"] != season, -x["upgrade_ppg"]))
        c = lg.card(pid)
        c.update(reason=reason, buyers=buyers[:3], kind="player", _order=rank_reason,
                 past_cliff=lg.past_cliff(pid, 0), projected_drop=round(1 - t_ppg / now_ppg, 2) if now_ppg else 0.0)
        sell.append(c)
    sell.sort(key=lambda c: (c["_order"], -c["val"]))
    for c in sell:
        c.pop("_order", None)
    sell = sell[:10]

    picks_owned = [p for p in lg.picks if p["owner_roster_id"] == rid]
    chips = []
    if contending:
        for p in picks_owned:
            if (p["value"] or 0) < 300:
                continue
            far = int(p["season"]) >= season + 2
            chips.append({"season": p["season"], "round": p["round"], "from": lg.names.get(p["original_roster_id"]),
                          "own": p["original_roster_id"] == rid, "exp_slot": p["exp_slot"], "value": round(p["value"] or 0),
                          "reason": "Far enough out to spend now" if far else "Spend only for a real upgrade"})
        chips.sort(key=lambda c: (-int(c["season"]), -c["value"]))

    # ---- keep: what the target season is built on --------------------------------------------
    keep, selling = [], {c["id"] for c in sell}
    for pid in lg.rosters[rid]:
        if pid in selling:
            continue
        s_t = lg.starts(rid, T, pid)
        age = lg.age(pid)
        young = age is not None and age < 25 and lg.value(pid) >= 800 and not contending
        if not s_t and not young:
            continue
        c = lg.card(pid)
        c["reason"] = (f"Starts at {s_t['label']} in {tseason} ({s_t['ppg']:.1f} ppg)" if s_t
                       else f"Young, projected {lg.ppg(pid, 2) or 0:.1f} ppg in {season + 2}")
        keep.append(c)
    keep.sort(key=lambda c: -c["val"])
    keep = keep[:8]

    # ---- season-by-season plan ---------------------------------------------------------------
    years = []
    for y in range(YEARS):
        sy = season + y
        goal = _goal(key, T, y, r[y], st["pt"])
        lu = lg.lineups[rid][y]
        holes, strong = [], []
        for s in lu:
            med = lg.median[y].get(s["label"], 0.0)
            item = {"slot": s["label"], "id": s["pid"], "name": lg.card(s["pid"])["name"] if s["pid"] else None,
                    "ppg": round(s["ppg"], 1), "median": round(med, 1), "gap": round(s["ppg"] - med, 1)}
            if s["ppg"] < med - 1.0:
                holes.append(item)
            elif s["ppg"] > med + 2.0:
                strong.append(item)
        holes.sort(key=lambda h: h["gap"])
        strong.sort(key=lambda h: -h["gap"])
        aging, rising = [], []
        for pid in lg.rosters[rid]:
            if lg.value(pid) < 800 and not lg.starts(rid, max(0, y - 1), pid):
                continue
            nm = lg.card(pid)["name"]
            if lg.past_cliff(pid, y) and (y == 0 or not lg.past_cliff(pid, y - 1)):
                prev = lg.ppg(pid, y - 1) if y > 0 else None
                cur = lg.ppg(pid, y) or 0.0
                if y == 0 or (prev and cur < 0.9 * prev):
                    aging.append({"id": pid, "name": nm, "pos": lg.pos(pid), "age": round(lg.age(pid, y), 1),
                                  "starts": lg.starts(rid, y, pid) is not None})
            if y > 0 and (lg.age(pid) or 99) < 25:
                d = (lg.ppg(pid, y) or 0) - (lg.ppg(pid, y - 1) or 0)
                if d >= 2.0 and (lg.ppg(pid, y) or 0) >= 8:
                    rising.append({"id": pid, "name": nm, "pos": lg.pos(pid), "ppg": round(lg.ppg(pid, y), 1),
                                   "gain": round(d, 1)})
        draft = [{"round": p["round"], "from": lg.names.get(p["original_roster_id"]),
                  "own": p["original_roster_id"] == rid, "exp_slot": p["exp_slot"], "value": round(p["value"] or 0)}
                 for p in picks_owned if int(p["season"]) == sy]
        years.append({
            "season": sy, "goal": goal, "rank": r[y], "strength": lg.strength[rid][y],
            "median_strength": round(lg.median_strength[y], 1),
            "holes": holes[:3], "strong": strong[:3], "aging": aging[:4], "rising": rising[:4], "draft": draft,
            "moves": _moves(lg, rid, key, T, y, goal, holes[:3], aging, rising, draft, buy, sell, open_deadline),
        })

    my_picks = [
        {"season": p["season"], "round": p["round"], "from": lg.names.get(p["original_roster_id"]),
         "own": p["original_roster_id"] == rid, "exp_slot": p["exp_slot"], "p_top3": p["p_top3"],
         "p_slot1": p["p_slot1"], "value": p["value"]}
        for p in picks_owned
    ]
    rk = lg.record.get(rid, {})
    return {
        "roster_id": rid,
        "window": tw.get("window"),
        "strategy": {"key": key, "title": title, "summary": summary, "target_season": tseason, "why": why},
        "ranks": {"rank_now": r[0], "rank_next": r[1], "rank_later": r[2]},
        "record": f"{rk.get('wins') or 0}-{rk.get('losses') or 0}" + (f"-{rk['ties']}" if rk.get("ties") else ""),
        "exp_wins": sim.get("exp_wins"),
        "p_playoffs": sim.get("p_playoffs"),
        "p_title": sim.get("p_title"),
        "p_first_pick": sim.get("p_first_pick"),
        "p_top3": sim.get("p_top3"),
        "exp_slot": sim.get("exp_slot"),
        "trade_deadline_week": dl,
        "weeks_to_deadline": lg.weeks_to_deadline,
        "assets": {"players": tw.get("player_value"), "picks": tw.get("pick_value"), "total": tw.get("total_assets"),
                   "rank": lg.asset_rank[rid]},
        "years": years,
        "buy": buy,
        "sell": sell,
        "chips": chips[:8],
        "sell_kind": "chips" if contending else ("shop" if key == "deadline" else "sell"),
        "keep": keep,
        "my_picks": my_picks,
    }


def _goal(key: str, T: int, y: int, rank: int, p_title: float) -> str:
    if key == "sustain":
        return "Win the title" if y == 0 else "Contend"
    if key == "allin":
        if y == 0:
            return "Win the title"
        return "Contend" if rank <= 5 else "Retool"
    if key == "climb":
        if y == 0:
            return "Make the playoffs"
        return "Contend" if y >= T else "Build"
    if key == "run":
        if y == 0:
            return "Make the playoffs"
        return "Contend" if rank <= 4 else "Retool"
    if key == "deadline":
        if y == 0:
            return "Push or sell"
        return "Contend" if rank <= 4 else "Retool"
    if key == "next":
        if y == 0:
            return "Make the playoffs"
        return "Contend" if y >= T else "Build"
    if key == "reload":
        if y == 0:
            return "Sell and reload"
        return "Contend" if y >= T else "Build"
    # rebuild
    return ("Sell and collect", "Develop", "Aim to contend")[y]


def _names(items: list[dict], k: int = 2) -> str:
    names = [i["name"] for i in items[:k] if i.get("name")]
    return " and ".join(names) if len(names) <= 2 else ", ".join(names[:-1]) + " and " + names[-1]


def _moves(lg: League, rid: int, key: str, T: int, y: int, goal: str, holes, aging, rising, draft, buy, sell,
           open_deadline: bool) -> list[str]:
    season, sy, dl = lg.season, lg.season + y, lg.deadline
    out = []
    if y == 0:
        if not open_deadline:
            out.append("The trade deadline has passed: set the best lineup each week and plan the offseason.")
        elif goal == "Win the title":
            fits = [b for b in buy if b["season"] == sy]
            hole = holes[0]["slot"] if holes else None
            if fits:
                adds = ", ".join(f"{b['name']} ({b['slot']}, +{b['upgrade_ppg']} ppg)" for b in fits[:3])
                out.append(f"Before the week {dl} deadline, add a starter: {adds}.")
            elif hole:
                out.append(f"Before the week {dl} deadline, look for an upgrade at {hole}.")
            out.append("Pay with bench depth and picks two or more years out; keep players who start next season.")
        elif goal == "Make the playoffs" and key == "run":
            cheap = [b for b in buy if b["season"] == sy and b["val"] <= 2500]
            out.append(f"Before the week {dl} deadline, add cheap veterans" + (f" such as {_names(cheap, 3)}." if cheap else "."))
            out.append("Don't pay in firsts or young starters: this lineup fades after this season.")
        elif goal == "Make the playoffs" and key == "climb":
            out.append(f"Only cheap upgrades before the week {dl} deadline: keep picks and young players for {lg.season + T}.")
        elif goal == "Make the playoffs":
            out.append("Hold at the deadline: don't trade picks or young players for a run this year.")
        elif goal == "Push or sell":
            out.append(f"If the playoff odds pass 50% by week {dl}, buy cheap veterans; if not, sell them.")
            if sell:
                out.append(f"Veterans to shop if it goes wrong: {_names(sell, 3)}.")
        else:
            if sell:
                out.append(f"Sell {_names(sell, 3)} to contenders before the week {dl} deadline.")
            out.append("Keep young starters; ask for firsts and players 24 or younger in return.")
        riding = [a for a in aging if a["starts"]]
        if riding and goal in ("Win the title", "Make the playoffs"):
            out.append(f"Ride {_names(riding, 2)} this year; {'they are' if len(riding) > 1 else 'he is'} at the age "
                       f"cliff, so plan to replace {'them' if len(riding) > 1 else 'him'} within a year.")
        return out

    if draft:
        firsts = [d for d in draft if d["round"] == 1]
        line = f"Draft: {len(draft)} pick{'s' if len(draft) != 1 else ''}"
        if firsts:
            slots = ", ".join(f"1.{round(d['exp_slot']):02d}" if d.get("exp_slot") else "1st" for d in firsts)
            line += f", including {'a first' if len(firsts) == 1 else f'{len(firsts)} firsts'} (about {slots})"
        out.append(line + ".")
    elif goal in ("Build", "Develop", "Sell and collect"):
        out.append(f"No picks in the {sy} draft: buy back in with veterans.")
    last = [a for a in aging if a["starts"] and goal in ("Contend", "Aim to contend")]
    move = [a for a in aging if a not in last]
    if last:
        out.append(f"Last good season for {_names(last, 2)}: start {'them' if len(last) > 1 else 'him'}, then sell.")
    if move:
        out.append(f"Aging out: {_names(move, 3)}. Sell before {sy} if you haven't.")
    if rising:
        out.append(f"Stepping up: {_names(rising, 3)}.")
    if holes:
        h = holes[0]
        need = ", ".join(x["slot"] for x in holes)
        if goal in ("Contend", "Aim to contend"):
            fits = [b for b in buy if b["season"] == sy]
            tip = f" Targets: {_names(fits, 2)}." if fits else ""
            out.append(f"Fill {need}: your {h['slot']} projects {h['ppg']} ppg against a league median of {h['median']}.{tip}")
        else:
            out.append(f"Weak spots to build for: {need}.")
    if goal == "Retool" and (y == 1 or _goal(key, T, y - 1, 99, 0) != "Retool"):
        out.append(f"Retool before {sy}: sell starters who won't last and restock with young players.")
    return out

"""League-mate profiles, built from every transaction and draft this league has, plus each
manager's drafts in their other Sleeper leagues.

Per manager:
  trading      trades made, partners, hindsight result (what each side holds now, at
               today's values), positions they trade for, picks bought vs sold
  waivers      claims, free-agent adds, FAAB spent and biggest bid
  drafting     position mix in early rounds (rookie drafts here, all drafts elsewhere),
               compared with the league as a whole
  tags         short labels for what stands out
Also trade_log(): every completed trade with each side valued at today's values, used draft
picks shown as the player taken with them (exported to the app's Past trades view).

Writes owner_profile, trade_review.
"""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter, defaultdict

from .store import Store

SKILL = ("QB", "RB", "WR", "TE")


SLOT_WINDOW = 3  # picks either side used to value a draft slot


def _pick_player_map(store: Store, val: dict | None = None) -> dict:
    """(season, round, original roster) of a used pick -> {player_id, pick, by_roster, league_id,
    slot_value} for every completed draft.

    The original roster comes from the draft's slot_to_roster_id, or from draft_order (user -> slot)
    when Sleeper leaves that out. Rookie drafts win over startup drafts for the same key.
    by_roster is the roster that made the pick. slot_value is what that draft slot turned out to be
    worth: today's average value of the players taken within SLOT_WINDOW picks of it in that draft,
    for teams that traded the pick on before the draft (the player taken wasn't their choice)."""
    val = val or {}
    owner_of = {(r["league_id"], r["owner_id"]): r["roster_id"]
                for r in store.query("SELECT league_id, roster_id, owner_id FROM roster")}
    out = {}
    drafts = store.query("SELECT * FROM draft WHERE status='complete' ORDER BY (player_type=1)")
    for d in drafts:  # startup drafts first, so rookie drafts overwrite on a clash
        s2r = {int(k): int(v) for k, v in json.loads(d["slot_to_roster_id"] or "{}").items() if v is not None}
        if not s2r:
            for uid, slot in json.loads(d["draft_order"] or "{}").items():
                rid = owner_of.get((d["league_id"], uid))
                if rid is not None:
                    s2r[int(slot)] = rid
        if not s2r:
            continue
        n = len(s2r)
        made = store.query("SELECT pick_no, round, draft_slot, roster_id, player_id FROM draft_pick_made "
                           "WHERE draft_id=?", (d["draft_id"],))
        by_no = {p["pick_no"]: val.get(p["player_id"], 0.0) for p in made if p["player_id"]}
        for p in made:
            orig = s2r.get(p["draft_slot"])
            if orig is None or not p["player_id"]:
                continue
            in_round = p["pick_no"] - (p["round"] - 1) * n
            near = [v for k, v in by_no.items() if abs(k - p["pick_no"]) <= SLOT_WINDOW]
            out[(str(d["season"]), p["round"], orig)] = {
                "player_id": p["player_id"], "pick": f"{p['round']}.{in_round:02d}", "pick_no": p["pick_no"],
                "by_roster": p["roster_id"], "league_id": d["league_id"], "drafted_ms": d["start_time"],
                "slot_value": sum(near) / len(near) if near else 0.0}
    return out


def _package(values: list[float], p: float | None = None) -> float:
    from .trade import package_value
    return package_value(values, p)


PLAYOFF_WEIGHT = 2.0   # a start in the winners' bracket counts double
TITLE_WEIGHT = 3.0     # ... and triple in the champion's own playoff run
FLEX_POS = ("RB", "WR", "TE")
REPL_DEFAULT = {"QB": 15.8, "RB": 8.3, "WR": 8.4, "TE": 8.1}
DAY_LOOKBACK = 3       # days back to find the value snapshot for "on the day"


def _day(ms) -> str | None:
    if not ms:
        return None
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def _days(a: str, b: str) -> float:
    """Days from date a to date b (YYYY-MM-DD)."""
    f = lambda x: time.mktime(time.strptime(x, "%Y-%m-%d"))  # noqa: E731
    return (f(b) - f(a)) / 86400


class _History:
    """Daily value snapshots (players and picks) for 'value on a date' lookups."""

    def __init__(self, store: Store):
        self.players = defaultdict(dict)
        for r in store.query("SELECT snapshot_date d, player_id, value FROM player_value"):
            self.players[r["d"]][r["player_id"]] = r["value"] or 0.0
        self.picks = defaultdict(dict)
        if store.query("SELECT 1 FROM sqlite_master WHERE name='pick_value_history'"):
            for r in store.query("SELECT snapshot_date d, season, round, original_roster_id o, value "
                                 "FROM pick_value_history"):
                self.picks[r["d"]][(str(r["season"]), r["round"], r["o"])] = r["value"] or 0.0
        self.dates = sorted(self.players)

    def after(self, day: str | None) -> str | None:
        """First snapshot on or after a day, within DAY_LOOKBACK days (value when a player left)."""
        if not day:
            return None
        d = next((d for d in self.dates if d >= day), None)
        return d if d and _days(day, d) <= DAY_LOOKBACK else None

    def on(self, day: str | None) -> str | None:
        """Snapshot in force on a day: the latest on or before it, within DAY_LOOKBACK days."""
        if not day:
            return None
        best = None
        for d in self.dates:
            if d <= day:
                best = d
        return best if best and _days(best, day) <= DAY_LOOKBACK else None


def value_per_point(store: Store) -> float:
    """Value units per point above replacement: the median ratio of today's value to the model's
    discounted points above replacement (model_raw) among the 200 most valuable players. A median
    of the startable pool, so the stars at the top of the market's convex curve don't set the rate
    for every start."""
    vd = store.query("SELECT MAX(snapshot_date) d FROM player_value")[0]["d"]
    cols = {r["name"] for r in store.query("PRAGMA table_info(player_value)")}
    if not vd or "model_raw" not in cols:
        return 10.0
    ratios = sorted(r["value"] / r["model_raw"] for r in store.query(
        "SELECT value, model_raw FROM player_value WHERE snapshot_date=? AND model_raw>1 AND value>0 "
        "ORDER BY value DESC LIMIT 200", (vd,)))
    return ratios[len(ratios) // 2] if ratios else 10.0


FAIR = 0.10        # a trade is even when every side is within 10%
FAIR_EST = 0.25    # ... and within 25% when judged at estimated (model-only) values of the day


def _sides_net(sides: list[dict], val, p: float) -> list[tuple[float, float]]:
    """(got, gave) per side, packaged per counterparty: what side i got from side j is packaged
    on its own, and that same package is what j gave i. A three-team trade then nets to zero."""
    n = len(sides)
    pk = {}
    for i, sd in enumerate(sides):
        by_from = defaultdict(list)
        for a in sd["got"]:
            if a["k"] != "faab" and a.get("from") is not None and val(a) is not None:
                by_from[a["from"]].append(val(a))
        for j, vs in by_from.items():
            pk[(j, i)] = _package(vs, p)
    return [(sum(v for (j, i2), v in pk.items() if i2 == i), sum(v for (j2, _), v in pk.items() if j2 == i))
            for i in range(n)]


def _verdict(nets: list[tuple[float, float]], band: float) -> dict:
    net = [g - v for g, v in nets]
    big = max([g for g, _ in nets] + [1])
    even = all(abs(x) <= band * big for x in net)
    best = max(range(len(net)), key=lambda i: net[i]) if net else None
    return {"even": even, "winner": None if even or best is None else best,
            "margin": 0 if best is None else round(net[best])}


def trade_log(store: Store) -> list[dict]:
    """Every completed trade in this league's history, newest first.

    Each side lists what it received; each asset counts as banked value + remaining value:
      banked     what his starts for that team added after the trade: points over the bench player
                 he kept out of the lineup (or a replacement-level starter), winners-bracket games
                 x2 and the champion's own title run x3, converted at value_per_point.
      remaining  today's value while he's on that team. Traded on: his share of what that later
                 trade brought back, packaged (a trade tree). Dropped: nothing.
    'once' is the same but with an asset traded on valued at his value when he left, so adding up a
    manager's trades counts each later trade only once (profiles and team totals use it).
    Used picks carry the player taken (banked from draft day). A pick traded on before the draft
    counts as what it was traded for (else its slot's worth). Future picks carry their pick value.
    Sides are packaged per counterparty with the exponent fitted to this league's trades
    (fit_package); a trade is even when every side is within 10% (25% at estimated day values).
    Rosters are followed by owner, so a franchise that changes hands keeps its own history."""
    lg = store.query("SELECT * FROM league_season WHERE is_current=1")[0]
    cur = lg["league_id"]
    leagues = {r["league_id"]: r["season"] for r in store.query("SELECT league_id, season FROM league_season")}
    owner_of = {(r["league_id"], r["roster_id"]): r["owner_id"]
                for r in store.query("SELECT league_id, roster_id, owner_id FROM roster")}
    cur_rid = {r["owner_id"]: r["roster_id"] for r in store.query(
        "SELECT roster_id, owner_id FROM roster WHERE league_id=? AND owner_id IS NOT NULL", (cur,))}
    names = {}
    for r in store.query("""SELECT o.user_id, o.display_name, m.team_name FROM owner o
                            LEFT JOIN league_member m ON m.user_id=o.user_id AND m.league_id=?""", (cur,)):
        names[r["user_id"]] = {"owner": r["display_name"], "team": (r["team_name"] or r["display_name"] or "").strip()}

    def who(lid, rid):
        """A franchise across seasons: its owner (the roster id within a league if unowned)."""
        return owner_of.get((lid, rid)) or f"{lid}:{rid}"

    vd = store.query("SELECT MAX(snapshot_date) d FROM player_value")[0]["d"]
    val = {r["player_id"]: r["value"] or 0.0 for r in store.query(
        "SELECT player_id, value FROM player_value WHERE snapshot_date=?", (vd,))}
    info = {r["player_id"]: r for r in store.query("SELECT player_id, full_name, position, team FROM player")}
    future = {(str(r["season"]), r["round"], r["original_roster_id"]): r for r in store.query("SELECT * FROM pick_value")}
    used = _pick_player_map(store, val)
    snap = store.query("SELECT MAX(snapshot_date) d FROM roster_snapshot WHERE league_id=?", (cur,))[0]["d"]
    now_on = {r["player_id"]: r["roster_id"] for r in store.query(
        "SELECT roster_id, player_id FROM roster_snapshot WHERE league_id=? AND snapshot_date=?", (cur, snap))}

    def player(pid):
        i = info.get(pid) or {}
        return {"id": pid, "name": i["full_name"] if i else f"Player {pid}", "pos": i["position"] if i else None,
                "nfl": i["team"] if i else None, "value": round(val.get(pid, 0.0)), "now_on": now_on.get(pid)}

    # ---- banked value: lineup gains over the player each start displaced, title runs weighted up
    hist = _History(store)
    k = value_per_point(store)
    repl = dict(REPL_DEFAULT)
    if store.query("SELECT 1 FROM sqlite_master WHERE name='player_projection'"):
        pc = {r["name"] for r in store.query("PRAGMA table_info(player_projection)")}
        if "repl_ppg" in pc:
            for r in store.query("SELECT position, MAX(repl_ppg) r FROM player_projection GROUP BY position"):
                if r["position"] in repl and r["r"]:
                    repl[r["position"]] = r["r"]
    season_n = {lid: int(sv) for lid, sv in leagues.items()}
    po_start = {r["league_id"]: r["playoff_week_start"] or 15
                for r in store.query("SELECT league_id, playoff_week_start FROM league_season")}
    in_bracket = set()      # (league, week, roster) that played a winners-bracket game that week
    champ = {}
    for r in store.query("SELECT * FROM bracket WHERE bracket='winners'"):
        wk = po_start.get(r["league_id"], 15) + int(r["round"] or 1) - 1
        for t in (r["t1"], r["t2"]):
            if t is not None:
                in_bracket.add((r["league_id"], wk, t))
        if r["place"] == 1 and r["winner"] is not None:
            champ[r["league_id"]] = r["winner"]
    pos_of = lambda pid: (info.get(pid) or {})["position"] if info.get(pid) else None  # noqa: E731
    week_rows = defaultdict(list)   # (league, week, roster) -> [(player, points, starter)]
    games = defaultdict(list)       # (league, player) -> [(week, points)] for weeks he scored
    for r in store.query("SELECT league_id, week, roster_id, player_id, points, is_starter FROM matchup_player"):
        if r["league_id"] not in season_n:
            continue
        week_rows[(r["league_id"], r["week"], r["roster_id"])].append(
            (r["player_id"], r["points"] or 0.0, bool(r["is_starter"])))
        if r["points"]:
            games[(r["league_id"], r["player_id"])].append((r["week"], r["points"]))
    ppg_cache = {}

    def ppg_before(lid, pid, w):
        key = (lid, pid, w)
        if key not in ppg_cache:
            g = [p for wk, p in games.get((lid, pid), []) if wk < w]
            ppg_cache[key] = sum(g) / len(g) if g else None
        return ppg_cache[key]

    def displaced(lid, w, rid, pid, pos):
        """Points the team would have had without him: the bench player it would most likely have
        started instead (best PPG so far that season, played that week, at least replacement
        level): same position first, else for a RB/WR/TE any flex-eligible backup; failing that,
        a replacement-level starter."""
        rows_ = week_rows.get((lid, w, rid), [])
        for allowed in ((pos,), FLEX_POS if pos in FLEX_POS else ()):
            best = None
            for q, pts, st in rows_:
                qpos = pos_of(q)
                if st or q == pid or not pts or qpos not in allowed:
                    continue
                pp = ppg_before(lid, q, w)
                if pp is not None and pp >= repl.get(qpos, 99) and (best is None or pp > best[0]):
                    best = (pp, pts)
            if best:
                return best[1]
        return repl[pos]

    starts = defaultdict(list)    # (franchise, player) -> [(season, week, gain, weight, title season)]
    for (lid, w, rid), rows_ in week_rows.items():
        bracket_game = (lid, w, rid) in in_bracket
        for pid, pts, st in rows_:
            pos = pos_of(pid)
            if not st or pos not in repl:
                continue
            title = bracket_game and champ.get(lid) == rid
            wgt = TITLE_WEIGHT if title else PLAYOFF_WEIGHT if bracket_game else 1.0
            gain = pts - displaced(lid, w, rid, pid, pos)
            starts[(who(lid, rid), pid)].append((season_n[lid], w, gain, wgt, season_n[lid] if title else None))
    # when each player and pick left each franchise: dropped, or sent away in a trade
    exits = defaultdict(list)       # (franchise, player) -> [(ms, (season, week), how, transaction)]
    pick_exits = defaultdict(list)  # (franchise, season, round, original roster) -> [(ms, transaction)]
    for r in store.query(
        """SELECT m.kind, m.roster_id, m.from_roster_id, m.player_id, m.pick_season, m.pick_round,
                  m.pick_original_roster, t.league_id, t.week, t.transaction_id,
                  COALESCE(t.status_updated, t.created) ts, t.type
           FROM txn_move m JOIN txn t ON t.transaction_id=m.transaction_id
           WHERE t.status='complete' AND m.kind IN ('add','drop','pick')"""):
        lid = r["league_id"]
        if lid not in season_n:
            continue
        sw = (season_n[lid], r["week"] or 0)
        if r["kind"] == "pick":
            if r["from_roster_id"] is not None:
                pick_exits[(who(lid, r["from_roster_id"]), str(r["pick_season"]), r["pick_round"],
                            r["pick_original_roster"])].append((r["ts"] or 0, r["transaction_id"]))
        elif r["player_id"] is None:
            continue
        elif r["kind"] == "drop":
            exits[(who(lid, r["roster_id"]), r["player_id"])].append((r["ts"] or 0, sw, "dropped", None))
        elif r["from_roster_id"] is not None:
            exits[(who(lid, r["from_roster_id"]), r["player_id"])].append(
                (r["ts"] or 0, sw, "traded", r["transaction_id"]))

    # ---- package exponent, fitted first: on-the-day values from snapshots don't depend on trees
    day_scale = _DayScale(store, hist, info)
    fit = fit_package(_snapshot_samples(store, hist))
    P = fit["p"]

    # trade trees: (transaction, franchise) -> what it got there (packaged) and the weights of what
    # it gave. Filled newest trade first, so a later trade is always known before an earlier one.
    returns = {}

    def follow(txn, fr, key):
        """(share, credit, names) for one asset the franchise sent away in a later trade, or None
        when that trade brought back nothing valued (e.g. only FAAB)."""
        ret = returns.get((txn, fr))
        if not ret or key not in ret["gave"] or ret["got"] <= 0:
            return None
        tot = sum(ret["gave"].values()) or 1.0
        share = ret["gave"][key] / tot
        return share, share * ret["got"], ret["names"]

    def value_left(pid, day):
        """A player's value on the day he left: that day's snapshot, else today's."""
        snap_ = hist.after(day)
        return (hist.players[snap_].get(pid, 0.0), "then") if snap_ else (val.get(pid, 0.0), "today")

    def hold(fr, pid, since_ms, since_sw):
        """Banked + remaining value of a player a franchise acquired at since_ms / since_sw."""
        pos = pos_of(pid)
        out_ = next((e for e in sorted(exits.get((fr, pid), []), key=lambda e: e[0]) if e[0] > since_ms), None)
        end_sw = out_[1] if out_ else (9999, 99)
        par = n = 0
        titles = set()
        if pos in repl:
            for se, wk, gain, wgt, title in starts.get((fr, pid), []):
                if since_sw <= (se, wk) <= end_sw:
                    par += wgt * gain
                    n += 1
                    if title:
                        titles.add(title)
        banked = k * par
        res = {"banked": round(banked), "banked_pts": round(par, 1), "starts": n}
        if titles:
            res["titles"] = sorted(titles)
        if out_:
            day = _day(out_[0])
            res["left"] = {"date": day, "how": out_[2]}
            if out_[2] == "dropped":
                remain = once = 0.0            # a dropped player brings nothing back
                res["left"]["valued"] = "dropped"
            else:
                once, when = value_left(pid, day)
                tree = follow(out_[3], fr, ("p", pid))
                if tree:
                    remain = tree[1]
                    res["left"].update(valued="tree", share=round(tree[0], 3), got=tree[2])
                else:
                    remain = once
                    res["left"]["valued"] = when
        else:
            remain = once = val.get(pid, 0.0)
        res["remain"] = round(remain)
        res["value"] = round(max(0.0, banked + remain))
        res["once"] = round(max(0.0, banked + once))
        return res

    def pick_left(fr, key, since_ms):
        """(exit ms, transaction) when the franchise traded this pick on after since_ms."""
        return next((x for x in sorted(pick_exits.get((fr,) + key, [])) if x[0] > since_ms), None)

    def team_of(lid, rid):
        uid = owner_of.get((lid, rid))
        return uid, cur_rid.get(uid), names.get(uid, {})

    out = []
    for t in store.query("SELECT * FROM txn WHERE status='complete' AND type='trade' "
                         "ORDER BY COALESCE(status_updated, created) DESC"):
        lid = t["league_id"]
        rids = json.loads(t["roster_ids"] or "[]")
        moves = store.query("SELECT * FROM txn_move WHERE transaction_id=? ORDER BY seq", (t["transaction_id"],))
        for m in moves:  # include any roster that only appears in the moves
            for r in (m["roster_id"], m["from_roster_id"]):
                if r is not None and r not in rids and m["kind"] != "drop":
                    rids.append(r)
        idx = {rid: i for i, rid in enumerate(rids)}
        sides = []
        for rid in rids:
            uid, crid, nm = team_of(lid, rid)
            sides.append({"roster_id": crid, "user_id": uid, "team": nm.get("team") or f"Team {rid}",
                          "owner": nm.get("owner"), "left_league": crid is None, "got": []})
        t_ms = t["status_updated"] or t["created"] or 0
        t_sw = (season_n.get(lid, 0), t["week"] or 0)
        t_day = _day(t_ms)
        snap_day = hist.on(t_day)
        est = day_scale.cut(t_day) if not snap_day else None
        keys = {}   # id(asset) -> tree key
        for m in moves:
            if m["kind"] == "drop" or m["roster_id"] not in idx:
                continue
            frm = idx.get(m["from_roster_id"])
            fr = who(lid, m["roster_id"])
            if m["kind"] == "add":
                a = {"k": "player", "from": frm, **player(m["player_id"])}
                a["today"] = a["value"]          # today's value, before banked/remaining replaces it
                a.update(hold(fr, m["player_id"], t_ms, t_sw))
                if snap_day:
                    a["day"] = round(hist.players[snap_day].get(m["player_id"], 0.0))
                elif est:
                    a["day"] = day_scale.player(est, m["player_id"], pos_of(m["player_id"]))
                keys[id(a)] = ("p", m["player_id"])
            elif m["kind"] == "pick":
                season, rnd, orig = str(m["pick_season"]), m["pick_round"], m["pick_original_roster"]
                _, orig_cur, orig_nm = team_of(lid, orig)
                a = {"k": "pick", "from": frm, "season": season, "round": rnd,
                     "orig": orig_cur, "orig_team": orig_nm.get("team")}
                hit = used.get((season, rnd, orig))
                fut = future.get((season, rnd, orig_cur if orig_cur is not None else orig))
                ex = pick_left(fr, (season, rnd, orig), t_ms)
                if hit and ex and hit.get("drafted_ms") and ex[0] > hit["drafted_ms"]:
                    ex = None                    # traded after the draft: that's the player's exit
                moved = follow(ex[1], fr, ("k", season, rnd, orig)) if ex else None
                if hit and (ex or (hit.get("by_roster") is not None and who(hit["league_id"], hit["by_roster"]) != fr)):
                    # passed on before the draft: another team chose the player, so this team is
                    # credited with what it got for the pick (trade tree), else the slot's worth
                    _, by_cur, by_nm = team_of(hit["league_id"], hit["by_roster"])
                    b = player(hit["player_id"])
                    a.update(status="flipped", pick=hit["pick"], became=b,
                             drafted_by=by_cur, drafted_by_team=by_nm.get("team"),
                             value=round(moved[1] if moved else hit["slot_value"]),
                             once=round(hit["slot_value"]), today=b["value"])
                    if moved:
                        a["left"] = {"date": _day(ex[0]), "how": "traded", "valued": "tree",
                                     "share": round(moved[0], 3), "got": moved[2]}
                elif hit:
                    b = player(hit["player_id"])
                    h = hold(fr, hit["player_id"], hit.get("drafted_ms") or t_ms,
                             (season_n.get(hit["league_id"], int(season)), 0))
                    a.update(status="used", pick=hit["pick"], became=b, today=b["value"], **h)
                elif fut:
                    pv = round(fut["value"] or 0)
                    a.update(status="future", exp_slot=fut["exp_slot"], value=pv, once=pv, today=pv)
                    if moved:
                        a.update(value=round(moved[1]),
                                 left={"date": _day(ex[0]), "how": "traded", "valued": "tree",
                                       "share": round(moved[0], 3), "got": moved[2]})
                else:
                    a.update(status="unknown", value=0, once=0)
                a.setdefault("today", a["value"])
                a.setdefault("once", a["value"])
                key = (season, rnd, orig_cur if orig_cur is not None else orig)
                if snap_day:
                    if key in hist.picks.get(snap_day, {}):
                        a["day"] = round(hist.picks[snap_day][key])
                elif est:
                    a["day"] = day_scale.pick(est, season, rnd,
                                              hit["pick"] if hit and int(season) == est[0] and est[1] <= 0 else None)
                keys[id(a)] = ("k", season, rnd, orig)
            elif m["kind"] == "faab":
                a = {"k": "faab", "from": frm, "amount": m["amount"], "value": 0, "once": 0}
            else:
                continue
            sides[idx[m["roster_id"]]]["got"].append(a)
        # what each franchise got and gave here, for earlier trades whose assets came through it
        for rid, i in idx.items():
            got = [a for a in sides[i]["got"] if a["k"] != "faab"]
            gave = {}
            for o in sides:
                for a in o["got"]:
                    if a.get("from") == i and a["k"] != "faab":
                        w = a.get("day")
                        if not w or w <= 0:            # no usable value of the day: today's / at exit
                            w = a.get("once") or a.get("today") or a["value"]
                        gave[keys[id(a)]] = gave.get(keys[id(a)], 0.0) + max(1.0, w or 0.0)
            returns[(t["transaction_id"], who(lid, rid))] = {
                "got": _package([a["value"] for a in got], P), "gave": gave,
                "names": [x["name"] if x["k"] == "player" else f"{x['season']} round {x['round']}"
                          for x in sorted(got, key=lambda x: -x["value"])][:3]}
        rec = {"id": t["transaction_id"], "season": leagues.get(lid), "week": t["week"],
               "ts": t["status_updated"] or t["created"], "sides": sides}
        assets = [a for sd in sides for a in sd["got"] if a["k"] != "faab"]
        if not assets:
            rec["faab_only"] = True             # FAAB for FAAB: shown, never judged
        elif all(a.get("day") is not None for a in assets):
            rec["day"] = {"date": snap_day or t_day, "est": bool(est)}
            if est:
                for a in assets:
                    a["day_est"] = True
        out.append(rec)

    for rec in out:
        sides = rec["sides"]
        if rec.get("faab_only"):
            for sd in sides:
                sd.update(got_value=0, got_raw=0, gave_value=0, net=0, net_once=0, banked=0)
            rec.update(even=True, winner=None, margin=0)
            continue
        nets = _sides_net(sides, lambda a: a["value"], P)
        once = _sides_net(sides, lambda a: a.get("once", a["value"]), P)
        for i, sd in enumerate(sides):
            sd["got_value"], sd["gave_value"] = round(nets[i][0]), round(nets[i][1])
            sd["got_raw"] = round(sum(a["value"] for a in sd["got"] if a["k"] != "faab"))
            sd["net"] = sd["got_value"] - sd["gave_value"]
            sd["net_once"] = round(once[i][0] - once[i][1])
            sd["banked"] = sum(a.get("banked", 0) for a in sd["got"])
        rec.update(_verdict(nets, FAIR))
        rec["verdict_once"] = _verdict(once, FAIR)
        if "day" in rec:
            dn = _sides_net(sides, lambda a: a.get("day"), P)
            for i, sd in enumerate(sides):
                sd["day_got"], sd["day_gave"] = round(dn[i][0]), round(dn[i][1])
                sd["day_net"] = sd["day_got"] - sd["day_gave"]
            rec["day"].update(_verdict(dn, FAIR_EST if rec["day"]["est"] else FAIR))
    store.snapshot("package_fit", fit)
    return out


def _snapshot_samples(store: Store, hist: "_History") -> list[tuple[list, list]]:
    """(side A values, side B values) at the values of the day, for two-team trades made since
    the daily value history began (real snapshot values only, no estimates)."""
    from collections import defaultdict as dd
    out = []
    for t in store.query("SELECT transaction_id, COALESCE(status_updated, created) ts, roster_ids FROM txn "
                         "WHERE status='complete' AND type='trade'"):
        day = hist.on(_day(t["ts"]))
        if not day:
            continue
        sides = dd(list)
        ok = True
        for m in store.query("SELECT * FROM txn_move WHERE transaction_id=? AND kind IN ('add','pick')",
                             (t["transaction_id"],)):
            if m["kind"] == "add":
                v = hist.players[day].get(m["player_id"])
            else:
                v = None
                for (s, rnd, o), pv in hist.picks.get(day, {}).items():
                    if s == str(m["pick_season"]) and rnd == m["pick_round"] and o == m["pick_original_roster"]:
                        v = pv
            if v is None:
                ok = False
                break
            sides[m["roster_id"]].append(v)
        if ok and len(sides) == 2:
            a, b = sides.values()
            out.append((a, b))
    return out


PKG_DEFAULT = 1.3
PKG_GRID = [round(1.0 + 0.05 * i, 2) for i in range(21)]   # 1.0 .. 2.0
PKG_MIN_TRADES = 30  # multi-piece trades at real values of the day before the fit is used at all
PKG_PRIOR_SD = 0.15  # how far from 1.3 the exponent plausibly is, before this league's evidence


def fit_package(samples: list[tuple[list, list]], boot: int = 200) -> dict:
    """The package exponent P that makes this league's two-team trades most even at the values of
    their day (snapshot values only: estimates are too noisy). Trades are assumed fair on average,
    so the best P minimizes the squared log gap between the two sides' packages. Only trades with
    2+ pieces on a side say anything about P. Until PKG_MIN_TRADES such trades exist, P stays 1.3.
    Then the fit is shrunk toward 1.3 by how precise it is (a bootstrap of the trades): a flat,
    noisy fit moves P little."""
    from .trade import package_value
    import math
    import random as _r
    samples = [(a, b) for a, b in samples
               if a and b and max(len(a), len(b)) >= 2 and min(sum(a), sum(b)) > 0]
    n = len(samples)
    if not n:
        return {"p": PKG_DEFAULT, "fit": None, "trades": 0}

    def best(ss):
        def err(p):
            return sum((math.log(package_value([x for x in a if x > 0], p)) -
                        math.log(package_value([x for x in b if x > 0], p))) ** 2 for a, b in ss)
        return min(PKG_GRID, key=err)

    raw = best(samples)
    rng = _r.Random(5)
    bs = [best([samples[rng.randrange(n)] for _ in range(n)]) for _ in range(boot if n >= 5 else 0)]
    sd = statistics.pstdev(bs) if len(bs) > 1 else 1.0
    lo, hi = (sorted(bs)[int(0.05 * len(bs))], sorted(bs)[int(0.95 * len(bs)) - 1]) if bs else (None, None)
    if n < PKG_MIN_TRADES:
        p = PKG_DEFAULT
    else:
        wgt = PKG_PRIOR_SD ** 2 / (PKG_PRIOR_SD ** 2 + max(sd, 1e-3) ** 2)
        p = round(PKG_DEFAULT + (raw - PKG_DEFAULT) * wgt, 2)
    return {"p": p, "fit": raw, "trades": n, "range": [lo, hi], "needed": PKG_MIN_TRADES}


class _DayScale:
    """'On the day' values for trades older than the value history: the model as of that day
    (dayvalues.model_value_asof), mapped by rank onto the blended values of the oldest snapshot.
    Picks use the oldest pick values of their round (the slot's value once the order was set)."""

    def __init__(self, store: Store, hist: "_History", info: dict):
        from . import dayvalues
        self.dv = dayvalues
        last = hist.dates[-1] if hist.dates else None
        self.today = hist.players.get(last, {}) if last else {}
        self.first = hist.dates[0] if hist.dates else None
        self.by_cut = defaultdict(dict)
        if self.first and store.query("SELECT 1 FROM sqlite_master WHERE name='model_value_asof'"):
            for r in store.query("SELECT cut, player_id, model_value FROM model_value_asof"):
                self.by_cut[r["cut"]][r["player_id"]] = r["model_value"] or 0.0
        base = hist.players.get(self.first, {}) if self.first else {}
        self.base = base
        self.curve = sorted((v for p, v in base.items() if info.get(p) and info[p]["position"] in SKILL), reverse=True)
        self.scaled = {}
        self.rounds = defaultdict(list)     # (season, round) -> values, best first
        # oldest pick snapshot (pick history may start later than player history), else today's
        pdays = sorted(hist.picks)
        picks = hist.picks[pdays[0]] if pdays else {}
        if not picks and store.query("SELECT 1 FROM sqlite_master WHERE name='pick_value'"):
            picks = {(str(r["season"]), r["round"], r["original_roster_id"]): r["value"] or 0.0
                     for r in store.query("SELECT season, round, original_roster_id, value FROM pick_value")}
        for (s, rnd, _), v in picks.items():
            self.rounds[(str(s), rnd)].append(v)
        for key in self.rounds:
            self.rounds[key].sort(reverse=True)

    def cut(self, day: str | None):
        """(season, weeks) for a day before the value history, when its model values exist."""
        if not day or not self.first or day >= self.first:
            return None
        c = self.dv.cut_for(day)
        return c if self.dv.cut_key(c) in self.by_cut else None

    def player(self, cut, pid, pos) -> int | None:
        """That day's value, or None when the model had nothing usable for him that day (no id
        match, or rated at zero: e.g. a two-way rookie it lists at another position). Unknown
        keeps the trade out of the on-the-day verdict rather than judging it on a wrong 0."""
        key = self.dv.cut_key(cut)
        if key not in self.scaled:
            order = sorted(self.by_cut[key].items(), key=lambda x: -x[1])
            self.scaled[key] = {p: (self.curve[i] if i < len(self.curve) and mv > 0 else 0.0)
                                for i, (p, mv) in enumerate(order)}
        v = self.scaled[key].get(pid)
        if v:
            return round(v)
        if pos in ("K", "DEF") or (pos not in SKILL and not self.today.get(pid)):
            return 0          # kickers and defenses carry no dynasty value
        return None           # e.g. a two-way rookie Sleeper lists at DB: value unknown that day

    def pick(self, cut, season, rnd, slot: str | None) -> int:
        """A pick's value that day: the oldest pick values for that draft year and round (the
        nearest year we have when there's none), the slot's once the draft order was set."""
        years = sorted({s for s, r in self.rounds if r == rnd})
        if not years:
            return 0
        yr = min(years, key=lambda y: (abs(int(y) - int(season)), y))
        vals = self.rounds[(yr, rnd)]
        if slot:   # the draft order was set: that slot's value
            i = int(str(slot).split(".")[-1]) - 1
            return round(vals[min(max(i, 0), len(vals) - 1)])
        return round(sum(vals) / len(vals))


DYNASTY_TYPES = (1, 2)   # Sleeper league type: 0 redraft, 1 keeper, 2 dynasty


def _elsewhere(store: Store, age: dict) -> dict:
    """Per manager: tendencies from trades in their other Sleeper leagues (ext_trade). Used for
    scouting only, never valued: other leagues score and value players differently. Ages and picks
    count only from dynasty and keeper leagues (in redraft neither matters)."""
    if not store.query("SELECT 1 FROM sqlite_master WHERE name='ext_trade'"):
        return {}
    from datetime import datetime, timezone
    pos = {r["player_id"]: r["position"] for r in store.query("SELECT player_id, position FROM player")}
    now = datetime.now(tz=timezone.utc).timestamp() * 1000
    leagues_in = Counter()
    types_in = defaultdict(Counter)
    ours = {r["league_id"] for r in store.query("SELECT league_id FROM league_season")}
    for r in store.query("SELECT user_id, league_id, league_type FROM owner_league"):
        if r["league_id"] not in ours:
            leagues_in[r["user_id"]] += 1
            types_in[r["user_id"]]["dynasty" if r["league_type"] in DYNASTY_TYPES else "redraft"] += 1
    acc = defaultdict(lambda: {"n": 0, "dyn": 0, "red": 0, "leagues": set(), "got_pos": Counter(), "gave_pos": Counter(),
                               "got_age": [], "gave_age": [], "got_picks": 0, "gave_picks": 0, "got_firsts": 0,
                               "gave_firsts": 0, "pieces_got": [], "pieces_gave": [], "in_season": 0, "offseason": 0})
    for r in store.query("SELECT * FROM ext_trade"):
        a = acc[r["user_id"]]
        dyn = r["league_type"] in DYNASTY_TYPES
        a["n"] += 1
        a["dyn" if dyn else "red"] += 1
        a["leagues"].add(r["league_id"])
        yrs = max(0.0, (now - (r["ts"] or now)) / (365.25 * 86400 * 1000))
        when = datetime.fromtimestamp((r["ts"] or 0) / 1000, tz=timezone.utc) if r["ts"] else None
        a["in_season" if when and (when.month >= 9 or when.month == 1) else "offseason"] += 1
        got, gave = json.loads(r["got"] or "[]"), json.loads(r["gave"] or "[]")
        for lst, side in ((got, "got"), (gave, "gave")):
            for x in lst:
                if x["k"] == "player":
                    if pos.get(x["id"]) in SKILL:
                        a[f"{side}_pos"][pos[x["id"]]] += 1
                    if dyn and age.get(x["id"]):
                        a[f"{side}_age"].append(age[x["id"]] - yrs)   # his age on the day of the trade
                elif x["k"] == "pick" and dyn:
                    a[f"{side}_picks"] += 1
                    if x.get("round") == 1:
                        a[f"{side}_firsts"] += 1
        a["pieces_got"].append(len(got))
        a["pieces_gave"].append(len(gave))
    out = {}
    for uid, a in acc.items():
        n = a["n"]
        out[uid] = {
            "trades": n, "dynasty_trades": a["dyn"], "redraft_trades": a["red"],
            "leagues_traded_in": len(a["leagues"]), "leagues_in": leagues_in.get(uid, 0),
            "league_types": dict(types_in.get(uid, {})),
            "per_league": round(n / leagues_in[uid], 2) if leagues_in.get(uid) else None,
            "got_pos": dict(a["got_pos"]), "gave_pos": dict(a["gave_pos"]),
            "got_age": round(sum(a["got_age"]) / len(a["got_age"]), 1) if a["got_age"] else None,
            "gave_age": round(sum(a["gave_age"]) / len(a["gave_age"]), 1) if a["gave_age"] else None,
            "picks": {"got": a["got_picks"], "gave": a["gave_picks"], "firsts_got": a["got_firsts"],
                      "firsts_gave": a["gave_firsts"]},
            "pieces": {"got": round(sum(a["pieces_got"]) / n, 2), "gave": round(sum(a["pieces_gave"]) / n, 2)},
            "timing": {"in_season": a["in_season"], "offseason": a["offseason"]},
            "_raw": a,
        }
    for uid in leagues_in:
        out.setdefault(uid, {"trades": 0, "leagues_in": leagues_in[uid], "league_types": dict(types_in[uid])})
    return out


def _scouting(store: Store, log: list[dict], owner_of: dict, names: dict) -> dict:
    """Per-manager scouting data: trade record and tendencies, activity, lineup efficiency,
    season finishes and rookie-draft results. Keyed by user id."""
    from datetime import datetime, timezone
    out = defaultdict(lambda: {"tr": {"won": 0, "lost": 0, "even": 0, "margins": [], "best": None, "worst": None,
                                      "got_pos": Counter(), "gave_pos": Counter(), "got_age": [], "gave_age": [],
                                      "got_picks": 0, "gave_picks": 0, "got_firsts": 0, "gave_firsts": 0,
                                      "pieces_got": [], "pieces_gave": [], "in_season": 0, "offseason": 0,
                                      "vs": defaultdict(lambda: [0, 0.0])}})
    age = {r["player_id"]: r["age"] for r in store.query("SELECT player_id, age FROM player_projection") if r["age"]}
    for r in store.query("SELECT player_id, age FROM player WHERE age IS NOT NULL"):
        age.setdefault(r["player_id"], r["age"])
    birth = {}
    for r in store.query("SELECT player_id, birth_date FROM player WHERE birth_date IS NOT NULL"):
        try:
            birth[r["player_id"]] = datetime.fromisoformat(str(r["birth_date"])[:10]).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    for tr in log:
        if tr.get("faab_only"):
            continue
        when = datetime.fromtimestamp((tr["ts"] or 0) / 1000, tz=timezone.utc) if tr.get("ts") else None
        in_season = bool(when and (when.month >= 9 or when.month == 1))
        vo = tr.get("verdict_once") or tr
        for i, sd in enumerate(tr["sides"]):
            uid = sd["user_id"]
            if not uid:
                continue
            t = out[uid]["tr"]
            net = sd.get("net_once", sd["net"])
            res = "even" if vo["even"] else ("won" if net > 0 else "lost")
            t[res] += 1
            t["margins"].append(net)
            if t["best"] is None or net > t["best"][1]:
                t["best"] = (tr["id"], net)
            if t["worst"] is None or net < t["worst"][1]:
                t["worst"] = (tr["id"], net)
            gave = [a for o in tr["sides"] for a in o["got"] if a.get("from") == i]
            for lst, key_pos, key_age, key_pk, key_1st in ((sd["got"], "got_pos", "got_age", "got_picks", "got_firsts"),
                                                           (gave, "gave_pos", "gave_age", "gave_picks", "gave_firsts")):
                for a in lst:
                    if a["k"] == "player":
                        if a.get("pos") in SKILL:
                            t[key_pos][a["pos"]] += 1
                        # age when traded (as in other leagues' trades), not today
                        if a["id"] in birth and when:
                            t[key_age].append(round((when - birth[a["id"]]).days / 365.25, 1))
                        elif age.get(a["id"]):
                            t[key_age].append(age[a["id"]])
                    elif a["k"] == "pick":
                        t[key_pk] += 1
                        if a.get("round") == 1:
                            t[key_1st] += 1
            t["pieces_got"].append(sum(1 for a in sd["got"] if a["k"] != "faab"))
            t["pieces_gave"].append(sum(1 for a in gave if a["k"] != "faab"))
            t["in_season" if in_season else "offseason"] += 1
            for j, o in enumerate(tr["sides"]):
                if j != i and o["user_id"]:
                    t["vs"][o["user_id"]][0] += 1
                    t["vs"][o["user_id"]][1] += net

    # activity: latest transaction of any kind, and moves in the last 30 days
    now_ms = datetime.now(tz=timezone.utc).timestamp() * 1000
    act = defaultdict(lambda: {"last": 0, "last_type": None, "recent": 0})
    for r in store.query("SELECT league_id, type, roster_ids, created, status_updated FROM txn WHERE status='complete'"):
        ts = r["status_updated"] or r["created"] or 0
        for rid in json.loads(r["roster_ids"] or "[]"):
            uid = owner_of.get((r["league_id"], rid))
            if not uid:
                continue
            a = act[uid]
            if ts > a["last"]:
                a["last"], a["last_type"] = ts, r["type"]
            if now_ms - ts < 30 * 86400 * 1000:
                a["recent"] += 1

    # seasons: record, points, lineup efficiency (points / best possible), regular-season rank, playoff place
    from .picks import bracket_places
    seasons = defaultdict(list)
    for lg in store.query("SELECT league_id, season, status FROM league_season ORDER BY season"):
        rows = store.query("SELECT roster_id, owner_id, wins, losses, ties, fpts, max_pf FROM roster WHERE league_id=?",
                           (lg["league_id"],))
        if not rows:
            continue
        order = sorted(rows, key=lambda r: (-(r["wins"] or 0), -(r["fpts"] or 0)))
        rank = {r["roster_id"]: i for i, r in enumerate(order, 1)}
        places = bracket_places(store, lg["league_id"]) if lg["status"] == "complete" else {}
        effs = [(r["fpts"] or 0) / r["max_pf"] for r in rows if r["max_pf"]]
        league_eff = sum(effs) / len(effs) if effs else None
        for r in rows:
            if not r["owner_id"]:
                continue
            seasons[r["owner_id"]].append({
                "season": lg["season"], "wins": r["wins"] or 0, "losses": r["losses"] or 0, "ties": r["ties"] or 0,
                "pf": round(r["fpts"] or 0, 1), "rank": rank[r["roster_id"]], "place": places.get(r["roster_id"]),
                "eff": round((r["fpts"] or 0) / r["max_pf"], 3) if r["max_pf"] else None,
                "league_eff": round(league_eff, 3) if league_eff else None, "complete": lg["status"] == "complete"})

    # rookie drafts in this league: what each manager's picks are worth now
    vd = store.query("SELECT MAX(snapshot_date) d FROM player_value")[0]["d"]
    val = {r["player_id"]: r["value"] or 0.0 for r in store.query(
        "SELECT player_id, value FROM player_value WHERE snapshot_date=?", (vd,))}
    pname = {r["player_id"]: r["full_name"] for r in store.query("SELECT player_id, full_name FROM player")}
    rookie = defaultdict(list)
    by_round = defaultdict(list)
    for d in store.query("SELECT draft_id, season FROM draft WHERE player_type=1 AND status='complete'"):
        for p in store.query("SELECT pick_no, round, picked_by, player_id FROM draft_pick_made WHERE draft_id=? ORDER BY pick_no",
                             (d["draft_id"],)):
            v = val.get(p["player_id"], 0.0)
            by_round[p["round"]].append(v)
            if p["picked_by"]:
                rookie[p["picked_by"]].append({"season": d["season"], "round": p["round"], "pick_no": p["pick_no"],
                                               "id": p["player_id"], "name": pname.get(p["player_id"]),
                                               "value": round(v)})
    round_avg = {r: sum(v) / len(v) for r, v in by_round.items() if v}

    elsewhere = _elsewhere(store, age)

    def combined(t, n, e):
        """This league's trades plus other leagues' trades, for tendency tags (no values involved)."""
        raw = (e or {}).get("_raw")
        if not raw:
            raw = {"n": 0, "got_age": [], "gave_age": [], "pieces_got": [], "pieces_gave": [], "got_pos": Counter(),
                   "got_picks": 0, "gave_picks": 0}
        ga, va = t["got_age"] + raw["got_age"], t["gave_age"] + raw["gave_age"]
        pg, pv = t["pieces_got"] + raw["pieces_got"], t["pieces_gave"] + raw["pieces_gave"]
        tot = n + raw["n"]
        return {"trades": tot, "got_pos": dict(t["got_pos"] + raw["got_pos"]),
                "got_age": round(sum(ga) / len(ga), 1) if ga else None, "got_age_n": len(ga),
                "gave_age": round(sum(va) / len(va), 1) if va else None, "gave_age_n": len(va),
                "pieces": {"got": round(sum(pg) / tot, 2) if tot else None, "gave": round(sum(pv) / tot, 2) if tot else None},
                "net_picks": (t["got_picks"] + raw["got_picks"]) - (t["gave_picks"] + raw["gave_picks"])}

    result = {}
    uids = set(out) | set(act) | set(seasons) | set(rookie)
    for uid in uids:
        t = out[uid]["tr"]
        n = t["won"] + t["lost"] + t["even"]
        e = elsewhere.get(uid)
        picks_made = rookie.get(uid, [])
        exp = sum(round_avg.get(p["round"], 0) for p in picks_made)
        got = sum(p["value"] for p in picks_made)
        result[uid] = {
            "trade_record": {"won": t["won"], "lost": t["lost"], "even": t["even"]},
            "avg_margin": round(sum(t["margins"]) / n) if n else None,
            "best_trade": t["best"][0] if t["best"] else None, "best_margin": t["best"][1] if t["best"] else None,
            "worst_trade": t["worst"][0] if t["worst"] else None, "worst_margin": t["worst"][1] if t["worst"] else None,
            "got_pos": dict(t["got_pos"]), "gave_pos": dict(t["gave_pos"]),
            "got_age": round(sum(t["got_age"]) / len(t["got_age"]), 1) if t["got_age"] else None,
            "gave_age": round(sum(t["gave_age"]) / len(t["gave_age"]), 1) if t["gave_age"] else None,
            "picks": {"got": t["got_picks"], "gave": t["gave_picks"], "firsts_got": t["got_firsts"], "firsts_gave": t["gave_firsts"]},
            "pieces": {"got": round(sum(t["pieces_got"]) / n, 2) if n else None,
                       "gave": round(sum(t["pieces_gave"]) / n, 2) if n else None},
            "timing": {"in_season": t["in_season"], "offseason": t["offseason"]},
            "vs": sorted(({"team": names.get(o, {}).get("team"), "owner": names.get(o, {}).get("owner"), "n": c,
                           "net": round(net)} for o, (c, net) in t["vs"].items()), key=lambda x: -x["n"]),
            "last_move": act[uid]["last"] or None, "last_move_type": act[uid]["last_type"],
            "moves_30d": act[uid]["recent"],
            "seasons": seasons.get(uid, []),
            "rookie_picks": sorted(picks_made, key=lambda p: (p["season"], p["pick_no"]), reverse=True)[:12],
            "rookie_score": round(got / exp, 2) if exp else None,
            "elsewhere": {k: v for k, v in (e or {}).items() if k != "_raw"} or None,
            "all": combined(t, n, e),
        }
    return result


def build(store: Store) -> dict:
    lg = store.query("SELECT * FROM league_season WHERE is_current=1")[0]
    cur = lg["league_id"]
    owner_of = {(r["league_id"], r["roster_id"]): r["owner_id"] for r in store.query("SELECT league_id, roster_id, owner_id FROM roster")}
    names = {}
    for r in store.query("""SELECT o.user_id, o.display_name, m.team_name FROM owner o
                            LEFT JOIN league_member m ON m.user_id=o.user_id AND m.league_id=?""", (cur,)):
        names[r["user_id"]] = {"owner": r["display_name"], "team": (r["team_name"] or r["display_name"] or "").strip()}
    current_owners = [r["owner_id"] for r in store.query("SELECT owner_id FROM roster WHERE league_id=? AND owner_id IS NOT NULL", (cur,))]
    roster_of_owner = {r["owner_id"]: r["roster_id"] for r in store.query("SELECT roster_id, owner_id FROM roster WHERE league_id=?", (cur,))}

    prof = defaultdict(lambda: {"trades": 0, "partners": Counter(), "net": 0.0, "acq_pos": Counter(),
                                "picks_in": 0, "picks_out": 0, "waiver_adds": 0, "fa_adds": 0,
                                "faab": 0, "max_bid": 0, "moves": 0})
    log = trade_log(store)
    for tr in log:
        if tr.get("faab_only"):
            continue
        for i, sd in enumerate(tr["sides"]):
            uid = sd["user_id"]
            if not uid:
                continue
            p = prof[uid]
            p["trades"] += 1
            p["net"] += sd["net_once"]          # each later trade counted once (no trade trees)
            for j, o in enumerate(tr["sides"]):
                if j != i and o["user_id"]:
                    p["partners"][o["user_id"]] += 1
                p["picks_out"] += sum(1 for a in o["got"] if a["k"] == "pick" and a.get("from") == i)
            for a in sd["got"]:
                if a["k"] == "pick":
                    p["picks_in"] += 1
                elif a["k"] == "player" and a.get("pos") in SKILL:
                    p["acq_pos"][a["pos"]] += 1
    reviews = [{"transaction_id": tr["id"], "season": tr["season"], "week": tr["week"], "ts": tr["ts"],
                "even": int(tr["even"]), "winner": tr["winner"], "margin": tr["margin"], "sides": json.dumps(tr["sides"]),
                "day": json.dumps(tr.get("day"))} for tr in log]
    fit = store.query("SELECT body FROM raw_snapshot WHERE endpoint='package_fit'")
    if fit:   # the trade lab and finder price packages with the league's own exponent
        from . import trade as trade_mod
        trade_mod.P = json.loads(fit[0]["body"]).get("p") or trade_mod.P
    for t in store.query("SELECT * FROM txn WHERE status='complete' ORDER BY COALESCE(status_updated, created)"):
        lid = t["league_id"]
        moves = store.query("SELECT * FROM txn_move WHERE transaction_id=?", (t["transaction_id"],))
        if t["type"] in ("waiver", "free_agent"):
            for m in moves:
                if m["kind"] != "add":
                    continue
                uid = owner_of.get((lid, m["roster_id"]))
                if not uid:
                    continue
                p = prof[uid]
                if t["type"] == "waiver":
                    p["waiver_adds"] += 1
                    if lid == cur and m["amount"]:
                        p["faab"] += m["amount"]
                        p["max_bid"] = max(p["max_bid"], m["amount"])
                else:
                    p["fa_adds"] += 1
        for m in moves:
            uid = owner_of.get((lid, m["roster_id"]))
            if uid and m["kind"] in ("add", "drop"):
                prof[uid]["moves"] += 1

    # drafting: early-round position mix (rounds 1-2 rookie drafts here; rounds 1-3 elsewhere)
    here = defaultdict(Counter)
    for r in store.query("""SELECT pm.picked_by, pm.position FROM draft_pick_made pm JOIN draft d ON d.draft_id=pm.draft_id
                            WHERE d.player_type=1 AND pm.round<=2"""):
        if r["position"] in SKILL:
            here[r["picked_by"]][r["position"]] += 1
    elsewhere = defaultdict(Counter)
    for r in store.query("SELECT user_id, position FROM ext_draft_pick WHERE round<=3"):
        if r["position"] in SKILL:
            elsewhere[r["user_id"]][r["position"]] += 1
    league_mix = Counter()
    for c in elsewhere.values():
        league_mix.update(c)
    base_total = sum(league_mix.values()) or 1
    base_share = {p: league_mix[p] / base_total for p in SKILL}
    other_leagues = Counter(r["user_id"] for r in store.query(
        "SELECT user_id FROM owner_league WHERE league_id NOT IN (SELECT league_id FROM league_season)"))
    windows = {r["roster_id"]: r["window"] for r in store.query("SELECT roster_id, window FROM team_window")}

    scout = _scouting(store, log, owner_of, names)
    rows = []
    trade_counts = [prof[u]["trades"] for u in current_owners]
    avg_trades = sum(trade_counts) / len(trade_counts) if trade_counts else 0
    faab_rank = sorted(current_owners, key=lambda u: -prof[u]["faab"])
    for uid in current_owners:
        p = prof[uid]
        mix = elsewhere.get(uid, Counter())
        n_mix = sum(mix.values())
        share = {k: round(mix[k] / n_mix, 2) for k in SKILL} if n_mix else {}
        tags = []
        sc = scout.get(uid, {})
        al = sc.get("all") or {}
        ew = sc.get("elsewhere") or {}
        if p["trades"] == 0 and not ew.get("trades"):
            tags.append("Hasn't traded")
        elif p["trades"] >= max(3, 1.5 * avg_trades) or (ew.get("per_league") or 0) >= 2.5:
            tags.append("Active trader")
        if p["trades"] and p["net"] >= 1500:
            tags.append("Wins trades (by results so far)")
        elif p["trades"] and p["net"] <= -1500:
            tags.append("Loses trades (by results so far)")
        net_picks = al.get("net_picks", p["picks_in"] - p["picks_out"])   # here + other dynasty leagues
        if net_picks >= 2:
            tags.append("Collects picks")
        elif net_picks <= -2:
            tags.append("Sells picks")
        acq = Counter(al.get("got_pos") or {}) or p["acq_pos"]
        if acq:
            top, cnt = acq.most_common(1)[0]
            if cnt >= 3 and cnt / sum(acq.values()) >= 0.5:
                tags.append(f"Trades for {top}s")
        if uid in faab_rank[:3] and p["faab"] > 0:
            tags.append("Big FAAB spender")
        if n_mix >= 6:
            for k in SKILL:
                if share.get(k, 0) - base_share.get(k, 0) >= 0.12:
                    tags.append(f"Drafts {k}s early")
        if al.get("got_age") and al.get("gave_age") and min(al["got_age_n"], al["gave_age_n"]) >= 3:
            if al["gave_age"] - al["got_age"] >= 2:
                tags.append("Trades for youth")
            elif al["got_age"] - al["gave_age"] >= 2:
                tags.append("Trades for veterans")
        pcs = al.get("pieces") or {}
        if al.get("trades", 0) >= 3 and pcs.get("got") and pcs.get("gave"):
            if pcs["gave"] - pcs["got"] >= 0.5:
                tags.append("Consolidates (2-for-1s)")
            elif pcs["got"] - pcs["gave"] >= 0.5:
                tags.append("Splits stars into depth")
        cur_season = next((x for x in reversed(sc.get("seasons", [])) if not x["complete"]), None)
        if cur_season and cur_season.get("eff") and cur_season.get("league_eff"):
            if cur_season["eff"] - cur_season["league_eff"] >= 0.04:
                tags.append("Sharp lineup setter")
            elif cur_season["league_eff"] - cur_season["eff"] >= 0.05:
                tags.append("Leaves points on the bench")
        if sc.get("moves_30d") == 0:
            tags.append("Quiet lately")
        rid = roster_of_owner.get(uid)
        rows.append({
            "user_id": uid, "roster_id": rid,
            "owner": names.get(uid, {}).get("owner"), "team": names.get(uid, {}).get("team"),
            "window": windows.get(rid),
            "trades": p["trades"], "trade_net": round(p["net"]),
            "partners": json.dumps([{"team": names.get(o, {}).get("team"), "n": n} for o, n in p["partners"].most_common(4)]),
            "acquired_positions": json.dumps(dict(p["acq_pos"])),
            "picks_in": p["picks_in"], "picks_out": p["picks_out"],
            "waiver_adds": p["waiver_adds"], "fa_adds": p["fa_adds"], "faab_spent": p["faab"], "max_bid": p["max_bid"],
            "moves": p["moves"],
            "rookie_early": json.dumps(dict(here.get(uid, Counter()))),
            "draft_mix_other_leagues": json.dumps(share), "other_draft_picks": n_mix,
            "other_leagues": other_leagues.get(uid, 0),
            "tags": json.dumps(tags),
            "scout": json.dumps(scout.get(uid, {})),
        })
    store.write_frame("owner_profile", rows)
    store.conn.execute("DROP TABLE IF EXISTS trade_review")  # shape changed: sides now carry full asset detail
    if reviews:
        store.write_frame("trade_review", reviews)
    store.commit()
    return {"profiles": len(rows), "trades_reviewed": len(reviews), "value_per_point": round(value_per_point(store), 2),
            "league_draft_mix": {k: round(v, 2) for k, v in base_share.items()}}

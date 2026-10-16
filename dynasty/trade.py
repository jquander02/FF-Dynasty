"""Trade evaluation and the trade finder.

evaluate() scores a deal for both sides:
  value     raw totals and a consolidation-adjusted total. Two good players are worth less
            than one great one in a 12-team league (you can only start so many, and
            waivers refill depth), so each side's package is combined as
            (sum of value^P)^(1/P) with P = 1.3: one piece counts in full, two equal pieces
            count as 1.7x one, not 2x. The web app's trade lab uses the same rule.
  lineup    best-lineup points per game for each team before and after, for the rest of
            this season (injured players left out), next season and two seasons out
  verdict   fair within 10% of the larger side's adjusted value

find_trades() searches deals for your team: 1-for-1 and 2-for-1 (you give two) between
your players and each other team's players and picks. It keeps deals that are fair (within
10%) and that help the partner's window: buyers (contenders or 40%+ playoff odds) must get
better now, everyone else must get better over the next two seasons. Ranked by how much
they move your team toward its window, plus how much they help the partner.

Writes trade_suggestion.
"""

from __future__ import annotations

import itertools
import json
from collections import defaultdict
from dataclasses import dataclass, field

from .store import Store
from .teams import OUT_STATUSES, best_lineup

P = 1.3
FAIR = 0.10
CONTEND = ("Dynasty power", "Win now")


def package_value(values: list[float]) -> float:
    vals = [max(0.0, v or 0.0) for v in values]
    if not vals:
        return 0.0
    if len(vals) == 1:
        return vals[0]
    return sum(v ** P for v in vals) ** (1 / P)


@dataclass
class Ctx:
    roster_positions: list
    players: dict = field(default_factory=dict)     # pid -> {pos, val, p0, p1, p2, hurt, name, age}
    rosters: dict = field(default_factory=dict)     # rid -> [pid]
    picks: dict = field(default_factory=dict)       # key "2027-1-4" -> {owner, value, label}
    windows: dict = field(default_factory=dict)     # rid -> window label
    p_playoffs: dict = field(default_factory=dict)  # rid -> playoff odds this season
    names: dict = field(default_factory=dict)       # rid -> team name


def load(store: Store) -> Ctx:
    lg = store.query("SELECT * FROM league_season WHERE is_current=1")[0]
    lid = lg["league_id"]
    ctx = Ctx(roster_positions=json.loads(lg["roster_positions"] or "[]"))
    vd = store.query("SELECT MAX(snapshot_date) d FROM player_value")[0]["d"]
    val = {r["player_id"]: r["value"] for r in store.query("SELECT player_id, value FROM player_value WHERE snapshot_date=?", (vd,))}
    proj = {r["player_id"]: r for r in store.query("SELECT * FROM player_projection")}
    info = {r["player_id"]: dict(r) for r in store.query("SELECT player_id, full_name, position, injury_status FROM player")}
    snap = store.query("SELECT MAX(snapshot_date) d FROM roster_snapshot WHERE league_id=?", (lid,))[0]["d"]
    for r in store.query("SELECT roster_id, player_id, slot FROM roster_snapshot WHERE league_id=? AND snapshot_date=?", (lid, snap)):
        pid = r["player_id"]
        p, i = proj.get(pid), info.get(pid, {})
        ctx.rosters.setdefault(r["roster_id"], []).append(pid)
        ctx.players[pid] = {
            "pos": (p["position"] if p else i.get("position")),
            "name": i.get("full_name") or pid,
            "age": p["age"] if p else None,
            "val": val.get(pid, 0.0) or 0.0,
            "p0": p["ppg_ros"] if p else None, "p1": p["ppg_y1"] if p else None, "p2": p["ppg_y2"] if p else None,
            "hurt": r["slot"] == "reserve" or i.get("injury_status") in OUT_STATUSES,
        }
    for r in store.query("SELECT * FROM pick_value"):
        key = f"{r['season']}-{r['round']}-{r['original_roster_id']}"
        ctx.picks[key] = {"owner": r["owner_roster_id"], "value": r["value"] or 0.0,
                          "season": r["season"], "round": r["round"], "orig": r["original_roster_id"],
                          "exp_slot": r["exp_slot"]}
    for r in store.query("SELECT roster_id, window FROM team_window"):
        ctx.windows[r["roster_id"]] = r["window"]
    if store.query("SELECT 1 FROM sqlite_master WHERE name='season_sim'"):
        for r in store.query("SELECT roster_id, p_playoffs FROM season_sim"):
            ctx.p_playoffs[r["roster_id"]] = r["p_playoffs"] or 0.0
    for r in store.query(
        """SELECT r.roster_id, COALESCE(m.team_name, o.display_name) n FROM roster r
           LEFT JOIN owner o ON o.user_id=r.owner_id
           LEFT JOIN league_member m ON m.league_id=r.league_id AND m.user_id=r.owner_id
           WHERE r.league_id=?""", (lid,)):
        ctx.names[r["roster_id"]] = (r["n"] or "").strip()
    return ctx


def strengths(ctx: Ctx, pids: list[str]) -> tuple[float, float, float]:
    now, nxt, later = [], [], []
    for pid in pids:
        p = ctx.players.get(pid)
        if not p or p["p1"] is None:
            continue
        if not p["hurt"]:
            now.append((pid, p["pos"], p["p0"]))
        nxt.append((pid, p["pos"], p["p1"]))
        later.append((pid, p["pos"], p["p2"]))
    return tuple(round(best_lineup(x, ctx.roster_positions)[0], 2) for x in (now, nxt, later))


def asset_value(ctx: Ctx, a: str) -> float:
    return ctx.picks[a]["value"] if a in ctx.picks else ctx.players.get(a, {}).get("val", 0.0)


def evaluate(ctx: Ctx, team_a: int, team_b: int, a_gives: list[str], b_gives: list[str],
             base: dict | None = None) -> dict:
    """Assets are player ids or pick keys like '2027-1-4'."""
    va = [asset_value(ctx, x) for x in a_gives]
    vb = [asset_value(ctx, x) for x in b_gives]
    adj_a, adj_b = package_value(va), package_value(vb)
    ra, rb = ctx.rosters.get(team_a, []), ctx.rosters.get(team_b, [])
    give_a = {x for x in a_gives if x not in ctx.picks}
    give_b = {x for x in b_gives if x not in ctx.picks}
    after_a = [p for p in ra if p not in give_a] + list(give_b)
    after_b = [p for p in rb if p not in give_b] + list(give_a)
    base = base or {}
    before_a = base.get(team_a) or strengths(ctx, ra)
    before_b = base.get(team_b) or strengths(ctx, rb)
    sa, sb = strengths(ctx, after_a), strengths(ctx, after_b)
    big = max(adj_a, adj_b, 1.0)
    diff = adj_b - adj_a  # >0: team A receives more
    verdict = "fair" if abs(diff) <= FAIR * big else ("a_wins" if diff > 0 else "b_wins")
    return {
        "a_gives_value": round(sum(va)), "b_gives_value": round(sum(vb)),
        "a_gives_adj": round(adj_a), "b_gives_adj": round(adj_b),
        "a_net": round(diff), "verdict": verdict,
        "a_delta": [round(x - y, 2) for x, y in zip(sa, before_a)],
        "b_delta": [round(x - y, 2) for x, y in zip(sb, before_b)],
    }


def find_trades(store: Store, me: int, max_per_team: int = 3, total: int = 20) -> list[dict]:
    ctx = load(store)
    base = {rid: strengths(ctx, pids) for rid, pids in ctx.rosters.items()}
    mine = [p for p in ctx.rosters.get(me, []) if ctx.players[p]["val"] >= 400 and ctx.players[p]["p1"] is not None]
    my_window = ctx.windows.get(me)
    rebuilding = my_window in ("Rebuild", "Rising", "Middle")
    singles = [(p,) for p in mine]
    pairs = list(itertools.combinations(sorted(mine, key=lambda p: -ctx.players[p]["val"])[:14], 2))
    out = []
    for rid, pids in ctx.rosters.items():
        if rid == me:
            continue
        # buyers: contenders and anyone with 40%+ playoff odds; everyone else is building
        contending = ctx.windows.get(rid) in CONTEND or ctx.p_playoffs.get(rid, 0.0) >= 0.4
        theirs = [p for p in pids if ctx.players[p]["val"] >= 400]
        theirs += [k for k, pk in ctx.picks.items() if pk["owner"] == rid and pk["value"] >= 400]
        found = []
        for give in singles + pairs:
            g_adj = package_value([ctx.players[p]["val"] for p in give])
            for get in theirs:
                v = asset_value(ctx, get)
                if not (0.75 * g_adj <= v <= 1.25 * g_adj):
                    continue
                if rebuilding and get not in ctx.picks and (ctx.players[get]["age"] or 99) > 25.5:
                    continue  # a rebuilding team wants youth or picks back
                ev = evaluate(ctx, me, rid, list(give), [get], base)
                if ev["verdict"] != "fair":
                    continue  # lopsided offers rarely get accepted
                their_now = ev["b_delta"][0]
                their_future = (ev["b_delta"][1] + ev["b_delta"][2]) / 2
                if contending and their_now <= 0.3:
                    continue  # a buyer only bites if it helps now
                if not contending and their_future <= 0.3:
                    continue  # a builder only bites if it helps the next two seasons
                my_future = (ev["a_delta"][1] + ev["a_delta"][2]) / 2
                their_gain = their_now if contending else their_future
                score = ev["a_net"] / 100 + (my_future if rebuilding else ev["a_delta"][0]) * 10 + their_gain * 5
                found.append({
                    "partner": rid, "partner_team": ctx.names.get(rid), "partner_window": ctx.windows.get(rid),
                    "give": list(give), "get": [get], **ev, "score": round(score, 1),
                })
        found.sort(key=lambda x: -x["score"])
        seen_get, kept = set(), []
        for f in found:
            if f["get"][0] in seen_get:
                continue
            seen_get.add(f["get"][0])
            kept.append(f)
            if len(kept) >= max_per_team:
                break
        out += kept
    out.sort(key=lambda x: -x["score"])
    out = out[:total]
    for f in out:
        f["partner_buying"] = ctx.windows.get(f["partner"]) in CONTEND or ctx.p_playoffs.get(f["partner"], 0.0) >= 0.4

    def label(a):
        if a in ctx.picks:
            pk = ctx.picks[a]
            return f"{pk['season']} Rd {pk['round']} ({ctx.names.get(pk['orig'], '?')})"
        p = ctx.players.get(a, {})
        return f"{p.get('name')} ({p.get('pos')})"

    rows = []
    for i, f in enumerate(out, 1):
        rows.append({
            "rank": i, "partner": f["partner"], "partner_team": f["partner_team"],
            "partner_window": f["partner_window"],
            "give": json.dumps(f["give"]), "get": json.dumps(f["get"]),
            "give_label": " + ".join(label(a) for a in f["give"]),
            "get_label": " + ".join(label(a) for a in f["get"]),
            "give_value": f["a_gives_adj"], "get_value": f["b_gives_adj"],
            "my_delta": json.dumps(f["a_delta"]), "their_delta": json.dumps(f["b_delta"]),
            "score": f["score"], "partner_buying": int(f["partner_buying"]),
        })
    store.write_frame("trade_suggestion", rows) if rows else store.conn.execute("DROP TABLE IF EXISTS trade_suggestion")
    store.commit()
    return rows

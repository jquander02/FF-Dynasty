"""Rebuild planner for your roster.

  Tank tracker  your record, projected wins, odds of the 1.01 and a top-3 pick, and the
                expected slot of every pick you own
  Sell          veterans near their position's age cliff, or whose projection falls 15%+
                by two seasons out, with the buyers (contenders and teams with 40%+ playoff
                odds) whose lineup they'd upgrade most
  Buy           players 24 or younger sitting on other teams' benches (not in that team's
                best lineup), contenders first: easiest to pry loose
  Core          your young players to build around
"""

from __future__ import annotations

import json
from collections import defaultdict

from .store import Store

CLIFF = {"QB": 32, "RB": 26, "WR": 28, "TE": 29}
CONTEND = ("Dynasty power", "Win now")
SLOT_POS = {"QB": ("QB",), "RB": ("RB", "FLEX"), "WR": ("WR", "FLEX"), "TE": ("TE", "FLEX")}


def build(store: Store) -> dict:
    me = store.query("SELECT user_id FROM owner WHERE is_me=1")
    lg = store.query("SELECT * FROM league_season WHERE is_current=1")[0]
    lid = lg["league_id"]
    mine = None
    if me:
        r = store.query("SELECT roster_id FROM roster WHERE league_id=? AND (owner_id=? OR co_owners LIKE ?)",
                        (lid, me[0]["user_id"], f'%"{me[0]["user_id"]}"%'))
        mine = r[0]["roster_id"] if r else None
    if mine is None:
        return {}

    tw = {r["roster_id"]: dict(r) for r in store.query("SELECT * FROM team_window")}
    sim = {r["roster_id"]: dict(r) for r in store.query("SELECT * FROM season_sim")}
    names = {
        r["roster_id"]: (r["n"] or "").strip() for r in store.query(
            """SELECT r.roster_id, COALESCE(m.team_name, o.display_name) n FROM roster r
               LEFT JOIN owner o ON o.user_id=r.owner_id
               LEFT JOIN league_member m ON m.league_id=r.league_id AND m.user_id=r.owner_id
               WHERE r.league_id=?""", (lid,))
    }
    vd = store.query("SELECT MAX(snapshot_date) d FROM player_value")[0]["d"]
    val = {r["player_id"]: dict(r) for r in store.query("SELECT * FROM player_value WHERE snapshot_date=?", (vd,))}
    proj = {r["player_id"]: dict(r) for r in store.query("SELECT * FROM player_projection")}
    info = {r["player_id"]: dict(r) for r in store.query("SELECT player_id, full_name, team FROM player")}
    snap = store.query("SELECT MAX(snapshot_date) d FROM roster_snapshot WHERE league_id=?", (lid,))[0]["d"]
    owner_of, rosters = {}, defaultdict(list)
    for r in store.query("SELECT roster_id, player_id, slot FROM roster_snapshot WHERE league_id=? AND snapshot_date=?",
                         (lid, snap)):
        owner_of[r["player_id"]] = r["roster_id"]
        rosters[r["roster_id"]].append(r["player_id"])

    def card(pid: str) -> dict:
        p, v, i = proj.get(pid, {}), val.get(pid, {}), info.get(pid, {})
        mkt = v.get("fc_value") if v.get("fc_value") is not None else v.get("dp_scaled")
        return {
            "id": pid, "name": i.get("full_name") or p.get("name") or pid, "pos": p.get("position"),
            "nfl": i.get("team"), "age": p.get("age"), "val": round(v.get("value") or 0),
            "model": round(v["model_scaled"]) if v.get("model_scaled") is not None else None,
            "market": round(mkt) if mkt is not None else None,
            "ppg_now": p.get("ppg_ros"), "ppg_next": p.get("ppg_y1"), "ppg_2y": p.get("ppg_y2"),
        }

    # weakest current starter by slot type, per team (for "who'd upgrade")
    def weakest(rid: int) -> dict:
        starters = json.loads(tw.get(rid, {}).get("starters_now") or "[]")
        out = {}
        for pid in starters:
            p = proj.get(pid)
            if not p:
                continue
            for slot in SLOT_POS.get(p["position"], ()):
                cur = out.get(slot)
                if cur is None or p["ppg_ros"] < cur:
                    out[slot] = p["ppg_ros"]
        return out

    # buyers: contenders plus anyone in the playoff hunt (40%+ playoff odds)
    contenders = [rid for rid, t in tw.items() if rid != mine and (
        t.get("window") in CONTEND or t.get("rank_now", 99) <= 4 or (sim.get(rid, {}).get("p_playoffs") or 0) >= 0.4)]
    weak = {rid: weakest(rid) for rid in contenders}

    # ---- sell -------------------------------------------------------------------
    sell = []
    for pid in rosters[mine]:
        c = card(pid)
        if not c["pos"] or c["val"] < 600 or c["age"] is None:
            continue
        past_cliff = c["age"] >= CLIFF.get(c["pos"], 99) - 0.5
        base = c["ppg_next"] or 0.0
        drop = 1 - (c["ppg_2y"] or 0.0) / base if base > 0 else 0.0
        if not past_cliff and drop < 0.15:
            continue
        buyers = []
        for rid in contenders:
            gaps = [c["ppg_now"] - weak[rid][s] for s in SLOT_POS.get(c["pos"], ()) if s in weak[rid]]
            gain = max(gaps) if gaps else 0.0
            if gain > 0.5:
                buyers.append({"roster_id": rid, "team": names.get(rid), "upgrade_ppg": round(gain, 1)})
        buyers.sort(key=lambda b: -b["upgrade_ppg"])
        c.update(projected_drop=round(drop, 2), past_cliff=past_cliff, buyers=buyers[:3],
                 value_at_risk=round(c["val"] * max(drop, 0.2 if past_cliff else 0.0)))
        sell.append(c)
    sell.sort(key=lambda x: -x["value_at_risk"])

    # ---- buy -------------------------------------------------------------------
    buy = []
    for rid, plist in rosters.items():
        if rid == mine:
            continue
        starters = set(json.loads(tw.get(rid, {}).get("starters_now") or "[]"))
        for pid in plist:
            c = card(pid)
            if not c["pos"] or c["age"] is None or c["age"] > 24.5 or c["val"] < 800 or pid in starters:
                continue
            c.update(roster_id=rid, team=names.get(rid), owner_window=tw.get(rid, {}).get("window"))
            buy.append(c)
    buy.sort(key=lambda x: (x["owner_window"] not in CONTEND, -x["val"]))

    # ---- core ------------------------------------------------------------------
    core = sorted((card(p) for p in rosters[mine]), key=lambda x: -x["val"])
    core = [c for c in core if c["age"] is not None and c["age"] < 25.5 and c["val"] > 0][:8]

    # ---- picks + tank -----------------------------------------------------------
    my_picks = [
        {"season": r["season"], "round": r["round"], "from": names.get(r["original_roster_id"]),
         "own": r["original_roster_id"] == mine, "exp_slot": r["exp_slot"], "p_top3": r["p_top3"],
         "p_slot1": r["p_slot1"], "value": r["value"]}
        for r in store.query("SELECT * FROM pick_value WHERE owner_roster_id=? ORDER BY season, round, exp_slot",
                             (mine,))
    ]
    s = sim.get(mine, {})
    t = tw.get(mine, {})
    rk = store.query("SELECT wins, losses, ties, fpts FROM roster WHERE league_id=? AND roster_id=?", (lid, mine))[0]
    st = store.query("SELECT body FROM raw_snapshot WHERE endpoint='state/nfl'")
    week = (json.loads(st[0]["body"]) if st and st[0]["body"] else {}).get("week") or 0
    deadline = lg["trade_deadline"]
    return {
        "roster_id": mine,
        "window": t.get("window"),
        "ranks": {k: t.get(k) for k in ("rank_now", "rank_next", "rank_later")},
        "record": f"{rk['wins']}-{rk['losses']}" + (f"-{rk['ties']}" if rk["ties"] else ""),
        "exp_wins": s.get("exp_wins"),
        "p_first_pick": s.get("p_first_pick"),
        "p_top3": s.get("p_top3"),
        "exp_slot": s.get("exp_slot"),
        "p_playoffs": s.get("p_playoffs"),
        "trade_deadline_week": deadline,
        "weeks_to_deadline": max(0, (deadline or 0) - week) if deadline else None,
        "assets": {"players": t.get("player_value"), "picks": t.get("pick_value"), "total": t.get("total_assets")},
        "my_picks": my_picks,
        "sell": sell[:12],
        "buy": buy[:15],
        "core": core,
        "contenders": [{"roster_id": r, "team": names.get(r), "window": tw[r].get("window"),
                        "p_playoffs": sim.get(r, {}).get("p_playoffs")} for r in contenders],
    }

"""Team strength and contention windows.

For each roster, the best possible starting lineup (this league's slots) is built from
projected points per game for three horizons:
  now   — rest of this season (injured / IR players left out)
  next  — next season
  later — two seasons out
Teams are ranked on each, and labelled:
  Dynasty power  strong now and later      Win now   strong now, fading later
  Rising         weaker now, strong later  Rebuild   weak now and later
  Middle         everything else
Also: roster value split by age band, and pick capital (filled in by picks.py).

Writes team_window.
"""

from __future__ import annotations

import json
from collections import defaultdict

from .model import FLEX_ELIGIBLE, POSITIONS
from .store import Store

OUT_STATUSES = {"IR", "Out", "PUP", "Sus", "NA", "COV", "DNR"}


def best_lineup(players: list[tuple[str, str, float]], roster_positions: list[str]) -> tuple[float, set]:
    """players: (player_id, pos, ppg). Returns (total ppg of best lineup, starter ids)."""
    by_pos = defaultdict(list)
    for pid, pos, ppg in players:
        if pos in POSITIONS and ppg is not None:
            by_pos[pos].append((ppg, pid))
    for lst in by_pos.values():
        lst.sort(reverse=True)
    used = set()
    total = 0.0
    slots = [s for s in roster_positions if s in POSITIONS]
    flex = [FLEX_ELIGIBLE[s] for s in roster_positions if s in FLEX_ELIGIBLE]
    for s in slots:
        for ppg, pid in by_pos[s]:
            if pid not in used:
                used.add(pid)
                total += ppg
                break
    for elig in flex:
        best = None
        for pos in elig:
            for ppg, pid in by_pos[pos]:
                if pid not in used:
                    if best is None or ppg > best[0]:
                        best = (ppg, pid)
                    break
        if best:
            used.add(best[1])
            total += best[0]
    return total, used


def age_band(age) -> str:
    if age is None:
        return "unknown"
    return "young" if age < 25 else ("prime" if age < 29 else "veteran")


def build(store: Store) -> list[dict]:
    lg = store.query("SELECT * FROM league_season WHERE is_current=1")[0]
    lid = lg["league_id"]
    roster_positions = json.loads(lg["roster_positions"] or "[]")
    snap = store.query("SELECT MAX(snapshot_date) d FROM roster_snapshot WHERE league_id=?", (lid,))[0]["d"]

    proj = {r["player_id"]: r for r in store.query("SELECT * FROM player_projection")}
    vdate = store.query("SELECT MAX(snapshot_date) d FROM player_value")[0]["d"] if _has(store, "player_value") else None
    val = {r["player_id"]: r["value"] for r in store.query(
        "SELECT player_id, value FROM player_value WHERE snapshot_date=?", (vdate,))} if vdate else {}
    inj = {r["player_id"]: r["injury_status"] for r in store.query("SELECT player_id, injury_status FROM player")}

    rosters = defaultdict(list)
    for r in store.query("SELECT roster_id, player_id, slot FROM roster_snapshot WHERE league_id=? AND snapshot_date=?",
                         (lid, snap)):
        rosters[r["roster_id"]].append((r["player_id"], r["slot"]))

    out = []
    for rid, plist in rosters.items():
        now, nxt, later = [], [], []
        bands = defaultdict(float)
        for pid, slot in plist:
            p = proj.get(pid)
            v = val.get(pid, 0.0) or 0.0
            bands[age_band(p["age"] if p else None)] += v
            if not p:
                continue
            hurt = slot == "reserve" or inj.get(pid) in OUT_STATUSES
            if not hurt:
                now.append((pid, p["position"], p["ppg_ros"]))
            nxt.append((pid, p["position"], p["ppg_y1"]))
            later.append((pid, p["position"], p["ppg_y2"]))
        s_now, starters_now = best_lineup(now, roster_positions)
        s_next, _ = best_lineup(nxt, roster_positions)
        s_later, _ = best_lineup(later, roster_positions)
        out.append({
            "roster_id": rid,
            "strength_now": round(s_now, 1),
            "strength_next": round(s_next, 1),
            "strength_later": round(s_later, 1),
            "starters_now": json.dumps(sorted(starters_now)),
            "value_young": round(bands["young"]),
            "value_prime": round(bands["prime"]),
            "value_veteran": round(bands["veteran"]),
            "player_value": round(sum(bands.values())),
        })

    n = len(out)
    for key, rk in (("strength_now", "rank_now"), ("strength_next", "rank_next"), ("strength_later", "rank_later")):
        for i, t in enumerate(sorted(out, key=lambda x: -x[key]), 1):
            t[rk] = i
    top, bottom = max(1, round(n / 3)), n - max(1, round(n / 3)) + 1
    for t in out:
        future = min(t["rank_next"], t["rank_later"])
        if t["rank_now"] <= top and future <= top:
            t["window"] = "Dynasty power"
        elif t["rank_now"] <= top:
            t["window"] = "Win now"
        elif t["rank_now"] >= bottom and future >= bottom:
            t["window"] = "Rebuild"
        elif future <= top:
            t["window"] = "Rising"
        else:
            t["window"] = "Middle"
    store.write_frame("team_window", out)
    store.commit()
    return out


def _has(store: Store, t: str) -> bool:
    return bool(store.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)))

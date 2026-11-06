"""Player-by-player season simulation (after ffsimulator's approach).

For every remaining week after this one, regular season and playoffs, each simulated week:
  1. each player plays or not (his chance of playing: byes, injury designations, games-share),
  2. each team starts its best available players by projection (managers set lineups knowing who's
     out, not how they'll score),
  3. each starter scores his projection plus noise (spread by position, SD_FIT), floored at 0.
The team's simulated score feeds the standings and the playoff bracket (picks.simulate_season), so
playoff and title odds reflect roster depth, byes and injuries rather than one average per team.

Inputs come from sim_player_week (written by dynasty/weekly.py). The same inputs are exported to the
app (sim.json) where the trade lab re-runs this simulation with a trade applied.
"""

from __future__ import annotations

import json
from collections import defaultdict

import numpy as np

from .store import Store
from .weekly import FLEX_ELIGIBLE, SD_FIT, SLOTS, slot_order

SIMS = 4000


def _has(store: Store, t: str) -> bool:
    return bool(store.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)))


def load(store: Store) -> dict | None:
    if not _has(store, "sim_player_week"):
        return None
    lg = store.query("SELECT roster_positions FROM league_season WHERE is_current=1")
    slots = [s for s in json.loads(lg[0]["roster_positions"] or "[]") if s in SLOTS] if lg else []
    rows = store.query("SELECT roster_id, player_id, pos, week, m, p FROM sim_player_week")
    if not rows or not slots:
        return None
    weeks = sorted({r["week"] for r in rows})
    wi = {w: i for i, w in enumerate(weeks)}
    roster = defaultdict(list)
    players = {}
    for r in rows:
        pid = r["player_id"]
        if pid not in players:
            players[pid] = {"pos": r["pos"], "m": [0.0] * len(weeks), "p": [0.0] * len(weeks)}
            roster[r["roster_id"]].append(pid)
        players[pid]["m"][wi[r["week"]]] = r["m"] or 0.0
        players[pid]["p"][wi[r["week"]]] = r["p"] or 0.0
    return {"slots": slots, "weeks": weeks, "roster": dict(roster), "players": players}


def team_scores(data: dict, pids: list[str], sims: int = SIMS, seed: int = 0) -> np.ndarray:
    """Simulated weekly scores for one roster: array [sims, weeks]."""
    slots, weeks, P = data["slots"], data["weeks"], data["players"]
    order = slot_order(slots)
    rng = np.random.default_rng(seed)
    pids = [p for p in pids if p in P]
    out = np.zeros((sims, len(weeks)))
    if not pids:
        return out
    pos = [P[p]["pos"] for p in pids]
    for j in range(len(weeks)):
        m = np.array([P[p]["m"][j] for p in pids])
        pp = np.array([P[p]["p"][j] for p in pids])
        a = np.array([SD_FIT.get(x, (5.0, 0.3))[0] for x in pos])
        k = np.array([SD_FIT.get(x, (5.0, 0.3))[1] for x in pos])
        rank = np.argsort(-m)                                   # lineup preference: best projection first
        avail = rng.random((sims, len(pids))) < pp[None, :]
        score = np.maximum(0.0, m[None, :] + (a + k * m)[None, :] * rng.standard_normal((sims, len(pids))))
        used = np.zeros((sims, len(pids)), bool)
        total = np.zeros(sims)
        for si in order:
            ok = np.array([pos[i] in FLEX_ELIGIBLE.get(slots[si], (slots[si],)) for i in range(len(pids))])
            cand = avail & ~used & ok[None, :]
            cand_r = cand[:, rank]                               # in preference order
            has = cand_r.any(axis=1)
            first = np.argmax(cand_r, axis=1)
            chosen = rank[first]
            rows = np.nonzero(has)[0]
            used[rows, chosen[rows]] = True
            total[rows] += score[rows, chosen[rows]]
        out[:, j] = total
    return out


def all_scores(data: dict, sims: int = SIMS, seed: int = 7) -> dict:
    """rid -> {week: array of sims scores}. Common random numbers per roster (seeded by roster id)."""
    res = {}
    for rid, pids in data["roster"].items():
        arr = team_scores(data, pids, sims, seed=seed * 1000 + int(rid))
        res[rid] = {w: arr[:, j] for j, w in enumerate(data["weeks"])}
    return res

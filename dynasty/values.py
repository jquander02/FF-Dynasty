"""Blends the projection model with market values into one dynasty value per player.

All three inputs are put on FantasyCalc's scale by rank: the player ranked Nth by a source
gets the value FantasyCalc gives its own Nth player. That keeps each source's ordering
while giving every source the same (trade-shaped, top-heavy) value curve.

Default weights (set in config.yaml as value_weights): model 0.6, FantasyCalc 0.3,
DynastyProcess 0.1. A missing source is dropped and the rest renormalized.
A model value of 0 (projects below replacement) counts as a real 0, not as missing.
"""

from __future__ import annotations

import time
from .store import Store

DEFAULT_WEIGHTS = {"model": 0.6, "fantasycalc": 0.3, "dynastyprocess": 0.1}
SCHEMA = """
CREATE TABLE IF NOT EXISTS player_value (
    snapshot_date TEXT, player_id TEXT, position TEXT,
    value REAL, overall_rank INTEGER, position_rank INTEGER,
    model_scaled REAL, fc_value REAL, dp_scaled REAL, model_raw REAL,
    PRIMARY KEY (snapshot_date, player_id)
);
"""


def rank_map(scores: dict[str, float], curve: list[float]) -> dict[str, float]:
    """Map positive scores onto a descending value curve by rank; zeros stay zero."""
    if not curve:
        return {}
    pos = sorted(((v, k) for k, v in scores.items() if v and v > 0), reverse=True)
    out = {k: 0.0 for k, v in scores.items() if not v or v <= 0}
    last_score = pos[len(curve) - 1][0] if len(pos) >= len(curve) else None
    for i, (v, k) in enumerate(pos):
        if i < len(curve):
            out[k] = curve[i]
        else:  # below the curve's end: scale the last value down proportionally
            out[k] = curve[-1] * (v / last_score) if last_score else 0.0
    return out


def build(store: Store, weights: dict | None = None) -> dict:
    w = dict(DEFAULT_WEIGHTS)
    w.update(weights or {})
    store.conn.executescript(SCHEMA)
    has_market = bool(store.query("SELECT 1 FROM sqlite_master WHERE name='market_value'"))

    def latest(source: str) -> dict[str, float]:
        if not has_market:
            return {}
        rows = store.query(
            """SELECT player_id, value FROM market_value
               WHERE source=? AND player_id IS NOT NULL AND position != 'PICK'
                 AND snapshot_date=(SELECT MAX(snapshot_date) FROM market_value WHERE source=?)""",
            (source, source),
        )
        return {r["player_id"]: r["value"] or 0.0 for r in rows}

    fc = latest("fantasycalc")
    dp = latest("dynastyprocess")
    proj = {
        r["player_id"]: (r["position"], r["model_value"] or 0.0)
        for r in store.query("SELECT player_id, position, model_value FROM player_projection")
    } if store.query("SELECT 1 FROM sqlite_master WHERE name='player_projection'") else {}

    curve = sorted((v for v in fc.values() if v > 0), reverse=True)
    if not curve:  # no FantasyCalc today: fall back to DP's own curve, then a flat 0-10000 scale
        curve = sorted((v for v in dp.values() if v > 0), reverse=True)
    if not curve:
        n = max(1, sum(1 for _, v in proj.values() if v > 0))
        curve = [10000 * (1 - i / n) ** 2 for i in range(n)]

    model_scaled = rank_map({k: v for k, (_, v) in proj.items()}, curve)
    # Rank DynastyProcess on its full list (incl. players we couldn't map) so ranks aren't inflated.
    dp_scaled = {}
    if dp:
        rows = store.query(
            """SELECT key, player_id, value FROM market_value
               WHERE source='dynastyprocess' AND position != 'PICK'
                 AND snapshot_date=(SELECT MAX(snapshot_date) FROM market_value WHERE source='dynastyprocess')"""
        )
        by_key = rank_map({r["key"]: r["value"] or 0.0 for r in rows}, curve)
        dp_scaled = {r["player_id"]: by_key[r["key"]] for r in rows if r["player_id"] and r["key"] in by_key}

    pos_of = {k: p for k, (p, _) in proj.items()}
    need = (set(fc) | set(dp)) - set(pos_of)
    if need:
        for r in store.query("SELECT player_id, position FROM player"):
            if r["player_id"] in need:
                pos_of[r["player_id"]] = r["position"]

    rows = []
    for pid in set(model_scaled) | set(fc) | set(dp_scaled):
        parts = []
        if pid in model_scaled:
            parts.append((w["model"], model_scaled[pid]))
        if pid in fc:
            parts.append((w["fantasycalc"], fc[pid]))
        if pid in dp_scaled:
            parts.append((w["dynastyprocess"], dp_scaled[pid]))
        tw = sum(a for a, _ in parts)
        val = sum(a * b for a, b in parts) / tw if tw else 0.0
        rows.append({
            "player_id": pid,
            "position": pos_of.get(pid),
            "value": round(val, 1),
            "model_scaled": round(model_scaled[pid], 1) if pid in model_scaled else None,
            "fc_value": fc.get(pid),
            "dp_scaled": round(dp_scaled[pid], 1) if pid in dp_scaled else None,
            "model_raw": proj.get(pid, (None, None))[1],
        })
    rows.sort(key=lambda r: -r["value"])
    pos_counter: dict[str, int] = {}
    d = time.strftime("%Y-%m-%d", time.gmtime())
    for i, r in enumerate(rows, 1):
        r["overall_rank"] = i
        pos_counter[r["position"]] = pos_counter.get(r["position"], 0) + 1
        r["position_rank"] = pos_counter[r["position"]]
        r["snapshot_date"] = d
    store.delete("player_value", "snapshot_date=?", (d,))
    store.upsert("player_value", rows)
    store.commit()
    return {
        "valued": len(rows),
        "with_model": len(model_scaled),
        "with_fantasycalc": len(fc),
        "with_dynastyprocess": len(dp_scaled),
    }

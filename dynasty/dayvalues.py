"""Model values as of past trade dates ("on the day" for trades older than the daily value history).

Daily value snapshots only start in October 2026, so older trades can't be judged at the values of
their day. For those, the hand-built model is re-run as of the trade's date, using only data that
existed then: before a season, every finished season; during a season, the weeks already played.
Each cut (season, weeks played) is computed once and kept in `model_value_asof`.

The model alone is on a different scale from the blended values (model + FantasyCalc +
DynastyProcess), and the market sources have no history, so trade_log maps each player's model
rank on that day to the blended value at the same rank in the oldest value snapshot (owners._DayScale).
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from .store import Store

log = logging.getLogger(__name__)

POSITIONS = ("QB", "RB", "WR", "TE")
LINEUP = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX"]


def kickoff(season: int) -> date:
    """NFL kickoff: the Thursday after Labor Day (first Monday of September)."""
    d = date(season, 9, 1)
    labor = d + timedelta(days=(0 - d.weekday()) % 7)
    return labor + timedelta(days=3)


def nfl_draft(season: int) -> date:
    """First day of the NFL draft: the last Thursday of April."""
    d = date(season, 4, 30)
    return d - timedelta(days=(d.weekday() - 3) % 7)


def cut_for(day: str) -> tuple[int, int]:
    """(season, weeks played) the model would have used on a date (YYYY-MM-DD).

    Before kickoff (and January/February, after the last season) it's the coming season with no
    weeks played; during the season, the weeks finished by then (a week ends the Monday after its
    Thursday game). Weeks -1 means the offseason before that year's NFL draft (no rookies yet)."""
    d = date.fromisoformat(day)
    y = d.year
    if d < nfl_draft(y):
        return y, -1
    k0 = kickoff(y)
    if d < k0:
        return y, 0
    done = (d - k0).days
    weeks = 0 if done < 5 else (done - 5) // 7 + 1
    return y, min(weeks, 18)


def cut_key(cut: tuple[int, int]) -> str:
    return f"{cut[0]}w{cut[1]}"


def model_values(b: dict, season: int, weeks: int, cfg=None) -> dict:
    """gsis id -> model value (discounted points above replacement) as of (season, weeks played)."""
    from . import learned, model
    cfg = cfg or model.ModelConfig()
    d = b if isinstance(b, learned.Data) else learned.Data(b)
    base, share = learned.base_projections(d, season, max(0, weeks), cfg)
    proj = {g: v["_p"] for g, v in base.items() if v["_p"]["position"] in POSITIONS}
    if weeks < 0:   # before the NFL draft: that class isn't known yet
        rookies = {p["gsis_id"] for p in d.b["picks"] if p["year"] == season}
        proj = {g: v for g, v in proj.items() if g not in rookies}
    weeks = max(0, weeks)
    if weeks == 0:
        # base projections before a season run through the last finished season: shift so the
        # first horizon is the coming season, as the live model sees it in the offseason
        proj = {g: dict(v, ppg_ros=v["ppg_y1"], ppg_y1=v["ppg_y2"], ppg_y2=v["ppg_y3"]) for g, v in proj.items()}
    repl, _ = model.replacement_levels({g: (v["position"], v["ppg_y1"]) for g, v in proj.items()},
                                       LINEUP, cfg.teams, cfg.replacement_depth)
    left = max(0, cfg.season_games - weeks)
    out = {}
    for g, v in proj.items():
        out[g] = sum(model.discounted(model.value_parts(v, repl.get(v["position"], 0.0), share, cfg, left), left, cfg))
    return out


def needed_cuts(store: Store, first_snapshot: str | None) -> set[tuple[int, int]]:
    """Cuts for every completed trade made before the value history began."""
    cuts = set()
    for t in store.query("SELECT COALESCE(status_updated, created) ts FROM txn "
                         "WHERE status='complete' AND type='trade'"):
        if not t["ts"]:
            continue
        day = time.strftime("%Y-%m-%d", time.gmtime(t["ts"] / 1000))
        if first_snapshot and day >= first_snapshot:
            continue
        cuts.add(cut_for(day))
    return cuts


def build(store: Store, bundle: dict | None = None) -> dict:
    """Fill model_value_asof for any trade-date cut not computed yet. A cut is final once its
    weeks are over, so each is computed once; the current cut is never needed (snapshots exist)."""
    store.conn.execute("CREATE TABLE IF NOT EXISTS model_value_asof (cut TEXT, player_id TEXT, gsis_id TEXT, "
                       "model_value REAL, PRIMARY KEY (cut, player_id))")
    first = None
    if store.query("SELECT 1 FROM sqlite_master WHERE name='player_value'"):
        first = store.query("SELECT MIN(snapshot_date) d FROM player_value")[0]["d"]
    have = {r["cut"] for r in store.query("SELECT DISTINCT cut FROM model_value_asof")}
    todo = sorted(c for c in needed_cuts(store, first) if cut_key(c) not in have)
    if not todo:
        return {"cuts": len(have), "new": 0}
    if bundle is None:
        from .backtest import add_late_ppg, add_parts, make_bundle
        bundle = make_bundle(store)
        add_late_ppg(bundle)
        add_parts(bundle)
    from . import learned
    d = learned.Data(bundle)
    sleeper = {}
    for r in store.query("SELECT player_id, gsis_id FROM player_xref WHERE gsis_id IS NOT NULL"):
        sleeper.setdefault(r["gsis_id"], r["player_id"])
    done = 0
    for c in todo:
        try:
            vals = model_values(d, c[0], c[1])
        except Exception as exc:  # noqa: BLE001 - one bad cut shouldn't stop the rest
            log.warning("as-of values %s failed: %s", cut_key(c), exc)
            continue
        rows = [{"cut": cut_key(c), "player_id": sleeper[g], "gsis_id": g, "model_value": round(v, 2)}
                for g, v in vals.items() if g in sleeper]
        store.upsert("model_value_asof", rows)
        done += 1
    store.commit()
    return {"cuts": len(have) + done, "new": done}

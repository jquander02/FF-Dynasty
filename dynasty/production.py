"""League-scored production: every player-week re-scored with this league's settings,
joined to usage (snaps, targets, carries) and expected fantasy points.

Writes:
  player_week   one row per player per regular-season week
  player_season one row per player per season (games, points per game, usage averages)
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict

from .scoring import make_scorer, resolve_columns, unmapped_keys
from .store import Store

log = logging.getLogger(__name__)
SKILL = ("QB", "RB", "WR", "TE", "K")


def _exists(store: Store, table: str) -> bool:
    return bool(store.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)))


def _cols(store: Store, table: str) -> list[str]:
    return [r["name"] for r in store.query(f"PRAGMA table_info({table})")]


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def build(store: Store) -> dict:
    if not _exists(store, "nfl_player_stats_week"):
        log.warning("No nflverse weekly stats yet; skipping production build")
        return {}
    lg = store.query("SELECT scoring_settings FROM league_season WHERE is_current=1")
    scoring = json.loads(lg[0]["scoring_settings"] or "{}") if lg else {}
    cols = _cols(store, "nfl_player_stats_week")
    score = make_scorer(scoring, cols)
    colmap = resolve_columns(cols)
    missing = unmapped_keys(scoring, cols)

    gsis_to_sleeper = {
        r["gsis_id"]: r["player_id"]
        for r in store.query("SELECT player_id, gsis_id FROM player_xref WHERE gsis_id IS NOT NULL")
    }

    # pfr id -> gsis id, for snap counts
    pfr_to_gsis = {}
    if _exists(store, "nfl_ff_playerids"):
        fcols = _cols(store, "nfl_ff_playerids")
        if "pfr_id" in fcols and "gsis_id" in fcols:
            for r in store.query("SELECT pfr_id, gsis_id FROM nfl_ff_playerids WHERE pfr_id IS NOT NULL AND gsis_id IS NOT NULL"):
                pfr_to_gsis[str(r["pfr_id"])] = str(r["gsis_id"])

    snaps = {}
    if _exists(store, "nfl_snap_counts"):
        scols = _cols(store, "nfl_snap_counts")
        if {"pfr_player_id", "season", "week", "offense_pct"} <= set(scols):
            for r in store.query("SELECT pfr_player_id, season, week, offense_pct FROM nfl_snap_counts"):
                g = pfr_to_gsis.get(str(r["pfr_player_id"]))
                if g:
                    pct = _num(r["offense_pct"])
                    if pct is not None and pct > 1.0:
                        pct /= 100.0
                    snaps[(g, _int(r["season"]), _int(r["week"]))] = pct

    xfp = {}
    if _exists(store, "nfl_ff_opportunity"):
        ocols = _cols(store, "nfl_ff_opportunity")
        exp_col = next((c for c in ("total_fantasy_points_exp", "fantasy_points_ppr_exp") if c in ocols), None)
        if exp_col and {"player_id", "season", "week"} <= set(ocols):
            for r in store.query(f'SELECT player_id, season, week, "{exp_col}" x FROM nfl_ff_opportunity'):
                xfp[(str(r["player_id"]), _int(r["season"]), _int(r["week"]))] = _num(r["x"])

    pick = lambda name: f'"{name}"' if name in cols else "NULL"  # noqa: E731
    stat_cols = sorted(set(colmap.values()))
    select = ", ".join(
        [f'"{c}"' for c in stat_cols]
        + [f"{pick('player_id')} AS gsis", f"{pick('position')} AS position", f"{pick('team')} AS team",
           f"{pick('season')} AS season", f"{pick('week')} AS week",
           f"{pick('season_type')} AS season_type",
           f"{pick('target_share')} AS target_share", f"{pick('air_yards_share')} AS air_yards_share",
           f"{pick('wopr')} AS wopr"]
    )
    rows = []
    for r in store.query(f"SELECT {select} FROM nfl_player_stats_week"):
        if r["season_type"] not in (None, "REG"):
            continue
        pos = r["position"]
        if pos not in SKILL:
            continue
        d = dict(r)
        g = str(d["gsis"])
        season, week = _int(d["season"]), _int(d["week"])
        val = lambda s: _num(d.get(colmap[s])) if s in colmap else None  # noqa: E731
        rows.append(
            {
                "gsis_id": g,
                "player_id": gsis_to_sleeper.get(g),
                "season": season,
                "week": week,
                "position": pos,
                "team": d["team"],
                "pts": score(d, pos),
                "pass_yd": val("pass_yd"),
                "pass_att": val("pass_att"),
                "pass_td": val("pass_td"),
                "pass_int": val("pass_int"),
                "rush_td": val("rush_td"),
                "rec_td": val("rec_td"),
                "rush_att": val("rush_att"),
                "rush_yd": val("rush_yd"),
                "targets": val("rec_tgt"),
                "rec": val("rec"),
                "rec_yd": val("rec_yd"),
                "target_share": _num(d["target_share"]),
                "air_yards_share": _num(d["air_yards_share"]),
                "wopr": _num(d["wopr"]),
                "snap_pct": snaps.get((g, season, week)),
                "xfp": xfp.get((g, season, week)),
            }
        )
    store.write_frame("player_week", rows)

    # ---- season aggregates ---------------------------------------------------
    agg: dict[tuple, dict] = {}
    for r in rows:
        k = (r["gsis_id"], r["season"])
        a = agg.setdefault(k, defaultdict(float) | {"pos": defaultdict(int), "n_snap": 0, "n_x": 0, "n_ts": 0})
        a["games"] += 1
        a["pts"] += r["pts"]
        a["pos"][r["position"]] += 1
        a["team"] = r["team"]
        a["player_id"] = r["player_id"]
        if r["snap_pct"] is not None:
            a["snap"] += r["snap_pct"]
            a["n_snap"] += 1
        if r["xfp"] is not None:
            a["x"] += r["xfp"]
            a["n_x"] += 1
        if r["target_share"] is not None:
            a["ts"] += r["target_share"]
            a["n_ts"] += 1
        a["targets"] += r["targets"] or 0
        a["rush_att"] += r["rush_att"] or 0
    seasons = []
    for (g, season), a in agg.items():
        seasons.append(
            {
                "gsis_id": g,
                "player_id": a["player_id"],
                "season": season,
                "position": max(a["pos"], key=a["pos"].get),
                "team": a["team"],
                "games": int(a["games"]),
                "pts": round(a["pts"], 2),
                "ppg": round(a["pts"] / a["games"], 2),
                "xppg": round(a["x"] / a["n_x"], 2) if a["n_x"] else None,
                "snap_pct": round(a["snap"] / a["n_snap"], 3) if a["n_snap"] else None,
                "target_share": round(a["ts"] / a["n_ts"], 3) if a["n_ts"] else None,
                "targets_pg": round(a["targets"] / a["games"], 2),
                "rush_att_pg": round(a["rush_att"] / a["games"], 2),
            }
        )
    store.write_frame("player_season", seasons)
    store.commit()

    out = {"player_weeks": len(rows), "player_seasons": len(seasons), "unmapped_scoring": missing}
    out.update(validate(store))
    return out


def validate(store: Store) -> dict:
    """Compare our re-scored points with Sleeper's own per-player matchup points."""
    rows = store.query(
        """SELECT pw.pts ours, mp.points theirs
           FROM matchup_player mp
           JOIN league_season ls ON ls.league_id = mp.league_id
           JOIN player_week pw ON pw.player_id = mp.player_id
                AND pw.season = CAST(ls.season AS INTEGER) AND pw.week = mp.week
           WHERE mp.points IS NOT NULL"""
    )
    if not rows:
        return {"validated_rows": 0}
    diffs = [abs((r["ours"] or 0) - (r["theirs"] or 0)) for r in rows]
    close = sum(1 for d in diffs if d <= 0.5)
    return {
        "validated_rows": len(diffs),
        "mean_abs_diff": round(sum(diffs) / len(diffs), 3),
        "pct_within_0_5": round(100 * close / len(diffs), 1),
    }

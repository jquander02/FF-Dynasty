"""Loads free NFL data from the nflverse project via nflreadpy.

Each dataset lands in its own `nfl_<name>` table. Per-season datasets are loaded one
season at a time: finished seasons are pulled once, the current season every run.
A dataset that fails (e.g. not published yet for a new season) is logged and skipped,
so one bad file never stops the refresh.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from .store import Store

log = logging.getLogger(__name__)


@dataclass
class Dataset:
    table: str
    loader: Callable  # (nfl_module, season|None) -> polars.DataFrame
    per_season: bool = True
    history: int | None = None  # seasons back; None = cfg.nfl_seasons_back
    extra: str | None = None  # name in cfg.nflverse_extras that switches it on/off


def _datasets() -> list[Dataset]:
    return [
        # Core production
        Dataset("nfl_player_stats_week", lambda nfl, s: nfl.load_player_stats([s], summary_level="week")),
        Dataset("nfl_snap_counts", lambda nfl, s: nfl.load_snap_counts([s])),
        Dataset("nfl_ff_opportunity", lambda nfl, s: nfl.load_ff_opportunity([s], stat_type="weekly")),
        Dataset("nfl_rosters", lambda nfl, s: nfl.load_rosters([s])),
        # Game times, byes and betting lines (weekly projections, lineup locks)
        Dataset("nfl_schedules", lambda nfl, s: nfl.load_schedules([s]), history=1),
        # Prospect profile (longer history, small files)
        Dataset("nfl_draft_picks", lambda nfl, s: nfl.load_draft_picks([s]), history=12),
        Dataset("nfl_combine", lambda nfl, s: nfl.load_combine([s]), history=12),
        # Extras (bigger or slower)
        Dataset("nfl_depth_charts", lambda nfl, s: nfl.load_depth_charts([s]), history=1, extra="depth_charts"),
        Dataset("nfl_injuries", lambda nfl, s: nfl.load_injuries([s]), history=1, extra="injuries"),
        Dataset("nfl_ngs_receiving", lambda nfl, s: nfl.load_nextgen_stats([s], stat_type="receiving"), history=5, extra="nextgen"),
        Dataset("nfl_ngs_rushing", lambda nfl, s: nfl.load_nextgen_stats([s], stat_type="rushing"), history=5, extra="nextgen"),
        Dataset("nfl_ngs_passing", lambda nfl, s: nfl.load_nextgen_stats([s], stat_type="passing"), history=5, extra="nextgen"),
        Dataset("nfl_pfr_rec", lambda nfl, s: nfl.load_pfr_advstats([s], stat_type="rec", summary_level="season"), history=5, extra="pfr_advstats"),
        Dataset("nfl_pfr_rush", lambda nfl, s: nfl.load_pfr_advstats([s], stat_type="rush", summary_level="season"), history=5, extra="pfr_advstats"),
        Dataset("nfl_pfr_pass", lambda nfl, s: nfl.load_pfr_advstats([s], stat_type="pass", summary_level="season"), history=5, extra="pfr_advstats"),
        # Whole-table datasets (refreshed every run)
        Dataset("nfl_ff_playerids", lambda nfl, s: nfl.load_ff_playerids(), per_season=False),
        Dataset("nfl_contracts", lambda nfl, s: nfl.load_contracts(), per_season=False),
        Dataset("nfl_ff_rankings", lambda nfl, s: nfl.load_ff_rankings(type="draft"), per_season=False),
    ]


def _rows(df) -> list[dict]:
    if df is None:
        return []
    if hasattr(df, "to_dicts"):  # polars
        return df.to_dicts()
    if hasattr(df, "to_dict"):  # pandas
        return df.to_dict(orient="records")
    return list(df)


def run(
    store: Store,
    current_season: int,
    seasons_back: int = 5,
    extras: list[str] | None = None,
    nfl=None,
) -> dict[str, int]:
    """Load every dataset. Pass `nfl` to inject a fake module in tests."""
    if nfl is None:
        import nflreadpy as nfl  # imported lazily so the rest works without it

    extras = set(extras or [])
    counts: dict[str, int] = {}
    for ds in _datasets():
        if ds.extra and ds.extra not in extras:
            # switched off: drop any old copy so the database stays small
            store.conn.execute(f"DROP TABLE IF EXISTS {ds.table}")
            store.delete("ingest_log", "key LIKE ?", (f"nfl:{ds.table}:%",))
            continue
        if not ds.per_season:
            try:
                n = store.write_frame(ds.table, _rows(ds.loader(nfl, None)))
                counts[ds.table] = n
            except Exception as exc:  # noqa: BLE001 - keep the refresh going
                log.warning("nflverse %s failed: %s", ds.table, exc)
                counts[ds.table] = -1
            store.commit()
            continue

        back = ds.history if ds.history is not None else seasons_back
        total = 0
        for season in range(current_season - back, current_season + 1):
            key = f"nfl:{ds.table}:{season}"
            if season < current_season and store.is_done(key):
                continue
            try:
                rows = _rows(ds.loader(nfl, season))
            except Exception as exc:  # noqa: BLE001
                log.warning("nflverse %s %s failed: %s", ds.table, season, exc)
                continue
            for r in rows:
                r["_season"] = season
            total += store.write_frame(ds.table, rows, replace_where=("_season=?", (season,)))
            if season < current_season and rows:
                store.mark_done(key)
            store.commit()
        counts[ds.table] = total
    return counts

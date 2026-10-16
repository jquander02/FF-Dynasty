"""SQLite store. One file holds every Sleeper record plus nflverse tables.

Sleeper tables have a fixed schema (below). nflverse tables are created from whatever
columns the loader returns (prefix `nfl_`), so new nflverse columns never break a run.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS league_season (
    league_id TEXT PRIMARY KEY,
    season TEXT NOT NULL,
    name TEXT,
    status TEXT,
    previous_league_id TEXT,
    total_rosters INTEGER,
    draft_rounds INTEGER,
    taxi_slots INTEGER,
    reserve_slots INTEGER,
    waiver_budget INTEGER,
    trade_deadline INTEGER,
    playoff_teams INTEGER,
    playoff_week_start INTEGER,
    last_scored_leg INTEGER,
    roster_positions TEXT,
    scoring_settings TEXT,
    settings TEXT,
    metadata TEXT,
    is_current INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS owner (
    user_id TEXT PRIMARY KEY,
    username TEXT,
    display_name TEXT,
    avatar TEXT,
    is_me INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS league_member (
    league_id TEXT,
    user_id TEXT,
    team_name TEXT,
    is_commissioner INTEGER DEFAULT 0,
    PRIMARY KEY (league_id, user_id)
);

CREATE TABLE IF NOT EXISTS roster (
    league_id TEXT,
    roster_id INTEGER,
    owner_id TEXT,
    co_owners TEXT,
    wins INTEGER, losses INTEGER, ties INTEGER,
    fpts REAL, fpts_against REAL, max_pf REAL,
    waiver_position INTEGER, waiver_budget_used INTEGER, total_moves INTEGER,
    streak TEXT, record TEXT,
    PRIMARY KEY (league_id, roster_id)
);

CREATE TABLE IF NOT EXISTS roster_snapshot (
    snapshot_date TEXT,
    league_id TEXT,
    roster_id INTEGER,
    player_id TEXT,
    slot TEXT,                -- starter | bench | taxi | reserve
    PRIMARY KEY (snapshot_date, league_id, roster_id, player_id)
);

CREATE TABLE IF NOT EXISTS matchup (
    league_id TEXT, week INTEGER, roster_id INTEGER,
    matchup_id INTEGER, points REAL, custom_points REAL,
    PRIMARY KEY (league_id, week, roster_id)
);

-- Future regular-season pairings for the current season (Sleeper publishes them ahead).
CREATE TABLE IF NOT EXISTS schedule (
    league_id TEXT, week INTEGER, roster_id INTEGER, matchup_id INTEGER,
    PRIMARY KEY (league_id, week, roster_id)
);

CREATE TABLE IF NOT EXISTS matchup_player (
    league_id TEXT, week INTEGER, roster_id INTEGER, player_id TEXT,
    points REAL, is_starter INTEGER,
    PRIMARY KEY (league_id, week, roster_id, player_id)
);

CREATE TABLE IF NOT EXISTS txn (
    transaction_id TEXT PRIMARY KEY,
    league_id TEXT, season TEXT, week INTEGER,
    type TEXT, status TEXT, created INTEGER, status_updated INTEGER,
    creator TEXT, roster_ids TEXT, consenter_ids TEXT,
    adds TEXT, drops TEXT, draft_picks TEXT, waiver_budget TEXT,
    settings TEXT, metadata TEXT
);

-- One row per thing that moved in a transaction (normalized for analysis).
CREATE TABLE IF NOT EXISTS txn_move (
    transaction_id TEXT,
    seq INTEGER,
    kind TEXT,                -- add | drop | pick | faab
    roster_id INTEGER,        -- receiving roster (add/pick/faab) or losing roster (drop)
    from_roster_id INTEGER,
    player_id TEXT,
    pick_season TEXT, pick_round INTEGER, pick_original_roster INTEGER,
    amount INTEGER,
    PRIMARY KEY (transaction_id, seq)
);

CREATE TABLE IF NOT EXISTS traded_pick (
    league_id TEXT, season TEXT, round INTEGER, original_roster_id INTEGER,
    owner_id INTEGER, previous_owner_id INTEGER,
    PRIMARY KEY (league_id, season, round, original_roster_id)
);

-- Who owns every future rookie pick in the current league (derived).
CREATE TABLE IF NOT EXISTS draft_pick_owned (
    season TEXT, round INTEGER, original_roster_id INTEGER,
    owner_roster_id INTEGER, traded INTEGER,
    PRIMARY KEY (season, round, original_roster_id)
);

CREATE TABLE IF NOT EXISTS bracket (
    league_id TEXT, bracket TEXT, round INTEGER, matchup INTEGER,
    t1 INTEGER, t2 INTEGER, winner INTEGER, loser INTEGER, place INTEGER,
    PRIMARY KEY (league_id, bracket, round, matchup)
);

CREATE TABLE IF NOT EXISTS draft (
    draft_id TEXT PRIMARY KEY,
    league_id TEXT, season TEXT, type TEXT, status TEXT,
    start_time INTEGER, rounds INTEGER, player_type INTEGER,
    draft_order TEXT, slot_to_roster_id TEXT, settings TEXT, metadata TEXT
);

CREATE TABLE IF NOT EXISTS draft_pick_made (
    draft_id TEXT, pick_no INTEGER, round INTEGER, draft_slot INTEGER,
    roster_id INTEGER, picked_by TEXT, player_id TEXT, is_keeper INTEGER,
    position TEXT, metadata TEXT,
    PRIMARY KEY (draft_id, pick_no)
);

CREATE TABLE IF NOT EXISTS player (
    player_id TEXT PRIMARY KEY,
    full_name TEXT, first_name TEXT, last_name TEXT,
    position TEXT, fantasy_positions TEXT, team TEXT,
    age REAL, birth_date TEXT, years_exp INTEGER, college TEXT,
    status TEXT, injury_status TEXT, injury_body_part TEXT,
    depth_chart_position TEXT, depth_chart_order INTEGER, search_rank INTEGER,
    height TEXT, weight TEXT, number INTEGER,
    gsis_id TEXT, espn_id TEXT, yahoo_id TEXT, sportradar_id TEXT, rotowire_id TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS player_xref (
    player_id TEXT PRIMARY KEY,   -- Sleeper id
    gsis_id TEXT, pfr_id TEXT, espn_id TEXT, ktc_id TEXT,
    fantasypros_id TEXT, pff_id TEXT,
    match_method TEXT             -- ff_playerids | sleeper_gsis | name_birthdate
);

CREATE TABLE IF NOT EXISTS trending (
    fetched_date TEXT, kind TEXT, player_id TEXT, count INTEGER,
    PRIMARY KEY (fetched_date, kind, player_id)
);

CREATE TABLE IF NOT EXISTS owner_league (
    user_id TEXT, league_id TEXT, season TEXT, name TEXT,
    total_rosters INTEGER, league_type INTEGER, ppr REAL, status TEXT,
    PRIMARY KEY (user_id, league_id)
);

-- Picks our league-mates made in their OTHER leagues' drafts.
CREATE TABLE IF NOT EXISTS ext_draft_pick (
    draft_id TEXT, pick_no INTEGER, league_id TEXT, season TEXT,
    draft_type TEXT, player_type INTEGER, teams INTEGER,
    user_id TEXT, round INTEGER, player_id TEXT, position TEXT,
    PRIMARY KEY (draft_id, pick_no)
);

CREATE TABLE IF NOT EXISTS raw_snapshot (
    endpoint TEXT PRIMARY KEY, fetched_at TEXT, body TEXT
);

CREATE TABLE IF NOT EXISTS ingest_log (
    key TEXT PRIMARY KEY, done_at TEXT, note TEXT
);

CREATE INDEX IF NOT EXISTS idx_txn_league ON txn(league_id, week);
CREATE INDEX IF NOT EXISTS idx_move_player ON txn_move(player_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_player ON roster_snapshot(player_id);
CREATE INDEX IF NOT EXISTS idx_mp_player ON matchup_player(player_id);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def js(value: Any) -> str | None:
    """JSON-encode nested values for TEXT columns."""
    if value is None:
        return None
    return json.dumps(value, separators=(",", ":"))


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.commit()
        self.conn.close()

    # ---- generic writes -----------------------------------------------------
    def upsert(self, table: str, rows: Iterable[dict]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        cols = list(rows[0].keys())
        sql = (
            f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) "
            f"VALUES ({','.join('?' for _ in cols)})"
        )
        self.conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])
        return len(rows)

    def delete(self, table: str, where: str, params: tuple = ()) -> None:
        self.conn.execute(f"DELETE FROM {table} WHERE {where}", params)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def commit(self):
        self.conn.commit()

    # ---- bookkeeping --------------------------------------------------------
    def is_done(self, key: str) -> bool:
        return bool(self.query("SELECT 1 FROM ingest_log WHERE key=?", (key,)))

    def mark_done(self, key: str, note: str = "") -> None:
        self.upsert("ingest_log", [{"key": key, "done_at": _now(), "note": note}])

    def snapshot(self, endpoint: str, body: Any) -> None:
        self.upsert("raw_snapshot", [{"endpoint": endpoint, "fetched_at": _now(), "body": js(body)}])

    # ---- dynamic tables (nflverse) -------------------------------------------
    def write_frame(
        self,
        table: str,
        rows: list[dict],
        replace_where: tuple[str, tuple] | None = None,
    ) -> int:
        """Write rows to a table whose columns follow the data.

        replace_where=None replaces the whole table; otherwise deletes matching rows first
        (e.g. ("season IN (?,?)", (2025, 2026))) so a season can be refreshed in place.
        """
        if not rows:
            return 0
        types = _infer_types(rows)
        exists = self.query("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        if exists and replace_where is None:
            self.conn.execute(f"DROP TABLE {table}")
            exists = []
        if not exists:
            cols_sql = ", ".join(f'"{c}" {t}' for c, t in types.items())
            self.conn.execute(f"CREATE TABLE {table} ({cols_sql})")
        else:
            have = {r["name"] for r in self.query(f"PRAGMA table_info({table})")}
            for c, t in types.items():
                if c not in have:
                    self.conn.execute(f'ALTER TABLE {table} ADD COLUMN "{c}" {t}')
            if replace_where:
                self.conn.execute(f"DELETE FROM {table} WHERE {replace_where[0]}", replace_where[1])
        cols = list(types.keys())
        sql = f"INSERT INTO {table} ({','.join(chr(34) + c + chr(34) for c in cols)}) VALUES ({','.join('?' for _ in cols)})"
        self.conn.executemany(sql, [tuple(_cell(r.get(c)) for c in cols) for r in rows])
        return len(rows)


def _cell(v: Any) -> Any:
    if isinstance(v, (list, dict)):
        return js(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _infer_types(rows: list[dict]) -> dict[str, str]:
    """SQLite column type per key: TEXT if any sampled value is non-numeric, else REAL/INTEGER."""
    kinds: dict[str, set[str]] = {}
    for r in rows[:2000]:
        for c, v in r.items():
            seen = kinds.setdefault(c, set())
            if v is None:
                continue
            if isinstance(v, (bool, int)):
                seen.add("INTEGER")
            elif isinstance(v, float):
                seen.add("REAL")
            else:
                seen.add("TEXT")
    for r in rows[2000:]:
        for c in r:
            kinds.setdefault(c, set())
    types = {}
    for c, seen in kinds.items():
        if not seen or "TEXT" in seen:
            types[c] = "TEXT"
        elif "REAL" in seen:
            types[c] = "REAL"
        else:
            types[c] = "INTEGER"
    return types

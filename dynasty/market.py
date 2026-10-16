"""Dynasty market values from public sources.

FantasyCalc: values computed from real trades; we request 12-team, 1QB, full PPR dynasty.
DynastyProcess: open-licensed player and pick values (updated less often).

Each pull is stored by date in `market_value`, so value trends are available later.
Picks are stored too (key 'PICK:<name>') for pick valuation in phase 3.
"""

from __future__ import annotations

import csv
import io
import logging
import time

import requests

from .store import Store, js

log = logging.getLogger(__name__)

FANTASYCALC_URL = "https://api.fantasycalc.com/values/current"
DYNASTYPROCESS_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/values.csv"

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_value (
    snapshot_date TEXT, source TEXT, key TEXT,
    player_id TEXT, name TEXT, position TEXT,
    value REAL, overall_rank INTEGER, position_rank INTEGER,
    extra TEXT,
    PRIMARY KEY (snapshot_date, source, key)
);
"""


def today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def parse_fantasycalc(data: list[dict]) -> list[dict]:
    rows = []
    for e in data or []:
        p = e.get("player") or {}
        pos = p.get("position")
        name = p.get("name")
        sid = p.get("sleeperId")
        key = f"PICK:{name}" if pos == "PICK" else (str(sid) if sid else f"NAME:{name}")
        rows.append(
            {
                "key": key,
                "player_id": str(sid) if sid and pos != "PICK" else None,
                "name": name,
                "position": pos,
                "value": e.get("value"),
                "overall_rank": e.get("overallRank"),
                "position_rank": e.get("positionRank"),
                "extra": js({
                    "trend30": e.get("trend30Day"),
                    "draft": p.get("maybeDraftInfo"),
                    "age": p.get("maybeAge"),
                }),
            }
        )
    return rows


def parse_dynastyprocess(text: str, fp_to_sleeper: dict[str, str]) -> list[dict]:
    rows = []
    reader = csv.DictReader(io.StringIO(text))
    for r in reader:
        pos = r.get("pos")
        name = r.get("player")
        fp = (r.get("fp_id") or "").strip()
        sid = fp_to_sleeper.get(fp) if fp and fp != "NA" else None
        key = f"PICK:{name}" if pos == "PICK" else (sid or f"NAME:{name}")
        try:
            val = float(r.get("value_1qb") or 0)
        except ValueError:
            val = None
        rows.append(
            {
                "key": key,
                "player_id": sid,
                "name": name,
                "position": pos,
                "value": val,
                "overall_rank": None,
                "position_rank": None,
                "extra": js({"ecr_1qb": r.get("ecr_1qb"), "scrape_date": r.get("scrape_date"), "fp_id": fp}),
            }
        )
    rows.sort(key=lambda x: -(x["value"] or 0))
    for i, r in enumerate(rows, 1):
        r["overall_rank"] = i
    return rows


def _store(store: Store, source: str, rows: list[dict]) -> int:
    d = today()
    store.conn.executescript(SCHEMA)
    store.delete("market_value", "snapshot_date=? AND source=?", (d, source))
    seen = set()
    out = []
    for r in rows:
        if r["key"] in seen:
            continue
        seen.add(r["key"])
        out.append({"snapshot_date": d, "source": source, **r})
    return store.upsert("market_value", out)


def run(store: Store, teams: int = 12, ppr: float = 1.0, num_qbs: int = 1,
        fetch=None) -> dict[str, int]:
    """fetch(url, params) -> response-like with .json()/.text; injectable for tests."""
    fetch = fetch or (lambda url, params=None: requests.get(url, params=params, timeout=30))
    store.conn.executescript(SCHEMA)
    counts = {}
    try:
        resp = fetch(FANTASYCALC_URL, {"isDynasty": "true", "numQbs": num_qbs,
                                       "numTeams": teams, "ppr": ppr})
        counts["fantasycalc"] = _store(store, "fantasycalc", parse_fantasycalc(resp.json()))
    except Exception as exc:  # noqa: BLE001
        log.warning("FantasyCalc pull failed: %s", exc)
        counts["fantasycalc"] = -1

    fp_map = {
        str(r["fantasypros_id"]): r["player_id"]
        for r in store.query("SELECT player_id, fantasypros_id FROM player_xref WHERE fantasypros_id IS NOT NULL")
    }
    try:
        resp = fetch(DYNASTYPROCESS_URL, None)
        counts["dynastyprocess"] = _store(store, "dynastyprocess", parse_dynastyprocess(resp.text, fp_map))
    except Exception as exc:  # noqa: BLE001
        log.warning("DynastyProcess pull failed: %s", exc)
        counts["dynastyprocess"] = -1
    store.commit()
    return counts

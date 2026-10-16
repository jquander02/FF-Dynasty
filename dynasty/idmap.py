"""Maps Sleeper player ids to nflverse (gsis) and other ids.

Order of preference:
1. nflverse ff_playerids row whose sleeper_id matches
2. Sleeper's own gsis_id field
3. Same normalized name + birthdate (+ position when both have one)
"""

from __future__ import annotations

import re
import unicodedata

from .store import Store

FANTASY_POS = ("QB", "RB", "WR", "TE", "K")
SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def norm_name(name: str | None) -> str:
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", "", s)
    s = SUFFIXES.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def _table_exists(store: Store, table: str) -> bool:
    return bool(store.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)))


def _clean(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if s in ("", "nan", "None", "NA"):
        return None
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def build(store: Store) -> dict[str, int]:
    players = store.query(
        "SELECT player_id, full_name, position, birth_date, gsis_id, espn_id FROM player "
        "WHERE position IN ('QB','RB','WR','TE','K')"
    )
    ffids: dict[str, dict] = {}
    by_gsis: dict[str, dict] = {}
    by_name_bd: dict[tuple[str, str], list[dict]] = {}
    if _table_exists(store, "nfl_ff_playerids"):
        cols = {r["name"] for r in store.query("PRAGMA table_info(nfl_ff_playerids)")}
        want = [c for c in ("sleeper_id", "gsis_id", "pfr_id", "espn_id", "ktc_id",
                            "fantasypros_id", "pff_id", "name", "merge_name", "birthdate", "position")
                if c in cols]
        for r in store.query(f"SELECT {','.join(want)} FROM nfl_ff_playerids"):
            d = {k: _clean(r[k]) for k in want}
            if d.get("sleeper_id"):
                ffids[d["sleeper_id"]] = d
            if d.get("gsis_id"):
                by_gsis[d["gsis_id"]] = d
            key = (norm_name(d.get("merge_name") or d.get("name")), (d.get("birthdate") or "")[:10])
            if key[0] and key[1]:
                by_name_bd.setdefault(key, []).append(d)

    rows, counts = [], {"ff_playerids": 0, "sleeper_gsis": 0, "name_birthdate": 0, "unmatched": 0}
    for p in players:
        pid = p["player_id"]
        src, method = None, None
        if pid in ffids:
            src, method = ffids[pid], "ff_playerids"
        elif _clean(p["gsis_id"]):
            src = by_gsis.get(_clean(p["gsis_id"]), {"gsis_id": _clean(p["gsis_id"])})
            method = "sleeper_gsis"
        else:
            cands = by_name_bd.get((norm_name(p["full_name"]), (p["birth_date"] or "")[:10]), [])
            cands = [c for c in cands if not c.get("position") or c["position"] == p["position"]] or cands
            if len(cands) == 1:
                src, method = cands[0], "name_birthdate"
        if not src:
            counts["unmatched"] += 1
            continue
        counts[method] += 1
        rows.append(
            {
                "player_id": pid,
                "gsis_id": src.get("gsis_id"),
                "pfr_id": src.get("pfr_id"),
                "espn_id": src.get("espn_id") or _clean(p["espn_id"]),
                "ktc_id": src.get("ktc_id"),
                "fantasypros_id": src.get("fantasypros_id"),
                "pff_id": src.get("pff_id"),
                "match_method": method,
            }
        )
    store.delete("player_xref", "1=1")
    store.upsert("player_xref", rows)
    store.commit()
    return counts

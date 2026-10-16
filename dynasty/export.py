"""Writes small JSON files the web app reads (site/data/*.json)."""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

from .store import Store

SKILL = ("QB", "RB", "WR", "TE")


def _age(birth_date: str | None, fallback) -> float | None:
    if birth_date:
        try:
            y, m, d = (int(x) for x in birth_date[:10].split("-"))
            return round((date.today() - date(y, m, d)).days / 365.25, 1)
        except ValueError:
            pass
    return fallback


def _write(out: Path, name: str, data) -> None:
    (out / name).write_text(json.dumps(data, separators=(",", ":")))


def run(store: Store, site_dir: Path) -> dict[str, int]:
    out = Path(site_dir) / "data"
    out.mkdir(parents=True, exist_ok=True)

    lg = store.query("SELECT * FROM league_season WHERE is_current=1")
    if not lg:
        raise SystemExit("No league ingested yet; run a refresh first.")
    lg = lg[0]
    lid = lg["league_id"]
    settings = json.loads(lg["settings"] or "{}")
    state = store.query("SELECT body FROM raw_snapshot WHERE endpoint='state/nfl'")
    state = json.loads(state[0]["body"]) if state and state[0]["body"] else {}
    me = store.query("SELECT user_id FROM owner WHERE is_me=1")
    my_user = me[0]["user_id"] if me else None

    # ---- teams ------------------------------------------------------------
    teams = []
    rows = store.query(
        """SELECT r.*, o.display_name, o.avatar, m.team_name
           FROM roster r
           LEFT JOIN owner o ON o.user_id = r.owner_id
           LEFT JOIN league_member m ON m.league_id = r.league_id AND m.user_id = r.owner_id
           WHERE r.league_id=? ORDER BY r.roster_id""",
        (lid,),
    )
    name_of = {}
    my_roster = None
    for r in rows:
        label = r["team_name"] or r["display_name"] or f"Team {r['roster_id']}"
        name_of[r["roster_id"]] = label
        is_me = r["owner_id"] == my_user or (my_user and my_user in (r["co_owners"] or ""))
        if is_me:
            my_roster = r["roster_id"]
        teams.append(
            {
                "roster_id": r["roster_id"],
                "team": label,
                "owner": r["display_name"],
                "avatar": r["avatar"],
                "wins": r["wins"] or 0,
                "losses": r["losses"] or 0,
                "ties": r["ties"] or 0,
                "pf": round(r["fpts"] or 0, 2),
                "pa": round(r["fpts_against"] or 0, 2),
                "max_pf": round(r["max_pf"] or 0, 2),
                "faab_left": (settings.get("waiver_budget") or 0) - (r["waiver_budget_used"] or 0),
                "streak": r["streak"],
                "is_me": bool(is_me),
            }
        )

    # ---- values (phase 2) ---------------------------------------------------
    vals, proj, cur_ppg, prev_val = _value_maps(store, int(lg["season"]))

    # ---- rosters ----------------------------------------------------------
    snap_date = store.query(
        "SELECT MAX(snapshot_date) d FROM roster_snapshot WHERE league_id=?", (lid,)
    )[0]["d"]
    roster_players: dict[int, list] = {t["roster_id"]: [] for t in teams}
    for p in store.query(
        """SELECT s.roster_id, s.slot, s.player_id, p.full_name, p.position, p.team, p.age,
                  p.birth_date, p.years_exp, p.injury_status
           FROM roster_snapshot s LEFT JOIN player p ON p.player_id = s.player_id
           WHERE s.league_id=? AND s.snapshot_date=?""",
        (lid, snap_date),
    ):
        roster_players.setdefault(p["roster_id"], []).append(
            {
                "id": p["player_id"],
                "name": p["full_name"] or p["player_id"],
                "pos": p["position"] or ("DEF" if not (p["player_id"] or "").isdigit() else "?"),
                "team": p["team"],
                "age": _age(p["birth_date"], p["age"]),
                "exp": p["years_exp"],
                "slot": p["slot"],
                "inj": p["injury_status"],
                **_value_fields(p["player_id"], vals, proj, cur_ppg),
            }
        )
    owner_of = {}
    for t in teams:
        plist = roster_players.get(t["roster_id"], [])
        ages = [p["age"] for p in plist if p["pos"] in SKILL and p["age"]]
        t["avg_age"] = round(sum(ages) / len(ages), 1) if ages else None
        t["players"] = len(plist)
        t["value"] = round(sum(p.get("val") or 0 for p in plist))
        t["value_by_pos"] = {
            pos: round(sum(p.get("val") or 0 for p in plist if p["pos"] == pos)) for pos in SKILL
        }
        for p in plist:
            owner_of[p["id"]] = t["roster_id"]
    for i, t in enumerate(sorted(teams, key=lambda x: -x["value"]), 1):
        t["value_rank"] = i if t["value"] else None

    # ---- future picks -------------------------------------------------------
    picks = [
        {
            "season": r["season"],
            "round": r["round"],
            "from": r["original_roster_id"],
            "from_team": name_of.get(r["original_roster_id"]),
            "owner": r["owner_roster_id"],
            "owner_team": name_of.get(r["owner_roster_id"]),
            "traded": bool(r["traded"]),
        }
        for r in store.query("SELECT * FROM draft_pick_owned ORDER BY season, round, original_roster_id")
    ]

    # ---- activity -----------------------------------------------------------
    pname = {
        r["player_id"]: (r["full_name"], r["position"])
        for r in store.query(
            "SELECT DISTINCT p.player_id, p.full_name, p.position FROM txn_move m "
            "JOIN txn t ON t.transaction_id=m.transaction_id "
            "JOIN player p ON p.player_id=m.player_id WHERE t.league_id=?",
            (lid,),
        )
    }
    activity = []
    for t in store.query(
        "SELECT * FROM txn WHERE league_id=? AND status='complete' "
        "ORDER BY COALESCE(status_updated, created) DESC LIMIT 75",
        (lid,),
    ):
        moves = []
        for m in store.query("SELECT * FROM txn_move WHERE transaction_id=? ORDER BY seq", (t["transaction_id"],)):
            nm = pname.get(m["player_id"], (m["player_id"], None))
            moves.append(
                {
                    "kind": m["kind"],
                    "to": name_of.get(m["roster_id"]),
                    "from": name_of.get(m["from_roster_id"]),
                    "player": nm[0],
                    "pos": nm[1],
                    "pick": (
                        f"{m['pick_season']} R{m['pick_round']} ({name_of.get(m['pick_original_roster'], '?')})"
                        if m["kind"] == "pick" else None
                    ),
                    "amount": m["amount"],
                }
            )
        activity.append(
            {"type": t["type"], "week": t["week"], "ts": t["status_updated"] or t["created"], "moves": moves}
        )

    # ---- history -------------------------------------------------------------
    history = []
    for s in store.query("SELECT league_id, season, status FROM league_season ORDER BY season DESC"):
        champ = store.query(
            "SELECT winner FROM bracket WHERE league_id=? AND bracket='winners' AND place=1",
            (s["league_id"],),
        )
        champ_team = None
        if champ and champ[0]["winner"]:
            o = store.query(
                """SELECT COALESCE(m.team_name, o.display_name) n FROM roster r
                   LEFT JOIN owner o ON o.user_id=r.owner_id
                   LEFT JOIN league_member m ON m.league_id=r.league_id AND m.user_id=r.owner_id
                   WHERE r.league_id=? AND r.roster_id=?""",
                (s["league_id"], champ[0]["winner"]),
            )
            champ_team = o[0]["n"] if o else None
        n_txn = store.query("SELECT COUNT(*) c FROM txn WHERE league_id=?", (s["league_id"],))[0]["c"]
        n_trades = store.query(
            "SELECT COUNT(*) c FROM txn WHERE league_id=? AND type='trade' AND status='complete'",
            (s["league_id"],),
        )[0]["c"]
        history.append(
            {"season": s["season"], "status": s["status"], "champion": champ_team,
             "transactions": n_txn, "trades": n_trades}
        )

    meta = {
        "league": lg["name"],
        "league_id": lid,
        "season": lg["season"],
        "week": state.get("week"),
        "season_type": state.get("season_type"),
        "refreshed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "my_roster_id": my_roster,
        "settings": {
            "teams": lg["total_rosters"],
            "roster_positions": json.loads(lg["roster_positions"] or "[]"),
            "taxi_slots": lg["taxi_slots"],
            "faab": lg["waiver_budget"],
            "draft_rounds": lg["draft_rounds"],
            "trade_deadline": lg["trade_deadline"],
            "playoff_teams": lg["playoff_teams"],
            "playoff_week_start": lg["playoff_week_start"],
            "ppr": json.loads(lg["scoring_settings"] or "{}").get("rec"),
        },
    }

    _write(out, "meta.json", meta)
    _write(out, "teams.json", teams)
    _write(out, "rosters.json", {str(k): v for k, v in roster_players.items()})
    _write(out, "picks.json", picks)
    _write(out, "activity.json", activity)
    _write(out, "history.json", history)

    # ---- values list ----------------------------------------------------------
    info = {
        r["player_id"]: r
        for r in store.query("SELECT player_id, full_name, position, team, age, birth_date, years_exp FROM player "
                             "WHERE position IN ('QB','RB','WR','TE')")
    }
    ranked = sorted(vals.items(), key=lambda kv: kv[1]["overall_rank"])
    value_list = []
    for pid, v in ranked:
        if v["overall_rank"] > 600 and pid not in owner_of:
            continue
        p = info.get(pid)
        if not p:
            continue
        value_list.append({
            "id": pid, "name": p["full_name"], "pos": p["position"], "team": p["team"],
            "age": _age(p["birth_date"], p["age"]), "exp": p["years_exp"],
            "owner": owner_of.get(pid),
            **_value_fields(pid, vals, proj, cur_ppg),
            "trend": round(v["value"] - prev_val[pid]) if pid in prev_val else None,
        })
    _write(out, "values.json", value_list)
    return {"teams": len(teams), "picks": len(picks), "activity": len(activity), "seasons": len(history),
            "values": len(value_list)}


def _table(store: Store, name: str) -> bool:
    return bool(store.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))


def _value_maps(store: Store, season: int):
    vals, proj, cur_ppg, prev = {}, {}, {}, {}
    if _table(store, "player_value"):
        d = store.query("SELECT MAX(snapshot_date) d FROM player_value")[0]["d"]
        for r in store.query("SELECT * FROM player_value WHERE snapshot_date=?", (d,)):
            vals[r["player_id"]] = dict(r)
        old = store.query(
            "SELECT MAX(snapshot_date) d FROM player_value WHERE snapshot_date <= date(?, '-7 day')", (d,)
        )[0]["d"]
        if old:
            for r in store.query("SELECT player_id, value FROM player_value WHERE snapshot_date=?", (old,)):
                prev[r["player_id"]] = r["value"]
    if _table(store, "player_projection"):
        for r in store.query("SELECT player_id, ppg_y1, talent_ppg, model_value FROM player_projection"):
            proj[r["player_id"]] = dict(r)
    if _table(store, "player_season"):
        for r in store.query("SELECT player_id, ppg, games FROM player_season WHERE season=? AND player_id IS NOT NULL",
                             (season,)):
            cur_ppg[r["player_id"]] = (r["ppg"], r["games"])
    return vals, proj, cur_ppg, prev


def _value_fields(pid, vals, proj, cur_ppg) -> dict:
    v = vals.get(pid)
    pr = proj.get(pid)
    c = cur_ppg.get(pid)
    if not v and not pr and not c:
        return {}
    return {
        "val": round(v["value"]) if v else None,
        "rank": v["overall_rank"] if v else None,
        "prank": v["position_rank"] if v else None,
        "model": round(v["model_scaled"]) if v and v["model_scaled"] is not None else None,
        "fc": round(v["fc_value"]) if v and v["fc_value"] is not None else None,
        "dp": round(v["dp_scaled"]) if v and v["dp_scaled"] is not None else None,
        "proj": pr["ppg_y1"] if pr else None,
        "ppg": c[0] if c else None,
        "gp": c[1] if c else None,
    }

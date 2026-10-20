"""Pulls every record Sleeper exposes for the league, its full history, and its owners.

Completed seasons are fetched once and then skipped (tracked in ingest_log).
The current season is refreshed on every run.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from .sleeper import DAY, FOREVER, HOUR, SleeperClient
from .store import Store, js

log = logging.getLogger(__name__)

MAX_WEEK = 18
TAXI, RESERVE, STARTER, BENCH = "taxi", "reserve", "starter", "bench"


@dataclass
class IngestResult:
    current_league_id: str = ""
    season: str = ""
    my_user_id: str = ""
    my_roster_id: int | None = None
    seasons: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, key: str, n: int) -> None:
        self.counts[key] = self.counts.get(key, 0) + n


def today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


# ---------------------------------------------------------------------------
# League history chain
# ---------------------------------------------------------------------------
def league_chain(client: SleeperClient, league_id: str, max_seasons: int = 25) -> list[dict]:
    """Current league first, then each previous season via previous_league_id."""
    chain, seen = [], set()
    lid = league_id
    while lid and lid not in ("0", "None") and lid not in seen and len(chain) < max_seasons:
        seen.add(lid)
        # A league from an earlier link in the chain has finished, so it never changes.
        league = client.league(lid, ttl=HOUR if not chain else DAY)
        if not league:
            break
        chain.append(league)
        lid = str(league.get("previous_league_id") or "")
    return chain


def league_row(league: dict, is_current: bool) -> dict:
    s = league.get("settings") or {}
    return {
        "league_id": league["league_id"],
        "season": str(league.get("season")),
        "name": league.get("name"),
        "status": league.get("status"),
        "previous_league_id": league.get("previous_league_id"),
        "total_rosters": league.get("total_rosters"),
        "draft_rounds": s.get("draft_rounds"),
        "taxi_slots": s.get("taxi_slots"),
        "reserve_slots": s.get("reserve_slots"),
        "waiver_budget": s.get("waiver_budget"),
        "trade_deadline": s.get("trade_deadline"),
        "playoff_teams": s.get("playoff_teams"),
        "playoff_week_start": s.get("playoff_week_start"),
        "last_scored_leg": s.get("last_scored_leg"),
        "roster_positions": js(league.get("roster_positions")),
        "scoring_settings": js(league.get("scoring_settings")),
        "settings": js(s),
        "metadata": js(league.get("metadata")),
        "is_current": 1 if is_current else 0,
    }


# ---------------------------------------------------------------------------
# Per-season pieces
# ---------------------------------------------------------------------------
def ingest_users(store: Store, client: SleeperClient, league_id: str, ttl) -> int:
    users = client.league_users(league_id, ttl=ttl)
    owners, members = [], []
    for u in users:
        owners.append(
            {
                "user_id": u["user_id"],
                "username": u.get("username"),
                "display_name": u.get("display_name"),
                "avatar": u.get("avatar"),
            }
        )
        members.append(
            {
                "league_id": league_id,
                "user_id": u["user_id"],
                "team_name": (u.get("metadata") or {}).get("team_name"),
                "is_commissioner": 1 if u.get("is_owner") else 0,
            }
        )
    # Don't clobber is_me / username set elsewhere: update display fields only.
    for o in owners:
        store.conn.execute(
            "INSERT INTO owner (user_id, username, display_name, avatar) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET display_name=excluded.display_name, "
            "avatar=excluded.avatar, username=COALESCE(excluded.username, owner.username)",
            (o["user_id"], o["username"], o["display_name"], o["avatar"]),
        )
    store.upsert("league_member", members)
    return len(users)


def _decimal(settings: dict, base: str) -> float | None:
    if settings.get(base) is None:
        return None
    return settings.get(base, 0) + settings.get(f"{base}_decimal", 0) / 100


def ingest_rosters(store: Store, client: SleeperClient, league_id: str, ttl, snapshot: bool) -> int:
    rosters = client.rosters(league_id, ttl=ttl)
    rows, snap = [], []
    date = today()
    for r in rosters:
        s = r.get("settings") or {}
        md = r.get("metadata") or {}
        rows.append(
            {
                "league_id": league_id,
                "roster_id": r["roster_id"],
                "owner_id": r.get("owner_id"),
                "co_owners": js(r.get("co_owners")),
                "wins": s.get("wins"),
                "losses": s.get("losses"),
                "ties": s.get("ties"),
                "fpts": _decimal(s, "fpts"),
                "fpts_against": _decimal(s, "fpts_against"),
                "max_pf": _decimal(s, "ppts"),
                "waiver_position": s.get("waiver_position"),
                "waiver_budget_used": s.get("waiver_budget_used"),
                "total_moves": s.get("total_moves"),
                "streak": md.get("streak"),
                "record": md.get("record"),
            }
        )
        if snapshot:
            for pid, slot in roster_slots(r).items():
                snap.append(
                    {
                        "snapshot_date": date,
                        "league_id": league_id,
                        "roster_id": r["roster_id"],
                        "player_id": pid,
                        "slot": slot,
                    }
                )
    store.upsert("roster", rows)
    if snapshot:
        store.delete("roster_snapshot", "snapshot_date=? AND league_id=?", (date, league_id))
        store.upsert("roster_snapshot", snap)
        store.snapshot(f"league/{league_id}/rosters", rosters)
    return len(rosters)


def roster_slots(roster: dict) -> dict[str, str]:
    """player_id -> slot. Taxi and IR win over bench; starters win over bench."""
    slots: dict[str, str] = {}
    for pid in roster.get("players") or []:
        slots[pid] = BENCH
    for pid in roster.get("starters") or []:
        if pid and pid != "0":
            slots[pid] = STARTER
    for pid in roster.get("reserve") or []:
        slots[pid] = RESERVE
    for pid in roster.get("taxi") or []:
        slots[pid] = TAXI
    return slots


def ingest_matchups(store: Store, client: SleeperClient, league_id: str, weeks, ttl_for) -> int:
    n = 0
    for week in weeks:
        data = client.matchups(league_id, week, ttl=ttl_for(week))
        if not data:
            continue
        rows, players = [], []
        for m in data:
            rid = m["roster_id"]
            rows.append(
                {
                    "league_id": league_id,
                    "week": week,
                    "roster_id": rid,
                    "matchup_id": m.get("matchup_id"),
                    "points": m.get("points"),
                    "custom_points": m.get("custom_points"),
                }
            )
            starters = set(m.get("starters") or [])
            for pid, pts in (m.get("players_points") or {}).items():
                players.append(
                    {
                        "league_id": league_id,
                        "week": week,
                        "roster_id": rid,
                        "player_id": pid,
                        "points": pts,
                        "is_starter": 1 if pid in starters else 0,
                    }
                )
        store.delete("matchup_player", "league_id=? AND week=?", (league_id, week))
        store.upsert("matchup", rows)
        store.upsert("matchup_player", players)
        n += len(rows)
    return n


def ingest_schedule(store: Store, client: SleeperClient, league_id: str, first_week: int, last_week: int) -> int:
    """Store pairings for upcoming regular-season weeks (points not played yet)."""
    rows = []
    for week in range(max(1, first_week), last_week + 1):
        for m in client.matchups(league_id, week, ttl=DAY):
            if m.get("matchup_id") is not None:
                rows.append({"league_id": league_id, "week": week, "roster_id": m["roster_id"],
                             "matchup_id": m["matchup_id"]})
    store.delete("schedule", "league_id=?", (league_id,))
    return store.upsert("schedule", rows)


def transaction_rows(league_id: str, season: str, t: dict) -> tuple[dict, list[dict]]:
    tid = str(t["transaction_id"])
    row = {
        "transaction_id": tid,
        "league_id": league_id,
        "season": season,
        "week": t.get("leg"),
        "type": t.get("type"),
        "status": t.get("status"),
        "created": t.get("created"),
        "status_updated": t.get("status_updated"),
        "creator": t.get("creator"),
        "roster_ids": js(t.get("roster_ids")),
        "consenter_ids": js(t.get("consenter_ids")),
        "adds": js(t.get("adds")),
        "drops": js(t.get("drops")),
        "draft_picks": js(t.get("draft_picks")),
        "waiver_budget": js(t.get("waiver_budget")),
        "settings": js(t.get("settings")),
        "metadata": js(t.get("metadata")),
    }
    moves, seq = [], 0
    adds = t.get("adds") or {}
    drops = t.get("drops") or {}

    def move(**kw):
        nonlocal seq
        base = {
            "transaction_id": tid,
            "seq": seq,
            "kind": None,
            "roster_id": None,
            "from_roster_id": None,
            "player_id": None,
            "pick_season": None,
            "pick_round": None,
            "pick_original_roster": None,
            "amount": None,
        }
        base.update(kw)
        moves.append(base)
        seq += 1

    bid = (t.get("settings") or {}).get("waiver_bid")
    for pid, rid in adds.items():
        move(kind="add", roster_id=rid, from_roster_id=drops.get(pid), player_id=pid,
             amount=bid if t.get("type") == "waiver" else None)
    for pid, rid in drops.items():
        if pid not in adds:
            move(kind="drop", roster_id=rid, player_id=pid)
    for p in t.get("draft_picks") or []:
        move(kind="pick", roster_id=p.get("owner_id"), from_roster_id=p.get("previous_owner_id"),
             pick_season=str(p.get("season")), pick_round=p.get("round"),
             pick_original_roster=p.get("roster_id"))
    for w in t.get("waiver_budget") or []:
        move(kind="faab", roster_id=w.get("receiver"), from_roster_id=w.get("sender"),
             amount=w.get("amount"))
    return row, moves


def ingest_transactions(store: Store, client: SleeperClient, league_id: str, season: str, weeks, ttl_for) -> int:
    n = 0
    for week in weeks:
        data = client.transactions(league_id, week, ttl=ttl_for(week))
        for t in data:
            row, moves = transaction_rows(league_id, season, t)
            store.delete("txn_move", "transaction_id=?", (row["transaction_id"],))
            store.upsert("txn", [row])
            store.upsert("txn_move", moves)
            n += 1
    return n


def ingest_traded_picks(store: Store, client: SleeperClient, league_id: str, ttl) -> list[dict]:
    picks = client.traded_picks(league_id, ttl=ttl)
    store.delete("traded_pick", "league_id=?", (league_id,))
    store.upsert(
        "traded_pick",
        [
            {
                "league_id": league_id,
                "season": str(p["season"]),
                "round": p["round"],
                "original_roster_id": p["roster_id"],
                "owner_id": p.get("owner_id"),
                "previous_owner_id": p.get("previous_owner_id"),
            }
            for p in picks
        ],
    )
    return picks


def ingest_brackets(store: Store, client: SleeperClient, league_id: str, ttl) -> int:
    n = 0
    for name, fetch in (("winners", client.winners_bracket), ("losers", client.losers_bracket)):
        rows = [
            {
                "league_id": league_id,
                "bracket": name,
                "round": b.get("r"),
                "matchup": b.get("m"),
                "t1": b.get("t1") if isinstance(b.get("t1"), int) else None,
                "t2": b.get("t2") if isinstance(b.get("t2"), int) else None,
                "winner": b.get("w"),
                "loser": b.get("l"),
                "place": b.get("p"),
            }
            for b in fetch(league_id, ttl=ttl)
        ]
        n += store.upsert("bracket", rows)
    return n


def ingest_drafts(store: Store, client: SleeperClient, league_id: str, ttl) -> list[dict]:
    drafts = client.league_drafts(league_id, ttl=ttl)
    out = []
    for d in drafts:
        did = d["draft_id"]
        done = d.get("status") == "complete"
        full = client.draft(did, ttl=FOREVER if done else HOUR) or d
        s = full.get("settings") or {}
        store.upsert(
            "draft",
            [
                {
                    "draft_id": did,
                    "league_id": league_id,
                    "season": str(full.get("season")),
                    "type": full.get("type"),
                    "status": full.get("status"),
                    "start_time": full.get("start_time"),
                    "rounds": s.get("rounds"),
                    "player_type": s.get("player_type"),
                    "draft_order": js(full.get("draft_order")),
                    "slot_to_roster_id": js(full.get("slot_to_roster_id")),
                    "settings": js(s),
                    "metadata": js(full.get("metadata")),
                }
            ],
        )
        picks = client.draft_picks(did, ttl=FOREVER if done else HOUR)
        store.delete("draft_pick_made", "draft_id=?", (did,))
        store.upsert(
            "draft_pick_made",
            [
                {
                    "draft_id": did,
                    "pick_no": p["pick_no"],
                    "round": p.get("round"),
                    "draft_slot": p.get("draft_slot"),
                    "roster_id": p.get("roster_id"),
                    "picked_by": p.get("picked_by"),
                    "player_id": p.get("player_id"),
                    "is_keeper": 1 if p.get("is_keeper") else 0,
                    "position": (p.get("metadata") or {}).get("position"),
                    "metadata": js(p.get("metadata")),
                }
                for p in picks
            ],
        )
        out.append(full)
    return out


# ---------------------------------------------------------------------------
# Pick ownership (current league)
# ---------------------------------------------------------------------------
def build_pick_ownership(
    store: Store, league: dict, drafts: list[dict], traded: list[dict], years_ahead: int = 3
) -> int:
    season = int(league["season"])
    rounds = (league.get("settings") or {}).get("draft_rounds") or 0
    n_teams = league.get("total_rosters") or 0
    this_year_done = any(
        str(d.get("season")) == str(season) and d.get("status") == "complete" for d in drafts
    )
    first = season + 1 if this_year_done else season
    seasons = [str(y) for y in range(first, first + years_ahead)]
    owner = {(str(p["season"]), p["round"], p["roster_id"]): p.get("owner_id") for p in traded}
    rows = []
    for yr in seasons:
        for rnd in range(1, rounds + 1):
            for rid in range(1, n_teams + 1):
                own = owner.get((yr, rnd, rid), rid)
                rows.append(
                    {
                        "season": yr,
                        "round": rnd,
                        "original_roster_id": rid,
                        "owner_roster_id": own,
                        "traded": 1 if own != rid else 0,
                    }
                )
    store.delete("draft_pick_owned", "1=1")
    return store.upsert("draft_pick_owned", rows)


# ---------------------------------------------------------------------------
# Players, trending, state
# ---------------------------------------------------------------------------
PLAYER_FIELDS = [
    "full_name", "first_name", "last_name", "position", "team", "age", "birth_date",
    "years_exp", "college", "status", "injury_status", "injury_body_part",
    "depth_chart_position", "depth_chart_order", "search_rank", "height", "weight",
    "number", "gsis_id", "espn_id", "yahoo_id", "sportradar_id", "rotowire_id",
]


def player_row(pid: str, p: dict) -> dict:
    row = {"player_id": pid}
    for f in PLAYER_FIELDS:
        v = p.get(f)
        row[f] = str(v).strip() if f.endswith("_id") and v is not None else v
    if not row["full_name"]:
        # Team defenses have no full_name.
        row["full_name"] = " ".join(x for x in (p.get("first_name"), p.get("last_name")) if x) or pid
    row["fantasy_positions"] = js(p.get("fantasy_positions"))
    row["updated_at"] = today()
    return row


def ingest_players(store: Store, client: SleeperClient) -> int:
    players = client.players()
    rows = [player_row(pid, p) for pid, p in players.items()]
    return store.upsert("player", rows)


def ingest_trending(store: Store, client: SleeperClient) -> int:
    n = 0
    for kind in ("add", "drop"):
        rows = [
            {"fetched_date": today(), "kind": kind, "player_id": t["player_id"], "count": t.get("count")}
            for t in client.trending(kind)
        ]
        n += store.upsert("trending", rows)
    return n


# ---------------------------------------------------------------------------
# Sleeper's weekly projections (Week tab)
# ---------------------------------------------------------------------------
KDEF_POS = {"K", "DEF"}


def projection_points(stats: dict, scoring: dict, position: str | None) -> float | None:
    """Fantasy points from a projected stat line. Skill players are scored with this league's
    settings (Sleeper's stat keys are its scoring keys); kickers and defenses use Sleeper's own
    totals, since their bracket scoring (points allowed 0, 1-6, ...) can't be applied to an average."""
    ppr = scoring.get("rec", 0) or 0
    own = stats.get("pts_ppr" if ppr >= 1 else "pts_half_ppr" if ppr >= 0.5 else "pts_std")
    if position in KDEF_POS:
        return own
    pts = sum(float(v) * float(scoring[k]) for k, v in stats.items()
              if k in scoring and isinstance(v, (int, float)) and not k.startswith("pts_"))
    if pts == 0 and own:
        return own
    return round(pts, 2)


def ingest_projections(store: Store, client: SleeperClient, season: str, weeks, ttl_for) -> int:
    lg = store.query("SELECT scoring_settings FROM league_season WHERE is_current=1")
    scoring = json.loads(lg[0]["scoring_settings"] or "{}") if lg else {}
    pos = {r["player_id"]: r["position"] for r in store.query("SELECT player_id, position FROM player")}
    rows = []
    for week in weeks:
        try:
            got = client.projections(season, week, ttl=ttl_for(week))
        except Exception as exc:  # noqa: BLE001 - unofficial endpoint: never block the refresh
            log.warning("Sleeper projections week %s failed: %s", week, exc)
            continue
        for pid, st in got.items():
            p = projection_points(st, scoring, pos.get(pid))
            if p is not None:
                rows.append({"season": str(season), "week": week, "player_id": pid, "pts": p,
                             "pts_sleeper": st.get("pts_ppr")})
    if rows:
        store.write_frame("sleeper_projection", rows, replace_where=("season=?", (str(season),)))
    return len(rows)


# ---------------------------------------------------------------------------
# Owners' other leagues
# ---------------------------------------------------------------------------
def ingest_owner_leagues(
    store: Store,
    client: SleeperClient,
    owner_ids: list[str],
    seasons: list[str],
    our_league_ids: set[str],
    max_leagues_per_owner: int = 25,
) -> tuple[int, int]:
    n_leagues = n_picks = 0
    for uid in owner_ids:
        leagues = []
        for season in seasons:
            leagues += client.user_leagues(uid, season)
        rows = []
        for lg in leagues[: max_leagues_per_owner * len(seasons)]:
            rows.append(
                {
                    "user_id": uid,
                    "league_id": lg["league_id"],
                    "season": str(lg.get("season")),
                    "name": lg.get("name"),
                    "total_rosters": lg.get("total_rosters"),
                    "league_type": (lg.get("settings") or {}).get("type"),
                    "ppr": (lg.get("scoring_settings") or {}).get("rec"),
                    "status": lg.get("status"),
                }
            )
        store.upsert("owner_league", rows)
        n_leagues += len(rows)

        for lg in rows:
            if lg["league_id"] in our_league_ids:
                continue
            for d in client.league_drafts(lg["league_id"], ttl=DAY):
                if d.get("status") != "complete":
                    continue
                key = f"ext_draft:{d['draft_id']}:{uid}"
                if store.is_done(key):
                    continue
                s = d.get("settings") or {}
                picks = client.draft_picks(d["draft_id"], ttl=FOREVER)
                mine = [
                    {
                        "draft_id": d["draft_id"],
                        "pick_no": p["pick_no"],
                        "league_id": lg["league_id"],
                        "season": str(d.get("season")),
                        "draft_type": d.get("type"),
                        "player_type": s.get("player_type"),
                        "teams": s.get("teams"),
                        "user_id": uid,
                        "round": p.get("round"),
                        "player_id": p.get("player_id"),
                        "position": (p.get("metadata") or {}).get("position"),
                    }
                    for p in picks
                    if p.get("picked_by") == uid
                ]
                n_picks += store.upsert("ext_draft_pick", mine)
                store.mark_done(key)
    return n_leagues, n_picks


def ext_trade_rows(t: dict, league: dict, owners: dict, mates: set) -> list[dict]:
    """One row per league-mate side of a completed trade in another league."""
    if t.get("type") != "trade" or t.get("status") != "complete":
        return []
    adds, drops = t.get("adds") or {}, t.get("drops") or {}
    picks = t.get("draft_picks") or []
    rids = t.get("roster_ids") or []
    out = []
    for rid in rids:
        uid = next((u for u in owners.get(rid, []) if u in mates), None)
        if not uid:
            continue
        got = [{"k": "player", "id": pid} for pid, r in adds.items() if r == rid]
        got += [{"k": "pick", "season": str(p.get("season")), "round": p.get("round")} for p in picks
                if p.get("owner_id") == rid]
        gave = [{"k": "player", "id": pid} for pid, r in drops.items() if r == rid and adds.get(pid) not in (None, rid)]
        gave += [{"k": "pick", "season": str(p.get("season")), "round": p.get("round")} for p in picks
                 if p.get("previous_owner_id") == rid]
        out.append({"transaction_id": str(t["transaction_id"]), "user_id": uid, "league_id": league["league_id"],
                    "season": league["season"], "week": t.get("leg"),
                    "ts": t.get("status_updated") or t.get("created"), "league_type": league.get("league_type"),
                    "teams": league.get("total_rosters"), "partners": max(0, len(rids) - 1),
                    "got": js(got), "gave": js(gave)})
    return out


def ingest_owner_trades(store: Store, client: SleeperClient, our_league_ids: set[str], state: dict,
                        max_week: int = MAX_WEEK, call_budget: int = 2500) -> int:
    """Trades our league-mates made in their other leagues (dynasty, keeper and redraft): every week of
    each league they're in, finished weeks fetched once. The first backfill can be thousands of calls,
    so each run stops after call_budget API calls and the next run carries on where it left off."""
    start_calls = client.calls
    mates = {r["owner_id"] for r in store.query("SELECT DISTINCT owner_id FROM roster WHERE owner_id IS NOT NULL")}
    leagues = {}
    for r in store.query("SELECT league_id, season, status, league_type, total_rosters FROM owner_league"):
        if r["league_id"] not in our_league_ids:
            leagues[r["league_id"]] = dict(r)
    cur_season, cur_week = str(state.get("season")), int(state.get("week") or 0)
    n = 0
    for lid, lg in sorted(leagues.items(), key=lambda kv: kv[1]["season"], reverse=True):
        if client.calls - start_calls >= call_budget:
            log.info("other-league trades: call budget reached, continuing next run")
            break
        finished_league = lg["status"] == "complete" or lg["season"] < cur_season
        weeks = [w for w in range(0, max_week + 1) if not store.is_done(f"ext_txn:{lid}:{w}")]
        if not weeks:
            continue
        try:
            rosters = client.rosters(lid, ttl=FOREVER if finished_league else DAY) or []
        except Exception as exc:  # noqa: BLE001 - another league's data must never block the refresh
            log.warning("other league %s rosters failed: %s", lid, exc)
            continue
        owners = {r["roster_id"]: [r.get("owner_id")] + list(r.get("co_owners") or []) for r in rosters}
        if not any(u in mates for us in owners.values() for u in us):
            continue
        for w in weeks:
            done = finished_league or (lg["season"] == cur_season and w < cur_week - 1)
            try:
                txns = client.transactions(lid, w, ttl=FOREVER if done else DAY)
            except Exception as exc:  # noqa: BLE001
                log.warning("other league %s week %s transactions failed: %s", lid, w, exc)
                continue
            rows = [row for t in txns for row in ext_trade_rows(t, lg, owners, mates)]
            n += store.upsert("ext_trade", rows)
            if done:
                store.mark_done(f"ext_txn:{lid}:{w}")
        store.commit()
    return n


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run(
    store: Store,
    client: SleeperClient,
    league_id: str,
    username: str,
    owner_leagues: bool = True,
    players: bool = True,
) -> IngestResult:
    res = IngestResult()
    state = client.state() or {}
    store.snapshot("state/nfl", state)
    chain = league_chain(client, league_id)
    if not chain:
        raise SystemExit(f"League {league_id} not found on Sleeper.")
    current = chain[0]
    res.current_league_id = current["league_id"]
    res.season = str(current["season"])

    me = client.user(username)
    if not me:
        raise SystemExit(f"Sleeper user '{username}' not found.")
    res.my_user_id = me["user_id"]

    for idx, league in enumerate(chain):
        lid = league["league_id"]
        season = str(league["season"])
        res.seasons.append(season)
        is_current = idx == 0
        store.upsert("league_season", [league_row(league, is_current)])

        done_key = f"season:{lid}"
        if not is_current and store.is_done(done_key):
            continue
        finished = league.get("status") == "complete"
        ttl = FOREVER if finished else HOUR
        live_week = int(state.get("leg") or state.get("week") or 0) if season == str(state.get("season")) else MAX_WEEK

        def ttl_for(week: int, live_week=live_week, finished=finished):
            if finished or week < live_week - 1:
                return DAY if not finished else FOREVER
            return HOUR

        res.add("users", ingest_users(store, client, lid, ttl if not is_current else DAY))
        res.add("rosters", ingest_rosters(store, client, lid, ttl, snapshot=is_current))
        last_week = min(MAX_WEEK, max(live_week, 1)) if is_current else MAX_WEEK
        res.add("matchups", ingest_matchups(store, client, lid, range(1, last_week + 1), ttl_for))
        res.add(
            "transactions",
            ingest_transactions(store, client, lid, season, range(0, min(MAX_WEEK, last_week + 1) + 1), ttl_for),
        )
        traded = ingest_traded_picks(store, client, lid, ttl)
        res.add("brackets", ingest_brackets(store, client, lid, ttl))
        drafts = ingest_drafts(store, client, lid, ttl)
        res.add("drafts", len(drafts))
        if is_current:
            res.add("future_picks", build_pick_ownership(store, league, drafts, traded))
            st = league.get("settings") or {}
            scored = int(st.get("last_scored_leg") or 0)
            playoffs = int(st.get("playoff_week_start") or 15)
            res.add("schedule", ingest_schedule(store, client, lid, scored + 1, playoffs - 1))
        if finished:
            store.mark_done(done_key)
        store.commit()
        log.info("Season %s (%s) ingested", season, lid)

    store.conn.execute("UPDATE owner SET is_me = (user_id = ?)", (res.my_user_id,))
    store.conn.execute(
        "UPDATE owner SET username=? WHERE user_id=?", (me.get("username"), res.my_user_id)
    )
    row = store.query(
        "SELECT roster_id FROM roster WHERE league_id=? AND (owner_id=? OR co_owners LIKE ?)",
        (res.current_league_id, res.my_user_id, f'%"{res.my_user_id}"%'),
    )
    res.my_roster_id = row[0]["roster_id"] if row else None

    if players:
        res.add("players", ingest_players(store, client))
        res.add("trending", ingest_trending(store, client))

    # Sleeper's projections for this week through the end of the playoffs (every run: news moves them)
    if str(state.get("season")) == res.season and state.get("season_type") in ("regular", "post"):
        wk = int(state.get("week") or 0)
        cur = store.query("SELECT playoff_week_start FROM league_season WHERE is_current=1")
        end = int((cur[0]["playoff_week_start"] if cur else None) or 15) + 2
        if wk >= 1:
            res.add("projections", ingest_projections(
                store, client, res.season, range(wk, end + 1), lambda w, wk=wk: HOUR if w == wk else DAY))

    if owner_leagues:
        owners = [
            r["owner_id"]
            for r in store.query(
                "SELECT owner_id FROM roster WHERE league_id=? AND owner_id IS NOT NULL",
                (res.current_league_id,),
            )
        ]
        seasons = sorted({res.season, str(int(res.season) - 1)})
        nl, np_ = ingest_owner_leagues(
            store, client, owners, seasons, {lg["league_id"] for lg in chain}
        )
        res.add("owner_leagues", nl)
        res.add("owner_ext_picks", np_)
        res.add("owner_ext_trades", ingest_owner_trades(store, client, {lg["league_id"] for lg in chain}, state))

    store.commit()
    return res

"""Offline stand-in for the Sleeper API, shaped like real responses.

League settings and the 2026 draft are real responses from this league; the rest is
synthetic but follows the documented Sleeper shapes.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from dynasty.sleeper import SleeperClient

FIX = Path(__file__).parent / "fixtures"
LEAGUE = "1312163152787443712"
PREV = "1227040029461729281"
ME = "1227040852413526016"  # jquander
MY_ROSTER = 4

OWNER_IDS = [
    "1227806458830143488", "1005656010675511296", "1227351907924721664", ME,
    "1227202533936611328", "1227365115972558849", "1227789945452634112",
    "1227487852850319360", "468157346221125632", "1227039826046365696",
    "1130409372720508928", "1227234035021926400",
]
TEAM_NAMES = [
    "Hail Murray", "2 Many Jalens in the NFL", "Price Krispies Squares", "WR's 4 Sale",
    "Serving Lamb with Rice", "Ladd & the Lads", "Jaxson Spit-Njigba", "GetMeSpiderman! -JJJ",
    "Dobbins the house elf", "Dynasty League", "Cyprus Allen", "A\"Maye\"zing Grace",
]


def _load(name):
    return json.loads((FIX / name).read_text())


def build_routes() -> dict:
    league = _load(f"league_{LEAGUE}.json")
    prev = copy.deepcopy(league)
    prev.update(league_id=PREV, season="2025", status="complete", previous_league_id=None,
                draft_id="prevdraft")

    users = [
        {"user_id": uid, "display_name": f"user{i + 1}" if uid != ME else "jquander",
         "avatar": None, "metadata": {"team_name": TEAM_NAMES[i]}, "is_owner": i == 9}
        for i, uid in enumerate(OWNER_IDS)
    ]

    def roster(rid, uid):
        players = [f"{rid}00{k}" for k in range(1, 6)]
        r = {
            "roster_id": rid, "owner_id": uid, "co_owners": None,
            "players": players, "starters": players[:3] + ["0"], "reserve": [], "taxi": [],
            "settings": {"wins": rid % 5, "losses": 5 - rid % 5, "ties": 0, "fpts": 500 + rid,
                         "fpts_decimal": 42, "fpts_against": 480, "fpts_against_decimal": 10,
                         "ppts": 600, "ppts_decimal": 5, "waiver_budget_used": 25 * rid,
                         "total_moves": rid},
            "metadata": {"streak": "1W", "record": "W"},
        }
        if rid == MY_ROSTER:
            r["players"] = ["4034", "6794", "9999", "NYJ", "1111"]
            r["starters"] = ["4034", "6794", "NYJ"]
            r["taxi"] = ["9999"]
            r["reserve"] = ["1111"]
        return r

    rosters = [roster(i + 1, uid) for i, uid in enumerate(OWNER_IDS)]

    def matchups(week):
        return [
            {"roster_id": r["roster_id"], "matchup_id": (r["roster_id"] + 1) // 2,
             "points": 100.5 + week, "custom_points": None,
             "starters": r["starters"], "players": r["players"],
             "players_points": {p: 10.0 for p in r["players"]}}
            for r in rosters
        ]

    tx_week3 = [
        {"transaction_id": "t-trade-1", "type": "trade", "status": "complete", "leg": 3,
         "created": 1790000000000, "status_updated": 1790000100000, "creator": ME,
         "roster_ids": [4, 7], "consenter_ids": [4, 7],
         "adds": {"6794": 4, "7001": 7}, "drops": {"6794": 7, "7001": 4},
         "draft_picks": [{"season": "2027", "round": 1, "roster_id": 4,
                          "previous_owner_id": 4, "owner_id": 7}],
         "waiver_budget": [{"sender": 7, "receiver": 4, "amount": 50}],
         "settings": None, "metadata": None},
        {"transaction_id": "t-waiver-1", "type": "waiver", "status": "complete", "leg": 3,
         "created": 1790000200000, "status_updated": 1790000300000, "creator": ME,
         "roster_ids": [4], "adds": {"9999": 4}, "drops": {"4005": 4},
         "draft_picks": [], "waiver_budget": [], "settings": {"waiver_bid": 37}},
        {"transaction_id": "t-failed", "type": "waiver", "status": "failed", "leg": 3,
         "created": 1790000200000, "roster_ids": [2], "adds": {"9999": 2}, "drops": None,
         "draft_picks": [], "waiver_budget": [], "settings": {"waiver_bid": 20}},
    ]
    tx_week2 = [
        {"transaction_id": "t-fa-1", "type": "free_agent", "status": "complete", "leg": 2,
         "created": 1789000000000, "roster_ids": [1], "adds": None, "drops": {"1005": 1},
         "draft_picks": [], "waiver_budget": []},
    ]

    traded = [
        {"season": "2027", "round": 1, "roster_id": 4, "previous_owner_id": 4, "owner_id": 7},
        {"season": "2028", "round": 2, "roster_id": 9, "previous_owner_id": 9, "owner_id": 4},
        {"season": "2026", "round": 2, "roster_id": 1, "previous_owner_id": 1, "owner_id": 3},
    ]
    drafts = _load(f"drafts_{LEAGUE}.json")
    draft_picks_2026 = [
        {"draft_id": "1312163152795832320", "pick_no": 1, "round": 1, "draft_slot": 1,
         "roster_id": 1, "picked_by": OWNER_IDS[0], "player_id": "9998",
         "is_keeper": None, "metadata": {"position": "RB", "first_name": "A", "last_name": "B"}},
        {"draft_id": "1312163152795832320", "pick_no": 6, "round": 1, "draft_slot": 6,
         "roster_id": 4, "picked_by": ME, "player_id": "9999",
         "is_keeper": None, "metadata": {"position": "WR"}},
    ]
    prev_drafts = [{"draft_id": "prevdraft", "season": "2025", "status": "complete",
                    "type": "snake", "settings": {"rounds": 25, "player_type": 0, "teams": 12}}]
    prev_picks = [{"draft_id": "prevdraft", "pick_no": 1, "round": 1, "draft_slot": 1,
                   "roster_id": 6, "picked_by": OWNER_IDS[5], "player_id": "4034",
                   "metadata": {"position": "RB"}}]

    players = {
        "4034": {"full_name": "Christian McCaffrey", "first_name": "Christian",
                 "last_name": "McCaffrey", "position": "RB", "team": "SF", "age": 30,
                 "birth_date": "1996-06-07", "years_exp": 9, "gsis_id": " 00-0033280",
                 "fantasy_positions": ["RB"], "injury_status": None},
        "6794": {"full_name": "Justin Jefferson", "position": "WR", "team": "MIN",
                 "birth_date": "1999-06-16", "years_exp": 6, "gsis_id": None,
                 "fantasy_positions": ["WR"]},
        "9999": {"full_name": "Rookie Unknown", "position": "WR", "team": "NYG",
                 "birth_date": "2004-01-01", "years_exp": 0},
        "1111": {"full_name": "Hurt Guy", "position": "TE", "team": "DAL",
                 "birth_date": "1998-01-01", "years_exp": 5, "injury_status": "IR"},
        "NYJ": {"first_name": "New York", "last_name": "Jets", "position": "DEF", "team": "NYJ",
                "fantasy_positions": ["DEF"]},
        "7001": {"full_name": "Traded Away", "position": "WR", "team": "KC",
                 "birth_date": "2000-02-02", "years_exp": 3},
    }

    other_league = {"league_id": "OTHER-1", "season": "2026", "name": "Side league",
                    "total_rosters": 10, "settings": {"type": 0}, "scoring_settings": {"rec": 0.5},
                    "status": "complete"}
    other_drafts = [{"draft_id": "OTHER-D", "season": "2026", "status": "complete", "type": "snake",
                     "settings": {"player_type": 0, "teams": 10}}]
    other_picks = [
        {"pick_no": 1, "round": 1, "picked_by": ME, "player_id": "4034", "metadata": {"position": "RB"}},
        {"pick_no": 2, "round": 1, "picked_by": "someone-else", "player_id": "6794", "metadata": {"position": "WR"}},
        {"pick_no": 20, "round": 2, "picked_by": ME, "player_id": "1111", "metadata": {"position": "TE"}},
    ]

    routes = {
        "state/nfl": {"season": "2026", "week": 6, "leg": 6, "season_type": "regular"},
        "user/jquander": {"user_id": ME, "username": "jquander", "display_name": "jquander"},
        f"league/{LEAGUE}": league,
        f"league/{PREV}": prev,
        f"league/{LEAGUE}/users": users,
        f"league/{PREV}/users": users,
        f"league/{LEAGUE}/rosters": rosters,
        f"league/{PREV}/rosters": rosters,
        f"league/{LEAGUE}/traded_picks": traded,
        f"league/{PREV}/traded_picks": [],
        f"league/{LEAGUE}/winners_bracket": [],
        f"league/{PREV}/winners_bracket": [
            {"r": 3, "m": 7, "t1": 6, "t2": 2, "w": 6, "l": 2, "p": 1},
            {"r": 1, "m": 1, "t1": 3, "t2": {"w": 9}, "w": 3, "l": 6},
        ],
        f"league/{LEAGUE}/losers_bracket": [],
        f"league/{PREV}/losers_bracket": [],
        f"league/{LEAGUE}/drafts": drafts,
        f"league/{PREV}/drafts": prev_drafts,
        "draft/1312163152795832320": drafts[0],
        "draft/1312163152795832320/picks": draft_picks_2026,
        "draft/prevdraft": prev_drafts[0],
        "draft/prevdraft/picks": prev_picks,
        "players/nfl": players,
        "players/nfl/trending/add?lookback_hours=24&limit=50": [{"player_id": "9999", "count": 1200}],
        "players/nfl/trending/drop?lookback_hours=24&limit=50": [{"player_id": "1111", "count": 900}],
        f"user/{ME}/leagues/nfl/2026": [league, other_league],
        f"user/{ME}/leagues/nfl/2025": [prev],
        "league/OTHER-1/drafts": other_drafts,
        "league/OTHER-1/rosters": [{"roster_id": 1, "owner_id": ME}, {"roster_id": 2, "owner_id": "someone-else"},
                                   {"roster_id": 3, "owner_id": OWNER_IDS[6], "co_owners": None}],
        "draft/OTHER-D/picks": other_picks,
    }
    def future(week):  # Sleeper returns next weeks' pairings with 0 points
        return [{"roster_id": r["roster_id"], "matchup_id": (r["roster_id"] + week) % 6 + 1,
                 "points": 0.0, "starters": [], "players": [], "players_points": {}}
                for r in rosters]

    for w in range(1, 19):
        routes[f"league/{LEAGUE}/matchups/{w}"] = matchups(w) if w <= 5 else future(w)
        routes[f"league/{PREV}/matchups/{w}"] = matchups(w) if w <= 17 else []
    for w in range(0, 19):
        routes[f"league/{LEAGUE}/transactions/{w}"] = {2: tx_week2, 3: tx_week3}.get(w, [])
        routes[f"league/{PREV}/transactions/{w}"] = []
    # a trade in my redraft side league: I get 6794 + 1111 for 4034 (a 2-for-1 the other way)
    for w in range(0, 19):
        routes[f"league/OTHER-1/transactions/{w}"] = [
            {"transaction_id": "x-1", "type": "trade", "status": "complete", "leg": 4,
             "created": 1791000000000, "status_updated": 1791000000000, "roster_ids": [1, 2],
             "adds": {"6794": 1, "1111": 1, "4034": 2}, "drops": {"6794": 2, "1111": 2, "4034": 1},
             "draft_picks": [], "waiver_budget": []},
            {"transaction_id": "x-2", "type": "waiver", "status": "complete", "leg": 4, "roster_ids": [1],
             "adds": {"9999": 1}, "drops": None}] if w == 4 else []
    # Sleeper's weekly projections: the app's list shape for week 6, nothing published later
    pos = "&".join(f"position[]={p}" for p in ("QB", "RB", "WR", "TE", "K", "DEF"))
    for w in range(1, 19):
        routes[f"https://api.sleeper.app/projections/nfl/2026/{w}?season_type=regular&{pos}"] = (
            [{"player_id": "4034", "stats": {"rush_yd": 80, "rush_td": 0.7, "rec": 4, "rec_yd": 30, "pts_ppr": 19.2}},
             {"player_id": "NYJ", "stats": {"pts_ppr": 7.5}}] if w == 6 else [])
        routes[f"projections/nfl/regular/2026/{w}"] = {}
    return routes


class FakeSleeper(SleeperClient):
    """Serves canned routes; counts calls and records unknown paths."""

    def __init__(self, routes: dict | None = None):
        super().__init__(cache_dir=None)
        self.routes = routes or build_routes()
        self.paths: list[str] = []
        self.missing: list[str] = []

    def get(self, path, ttl=None):
        path = path.strip("/")
        self.calls += 1
        self.paths.append(path)
        if path in self.routes:
            return copy.deepcopy(self.routes[path])
        if path.startswith("user/") and "/leagues/" in path:
            return []
        self.missing.append(path)
        return None

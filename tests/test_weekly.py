import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from dynasty import picks, weekly
from dynasty.store import Store

SLOTS = ["QB", "RB", "WR", "TE", "FLEX", "K", "DEF"]
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)   # Saturday: Thursday's game is over


class LineupTest(unittest.TestCase):
    def test_flex_and_locks(self):
        pool = [("q", "QB", 20), ("r1", "RB", 15), ("r2", "RB", 12), ("w1", "WR", 14), ("w2", "WR", 9),
                ("t", "TE", 8), ("k", "K", 8), ("d", "DEF", 7)]
        best = weekly.best_lineup(SLOTS, pool)
        self.assertEqual(best, ["q", "r1", "w1", "t", "r2", "k", "d"])
        # a locked starter keeps his slot; the rest re-optimise around him
        fixed = weekly.best_lineup(SLOTS, [p for p in pool if p[0] != "w2"], fixed={4: "w2"})
        self.assertEqual(fixed[4], "w2")
        self.assertEqual(fixed[1], "r1")
        # the id-free version agrees on the total
        by_pos = {}
        for _, pos, x in pool:
            by_pos.setdefault(pos, []).append(x)
        for v in by_pos.values():
            v.sort(reverse=True)
        self.assertAlmostEqual(weekly.lineup_points(SLOTS, by_pos, weekly.slot_order(SLOTS)), 20 + 15 + 14 + 8 + 12 + 8 + 7)

    def test_win_prob_and_status(self):
        self.assertAlmostEqual(weekly.win_prob(100, 400, 100, 400), 0.5)
        self.assertGreater(weekly.win_prob(110, 400, 100, 400), 0.6)
        self.assertEqual(weekly.win_prob(90, 0, 80, 0), 1.0)
        kick = datetime(2026, 10, 18, 17, 0, tzinfo=timezone.utc)
        g = {"kick": kick}
        self.assertEqual(weekly.game_status(g, NOW)[0], "later")
        st, left = weekly.game_status(g, datetime(2026, 10, 18, 18, 35, tzinfo=timezone.utc))
        self.assertEqual(st, "live")
        self.assertAlmostEqual(left, 0.5, places=2)
        self.assertEqual(weekly.game_status(g, datetime(2026, 10, 18, 22, 0, tzinfo=timezone.utc))[0], "final")
        self.assertEqual(weekly.game_status(None, NOW)[0], "none")

    def test_kickoff_is_eastern(self):
        k = weekly._kickoff("2026-10-18", "13:00")
        self.assertEqual(k, datetime(2026, 10, 18, 17, 0, tzinfo=timezone.utc))

    def test_sim_uses_this_weeks_projection(self):
        teams = {1: {"wins": 0, "ties": 0, "pf": 0, "mean": 50}, 2: {"wins": 0, "ties": 0, "pf": 0, "mean": 150}}
        out = picks.simulate_season(teams, {6: [(1, 2)]}, [6], 20, 0, sims=200,
                                    fixed={6: {1: (200, 0.5), 2: (10, 0.5)}})
        self.assertEqual(out[1]["wins"], 200)


def league_db(path: str) -> Store:
    s = Store(path)
    s.upsert("league_season", [{"league_id": "L", "season": "2026", "is_current": 1, "total_rosters": 2,
                                "roster_positions": json.dumps(SLOTS + ["BN", "BN", "BN"]), "waiver_budget": 100,
                                "playoff_week_start": 15, "playoff_teams": 2, "last_scored_leg": 5}])
    s.snapshot("state/nfl", {"week": 6, "season": "2026", "season_type": "regular"})
    s.upsert("roster", [{"league_id": "L", "roster_id": 1, "waiver_budget_used": 10},
                        {"league_id": "L", "roster_id": 2, "waiver_budget_used": 0}])
    P = [  # id, pos, team, injury, ppg, future value
        ("q1", "QB", "KC", None, 20, 30), ("r1", "RB", "KC", None, 15, 40), ("r2", "RB", "NYJ", None, 10, 20),
        ("r3", "RB", "DAL", None, 6, 5), ("w1", "WR", "SF", None, 13, 30), ("w2", "WR", "BUF", "Out", 3, 0),
        ("t1", "TE", "KC", None, 9, 10), ("t3", "TE", "MIA", None, 2, 1), ("k1", "K", "KC", None, None, None), ("BUF", "DEF", "BUF", None, None, None),
        ("q2", "QB", "BUF", None, 18, 30), ("r4", "RB", "BUF", None, 12, 20), ("r5", "RB", "SF", None, 9, 10),
        ("w3", "WR", "KC", None, 14, 30), ("t2", "TE", "SF", None, 7, 5), ("w4", "WR", "DAL", None, 8, 5),
        ("k2", "K", "SF", None, None, None), ("SF", "DEF", "SF", None, None, None),
        ("fa1", "RB", "KC", None, 14, 15), ("fa2", "WR", "NYJ", None, 4, 0), ("KC", "DEF", "KC", None, None, None),
    ]
    s.upsert("player", [{"player_id": p, "full_name": p.upper(), "position": pos, "team": t, "injury_status": inj,
                         "status": "Active"} for p, pos, t, inj, _, _ in P])
    s.write_frame("player_projection", [{"player_id": p, "position": pos, "ppg_ros": ppg, "ros_share": 1.0,
                                         "model_value": fv * 1.5, "value_future": fv}
                                        for p, pos, t, inj, ppg, fv in P if ppg is not None])
    lineups = {1: ["q1", "r2", "w1", "t1", "r3", "k1", "BUF"], 2: ["q2", "r4", "w3", "t2", "r5", "k2", "SF"]}
    bench = {1: ["r1", "w2", "t3"], 2: ["w4"]}   # team 1's roster is full
    snap = []
    for rid in (1, 2):
        snap += [{"snapshot_date": "2026-10-17", "league_id": "L", "roster_id": rid, "player_id": p, "slot": "starter"}
                 for p in lineups[rid]]
        snap += [{"snapshot_date": "2026-10-17", "league_id": "L", "roster_id": rid, "player_id": p, "slot": "bench"}
                 for p in bench[rid]]
    s.upsert("roster_snapshot", snap)
    s.snapshot("league/L/rosters", [{"roster_id": rid, "starters": lineups[rid]} for rid in (1, 2)])
    s.upsert("matchup", [{"league_id": "L", "week": 6, "roster_id": rid, "matchup_id": 1, "points": 0} for rid in (1, 2)])
    s.upsert("matchup_player", [
        {"league_id": "L", "week": 6, "roster_id": 1, "player_id": "w1", "points": 12.0, "is_starter": 1},
        {"league_id": "L", "week": 6, "roster_id": 1, "player_id": "r3", "points": 4.0, "is_starter": 1},
        {"league_id": "L", "week": 6, "roster_id": 2, "player_id": "r5", "points": 20.0, "is_starter": 1},
        {"league_id": "L", "week": 6, "roster_id": 2, "player_id": "t2", "points": 6.0, "is_starter": 1},
        {"league_id": "L", "week": 6, "roster_id": 2, "player_id": "SF", "points": 9.0, "is_starter": 1},
        {"league_id": "L", "week": 6, "roster_id": 2, "player_id": "k2", "points": 7.0, "is_starter": 1},
    ])
    games = []
    for w in range(1, 18):
        games.append({"season": 2026, "game_type": "REG", "week": w, "gameday": "2026-10-18", "gametime": "13:00",
                      "home_team": "KC", "away_team": "BUF", "spread_line": 3.0, "total_line": 50.0})
        games.append({"season": 2026, "game_type": "REG", "week": w, "gameday": "2026-10-15", "gametime": "20:15",
                      "home_team": "SF", "away_team": "DAL", "spread_line": -1.0, "total_line": 41.0})
        if w != 6:   # the Jets are on bye in week 6
            games.append({"season": 2026, "game_type": "REG", "week": w, "gameday": "2026-10-18", "gametime": "13:00",
                          "home_team": "NYJ", "away_team": "MIA", "spread_line": 0.0, "total_line": 40.0})
    s.write_frame("nfl_schedules", games)
    s.upsert("txn", [{"transaction_id": "t1", "league_id": "L", "season": "2026", "week": 3, "type": "waiver",
                      "status": "complete"}])
    s.upsert("txn_move", [{"transaction_id": "t1", "seq": 0, "kind": "add", "roster_id": 1, "player_id": "r3",
                           "amount": 12}])
    s.commit()
    return s


class WeeklyBuildTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.s = league_db(self.path)
        self.out = weekly.build(self.s, now=NOW)
        self.r = weekly.report(self.s)

    def tearDown(self):
        self.s.close()
        os.remove(self.path)

    def test_lineup_check(self):
        t = self.r["teams"]["1"]
        why = {i["pid"]: i["why"] for i in t["issues"]}
        self.assertEqual(why.get("r2"), "On bye")
        self.assertIn("r1", t["best"]["start"])
        self.assertIn("r2", t["best"]["sit"])
        best = {x["slot"] + str(i): x["pid"] for i, x in enumerate(t["best"]["slots"])}
        self.assertEqual(best["WR2"], "w1")          # locked (game over) starters stay put
        self.assertEqual(best["FLEX4"], "r3")
        self.assertGreater(t["best"]["gain"], 10)
        self.assertEqual(t["now"], 16.0)
        self.assertNotIn("w2", t["best"]["start"])   # ruled out

    def test_matchup_odds(self):
        a, b = self.r["teams"]["1"], self.r["teams"]["2"]
        self.assertEqual(self.r["matchups"], [[1, 2]])
        self.assertAlmostEqual(a["win"] + b["win"], 1.0, places=3)
        self.assertGreater(a["best"]["win"], a["win"])
        rows = self.s.query("SELECT * FROM week_team ORDER BY roster_id")
        self.assertEqual([r["roster_id"] for r in rows], [1, 2])

    def test_vegas_and_live(self):
        P = self.r["players"]
        self.assertGreater(P["q1"]["proj"], 20)       # KC's implied total is above its average... and the league's
        self.assertEqual(P["w1"]["st"], "final")
        self.assertEqual(P["w1"]["pts"], 12.0)
        self.assertEqual(P["r2"]["st"], "bye")
        self.assertEqual(P["w2"]["proj"], 0.0)

    def test_waivers(self):
        recs = self.r["waivers"]["1"]
        top = recs[0]
        self.assertEqual(top["pid"], "fa1")
        self.assertGreater(top["gain"], 0)
        self.assertEqual(top["drop"], "w2")
        self.assertTrue(0 < top["bid"] <= 90)
        self.assertIn("KC", self.r["available"]["DEF"])
        self.assertEqual(self.r["bids"][0]["amount"], 12)
        self.assertNotIn("fa2", [x["pid"] for x in recs])   # on bye now, barely playable later


if __name__ == "__main__":
    unittest.main()

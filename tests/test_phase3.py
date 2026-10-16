import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dynasty import export, picks, planner, teams
from dynasty.store import Store

from fake_sleeper import MY_ROSTER
from test_values import ValuePipelineTest

LINEUP = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN"]


class PiecesTest(unittest.TestCase):
    def test_best_lineup_uses_flex(self):
        players = [("q", "QB", 20), ("r1", "RB", 15), ("r2", "RB", 14), ("r3", "RB", 13),
                   ("w1", "WR", 16), ("w2", "WR", 9), ("w3", "WR", 12.5), ("t", "TE", 8), ("k", "K", 9)]
        total, used = teams.best_lineup(players, LINEUP)
        self.assertEqual(used, {"q", "r1", "r2", "w1", "w3", "t", "r3"})  # r3 (13) wins the flex over w2 (9)
        self.assertEqual(total, 20 + 15 + 14 + 16 + 12.5 + 8 + 13)

    def test_simulation_orders_teams(self):
        t = {1: {"wins": 0, "ties": 0, "pf": 0, "mean": 70},
             2: {"wins": 0, "ties": 0, "pf": 0, "mean": 110},
             3: {"wins": 0, "ties": 0, "pf": 0, "mean": 110},
             4: {"wins": 5, "ties": 0, "pf": 600, "mean": 150}}
        out = teams_out = picks.simulate_season(t, {}, list(range(6, 15)), 20.0, 2, sims=2000)
        self.assertGreater(out[1]["slots"][1] / 2000, 0.9)   # weakest picks first
        self.assertGreater(out[4]["playoffs"] / 2000, 0.99)
        self.assertEqual(sum(teams_out[2]["slots"]), 2000)

    def test_playoff_finish_rule(self):
        import random
        t = {r: {"wins": 0, "ties": 0, "pf": 0, "mean": 100 + 10 * r} for r in range(1, 13)}
        out = picks.simulate_season(t, {}, [1], 1.0, 6, sims=500, rule="playoff_finish")
        # strongest team nearly always champion -> picks last; the 12th slot is a playoff team
        self.assertGreater(out[12]["slots"][12] / 500, 0.9)
        # non-playoff teams only ever pick 1-6
        self.assertEqual(sum(out[1]["slots"][7:]), 0)
        places = picks.playoff_places([1, 2, 3, 4, 5, 6], {i: 200 - i for i in range(1, 7)}, 0.01, random.Random(1))
        self.assertEqual(places[:2], [1, 2])
        self.assertEqual(sorted(places), [1, 2, 3, 4, 5, 6])

    def test_strength_slots(self):
        out = picks.strength_slots({1: 100, 2: 120, 3: 140}, noise=5, sims=2000)
        self.assertGreater(out[1][1], out[3][1])

    def test_tier_interpolation(self):
        tbl = {("2027", 1, "Early"): 3000, ("2027", 1, "Mid"): 2000, ("2027", 1, "Late"): 1000}
        self.assertAlmostEqual(picks.tier_value(tbl, "2027", 1, 2.5, 12), 3000)
        self.assertAlmostEqual(picks.tier_value(tbl, "2027", 1, 6.5, 12), 2000)
        self.assertGreater(picks.tier_value(tbl, "2027", 1, 1, 12), 3000)
        self.assertLessEqual(picks.tier_value(tbl, "2027", 1, 1, 12), 3450)
        self.assertGreater(picks.tier_value(tbl, "2027", 1, 9, 12), 1000)
        self.assertIsNone(picks.tier_value(tbl, "2028", 1, 5, 12))
        self.assertEqual(picks.tier_value({("2028", 2, None): 900}, "2028", 2, 5, 12), 900)

    def test_name_parsing(self):
        m = picks.FC_PICK.match("2027 1st (Early)")
        self.assertEqual(m.groups(), ("2027", "1", "Early"))
        m = picks.DP_PICK.match("2027 Mid 2nd")
        self.assertEqual(m.groups(), ("2027", "Mid", "2"))
        m = picks.DP_EXACT.match("2026 Pick 1.01")
        self.assertEqual(m.groups(), ("2026", "1", "01"))

    def test_league_pick_curve(self):
        tmp = Path(tempfile.mkdtemp())
        s = Store(tmp / "c.db")
        s.upsert("draft", [{"draft_id": "d", "player_type": 1, "status": "complete", "season": "2026"}])
        s.upsert("draft_pick_made", [{"draft_id": "d", "pick_no": i, "player_id": f"p{i}"} for i in range(1, 25)])
        s.conn.executescript("CREATE TABLE player_value (snapshot_date TEXT, player_id TEXT, value REAL)")
        s.upsert("player_value", [{"snapshot_date": "2026-10-16", "player_id": f"p{i}",
                                   "value": 6000 * 0.85 ** (i - 1)} for i in range(1, 25)])
        curve = picks.league_pick_curve(s)
        self.assertIsNotNone(curve)
        v1, v12 = picks.curve_value(curve, 1), picks.curve_value(curve, 12)
        self.assertGreater(v1, 3 * v12)
        self.assertGreater(v1, 4000)
        s.close()
        shutil.rmtree(tmp)


class Phase3PipelineTest(ValuePipelineTest):
    def setUp(self):
        super().setUp()
        self.tw = teams.build(self.store)
        self.pk = picks.build(self.store)

    def test_schedule_and_sim(self):
        self.assertEqual(self.pk["simulated_weeks"], 9)      # weeks 6-14
        self.assertEqual(self.pk["schedule_weeks"], 9)
        sim = {r["roster_id"]: r for r in self.q("SELECT * FROM season_sim")}
        self.assertEqual(len(sim), 12)
        self.assertAlmostEqual(sum(r["p_playoffs"] for r in sim.values()), 6.0, places=1)
        self.assertAlmostEqual(sum(r["p_first_pick"] for r in sim.values()), 1.0, places=2)

    def test_windows(self):
        t = {r["roster_id"]: r for r in self.q("SELECT * FROM team_window")}
        self.assertEqual(len(t), 12)
        mine = t[MY_ROSTER]
        self.assertGreater(mine["strength_now"], 0)
        self.assertEqual(mine["rank_now"], 1)  # only roster with projected players in the fixture
        self.assertIn(mine["window"], ("Dynasty power", "Win now"))
        self.assertGreaterEqual(mine["total_assets"], mine["player_value"])

    def test_pick_values(self):
        rows = self.q("SELECT * FROM pick_value")
        self.assertEqual(len(rows), 108)
        p = [r for r in rows if r["season"] == "2027" and r["round"] == 1 and r["original_roster_id"] == 4][0]
        self.assertEqual(p["owner_roster_id"], 7)
        self.assertGreater(p["value"], 0)  # market values for 2027 1sts exist in the fixture
        odds = json.loads(p["slot_odds"])
        self.assertEqual(len(odds), 12)
        self.assertAlmostEqual(sum(odds), 1.0, places=2)
        # no market value for 2028 in the fixture and no league curve (only 2 rookie picks) -> 0
        later = [r for r in rows if r["season"] == "2028"]
        self.assertTrue(all(r["value"] == 0 for r in later))
        self.assertEqual(self.pk["draft_rule"]["slots"], 12)
        self.assertIn(self.pk["draft_rule"]["rule"], ("reverse_standings", "playoff_finish"))

    def test_plan_and_export(self):
        plan = planner.build(self.store)
        self.assertEqual(plan["roster_id"], MY_ROSTER)
        # the only roster with projected players has the best lineup in every season
        self.assertEqual(plan["ranks"], {"rank_now": 1, "rank_next": 1, "rank_later": 1})
        st = plan["strategy"]
        self.assertIn(st["key"], ("sustain", "climb", "next", "deadline", "reload"))
        self.assertTrue(st["title"] and st["summary"] and len(st["why"]) == 3)
        self.assertEqual([y["season"] for y in plan["years"]], [2026, 2027, 2028])
        for y in plan["years"]:
            self.assertTrue(y["goal"])
            self.assertEqual(y["rank"], 1)
            self.assertIsInstance(y["moves"], list)
        self.assertTrue(any(d["round"] == 2 for d in plan["years"][2]["draft"]))  # 2028 2nd via roster 9
        self.assertTrue(any(p["season"] == "2028" and p["round"] == 2 for p in plan["my_picks"]))
        # nothing is both kept and sold
        self.assertFalse({c["id"] for c in plan["keep"]} & {c["id"] for c in plan["sell"]})
        self.assertIn("Rookie Unknown", [c["name"] for c in plan["keep"]] + [c["name"] for c in plan["sell"]])
        site = self.tmp / "site"
        export.run(self.store, site)
        d = site / "data"
        plans = json.loads((d / "plan.json").read_text())
        self.assertEqual(len(plans), 12)   # every team gets a plan
        self.assertEqual(plans[str(MY_ROSTER)]["roster_id"], MY_ROSTER)
        teams_json = json.loads((d / "teams.json").read_text())
        self.assertTrue(all("window" in t for t in teams_json))
        pj = json.loads((d / "picks.json").read_text())
        self.assertIn("p_top3", pj[0])


class StrategyTest(unittest.TestCase):
    """Strategy rules on synthetic teams: (playoff odds, lineup ranks now / next / in two seasons)."""

    def pick(self, p, ranks):
        lg = planner.League.__new__(planner.League)
        lg.n, lg.rank, lg.sim = 12, {1: list(ranks)}, {1: {"p_playoffs": p, "p_title": 0.1}}
        return lg._strategy(1)

    def test_strategies(self):
        cases = [
            ((0.95, (1, 2, 3)), "sustain", 0),
            ((0.90, (2, 6, 10)), "allin", 0),
            ((0.85, (6, 6, 4)), "climb", 2),
            ((0.61, (7, 11, 12)), "run", 0),
            ((0.35, (6, 2, 3)), "next", 1),
            ((0.40, (5, 7, 7)), "deadline", 0),
            ((0.01, (9, 3, 2)), "reload", 1),
            ((0.01, (10, 7, 3)), "reload", 2),
            ((0.04, (10, 10, 11)), "rebuild", 2),
        ]
        for (p, ranks), key, target in cases:
            got = self.pick(p, ranks)
            self.assertEqual((got["key"], got["target"]), (key, target), (p, ranks))

    def test_lineup_slots(self):
        players = [("q", "QB", 20), ("r1", "RB", 15), ("r2", "RB", 10), ("r3", "RB", 9),
                   ("w1", "WR", 14), ("w2", "WR", 8), ("t", "TE", 7)]
        slots = planner.lineup_slots(players, ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN"])
        self.assertEqual([s["label"] for s in slots], ["QB1", "RB1", "RB2", "WR1", "WR2", "TE1", "FLEX"])
        self.assertEqual(slots[-1]["pid"], "r3")
        self.assertAlmostEqual(sum(s["ppg"] for s in slots), teams.best_lineup(players, ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX"])[0])


if __name__ == "__main__":
    unittest.main()

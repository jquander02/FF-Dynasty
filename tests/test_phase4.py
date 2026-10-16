import json
import unittest

from dynasty import export, owners, trade
from dynasty.trade import Ctx

from fake_sleeper import ME, MY_ROSTER, OWNER_IDS
from test_phase3 import Phase3PipelineTest

LINEUP = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN"]


def P(pos, val, p0, p1=None, p2=None, age=25, hurt=False, name=None):
    return {"pos": pos, "val": val, "p0": p0, "p1": p1 if p1 is not None else p0,
            "p2": p2 if p2 is not None else p0, "age": age, "hurt": hurt, "name": name or pos}


class EvaluateTest(unittest.TestCase):
    def ctx(self):
        c = Ctx(roster_positions=LINEUP)
        c.players = {
            "a_rb1": P("RB", 6000, 18), "a_rb2": P("RB", 1000, 8), "a_wr1": P("WR", 3000, 12),
            "a_wr2": P("WR", 2500, 11), "a_wr3": P("WR", 500, 9), "a_qb": P("QB", 2000, 18),
            "a_te": P("TE", 800, 7),
            "b_rb1": P("RB", 3000, 12, 15, 17, age=22), "b_rb2": P("RB", 2900, 11, 14, 16, age=22),
            "b_rb3": P("RB", 500, 5), "b_wr1": P("WR", 4000, 14), "b_wr2": P("WR", 1000, 9),
            "b_qb": P("QB", 1500, 16), "b_te": P("TE", 700, 6),
        }
        c.rosters = {1: [k for k in c.players if k.startswith("a_")], 2: [k for k in c.players if k.startswith("b_")]}
        c.picks = {"2027-1-2": {"owner": 2, "value": 2000, "season": "2027", "round": 1, "orig": 2, "exp_slot": 6}}
        return c

    def test_package_value_consolidation(self):
        self.assertEqual(trade.package_value([1000]), 1000)
        two = trade.package_value([500, 500])
        self.assertAlmostEqual(two, 500 * 2 ** (1 / 1.3), places=3)
        self.assertLess(two, 1000)
        self.assertEqual(trade.package_value([]), 0.0)

    def test_two_for_one_not_fair_at_raw_parity(self):
        c = self.ctx()
        ev = trade.evaluate(c, 1, 2, ["a_rb1"], ["b_rb1", "b_rb2"])
        self.assertEqual(ev["a_gives_value"], 6000)
        self.assertEqual(ev["b_gives_value"], 5900)
        # the 2 pieces are worth ~1.67x one 2950 piece < 6000 -> team A (giving the stud) loses
        self.assertEqual(ev["verdict"], "b_wins")

    def test_lineup_deltas(self):
        c = self.ctx()
        ev = trade.evaluate(c, 1, 2, ["a_rb1"], ["b_rb1", "b_rb2"])
        # A loses an 18-ppg RB1 and gains 12 + 11 ppg RBs: RB slots 18+8 -> 12+11, flex stays WR 9 -> net -3 now
        self.assertEqual(ev["a_delta"][0], -3.0)
        self.assertGreater(ev["a_delta"][2], 0)  # but the young backs are better in two years
        # B gets the 18-ppg back but sends two starters: RBs 12+11 + flex 5 -> 18+5, no flex left
        self.assertEqual(ev["b_delta"][0], -5.0)

    def test_picks_have_no_lineup_effect(self):
        c = self.ctx()
        ev = trade.evaluate(c, 1, 2, ["a_te"], ["2027-1-2"])
        self.assertEqual(ev["b_gives_value"], 2000)
        # B's TE goes 6 -> 7, and the old TE (6) beats RB3 (5) for the flex: +2
        self.assertEqual(ev["b_delta"][0], 2.0)
        self.assertEqual(ev["verdict"], "a_wins")


class Phase4PipelineTest(Phase3PipelineTest):
    def setUp(self):
        super().setUp()
        self.ow = owners.build(self.store)

    def test_profiles(self):
        prof = {r["user_id"]: r for r in self.q("SELECT * FROM owner_profile")}
        self.assertEqual(len(prof), 12)
        me = prof[ME]
        self.assertEqual(me["trades"], 1)
        self.assertEqual(me["picks_out"], 1)
        self.assertEqual((me["waiver_adds"], me["faab_spent"], me["max_bid"]), (1, 37, 37))
        partner = OWNER_IDS[6]  # roster 7
        self.assertEqual(prof[partner]["picks_in"], 1)
        self.assertEqual(json.loads(me["partners"])[0]["n"], 1)
        self.assertEqual(json.loads(me["acquired_positions"]), {"WR": 1})
        tags = json.loads(me["tags"])
        self.assertIn("Big FAAB spender", tags)

    def test_trade_review(self):
        rows = self.q("SELECT * FROM trade_review")
        self.assertEqual(len(rows), 1)
        sides = {s["roster_id"]: s for s in json.loads(rows[0]["sides"])}
        self.assertEqual(sides[MY_ROSTER]["net"], -sides[7]["net"])
        self.assertTrue(any("Justin Jefferson" in x for x in sides[MY_ROSTER]["received"]))
        self.assertTrue(any("$50 FAAB" in x for x in sides[MY_ROSTER]["received"]))

    def test_finder(self):
        # give roster 7 a young RB and a contending window so a deal exists
        self.store.upsert("player_projection", [{
            "player_id": "7002", "gsis_id": None, "position": "RB", "name": "Young Back", "age": 22.8,
            "nfl_year": 2, "draft_bucket": "13-32", "games_history": 20, "prior_ppg": 8, "talent_ppg": 8,
            "ppg_ros": 8.0, "ppg_y1": 11.5, "ppg_y2": 13.0, "ppg_y3": 13.0, "repl_ppg": 6.5,
            "model_value": 900, "model_value_now": 300}])
        d = self.q("SELECT MAX(snapshot_date) d FROM player_value")[0]["d"]
        self.store.upsert("player_value", [{"snapshot_date": d, "player_id": "7002", "position": "RB",
                                            "value": 4000, "overall_rank": 3, "position_rank": 2,
                                            "model_scaled": 4000, "fc_value": 4000, "dp_scaled": None,
                                            "model_raw": 900}])
        self.store.conn.execute("UPDATE team_window SET window='Win now' WHERE roster_id=7")
        self.store.conn.execute("UPDATE team_window SET window='Rebuild' WHERE roster_id=?", (MY_ROSTER,))
        self.store.commit()
        rows = trade.find_trades(self.store, MY_ROSTER)
        hit = [r for r in rows if r["partner"] == 7 and json.loads(r["get"]) == ["7002"]]
        self.assertTrue(hit, rows)
        r = hit[0]
        self.assertIn("Christian McCaffrey", r["give_label"])
        self.assertGreater(json.loads(r["their_delta"])[0], 0.3)   # helps the contender now
        self.assertGreaterEqual(r["get_value"], 0.75 * r["give_value"])
        # every team gets suggestions; ids are unique
        every = trade.find_trades_all(self.store)
        self.assertTrue(any(r["team"] == MY_ROSTER and json.loads(r["get"]) == ["7002"] for r in every))
        self.assertEqual(len({r["id"] for r in every}), len(every))
        self.assertTrue(all(r["team"] != r["partner"] for r in every))
        # export carries finder + owners + lineup projections for the trade lab
        site = self.tmp / "site"
        export.run(self.store, site)
        d = site / "data"
        fj = json.loads((d / "finder.json").read_text())
        self.assertTrue(any(x["get"] == ["7002"] for x in fj))
        oj = json.loads((d / "owners.json").read_text())
        self.assertEqual(len(oj["profiles"]), 12)
        self.assertEqual(len(oj["trades"]), 1)
        rj = json.loads((d / "rosters.json").read_text())
        cmc = [p for p in rj[str(MY_ROSTER)] if p["id"] == "4034"][0]
        self.assertIsNotNone(cmc["p0"])
        self.assertIsNotNone(cmc["p2"])


if __name__ == "__main__":
    unittest.main()

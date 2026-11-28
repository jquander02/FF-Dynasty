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
        sc = json.loads(me["scout"])
        rec = sc["trade_record"]
        self.assertEqual(rec["won"] + rec["lost"] + rec["even"], 1)
        self.assertEqual(sc["picks"]["gave"], 1)          # sent the 2027 1st
        self.assertEqual(sc["picks"]["firsts_gave"], 1)
        self.assertEqual(sc["got_pos"], {"WR": 1})
        self.assertEqual(sc["pieces"], {"got": 1.0, "gave": 2.0})   # Jefferson for a player + a pick
        self.assertTrue(sc["last_move"])
        self.assertTrue(sc["seasons"])
        self.assertIn("eff", sc["seasons"][-1])
        self.assertEqual(sc["vs"][0]["n"], 1)
        # other leagues: one redraft trade, counted for tendencies only (not the trade record)
        ew = sc["elsewhere"]
        self.assertEqual((ew["trades"], ew["redraft_trades"], ew["dynasty_trades"]), (1, 1, 0))
        self.assertEqual(ew["pieces"], {"got": 2.0, "gave": 1.0})
        self.assertIsNone(ew["got_age"])                   # redraft: ages don't count
        self.assertEqual(sc["all"]["trades"], 2)
        self.assertEqual(rec["won"] + rec["lost"] + rec["even"], 1)

    def test_trade_review(self):
        rows = self.q("SELECT * FROM trade_review")
        self.assertEqual(len(rows), 1)
        sides = {s["roster_id"]: s for s in json.loads(rows[0]["sides"])}
        self.assertEqual(sides[MY_ROSTER]["net"], -sides[7]["net"])
        mine = sides[MY_ROSTER]["got"]
        self.assertTrue(any(a["k"] == "player" and a["name"] == "Justin Jefferson" for a in mine))
        self.assertTrue(any(a["k"] == "faab" and a["amount"] == 50 for a in mine))
        pick = [a for a in sides[7]["got"] if a["k"] == "pick"][0]
        self.assertEqual((pick["season"], pick["round"], pick["status"]), ("2027", 1, "future"))
        self.assertEqual(pick["orig"], MY_ROSTER)
        self.assertGreater(pick["value"], 0)
        self.assertIsNotNone(pick["exp_slot"])
        # each asset points at the side that gave it up
        idx = [s["roster_id"] for s in json.loads(rows[0]["sides"])]
        self.assertEqual(idx[pick["from"]], MY_ROSTER)

    def test_used_pick_becomes_player(self):
        # an earlier trade sends the 2026 1st (original owner: my roster) to roster 7; the draft
        # order puts that pick at 1.06, where player 9999 was taken
        lid = self.q("SELECT league_id FROM league_season WHERE is_current=1")[0]["league_id"]
        self.store.upsert("txn", [{"transaction_id": "t-old", "league_id": lid, "season": "2026", "week": 1,
                                   "type": "trade", "status": "complete", "created": 1780000000000,
                                   "status_updated": 1780000000000, "roster_ids": json.dumps([MY_ROSTER, 7])}])
        self.store.upsert("txn_move", [
            {"transaction_id": "t-old", "seq": 0, "kind": "pick", "roster_id": 7, "from_roster_id": MY_ROSTER,
             "pick_season": "2026", "pick_round": 1, "pick_original_roster": MY_ROSTER},
            {"transaction_id": "t-old", "seq": 1, "kind": "add", "roster_id": MY_ROSTER, "from_roster_id": 7,
             "player_id": "6794"}])
        self.store.commit()
        log = owners.trade_log(self.store)
        self.assertEqual([t["id"] for t in log], ["t-trade-1", "t-old"])   # newest first
        old = log[1]
        got7 = [s for s in old["sides"] if s["roster_id"] == 7][0]["got"][0]
        # my roster made that pick (the pick came back before the draft): roster 7 flipped it, so
        # it's credited with the slot's worth, not with the player my roster chose
        self.assertEqual(got7["status"], "flipped")
        self.assertEqual(got7["pick"], "1.06")
        self.assertEqual(got7["became"]["id"], "9999")
        self.assertEqual(got7["drafted_by"], MY_ROSTER)
        self.assertGreaterEqual(got7["value"], 0)
        # when roster 7 makes the pick itself, it's credited with the player taken
        self.store.conn.execute("UPDATE draft_pick_made SET roster_id=7 WHERE player_id='9999'")
        self.store.commit()
        log = owners.trade_log(self.store)
        got7 = [s for s in log[1]["sides"] if s["roster_id"] == 7][0]["got"][0]
        self.assertEqual(got7["status"], "used")
        self.assertEqual(got7["became"]["name"], "Rookie Unknown")
        self.assertEqual(got7["value"], got7["became"]["value"])
        # verdict fields and package math
        for t in log:
            nets = [s["net"] for s in t["sides"]]
            self.assertEqual(sum(nets), 0)
            if not t["even"]:
                self.assertEqual(t["sides"][t["winner"]]["net"], max(nets))
        # profiles count both trades and use the same nets
        owners.build(self.store)
        me = {r["user_id"]: r for r in self.q("SELECT * FROM owner_profile")}[ME]
        self.assertEqual(me["trades"], 2)
        self.assertEqual(me["trade_net"], sum(s["net"] for t in log for s in t["sides"] if s["roster_id"] == MY_ROSTER))

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
        tj = json.loads((d / "trades.json").read_text())
        self.assertEqual(len(tj), 1)
        self.assertEqual({s["roster_id"] for s in tj[0]["sides"]}, {MY_ROSTER, 7})
        rj = json.loads((d / "rosters.json").read_text())
        cmc = [p for p in rj[str(MY_ROSTER)] if p["id"] == "4034"][0]
        self.assertIsNotNone(cmc["p0"])
        self.assertIsNotNone(cmc["p2"])


    def test_banked_value_and_day_of(self):
        """Jefferson goes to my roster in week 3 (t-trade-1). His starts for me after the trade bank
        value (playoff starts double); after I drop him, his value is frozen at the day he left."""
        lid = self.q("SELECT league_id FROM league_season WHERE is_current=1")[0]["league_id"]
        self.store.conn.execute("UPDATE league_season SET playoff_week_start=15 WHERE league_id=?", (lid,))
        self.store.conn.execute("DELETE FROM matchup_player WHERE player_id='6794'")
        self.store.conn.execute("DELETE FROM matchup_player WHERE is_starter=0")   # no bench: gains over replacement
        rows = [(7, 2, 40.0), (MY_ROSTER, 3, 30.0), (MY_ROSTER, 4, 20.0), (MY_ROSTER, 15, 25.0), (MY_ROSTER, 16, 50.0)]
        self.store.upsert("matchup_player", [{"league_id": lid, "week": w, "roster_id": r, "player_id": "6794",
                                              "points": p, "is_starter": 1} for r, w, p in rows])
        # bracket games in weeks 15 (round 1) and 16 (round 2)
        self.store.upsert("bracket", [{"league_id": lid, "bracket": "winners", "round": 1, "matchup": 1,
                                       "t1": MY_ROSTER, "t2": 7},
                                      {"league_id": lid, "bracket": "winners", "round": 2, "matchup": 3,
                                       "t1": MY_ROSTER, "t2": 5}])
        # dropped in week 16, after that week's games: the week 16 start still counts, nothing later
        self.store.upsert("txn", [{"transaction_id": "t-drop", "league_id": lid, "season": "2026", "week": 16,
                                   "type": "free_agent", "status": "complete", "created": 1795000000000,
                                   "status_updated": 1795000000000}])
        self.store.upsert("txn_move", [{"transaction_id": "t-drop", "seq": 0, "kind": "drop",
                                        "roster_id": MY_ROSTER, "player_id": "6794"}])
        self.store.upsert("matchup_player", [{"league_id": lid, "week": 17, "roster_id": MY_ROSTER,
                                              "player_id": "6794", "points": 99.0, "is_starter": 1}])
        # value snapshots on the trade's day (2026-09-21)
        self.store.upsert("player_value", [{"snapshot_date": "2026-09-21", "player_id": "6794", "value": 9000},
                                           {"snapshot_date": "2026-09-21", "player_id": "7001", "value": 3000}])
        self.store.write_frame("pick_value_history", [{"snapshot_date": "2026-09-21", "season": "2027", "round": 1,
                                                       "original_roster_id": MY_ROSTER, "value": 2500}],
                               replace_where=("1=1", ()))
        self.store.commit()
        tr = [t for t in owners.trade_log(self.store) if t["id"] == "t-trade-1"][0]
        mine = [s for s in tr["sides"] if s["roster_id"] == MY_ROSTER][0]
        jj = [a for a in mine["got"] if a["k"] == "player"][0]
        wr = owners.REPL_DEFAULT["WR"]
        proj = self.q("SELECT MAX(repl_ppg) r FROM player_projection WHERE position='WR'")
        if proj and proj[0]["r"]:
            wr = proj[0]["r"]
        expect = (30 - wr) + (20 - wr) + 2 * (25 - wr) + 2 * (50 - wr)
        self.assertEqual(jj["starts"], 4)                      # not roster 7's week 2, not week 17
        self.assertAlmostEqual(jj["banked_pts"], round(expect, 1), places=1)
        k = owners.value_per_point(self.store)
        self.assertEqual(jj["banked"], round(k * expect))
        self.assertEqual(jj["left"]["how"], "dropped")
        self.assertEqual(jj["remain"], 0)                          # dropped: nothing comes back
        self.assertEqual(jj["value"], jj["banked"])
        self.assertEqual(mine["banked"], jj["banked"])
        # on the day: 9000 for Jefferson vs 3000 + the 2027 1st (2500)
        self.assertEqual(jj["day"], 9000)
        self.assertEqual(tr["day"]["date"], "2026-09-21")
        self.assertEqual(mine["day_got"], 9000)
        self.assertGreater(mine["day_net"], 0)
        self.assertEqual(tr["day"]["winner"], [s["roster_id"] for s in tr["sides"]].index(MY_ROSTER))

    # ---- trade evaluation upgrades -----------------------------------------------------------
    def _jefferson(self, rows):
        lid = self.q("SELECT league_id FROM league_season WHERE is_current=1")[0]["league_id"]
        self.store.conn.execute("UPDATE league_season SET playoff_week_start=15 WHERE league_id=?", (lid,))
        self.store.conn.execute("DELETE FROM matchup_player WHERE roster_id=?", (MY_ROSTER,))
        self.store.upsert("matchup_player", [{"league_id": lid, "week": w, "roster_id": MY_ROSTER, "player_id": pid,
                                              "points": p, "is_starter": st} for pid, w, p, st in rows])
        self.store.commit()
        return lid

    def _mine(self, tid="t-trade-1"):
        tr = [t for t in owners.trade_log(self.store) if t["id"] == tid][0]
        side = [s for s in tr["sides"] if s["roster_id"] == MY_ROSTER][0]
        return tr, side

    def test_gain_over_displaced_bench_player(self):
        """A start is worth his points over the bench player who'd have started instead: the
        same-position backup with the best PPG so far (if he's at least replacement level)."""
        self.store.upsert("player", [{"player_id": "bwr", "full_name": "Bench Guy", "position": "WR"}])
        self._jefferson([("bwr", 2, 30.0, 1), ("6794", 4, 25.0, 1), ("bwr", 4, 18.0, 0)])
        _, mine = self._mine()
        jj = [a for a in mine["got"] if a["k"] == "player"][0]
        self.assertEqual(jj["starts"], 1)
        self.assertAlmostEqual(jj["banked_pts"], 25.0 - 18.0, places=1)   # not 25 - replacement

    def test_title_run_counts_triple(self):
        lid = self._jefferson([("6794", 15, 20.0, 1), ("6794", 16, 20.0, 1)])
        self.store.conn.execute("DELETE FROM matchup_player WHERE is_starter=0")
        self.store.upsert("bracket", [{"league_id": lid, "bracket": "winners", "round": 1, "matchup": 1,
                                       "t1": MY_ROSTER, "t2": 7, "winner": MY_ROSTER, "loser": 7, "place": None},
                                      {"league_id": lid, "bracket": "winners", "round": 2, "matchup": 3,
                                       "t1": MY_ROSTER, "t2": 5, "winner": MY_ROSTER, "loser": 5, "place": None},
                                      {"league_id": lid, "bracket": "winners", "round": 3, "matchup": 5,
                                       "t1": MY_ROSTER, "t2": 2, "winner": MY_ROSTER, "loser": 2, "place": 1}])
        self.store.commit()
        _, mine = self._mine()
        jj = [a for a in mine["got"] if a["k"] == "player"][0]
        wr = owners.REPL_DEFAULT["WR"]
        proj = self.q("SELECT MAX(repl_ppg) r FROM player_projection WHERE position='WR'")
        if proj and proj[0]["r"]:
            wr = proj[0]["r"]
        self.assertAlmostEqual(jj["banked_pts"], round(2 * owners.TITLE_WEIGHT * (20 - wr), 1), places=1)
        self.assertEqual(jj["titles"], [2026])

    def test_trade_tree(self):
        """When I trade Jefferson on, he's worth his share of what that later trade brought back."""
        lid = self._jefferson([])
        self.store.upsert("player", [{"player_id": "zz", "full_name": "Later Get", "position": "RB"}])
        vd = self.q("SELECT MAX(snapshot_date) d FROM player_value")[0]["d"]
        self.store.upsert("player_value", [{"snapshot_date": vd, "player_id": "zz", "value": 4321}])
        self.store.upsert("txn", [{"transaction_id": "t-later", "league_id": lid, "season": "2026", "week": 6,
                                   "type": "trade", "status": "complete", "created": 1795000000000,
                                   "status_updated": 1795000000000, "roster_ids": json.dumps([MY_ROSTER, 9])}])
        self.store.upsert("txn_move", [
            {"transaction_id": "t-later", "seq": 0, "kind": "add", "roster_id": 9, "from_roster_id": MY_ROSTER, "player_id": "6794"},
            {"transaction_id": "t-later", "seq": 1, "kind": "drop", "roster_id": MY_ROSTER, "player_id": "6794"},
            {"transaction_id": "t-later", "seq": 2, "kind": "add", "roster_id": MY_ROSTER, "from_roster_id": 9, "player_id": "zz"},
            {"transaction_id": "t-later", "seq": 3, "kind": "drop", "roster_id": 9, "player_id": "zz"}])
        self.store.commit()
        _, mine = self._mine()
        jj = [a for a in mine["got"] if a["k"] == "player"][0]
        self.assertEqual(jj["left"]["valued"], "tree")
        self.assertEqual(jj["left"]["share"], 1.0)
        self.assertEqual(jj["left"]["got"], ["Later Get"])
        self.assertEqual(jj["remain"], 4321)

    def test_day_estimate_from_model_asof(self):
        """A trade before the value history is judged at that day's model ranks, mapped onto the
        oldest snapshot's values."""
        from dynasty import dayvalues
        day = owners._day(1790000100000)
        first = "2030-01-01"
        self.store.upsert("player_value", [{"snapshot_date": first, "player_id": "6794", "value": 8000},
                                           {"snapshot_date": first, "player_id": "7001", "value": 5000}])
        self.store.conn.execute("CREATE TABLE IF NOT EXISTS model_value_asof (cut TEXT, player_id TEXT, gsis_id TEXT, "
                                "model_value REAL, PRIMARY KEY (cut, player_id))")
        key = dayvalues.cut_key(dayvalues.cut_for(day))
        # on that day the model ranked 7001 above Jefferson
        self.store.upsert("model_value_asof", [{"cut": key, "player_id": "7001", "model_value": 300},
                                               {"cut": key, "player_id": "6794", "model_value": 200}])
        self.store.conn.execute("DELETE FROM player_value WHERE snapshot_date<?", (first,))
        self.store.upsert("player_value", [{"snapshot_date": first, "player_id": "6794", "value": 8000}])
        self.store.commit()
        tr, mine = self._mine()
        jj = [a for a in mine["got"] if a["k"] == "player"][0]
        theirs = [s for s in tr["sides"] if s["roster_id"] == 7][0]
        p7001 = [a for a in theirs["got"] if a["k"] == "player"][0]
        self.assertTrue(tr["day"]["est"])
        self.assertTrue(jj["day_est"])
        self.assertEqual(p7001["day"], 8000)   # top-ranked that day: the top value in the curve
        self.assertEqual(jj["day"], 5000)
        pk = [a for a in theirs["got"] if a["k"] == "pick"][0]
        self.assertGreater(pk["day"], 0)                                     # pick values: never 0 for lack of history

    def test_cut_for(self):
        from dynasty import dayvalues
        self.assertEqual(dayvalues.cut_for("2026-03-10"), (2026, -1))   # before the NFL draft
        self.assertEqual(dayvalues.cut_for("2026-06-01"), (2026, 0))
        self.assertEqual(dayvalues.cut_for("2025-09-09"), (2025, 1))    # week 1 done (kickoff Sep 4)
        self.assertEqual(dayvalues.cut_for("2025-11-25"), (2025, 12))

    def test_fit_package(self):
        import random
        rng = random.Random(1)
        # two pieces traded for one, priced as if P were 1.5, with some noise
        samples = []
        for _ in range(60):
            x = rng.uniform(300, 3000)
            one = (2 * x ** 1.5) ** (1 / 1.5) * rng.uniform(0.9, 1.1)
            samples.append(([x, x], [one]))
        fit = owners.fit_package(samples)
        self.assertEqual(fit["trades"], 60)
        self.assertAlmostEqual(fit["fit"], 1.5, delta=0.1)
        self.assertGreater(fit["p"], owners.PKG_DEFAULT)
        self.assertLessEqual(fit["p"], fit["fit"])                          # shrunk toward 1.3
        # too few trades: stay at 1.3
        self.assertEqual(owners.fit_package(samples[:10])["p"], owners.PKG_DEFAULT)
        self.assertEqual(owners.fit_package([])["p"], owners.PKG_DEFAULT)


if __name__ == "__main__":
    unittest.main()

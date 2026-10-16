import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dynasty import export, idmap, ingest_sleeper, nflverse
from dynasty.store import Store

from fake_sleeper import LEAGUE, ME, MY_ROSTER, PREV, FakeSleeper


class FakeNfl:
    """Mimics the nflreadpy functions the loader calls; returns lists of dicts."""

    def __init__(self):
        self.calls = []

    def _log(self, name, seasons=None):
        self.calls.append((name, tuple(seasons) if seasons else None))

    def load_player_stats(self, seasons, summary_level="week"):
        self._log("player_stats", seasons)
        s = seasons[0]
        return [
            {"player_id": "00-0033280", "season": s, "week": 1, "position": "RB",
             "receptions": 5, "rushing_yards": 80.0, "fantasy_points_ppr": 20.5},
            {"player_id": "00-0036322", "season": s, "week": 1, "position": "WR",
             "receptions": 8, "rushing_yards": None, "fantasy_points_ppr": 25.0},
        ]

    def load_snap_counts(self, seasons):
        self._log("snap_counts", seasons)
        return [{"pfr_player_id": "McCaCh01", "season": seasons[0], "week": 1, "offense_pct": 0.91}]

    def load_ff_opportunity(self, seasons, stat_type="weekly"):
        self._log("ff_opportunity", seasons)
        return [{"player_id": "00-0033280", "season": str(seasons[0]), "week": 1, "total_fantasy_points_exp": 18.2}]

    def load_rosters(self, seasons):
        self._log("rosters", seasons)
        return [{"gsis_id": "00-0033280", "season": seasons[0], "team": "SF"}]

    def load_draft_picks(self, seasons):
        self._log("draft_picks", seasons)
        return [{"season": seasons[0], "round": 1, "pick": 8, "gsis_id": "00-0033280"}]

    def load_combine(self, seasons):
        self._log("combine", seasons)
        return [{"season": seasons[0], "player_name": "X", "forty": 4.48}]

    def load_depth_charts(self, seasons):
        self._log("depth_charts", seasons)
        return [{"season": seasons[0], "gsis_id": "00-0033280", "depth_team": 1}]

    def load_injuries(self, seasons):
        raise RuntimeError("404 injuries not published yet")

    def load_ff_playerids(self):
        self._log("ff_playerids")
        return [
            {"sleeper_id": 4034.0, "gsis_id": "00-0033280", "pfr_id": "McCaCh01", "ktc_id": 123,
             "name": "Christian McCaffrey", "merge_name": "christian mccaffrey",
             "birthdate": "1996-06-07", "position": "RB"},
            {"sleeper_id": None, "gsis_id": "00-0036322", "pfr_id": "JeffJu00", "ktc_id": 456,
             "name": "Justin Jefferson", "merge_name": "justin jefferson",
             "birthdate": "1999-06-16", "position": "WR"},
        ]

    def load_contracts(self):
        self._log("contracts")
        return [{"gsis_id": "00-0033280", "years": 2, "apy": 19.0}]

    def load_ff_rankings(self, type="all"):
        self._log("ff_rankings")
        return [{"id": "17258", "ecr": 12.5, "page_type": "dynasty-overall"}]


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp / "t.db")
        self.client = FakeSleeper()

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp)

    def ingest(self, **kw):
        return ingest_sleeper.run(self.store, self.client, LEAGUE, "jquander", **kw)

    def q(self, sql, params=()):
        return self.store.query(sql, params)

    # ---- Sleeper ------------------------------------------------------------
    def test_history_chain_and_identity(self):
        res = self.ingest()
        self.assertEqual(res.seasons, ["2026", "2025"])
        self.assertEqual(res.my_user_id, ME)
        self.assertEqual(res.my_roster_id, MY_ROSTER)
        self.assertEqual(self.client.missing, [])
        cur = self.q("SELECT * FROM league_season WHERE is_current=1")[0]
        self.assertEqual(cur["league_id"], LEAGUE)
        self.assertEqual((cur["taxi_slots"], cur["waiver_budget"], cur["draft_rounds"]), (3, 1000, 3))
        self.assertEqual(json.loads(cur["scoring_settings"])["rec"], 1.0)
        me = self.q("SELECT * FROM owner WHERE is_me=1")
        self.assertEqual([r["user_id"] for r in me], [ME])

    def test_rosters_slots_and_decimals(self):
        self.ingest()
        slots = {r["player_id"]: r["slot"] for r in self.q(
            "SELECT player_id, slot FROM roster_snapshot WHERE roster_id=?", (MY_ROSTER,))}
        self.assertEqual(slots, {"4034": "starter", "6794": "starter", "NYJ": "starter",
                                 "9999": "taxi", "1111": "reserve"})
        r = self.q("SELECT fpts, max_pf FROM roster WHERE league_id=? AND roster_id=1", (LEAGUE,))[0]
        self.assertAlmostEqual(r["fpts"], 501.42)
        self.assertAlmostEqual(r["max_pf"], 600.05)

    def test_transactions_normalized(self):
        self.ingest()
        moves = self.q("SELECT * FROM txn_move WHERE transaction_id='t-trade-1' ORDER BY seq")
        kinds = [(m["kind"], m["roster_id"], m["from_roster_id"]) for m in moves]
        self.assertIn(("add", 4, 7), kinds)       # Jefferson to me from roster 7
        self.assertIn(("add", 7, 4), kinds)
        self.assertIn(("pick", 7, 4), kinds)
        self.assertIn(("faab", 4, 7), kinds)
        pick = [m for m in moves if m["kind"] == "pick"][0]
        self.assertEqual((pick["pick_season"], pick["pick_round"], pick["pick_original_roster"]), ("2027", 1, 4))
        waiver = self.q("SELECT * FROM txn_move WHERE transaction_id='t-waiver-1'")
        self.assertEqual({(m["kind"], m["player_id"], m["amount"]) for m in waiver},
                         {("add", "9999", 37), ("drop", "4005", None)})
        self.assertEqual(self.q("SELECT status FROM txn WHERE transaction_id='t-failed'")[0]["status"], "failed")

    def test_future_pick_ownership(self):
        self.ingest()
        rows = self.q("SELECT * FROM draft_pick_owned")
        self.assertEqual(len(rows), 3 * 3 * 12)  # 2027-2029, 3 rounds, 12 teams
        self.assertEqual(sorted({r["season"] for r in rows}), ["2027", "2028", "2029"])
        own = {(r["season"], r["round"], r["original_roster_id"]): r["owner_roster_id"] for r in rows}
        self.assertEqual(own[("2027", 1, 4)], 7)
        self.assertEqual(own[("2028", 2, 9)], 4)
        self.assertEqual(own[("2027", 2, 4)], 4)

    def test_drafts_brackets_matchups(self):
        self.ingest()
        self.assertEqual(self.q("SELECT type FROM draft WHERE season='2026'")[0]["type"], "linear")
        self.assertEqual(self.q("SELECT player_type FROM draft WHERE season='2026'")[0]["player_type"], 1)
        champ = self.q("SELECT winner FROM bracket WHERE league_id=? AND place=1", (PREV,))
        self.assertEqual(champ[0]["winner"], 6)
        # t2 given as {"w": 9} (feeder reference) must not be stored as a roster id
        self.assertIsNone(self.q("SELECT t2 FROM bracket WHERE league_id=? AND round=1", (PREV,))[0]["t2"])
        # weeks 1-5 scored plus the in-progress week 6 (Sleeper returns its pairings with 0 points)
        self.assertEqual(self.q("SELECT COUNT(DISTINCT week) c FROM matchup WHERE league_id=?", (LEAGUE,))[0]["c"], 6)
        self.assertEqual(self.q("SELECT COUNT(DISTINCT week) c FROM matchup WHERE league_id=? AND points > 0", (LEAGUE,))[0]["c"], 5)
        self.assertEqual(self.q("SELECT COUNT(DISTINCT week) c FROM matchup WHERE league_id=?", (PREV,))[0]["c"], 17)

    def test_owner_cross_league_picks(self):
        self.ingest()
        picks = self.q("SELECT * FROM ext_draft_pick ORDER BY pick_no")
        self.assertEqual([(p["user_id"], p["position"]) for p in picks], [(ME, "RB"), (ME, "TE")])
        self.assertEqual(self.q("SELECT ppr FROM owner_league WHERE league_id='OTHER-1'")[0]["ppr"], 0.5)

    def test_second_run_skips_finished_season(self):
        self.ingest()
        first = self.client.calls
        self.client.calls = 0
        self.client.paths = []
        self.ingest()
        self.assertLess(self.client.calls, first)
        self.assertFalse(any(p.startswith(f"league/{PREV}/matchups") for p in self.client.paths))
        # no duplicate moves after re-ingesting the same transactions
        n = self.q("SELECT COUNT(*) c FROM txn_move WHERE transaction_id='t-trade-1'")[0]["c"]
        self.assertEqual(n, 4)

    def test_players_and_trending(self):
        self.ingest()
        cmc = self.q("SELECT * FROM player WHERE player_id='4034'")[0]
        self.assertEqual(cmc["gsis_id"], "00-0033280")  # whitespace stripped
        self.assertEqual(self.q("SELECT full_name FROM player WHERE player_id='NYJ'")[0]["full_name"], "New York Jets")
        self.assertEqual(len(self.q("SELECT * FROM trending")), 2)

    # ---- nflverse + ids -------------------------------------------------------
    def test_nflverse_and_idmap(self):
        self.ingest()
        nfl = FakeNfl()
        counts = nflverse.run(self.store, 2026, seasons_back=2,
                              extras=["depth_charts", "injuries"], nfl=nfl)
        self.assertEqual(counts["nfl_player_stats_week"], 6)  # 3 seasons x 2 rows
        self.assertEqual(counts["nfl_injuries"], 0)  # failure skipped, run continued
        self.assertNotIn("nfl_ngs_receiving", counts)  # extra not enabled
        self.assertEqual(counts["nfl_draft_picks"], 13)  # 12 seasons back + current

        # Re-run: finished seasons are not reloaded, current one is
        nfl2 = FakeNfl()
        nflverse.run(self.store, 2026, seasons_back=2, extras=[], nfl=nfl2)
        stats_calls = [c for c in nfl2.calls if c[0] == "player_stats"]
        self.assertEqual(stats_calls, [("player_stats", (2026,))])
        self.assertEqual(self.q("SELECT COUNT(*) c FROM nfl_player_stats_week")[0]["c"], 6)

        m = idmap.build(self.store)
        self.assertEqual(m["ff_playerids"], 1)
        self.assertEqual(m["name_birthdate"], 1)
        xref = {r["player_id"]: r for r in self.q("SELECT * FROM player_xref")}
        self.assertEqual(xref["4034"]["ktc_id"], "123")
        self.assertEqual(xref["6794"]["gsis_id"], "00-0036322")
        self.assertEqual(xref["6794"]["match_method"], "name_birthdate")
        self.assertNotIn("9999", xref)

    def test_write_frame_adds_new_columns(self):
        self.store.write_frame("nfl_x", [{"_season": 2025, "a": 1}], replace_where=("_season=?", (2025,)))
        self.store.write_frame("nfl_x", [{"_season": 2026, "a": 2.5, "b": "new"}], replace_where=("_season=?", (2026,)))
        self.store.write_frame("nfl_x", [{"_season": 2026, "a": 3, "b": "again"}], replace_where=("_season=?", (2026,)))
        rows = self.q("SELECT * FROM nfl_x ORDER BY _season")
        self.assertEqual([(r["_season"], r["a"], r["b"]) for r in rows], [(2025, 1, None), (2026, 3, "again")])

    # ---- export ----------------------------------------------------------------
    def test_export(self):
        self.ingest()
        site = self.tmp / "site"
        export.run(self.store, site)
        d = site / "data"
        meta = json.loads((d / "meta.json").read_text())
        self.assertEqual(meta["my_roster_id"], MY_ROSTER)
        self.assertEqual(meta["settings"]["faab"], 1000)
        teams = json.loads((d / "teams.json").read_text())
        mine = [t for t in teams if t["is_me"]]
        self.assertEqual(mine[0]["team"], "WR's 4 Sale")
        self.assertEqual(mine[0]["faab_left"], 1000 - 25 * MY_ROSTER)
        rosters = json.loads((d / "rosters.json").read_text())
        slots = {p["name"]: p["slot"] for p in rosters[str(MY_ROSTER)]}
        self.assertEqual(slots["Rookie Unknown"], "taxi")
        picks = json.loads((d / "picks.json").read_text())
        p = [x for x in picks if x["season"] == "2027" and x["round"] == 1 and x["from"] == 4][0]
        self.assertTrue(p["traded"])
        self.assertEqual(p["owner"], 7)
        activity = json.loads((d / "activity.json").read_text())
        self.assertEqual(activity[0]["type"], "waiver")  # newest first
        trade = [a for a in activity if a["type"] == "trade"][0]
        self.assertTrue(any(m["pick"] and m["pick"].startswith("2027 R1") for m in trade["moves"]))
        self.assertFalse(any(a for a in activity if a["type"] == "waiver" and a["moves"][0]["amount"] == 20))
        history = json.loads((d / "history.json").read_text())
        self.assertEqual([h["season"] for h in history], ["2026", "2025"])
        self.assertEqual(history[1]["champion"], "Ladd & the Lads")


class RosterSlotsTest(unittest.TestCase):
    def test_priority(self):
        r = {"players": ["a", "b", "c", "d"], "starters": ["a", "0"], "taxi": ["c"], "reserve": ["d"]}
        self.assertEqual(ingest_sleeper.roster_slots(r), {"a": "starter", "b": "bench", "c": "taxi", "d": "reserve"})


if __name__ == "__main__":
    unittest.main()

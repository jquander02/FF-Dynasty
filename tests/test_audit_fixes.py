"""Regression tests for the November 2026 audit fixes (pipeline side)."""

import json
import os
import random
import tempfile
import time
import unittest
from pathlib import Path

from dynasty import ingest_sleeper, market, model, picks, values
from dynasty.sleeper import FOREVER, HOUR, SleeperClient
from dynasty.store import Store

from fake_sleeper import LEAGUE, ME, FakeSleeper, build_routes


class RenewalTest(unittest.TestCase):
    def test_follows_renewed_league(self):
        routes = build_routes()
        new = dict(routes[f"league/{LEAGUE}"], league_id="NEWLG", season="2027", previous_league_id=LEAGUE)
        routes["league/NEWLG"] = new
        routes[f"user/{ME}/leagues/nfl/2027"] = [new]
        fs = FakeSleeper(routes)
        self.assertEqual(ingest_sleeper.follow_renewals(fs, LEAGUE, ME), "NEWLG")
        # no renewal yet: stays put
        self.assertEqual(ingest_sleeper.follow_renewals(FakeSleeper(), LEAGUE, ME), LEAGUE)


class CacheFinalTest(unittest.TestCase):
    def test_permanent_read_refetches_once(self):
        d = tempfile.mkdtemp()
        c = SleeperClient(cache_dir=Path(d))
        c._write_cache("draft/1/picks", [1, 2], HOUR)          # cached while the draft was running
        self.assertIsNotNone(c._read_cache("draft/1/picks", HOUR))
        self.assertIsNone(c._read_cache("draft/1/picks", FOREVER))   # not final: fetch again
        c._write_cache("draft/1/picks", [1, 2, 3], FOREVER)
        self.assertEqual(c._read_cache("draft/1/picks", FOREVER)["body"], [1, 2, 3])


class PlayoffTest(unittest.TestCase):
    def test_fixed_bracket_and_decided_games(self):
        seeds = [1, 2, 3, 4, 5, 6]
        mean = {i: 100.0 for i in seeds}
        # round 1 done: 6 upset 3 and 5 beat 4. Sleeper's bracket is fixed: 1 meets the 4/5 winner
        decided = {(0, frozenset((3, 6))): 6, (0, frozenset((4, 5))): 5,
                   (1, frozenset((1, 5))): 1, (1, frozenset((2, 6))): 6,
                   (2, frozenset((1, 6))): 6}
        places = picks.playoff_places(seeds, mean, 30.0, random.Random(3), decided=decided)
        self.assertEqual(places[:2], [6, 1])       # the champion is known

    def test_finished_season_champion_picks_last(self):
        t = {r: {"wins": 13 - r, "ties": 0, "pf": 0, "mean": 100.0} for r in range(1, 13)}
        decided = {(0, frozenset((3, 6))): 3, (0, frozenset((4, 5))): 4,
                   (1, frozenset((1, 4))): 4, (1, frozenset((2, 3))): 2, (2, frozenset((4, 2))): 4}
        out = picks.simulate_season(t, {}, [], 20.0, 6, sims=300, rule="playoff_finish", decided=decided)
        self.assertEqual(out[4]["titles"], 300)
        self.assertEqual(out[4]["slots"][12], 300)


class ValueWindowTest(unittest.TestCase):
    def test_no_jump_when_a_season_ends(self):
        cfg = model.ModelConfig()
        v = {"position": "WR", "nfl_year": 4, "games_history": 50, "play_share": 0.8, "draft_bucket": "R1",
             "ppg_ros": 15.0, "ppg_y1": 15.0, "ppg_y2": 14.5, "ppg_y3": 14.0}
        end = sum(model.discounted(model.value_parts(v, 9.0, {}, cfg, 0), 0, cfg))
        # the renewed season: next season is now "this season", with every game left
        v2 = dict(v, ppg_ros=15.0, ppg_y1=14.5, ppg_y2=14.0, ppg_y3=14.0 * 14.0 / 14.5)
        start = sum(model.discounted(model.value_parts(v2, 9.0, {}, cfg, cfg.season_games), cfg.season_games, cfg))
        self.assertAlmostEqual(end, start, delta=0.03 * end)

    def test_offseason_empty_season_not_counted_as_missed(self):
        cfg = model.ModelConfig()
        seasons = [{"gsis_id": "g", "season": y, "position": "WR", "games": 17, "pts": 255.0, "ppg": 15.0}
                   for y in (2023, 2024, 2025)]
        p = [{"key": "g", "gsis_id": "g", "position": "WR", "name": "x", "years_exp": 3, "birth": None,
              "draft": None}]
        proj = model.project(p, seasons, {}, {}, 2026, cfg, current_games=None)
        self.assertGreater(proj["g"]["play_share"], 0.75)


class MarketTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.s = Store(self.path)

    def tearDown(self):
        self.s.close()
        os.remove(self.path)

    def test_partial_pull_rejected(self):
        self.s.conn.executescript(market.SCHEMA)
        rows = [{"key": f"k{i}", "player_id": str(i), "name": "n", "position": "WR", "value": 100.0, "extra": "{}"}
                for i in range(400)]
        self.s.upsert("market_value", [dict(r, snapshot_date="2026-01-01", source="fantasycalc") for r in rows])
        self.assertEqual(market._store(self.s, "fantasycalc", rows[:100]), -1)
        self.assertGreater(market._store(self.s, "fantasycalc", rows[:390]), 0)

    def test_missing_from_full_market_counts_as_zero(self):
        from datetime import date
        d = date.today().isoformat()
        self.s.conn.executescript(market.SCHEMA)
        self.s.upsert("market_value", [{"snapshot_date": d, "source": "fantasycalc", "key": f"k{i}",
                                        "player_id": str(i), "name": "n", "position": "WR",
                                        "value": 5000.0 - i, "extra": "{}"} for i in range(400)])
        self.s.write_frame("player_projection", [{"player_id": "zz", "position": "QB", "model_value": 999.0},
                                                 {"player_id": "1", "position": "WR", "model_value": 5.0}])
        self.s.commit()
        values.build(self.s, {"model": 0.6, "fantasycalc": 0.3, "dynastyprocess": 0.1})
        v = {r["player_id"]: r["value"] for r in self.s.query("SELECT player_id, value FROM player_value")}
        # zz tops the model but FantasyCalc doesn't list him: 0.6 of the top value, not all of it
        self.assertLess(v["zz"], 0.7 * 5000)


class TradeMathTest(unittest.TestCase):
    def test_three_team_trade_nets_to_zero(self):
        from dynasty import owners
        sides = [{"got": [{"k": "player", "from": 1, "value": 3000}, {"k": "player", "from": 2, "value": 500}]},
                 {"got": [{"k": "player", "from": 2, "value": 2000}, {"k": "player", "from": 0, "value": 900}]},
                 {"got": [{"k": "player", "from": 0, "value": 1500}, {"k": "player", "from": 0, "value": 700}]}]
        nets = owners._sides_net(sides, lambda a: a["value"], 1.3)
        self.assertAlmostEqual(sum(g - v for g, v in nets), 0.0, places=6)

    def test_estimated_band_is_wider(self):
        from dynasty import owners
        nets = [(1000.0, 820.0), (820.0, 1000.0)]
        self.assertFalse(owners._verdict(nets, owners.FAIR)["even"])
        self.assertTrue(owners._verdict(nets, owners.FAIR_EST)["even"])


if __name__ == "__main__":
    unittest.main()

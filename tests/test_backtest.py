import random
import unittest

from dynasty import backtest
from dynasty.model import ModelConfig

from test_phase3 import Phase3PipelineTest


def synthetic_bundle(seed=3):
    """40 players per position, 2014-2024, true talent with an age peak plus weekly noise."""
    rng = random.Random(seed)
    seasons, picks, birth, names = [], [], {}, {}
    peak = {"QB": 29, "RB": 25, "WR": 26, "TE": 27}
    for pos in ("QB", "RB", "WR", "TE"):
        for i in range(40):
            g = f"{pos}{i:03d}"
            debut = rng.randint(2012, 2022)
            age0 = rng.uniform(21, 23)
            birth[g] = f"{int(debut - age0)}-03-01"
            names[g] = g
            talent = rng.uniform(6, 20)
            picks.append({"gsis_id": g, "year": debut, "pick": rng.randint(1, 200), "position": pos, "name": g})
            for yr in range(max(debut, 2014), 2025):
                age = age0 + (yr - debut)
                mult = 1 - 0.012 * (age - peak[pos]) ** 2
                games = rng.randint(4, 17)
                true = max(1.0, talent * mult)
                ppg = max(0.0, true + rng.gauss(0, 14.0 / (games ** 0.5)))
                seasons.append({"gsis_id": g, "season": yr, "position": pos, "games": games, "pts": ppg * games,
                                "ppg": round(ppg, 2), "xppg": round(true + rng.gauss(0, 1.0), 2)})
    return {"current_season": 2025, "seasons": seasons, "picks": picks, "birth": birth, "names": names}


class BacktestTest(unittest.TestCase):
    def test_runs_and_scores(self):
        rep = backtest.run(synthetic_bundle(), ModelConfig())
        self.assertGreater(rep["rows"], 200)
        h1 = rep["overall"]["h1"]
        self.assertGreater(h1["model"]["n"], 100)
        # averaging several seasons should beat last season's PPG on noisy data
        self.assertLess(h1["model"]["mae"], h1["last"]["mae"])
        self.assertTrue(all(v is None or -1 <= v <= 1 for v in rep["value_rank_3yr"].values()))
        self.assertIn("QB", rep["rank_h1"])
        self.assertEqual(len(rep["biggest_misses_h1"]), 15)

    def test_no_peeking(self):
        # projections as of 2018 must not change when later seasons are removed
        b = synthetic_bundle()
        cut = dict(b, seasons=[s for s in b["seasons"] if s["season"] <= 2018],
                   picks=[p for p in b["picks"] if p["year"] <= 2019])
        a = backtest.project_as_of(b, 2018, ModelConfig())
        c = backtest.project_as_of(cut, 2018, ModelConfig())
        self.assertEqual(set(a), set(c))
        for k in a:
            self.assertEqual(a[k]["ppg_y1"], c[k]["ppg_y1"])

    def test_draft_prior_ignores_seasons_before_the_data(self):
        from dynasty import model
        seasons = [{"gsis_id": f"r{i}", "season": 2014, "position": "RB", "games": 16, "pts": 160.0, "ppg": 10.0}
                   for i in range(5)]
        picks = [{"gsis_id": f"r{i}", "year": 2012, "pick": 5, "position": "RB"} for i in range(5)]
        share = {}
        prior = model.fit_draft_prior(picks, seasons, 2014, current_complete=True, share=share)
        # 2012 seasons aren't in the data: unknown, not zeros (the gap borrows the known year)
        self.assertEqual(prior[("RB", "1-12", 1)], 10.0)
        self.assertEqual(prior[("RB", "1-12", 3)], 10.0)  # per game played
        self.assertAlmostEqual(share[("RB", "1-12", 3)], 16 / 17, places=3)

    def test_spearman(self):
        self.assertAlmostEqual(backtest.spearman(list(range(10)), list(range(10))), 1.0)
        self.assertAlmostEqual(backtest.spearman(list(range(10)), list(range(10))[::-1]), -1.0)


class BundleTest(Phase3PipelineTest):
    def test_bundle_from_database(self):
        b = backtest.make_bundle(self.store)
        self.assertTrue(b["seasons"])
        self.assertTrue(all(s["position"] in ("QB", "RB", "WR", "TE") for s in b["seasons"]))
        self.assertEqual(b["current_season"], 2026)
        path = self.tmp / "b.json.gz"
        backtest.save_bundle(b, str(path))
        self.assertEqual(len(backtest.load_bundle(str(path))["seasons"]), len(b["seasons"]))


if __name__ == "__main__":
    unittest.main()

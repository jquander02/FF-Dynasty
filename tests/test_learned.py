import random
import unittest

from dynasty import learned
from dynasty.model import ModelConfig

from test_backtest import synthetic_bundle


def with_weeks(b, seed=5):
    """Add weekly rows (and team assignments) to the synthetic bundle."""
    rng = random.Random(seed)
    cols = ("gsis_id", "season", "week", "team", "pts", "targets", "rec", "rec_yd", "rec_td", "rush_att", "rush_yd",
            "rush_td", "pass_att", "pass_yd", "pass_td", "pass_int", "target_share", "air_yards_share", "wopr",
            "snap_pct", "xfp")
    w = {c: [] for c in cols}
    teams = [f"T{i}" for i in range(12)]
    team_of = {}
    for s in b["seasons"]:
        g = s["gsis_id"]
        team_of.setdefault(g, rng.choice(teams))
        if rng.random() < 0.1:
            team_of[g] = rng.choice(teams)
        for wk in range(1, s["games"] + 1):
            pts = max(0.0, s["ppg"] + rng.gauss(0, 4))
            row = {"gsis_id": g, "season": s["season"], "week": wk, "team": team_of[g], "pts": pts,
                   "targets": 5.0, "rec": 3.0, "rec_yd": 40.0, "rec_td": 0.3, "rush_att": 4.0, "rush_yd": 18.0,
                   "rush_td": 0.1, "pass_att": 30.0 if s["position"] == "QB" else 0.0, "pass_yd": 0.0, "pass_td": 0.0,
                   "pass_int": 0.0, "target_share": 0.15, "air_yards_share": 0.15, "wopr": 0.3, "snap_pct": 0.7,
                   "xfp": pts}
            for c in cols:
                w[c].append(row[c])
    b["weeks"] = w
    b["pick_teams"] = {"gsis_id": [p["gsis_id"] for p in b["picks"]], "season": [p["year"] for p in b["picks"]],
                       "pick": [p["pick"] for p in b["picks"]], "round": [1] * len(b["picks"]),
                       "team": [rng.choice(teams) for _ in b["picks"]], "position": [p["position"] for p in b["picks"]]}
    b["contracts"] = {"gsis_id": [], "year_signed": [], "years": [], "value": [], "guaranteed": [], "apy_cap_pct": [], "apy": []}
    return b


class LearnedTest(unittest.TestCase):
    def test_rows_targets_and_no_peeking(self):
        b = with_weeks(synthetic_bundle())
        d = learned.Data(b)
        rows = learned.build_rows(d, 2020, 0)
        self.assertTrue(rows)
        f = next(r["f"] for r in rows if r["f"].get("l1_games"))   # a player with a season behind him
        for key in ("age", "log_pick", "l1_ppg", "new_team", "rookie_comp"):
            self.assertIn(key, f)
        learned.attach_targets(d, rows)
        self.assertTrue(any("ros" in r["t"] for r in rows))
        # in-season rows only see weeks <= k
        r8 = learned.build_rows(d, 2020, 8)
        self.assertTrue(all(r["f"].get("c_games", 0) <= 8 for r in r8))
        # the current season's rest-of-season outcome is never a training target
        cur = learned.build_rows(d, 2025, 4)
        learned.attach_targets(d, cur)
        self.assertFalse(any("ros" in r["t"] for r in cur))

    def test_fit_and_live_corrections(self):
        b = with_weeks(synthetic_bundle())
        out = learned.current_season_from_bundle(b, ModelConfig(), 2025, 6)
        self.assertTrue(out)
        ppg, share = next(iter(out.values()))
        self.assertGreaterEqual(ppg, 0.0)
        self.assertTrue(0.0 <= share <= 1.0)

    def test_not_enough_history(self):
        b = with_weeks(synthetic_bundle())
        b["weeks"] = {k: [] for k in b["weeks"]}
        self.assertEqual(learned.current_season_from_bundle(b, ModelConfig(), 2025, 6), {})


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import unittest

from dynasty import schedule
from dynasty.store import Store


def league(path):
    """4 teams, weeks 1-2 played, week 3 in progress, weeks 4-5 left, playoffs from week 6.
    Team 1 scores 100 a week, team 2 90, team 3 80, team 4 120."""
    s = Store(path)
    s.upsert("league_season", [{"league_id": "L", "season": "2026", "is_current": 1, "total_rosters": 4,
                                "playoff_week_start": 6, "playoff_teams": 2, "last_scored_leg": 2}])
    s.snapshot("state/nfl", {"season": "2026", "week": 3, "season_type": "regular"})
    s.upsert("roster", [{"league_id": "L", "roster_id": r} for r in (1, 2, 3, 4)])
    score = {1: 100.0, 2: 90.0, 3: 80.0, 4: 120.0}
    pairs = {1: [(1, 2), (3, 4)], 2: [(1, 3), (2, 4)], 3: [(1, 4), (2, 3)], 4: [(1, 2), (3, 4)], 5: [(1, 3), (2, 4)]}
    rows = []
    for w in (1, 2, 3):
        for m, (a, b) in enumerate(pairs[w], 1):
            for r in (a, b):
                rows.append({"league_id": "L", "week": w, "roster_id": r, "matchup_id": m,
                             "points": score[r] if w < 3 else 10.0})
    s.upsert("matchup", rows)
    s.upsert("schedule", [{"league_id": "L", "week": w, "roster_id": r, "matchup_id": m}
                          for w in (4, 5) for m, (a, b) in enumerate(pairs[w], 1) for r in (a, b)])
    s.write_frame("season_sim", [{"roster_id": r, "mean_weekly": v} for r, v in score.items()])
    s.commit()
    return s


class ScheduleTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.s = league(self.path)
        self.out = schedule.build(self.s)
        self.r = schedule.report(self.s)

    def tearDown(self):
        self.s.close()
        os.remove(self.path)

    def test_played(self):
        t1 = self.r["teams"]["1"]
        self.assertEqual(t1["record"], [2, 0, 0])               # beat 2 (90) and 3 (80)
        self.assertEqual(t1["allplay"], [4.0, 2.0])             # beats 2 and 3 each week, loses to 4
        self.assertAlmostEqual(t1["luck"], 2 - 2 * 4 / 6, places=2)
        self.assertEqual(t1["opp_ppg"], 85.0)
        self.assertEqual(t1["sos_played"], 85.0 - 97.5)
        t3 = self.r["teams"]["3"]                                # faced 4 (120) and 1 (100): toughest
        self.assertEqual(t3["sos_played_rank"], 1)
        g = self.r["games"]["1"][0]
        self.assertEqual((g["week"], g["opp"], g["res"], g["st"]), (1, 2, "W", "final"))

    def test_left(self):
        t1 = self.r["teams"]["1"]
        self.assertEqual(t1["games_left"], 3)                    # weeks 3 (in progress), 4, 5
        self.assertEqual(t1["opp_left"], round((120 + 90 + 80) / 3, 1))
        g3 = self.r["games"]["1"][2]
        self.assertEqual((g3["opp"], g3["st"]), (4, "live"))
        self.assertLess(g3["win"], 0.5)
        t4 = self.r["teams"]["4"]                                 # strongest team: faces the rest
        self.assertGreater(t4["exp_wins_left"], t1["exp_wins_left"])
        ranks = sorted(t["sos_left_rank"] for t in self.r["teams"].values())
        self.assertEqual(ranks, [1, 2, 3, 4])
        self.assertEqual(len(self.r["games"]["1"]), 5)           # regular season only


if __name__ == "__main__":
    unittest.main()

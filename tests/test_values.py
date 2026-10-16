import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dynasty import export, idmap, ingest_sleeper, market, model, nflverse, production, scoring, values
from dynasty.store import Store

from fake_sleeper import FIX, LEAGUE, MY_ROSTER, FakeSleeper
from test_pipeline import FakeNfl

LEAGUE_SCORING = json.loads((FIX / f"league_{LEAGUE}.json").read_text())["scoring_settings"]
STAT_COLS = ["player_id", "position", "season", "week", "season_type", "team", "completions", "attempts",
             "passing_yards", "passing_tds", "passing_interceptions", "passing_2pt_conversions",
             "sacks_suffered", "sack_fumbles_lost", "carries", "rushing_yards", "rushing_tds",
             "rushing_fumbles_lost", "rushing_2pt_conversions", "receptions", "targets",
             "receiving_yards", "receiving_tds", "receiving_fumbles_lost", "receiving_2pt_conversions",
             "special_teams_tds", "target_share", "fg_made_40_49", "pat_made"]


def row(**kw):
    r = {c: 0 for c in STAT_COLS}
    r.update(season_type="REG", team="X")
    r.update(kw)
    return r


class ScoringTest(unittest.TestCase):
    def setUp(self):
        self.score = scoring.make_scorer(LEAGUE_SCORING, STAT_COLS)

    def test_qb(self):
        r = row(passing_yards=300, passing_tds=2, passing_interceptions=1, rushing_yards=20, rushing_fumbles_lost=1)
        # 12 + 8 - 2 + 2 - 2
        self.assertEqual(self.score(r, "QB"), 18.0)

    def test_wr_full_ppr(self):
        self.assertEqual(self.score(row(receptions=6, receiving_yards=85, receiving_tds=1), "WR"), 20.5)

    def test_no_te_premium(self):
        self.assertEqual(self.score(row(receptions=5, receiving_yards=50), "TE"), 10.0)

    def test_kicker(self):
        self.assertEqual(self.score(row(fg_made_40_49=1, pat_made=3), "K"), 7.0)

    def test_unmapped(self):
        self.assertEqual(scoring.unmapped_keys(LEAGUE_SCORING, STAT_COLS),
                         ["fgm_0_19", "fgm_20_29", "fgm_30_39", "fgm_50_59", "fgm_60p", "fum_rec_td", "xpmiss"])
        cols = [c for c in STAT_COLS if c != "passing_interceptions"] + ["interceptions"]
        self.assertNotIn("pass_int", scoring.unmapped_keys(LEAGUE_SCORING, cols))
        cols = [c for c in STAT_COLS if c != "passing_interceptions"]
        self.assertIn("pass_int", scoring.unmapped_keys(LEAGUE_SCORING, cols))


class ModelPiecesTest(unittest.TestCase):
    def test_rank_map(self):
        out = values.rank_map({"a": 50, "b": 100, "c": 0, "d": 10}, [9000, 5000])
        self.assertEqual(out["b"], 9000)
        self.assertEqual(out["a"], 5000)
        self.assertEqual(out["c"], 0.0)
        self.assertAlmostEqual(out["d"], 1000.0)  # 5000 * 10/50

    def test_replacement_with_flex(self):
        proj = {}
        for i in range(30):
            proj[f"rb{i}"] = ("RB", 30 - i)
            proj[f"wr{i}"] = ("WR", 29.5 - i)
        for i in range(15):
            proj[f"qb{i}"] = ("QB", 25 - i)
            proj[f"te{i}"] = ("TE", 15 - i)
        repl, used = model.replacement_levels(proj, ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN"], 2, 1.0)
        # 2 teams: 4 RB + 4 WR starters, 2 flex go to the best remaining (rb4=26, wr4=25.5 -> rb4, then wr4 vs rb5=25)
        self.assertEqual(used["RB"] + used["WR"], 10)
        self.assertEqual(used["QB"], 2)
        self.assertEqual(repl["QB"], 23)

    def test_age_curve_shapes(self):
        curves = model.fit_age_curves([], {}, 2026, model.ModelConfig())  # prior only
        self.assertGreater(model.age_factor(curves, "WR", 22, 24), 1.1)
        self.assertLess(model.age_factor(curves, "RB", 28, 30), 0.85)
        self.assertAlmostEqual(model.age_factor(curves, "QB", 29, 29), 1.0)

    def test_pick_bucket(self):
        self.assertEqual(model.pick_bucket(5), "1-12")
        self.assertEqual(model.pick_bucket(33), "33-64")
        self.assertEqual(model.pick_bucket(None), "UDFA")


class RichNfl(FakeNfl):
    """Several seasons of weekly stats for the fake league's players plus filler players."""

    PLAYERS = [
        # gsis, position, birth, base ppg, sleeper id in fake league
        ("00-0033280", "RB", "1996-06-07", 22.0),
        ("00-0036322", "WR", "1999-06-16", 20.0),
    ] + [(f"F-RB-{i}", "RB", f"{1995 + i % 8}-03-01", 18 - i * 0.6) for i in range(30)] \
      + [(f"F-WR-{i}", "WR", f"{1994 + i % 9}-05-01", 17 - i * 0.5) for i in range(30)] \
      + [(f"F-QB-{i}", "QB", f"{1990 + i % 10}-07-01", 21 - i * 0.6) for i in range(16)] \
      + [(f"F-TE-{i}", "TE", f"{1993 + i % 8}-09-01", 12 - i * 0.5) for i in range(16)]

    def load_player_stats(self, seasons, summary_level="week"):
        self._log("player_stats", seasons)
        s = seasons[0]
        weeks = 5 if s == 2026 else 17
        out = []
        for g, pos, bd, base in self.PLAYERS:
            age = s - int(bd[:4])
            drift = 1.0 - max(0, age - 27) * 0.06
            ppg = max(1.0, base * drift)
            for w in range(1, weeks + 1):
                if pos == "QB":
                    out.append(row(player_id=g, position=pos, season=s, week=w,
                                   passing_yards=round(ppg / 0.04 * 0.6), passing_tds=1,
                                   rushing_yards=round(ppg * 4)))
                else:
                    rec = round(ppg / 4)
                    out.append(row(player_id=g, position=pos, season=s, week=w, receptions=rec,
                                   targets=rec + 2, receiving_yards=round((ppg - rec) * 10)))
        return out

    def load_rosters(self, seasons):
        self._log("rosters", seasons)
        return [{"gsis_id": g, "season": seasons[0], "birth_date": bd, "position": pos}
                for g, pos, bd, _ in self.PLAYERS]

    def load_draft_picks(self, seasons):
        self._log("draft_picks", seasons)
        s = seasons[0]
        return [{"season": s, "round": 1, "pick": 10, "gsis_id": f"D-{s}-{i}", "position": "WR"}
                for i in range(4)]


class FakeResp:
    def __init__(self, data=None, text=""):
        self._d, self.text = data, text

    def json(self):
        return self._d


FC = [
    {"player": {"name": "Justin Jefferson", "sleeperId": "6794", "position": "WR",
                "maybeDraftInfo": {"year": 2020, "round": 1, "pick": 22}}, "value": 9000,
     "overallRank": 1, "positionRank": 1, "trend30Day": 120},
    {"player": {"name": "Christian McCaffrey", "sleeperId": "4034", "position": "RB"}, "value": 4000,
     "overallRank": 2, "positionRank": 1},
    {"player": {"name": "Rookie Unknown", "sleeperId": "9999", "position": "WR",
                "maybeDraftInfo": {"year": 2026, "round": 1, "pick": 8}}, "value": 3000,
     "overallRank": 3, "positionRank": 2},
    {"player": {"name": "2027 1st (Early)", "sleeperId": None, "position": "PICK"}, "value": 5000,
     "overallRank": 4, "positionRank": 1},
]
DP = ('"player","pos","team","age","draft_year","ecr_1qb","ecr_2qb","ecr_pos","value_1qb","value_2qb","scrape_date","fp_id"\n'
      '"Christian McCaffrey","RB","SF",30.2,2017,10,12,2,7000,6000,"2026-08-28","15514"\n'
      '"Unmapped Guy","WR","NYJ",25,2022,50,60,20,2000,1800,"2026-08-28","99999"\n'
      '"2027 Early 1st","PICK",NA,NA,NA,NA,NA,NA,6000,5800,"2026-08-28",NA\n')


class ValuePipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp / "t.db")
        ingest_sleeper.run(self.store, FakeSleeper(), LEAGUE, "jquander")
        nflverse.run(self.store, 2026, seasons_back=3, extras=[], nfl=RichNfl())
        idmap.build(self.store)
        self.store.conn.execute("UPDATE player_xref SET fantasypros_id='15514' WHERE player_id='4034'")

        def fetch(url, params=None):
            return FakeResp(FC) if "fantasycalc" in url else FakeResp(text=DP)

        self.market = market.run(self.store, fetch=fetch)
        self.prod = production.build(self.store)
        self.model = model.build(self.store)
        self.values = values.build(self.store)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp)

    def q(self, sql, p=()):
        return self.store.query(sql, p)

    def test_market(self):
        self.assertEqual(self.market, {"fantasycalc": 4, "dynastyprocess": 3})
        pick = self.q("SELECT * FROM market_value WHERE key='PICK:2027 1st (Early)'")[0]
        self.assertIsNone(pick["player_id"])
        dp = self.q("SELECT player_id FROM market_value WHERE source='dynastyprocess' AND name='Christian McCaffrey'")
        self.assertEqual(dp[0]["player_id"], "4034")

    def test_production_and_validation(self):
        self.assertEqual(self.prod["unmapped_scoring"],
                         ["fgm_0_19", "fgm_20_29", "fgm_30_39", "fgm_50_59", "fgm_60p", "fum_rec_td", "xpmiss"])
        self.assertGreater(self.prod["validated_rows"], 0)  # joined to Sleeper's own points
        s = self.q("SELECT * FROM player_season WHERE player_id='4034' AND season=2025")[0]
        self.assertEqual(s["games"], 17)
        self.assertGreater(s["ppg"], 10)

    def test_model(self):
        p = {r["player_id"]: r for r in self.q("SELECT * FROM player_projection")}
        self.assertIn("4034", p)
        self.assertIn("6794", p)
        # 30-year-old RB projects to decline; 27-year-old WR roughly holds
        self.assertLess(p["4034"]["ppg_y3"], p["4034"]["ppg_y1"])
        self.assertGreater(p["6794"]["model_value"], 0)
        # rookie with no stats gets the draft prior through FantasyCalc's draft info
        self.assertEqual(p["9999"]["draft_bucket"], "1-12")
        self.assertEqual(self.model["starters_counted"]["QB"], 12)
        self.assertTrue(self.q("SELECT COUNT(*) c FROM age_curve")[0]["c"] > 40)

    def test_blend(self):
        v = {r["player_id"]: r for r in self.q("SELECT * FROM player_value")}
        self.assertEqual(self.values["with_fantasycalc"], 3)
        jj = v["6794"]
        # blended value is the weighted mix of the parts present
        parts = [(0.6, jj["model_scaled"]), (0.3, jj["fc_value"])]
        self.assertAlmostEqual(jj["value"], round(sum(a * b for a, b in parts) / 0.9, 1), places=0)
        cmc = v["4034"]
        self.assertIsNotNone(cmc["dp_scaled"])
        self.assertEqual(sorted(r["overall_rank"] for r in v.values()), list(range(1, len(v) + 1)))

    def test_custom_weights_market_only(self):
        values.build(self.store, {"model": 0.0, "fantasycalc": 1.0, "dynastyprocess": 0.0})
        v = self.q("SELECT value FROM player_value WHERE player_id='6794'")[0]["value"]
        self.assertEqual(v, 9000)

    def test_export_includes_values(self):
        site = self.tmp / "site"
        export.run(self.store, site)
        teams = json.loads((site / "data" / "teams.json").read_text())
        mine = [t for t in teams if t["roster_id"] == MY_ROSTER][0]
        self.assertGreater(mine["value"], 0)
        self.assertEqual(mine["value_rank"], 1)  # only roster with valued players in the fixture
        vals = json.loads((site / "data" / "values.json").read_text())
        jj = [x for x in vals if x["id"] == "6794"][0]
        self.assertEqual(jj["owner"], MY_ROSTER)
        self.assertIsNotNone(jj["proj"])
        rosters = json.loads((site / "data" / "rosters.json").read_text())
        cmc = [p for p in rosters[str(MY_ROSTER)] if p["id"] == "4034"][0]
        self.assertIsNotNone(cmc["val"])


if __name__ == "__main__":
    unittest.main()

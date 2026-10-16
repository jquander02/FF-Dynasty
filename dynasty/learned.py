"""Learned projection model: gradient-boosted trees trained on this database's own history.

What it predicts, for every player active at an "as-of" point (a season S and a week k: k=0
is late August, after free agency and the draft; k=4/8/12 are in-season):
  ros  points per game and share of games played for the rest of season S (all of S when k=0)
  y1   the same for season S+1;  y2 for S+2;  y3 for S+3

What it looks at (all from player stats and team data already in the database):
  workload and efficiency, separately   targets, receptions, carries, pass attempts per game;
                                        yards per target / carry / attempt; touchdown rates
                                        (volume carries over between seasons, TD rate mostly
                                        doesn't, and the trees learn how much of each to trust)
  role                                  snap share, target share, share of the team's targets
                                        and carries, expected fantasy points
  this season so far                    the same numbers for weeks 1..k (in-season points)
  team as of the as-of point            new team or not; targets and carries that left the
                                        team (vacated); the best teammate at his position and
                                        rookies drafted there; the team's QB; team volume
  games                                 games and share of games over the last seasons
  contract                              cap share of the current deal, guaranteed share, years
                                        left (a team's commitment signals role security)
  draft and body                        draft slot, NFL year, age, combine measurements
  the hand-built model                  its projection for the same season, so the trees learn
                                        corrections on top of it rather than starting cold

Outcome ranges: for each position, horizon and projection tier, the model keeps how actual
PPG has differed from its projections in seasons it was not trained on. Value then averages
points above replacement over those real outcomes instead of assuming a bell curve.
"""

from __future__ import annotations

import logging
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from . import model
from .model import POSITIONS, ModelConfig

log = logging.getLogger(__name__)

CUTS = (0, 4, 8, 12)
HORIZONS = ("ros", "y1", "y2", "y3")
POS_CODE = {"QB": 0, "RB": 1, "WR": 2, "TE": 3}
MIN_TARGET_GAMES = {"ros": 3, "y1": 4, "y2": 4, "y3": 4}


def season_games(season: int) -> int:
    return 17 if season >= 2021 else 16


# ---------------------------------------------------------------------------------------------
# Data index
# ---------------------------------------------------------------------------------------------
class Data:
    """Everything indexed for fast as-of lookups. Built from a history bundle (backtest.make_bundle,
    which the live model also uses)."""

    STATS = ("pts", "targets", "rec", "rec_yd", "rec_td", "rush_att", "rush_yd", "rush_td",
             "pass_att", "pass_yd", "pass_td", "pass_int", "xfp")

    def __init__(self, b: dict):
        self.b = b
        w = b.get("weeks") or {}
        n = len(w.get("gsis_id", []))
        self.weeks = defaultdict(list)            # (g, season) -> [(week, team, row dict)]
        self.team_weeks = defaultdict(set)        # (team, season) -> weeks played
        cols = [c for c in w if c not in ("gsis_id", "season", "week", "team")]
        for i in range(n):
            g, s, wk, t = w["gsis_id"][i], w["season"][i], w["week"][i], w["team"][i]
            if g is None or s is None or wk is None:
                continue
            row = {c: w[c][i] for c in cols}
            self.weeks[(g, s)].append((wk, t, row))
            if t:
                self.team_weeks[(t, s)].add(wk)
        for v in self.weeks.values():
            v.sort(key=lambda x: x[0])
        self.pos = {}
        for s in b["seasons"]:
            self.pos[s["gsis_id"]] = s["position"]
        self.birth = {g: date.fromisoformat(d) for g, d in b.get("birth", {}).items()}
        self.names = b.get("names", {})
        self.draft = {}
        for p in b.get("picks", []):
            if p.get("gsis_id"):
                self.draft[p["gsis_id"]] = p
                self.pos.setdefault(p["gsis_id"], p.get("position"))
        pt = b.get("pick_teams") or {}
        self.draft_team = {}
        for i in range(len(pt.get("gsis_id", []))):
            g = pt["gsis_id"][i]
            if g:
                self.draft_team[g] = (pt["season"][i], pt["team"][i], pt["pick"][i], pt["position"][i])
        ro = b.get("rosters") or {}
        self.roster_team = {}
        for i in range(len(ro.get("gsis_id", []))):
            g, s, t = ro["gsis_id"][i], ro["season"][i], ro["team"][i]
            if g and s and t:
                self.roster_team[(g, int(s))] = t
        self.combine = {c["gsis_id"]: c for c in b.get("combine", [])}
        self.contracts = defaultdict(list)
        ct = b.get("contracts") or {}
        gcol = "gsis_id" if "gsis_id" in ct else None
        if gcol:
            keys = list(ct)
            for i in range(len(ct[gcol])):
                g = ct[gcol][i]
                if g:
                    self.contracts[g].append({k: ct[k][i] for k in keys})
        self.seasons_have = sorted({s for (_, s) in self.weeks})
        self.first_season = {}
        self.by_season = defaultdict(list)
        for (g, s) in self.weeks:
            self.first_season[g] = min(self.first_season.get(g, 9999), s)
            self.by_season[s].append(g)
        self._agg = {}

    # -- aggregates ----------------------------------------------------------------------
    def agg(self, g: str, season: int, upto: int | None = None, after: int | None = None) -> dict | None:
        """Season totals for weeks <= upto (or > after)."""
        key = (g, season, upto, after)
        if key in self._agg:
            return self._agg[key]
        res = self._agg[key] = self._agg_calc(g, season, upto, after)
        return res

    def _agg_calc(self, g, season, upto, after):
        rows = self.weeks.get((g, season))
        if not rows:
            return None
        sel = [r for r in rows if (upto is None or r[0] <= upto) and (after is None or r[0] > after)]
        if not sel:
            return None
        out = {k: 0.0 for k in self.STATS}
        n_x = n_snap = n_ts = 0
        snap = ts = ash = 0.0
        for _, _, r in sel:
            for k in self.STATS:
                v = r.get(k)
                if v is not None:
                    out[k] += v
                    if k == "xfp":
                        n_x += 1
            if r.get("snap_pct") is not None:
                snap += r["snap_pct"]
                n_snap += 1
            if r.get("target_share") is not None:
                ts += r["target_share"]
                ash += r.get("air_yards_share") or 0.0
                n_ts += 1
        out["games"] = len(sel)
        out["snap"] = snap / n_snap if n_snap else None
        out["tshare"] = ts / n_ts if n_ts else None
        out["ashare"] = ash / n_ts if n_ts else None
        out["xfp"] = out["xfp"] if n_x else None
        out["team"] = sel[-1][1]
        out["first_team"] = sel[0][1]
        return out

    def team_totals(self, team: str, season: int, upto: int | None = None) -> dict:
        key = (team, season, upto)
        if not hasattr(self, "_tt"):
            self._tt = {}
            self._tt_built = set()
        if (season, upto) not in self._tt_built:
            tot = defaultdict(lambda: defaultdict(float))
            for g in self.by_season.get(season, []):
                for wk, t, r in self.weeks[(g, season)]:
                    if upto is not None and wk > upto:
                        continue
                    if not t:
                        continue
                    for k in ("targets", "rush_att", "pass_att", "pts"):
                        tot[t][k] += r.get(k) or 0.0
            for t, d in tot.items():
                d["games"] = len([w for w in self.team_weeks.get((t, season), ()) if upto is None or w <= upto]) or 1
                self._tt[(t, season, upto)] = d
            self._tt_built.add((season, upto))
        return self._tt.get(key, {})

    def team_games(self, team: str | None, season: int, upto: int | None = None, after: int | None = None) -> int:
        if not team:
            return season_games(season) if upto is None and after is None else 0
        wks = self.team_weeks.get((team, season), set())
        return len([w for w in wks if (upto is None or w <= upto) and (after is None or w > after)])

    def age(self, g: str, season: int):
        bd = self.birth.get(g)
        return model.age_on(bd, season) if bd else None


# ---------------------------------------------------------------------------------------------
# Who's active, and their team, at an as-of point
# ---------------------------------------------------------------------------------------------
def active_players(d: Data, S: int, k: int) -> list[str]:
    out = set(d.by_season.get(S - 1, [])) | set(d.by_season.get(S - 2, []))
    if k > 0:
        out |= {g for g in d.by_season.get(S, []) if d.agg(g, S, upto=k)}
    for g, p in d.draft.items():
        if p.get("year") == S and p.get("position") in POSITIONS:
            out.add(g)
    return [g for g in out if d.pos.get(g) in POSITIONS]


def team_at(d: Data, g: str, S: int, k: int) -> str | None:
    """His team as of (S, k): in-season, his latest game; preseason, his first game of S (what a
    late-August depth chart would show), else his season roster entry, else last season's team."""
    if k > 0:
        a = d.agg(g, S, upto=k)
        if a:
            return a["team"]
    a = d.agg(g, S)
    if a and k == 0:
        return a["first_team"]
    t = d.roster_team.get((g, S))
    if t:
        return t
    dt = d.draft_team.get(g)
    if dt and dt[0] == S:
        return dt[1]
    prev = d.agg(g, S - 1)
    return prev["team"] if prev else None


# ---------------------------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------------------------
def _rate(a, b):
    return a / b if b else None


def _season_feats(a: dict | None, tt: dict | None, prefix: str, pos: str) -> dict:
    if not a or not a["games"]:
        return {f"{prefix}games": 0}
    g = a["games"]
    f = {
        f"{prefix}games": g,
        f"{prefix}ppg": a["pts"] / g,
        f"{prefix}xfp_pg": (a["xfp"] / g) if a.get("xfp") is not None else None,
        f"{prefix}tgt_pg": a["targets"] / g,
        f"{prefix}rec_pg": a["rec"] / g,
        f"{prefix}car_pg": a["rush_att"] / g,
        f"{prefix}ypt": _rate(a["rec_yd"], a["targets"]),
        f"{prefix}ypc": _rate(a["rush_yd"], a["rush_att"]),
        f"{prefix}rec_td_rate": _rate(a["rec_td"], a["targets"]),
        f"{prefix}rush_td_rate": _rate(a["rush_td"], a["rush_att"]),
        f"{prefix}td_pg": (a["rec_td"] + a["rush_td"] + a["pass_td"] * (1 if pos == "QB" else 0)) / g,
        f"{prefix}snap": a.get("snap"),
        f"{prefix}tshare": a.get("tshare"),
        f"{prefix}ashare": a.get("ashare"),
    }
    if pos == "QB":
        f.update({
            f"{prefix}patt_pg": a["pass_att"] / g,
            f"{prefix}ypa": _rate(a["pass_yd"], a["pass_att"]),
            f"{prefix}ptd_rate": _rate(a["pass_td"], a["pass_att"]),
            f"{prefix}int_rate": _rate(a["pass_int"], a["pass_att"]),
            f"{prefix}qb_rush_pg": a["rush_yd"] / g,
        })
    if tt:
        f[f"{prefix}team_tgt_share"] = _rate(a["targets"], tt.get("targets"))
        f[f"{prefix}team_car_share"] = _rate(a["rush_att"], tt.get("rush_att"))
    return f


def _contract_feats(d: Data, g: str, S: int) -> dict:
    deals = [c for c in d.contracts.get(g, []) if c.get("year_signed") and int(c["year_signed"]) <= S]
    if not deals:
        return {"ct_has": 0}
    c = max(deals, key=lambda c: int(c["year_signed"]))
    yrs = c.get("years")
    left = (int(c["year_signed"]) + int(yrs) - S) if yrs else None
    value, gtd = c.get("value"), c.get("guaranteed")
    return {"ct_has": 1, "ct_apy_cap": c.get("apy_cap_pct"), "ct_left": left,
            "ct_gtd_share": (gtd / value) if value and gtd is not None else None,
            "ct_apy": c.get("apy")}


def build_rows(d: Data, S: int, k: int, base: dict | None = None) -> list[dict]:
    """One feature row per active player at (S, k). `base` maps gsis -> hand-model projection."""
    players = active_players(d, S, k)
    team_now = {g: team_at(d, g, S, k) for g in players}
    # all players' team for vacated-opportunity math (anyone who played S-1)
    prev_players = list(d.by_season.get(S - 1, []))
    for g in prev_players:
        if g not in team_now:
            team_now[g] = team_at(d, g, S, k)
    # vacated opportunity: S-1 targets/carries on each team by players no longer there
    vac = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])   # team -> [tgt total, tgt kept, car total, car kept]
    best_at = defaultdict(list)                         # (team, pos) -> [(ppg, g)] last season
    qb_ppg = defaultdict(float)
    for g in prev_players:
        a = d.agg(g, S - 1)
        if not a:
            continue
        t_prev = a["team"]
        stay = team_now.get(g) == t_prev
        v = vac[t_prev]
        v[0] += a["targets"]; v[2] += a["rush_att"]
        if stay:
            v[1] += a["targets"]; v[3] += a["rush_att"]
        tn = team_now.get(g)
        if tn and a["games"] >= 4:
            best_at[(tn, d.pos.get(g))].append((a["pts"] / a["games"], g))
            if d.pos.get(g) == "QB" and a["games"] >= 6:
                qb_ppg[tn] = max(qb_ppg[tn], a["pts"] / a["games"])
    rookies_at = defaultdict(list)                      # (team, pos) -> rookie picks this season
    for g, (yr, t, pick, pos) in d.draft_team.items():
        if yr == S and t:
            rookies_at[(t, pos)].append(pick or 260)

    rows = []
    for g in players:
        pos = d.pos.get(g)
        tn = team_now.get(g)
        a1, a2 = d.agg(g, S - 1), d.agg(g, S - 2)
        f = {"pos": POS_CODE[pos], "k": k, "season": S}
        f["age"] = d.age(g, S)
        dr = d.draft.get(g)
        f["log_pick"] = math.log(dr["pick"]) if dr and dr.get("pick") else math.log(300)
        f["nfl_year"] = (S - dr["year"] + 1) if dr and dr.get("year") else None
        if f["nfl_year"] is None:
            first = d.first_season.get(g)
            f["nfl_year"] = (S - first + 1) if first else 1
        c = d.combine.get(g) or {}
        for key in ("forty", "wt", "vertical", "broad_jump", "cone", "shuttle"):
            f[f"cb_{key}"] = c.get(key)
        ht = c.get("ht")
        if isinstance(ht, str) and "-" in ht:
            try:
                ft, inch = ht.split("-")
                f["cb_ht"] = int(ft) * 12 + int(inch)
            except ValueError:
                f["cb_ht"] = None
        f.update(_season_feats(a1, d.team_totals(a1["team"], S - 1) if a1 else None, "l1_", pos))
        f.update(_season_feats(a2, d.team_totals(a2["team"], S - 2) if a2 else None, "l2_", pos))
        f["l1_game_share"] = (a1["games"] / season_games(S - 1)) if a1 else 0.0
        f["l2_game_share"] = (a2["games"] / season_games(S - 2)) if a2 else 0.0
        if k > 0:
            a0 = d.agg(g, S, upto=k)
            f.update(_season_feats(a0, d.team_totals(a0["team"], S, upto=k) if a0 else None, "c_", pos))
            tg = d.team_games(tn, S, upto=k)
            f["c_game_share"] = (a0["games"] / tg) if a0 and tg else 0.0
        # team context
        prev_team = a1["team"] if a1 else None
        f["new_team"] = 1 if (tn and prev_team and tn != prev_team) else 0
        v = vac.get(tn) if tn else None
        if v and v[0]:
            f["vac_tgt"] = 1 - v[1] / v[0]
            f["vac_car"] = 1 - v[3] / v[2] if v[2] else None
        others = sorted((p for p, gg in best_at.get((tn, pos), []) if gg != g), reverse=True)
        f["mate_best_ppg"] = others[0] if others else 0.0
        f["mate_2nd_ppg"] = others[1] if len(others) > 1 else 0.0
        rk = [p for p in rookies_at.get((tn, pos), [])
              if not (d.draft_team.get(g, (None,))[0] == S and d.draft_team[g][2] == p)]
        f["rookie_comp"] = math.log(min(rk)) if rk else math.log(300)
        f["team_qb_ppg"] = qb_ppg.get(tn) if pos != "QB" else None
        tt = d.team_totals(tn, S - 1) if tn else {}
        if tt:
            f["team_pass_pg"] = tt["pass_att"] / tt["games"]
            f["team_pts_pg"] = tt["pts"] / tt["games"]
        f.update(_contract_feats(d, g, S))
        if base and g in base:
            for h in HORIZONS:
                f[f"base_{h}"] = base[g].get(h)
            f["base_play"] = base[g].get("play")
        rows.append({"g": g, "pos": pos, "S": S, "k": k, "team": tn, "f": f})
    return rows


def attach_targets(d: Data, rows: list[dict]) -> None:
    """Outcomes for each row where they're known: PPG and share of games, per horizon."""
    for r in rows:
        g, S, k = r["g"], r["S"], r["k"]
        t = {}
        a = d.agg(g, S, after=k) if k > 0 else d.agg(g, S)
        tg = d.team_games(r["team"], S, after=k) if k > 0 else season_games(S)
        if S in d.seasons_have and (k == 0 or d.team_games(r["team"], S, after=k)):
            t["ros"] = (a["pts"] / a["games"] if a else None, (a["games"] / tg) if a and tg else 0.0, a["games"] if a else 0)
        for i, h in enumerate(("y1", "y2", "y3"), 1):
            s2 = S + i
            if s2 in d.seasons_have and s2 < (d.b.get("current_season") or 9999):
                a2 = d.agg(g, s2)
                t[h] = (a2["pts"] / a2["games"] if a2 else None, (a2["games"] / season_games(s2)) if a2 else 0.0,
                        a2["games"] if a2 else 0)
        if k == 0 and S >= (d.b.get("current_season") or 9999):
            t.pop("ros", None)
        r["t"] = t


# ---------------------------------------------------------------------------------------------
# Hand-built model projections as features
# ---------------------------------------------------------------------------------------------
def base_projections(d: Data, S: int, k: int, cfg: ModelConfig) -> dict:
    """The hand-built model's projections at (S, k), keyed by gsis id and mapped to horizons."""
    b = d.b
    if k == 0:
        through = S - 1
        seasons = [s for s in b["seasons"] if s["season"] <= through]
    else:
        through = S
        seasons = [s for s in b["seasons"] if s["season"] < S]
        for g in d.by_season.get(S, []):
            a = d.agg(g, S, upto=k)
            if a:
                seasons.append({"gsis_id": g, "season": S, "position": d.pos.get(g), "games": a["games"],
                                "pts": a["pts"], "ppg": a["pts"] / a["games"],
                                "xppg": (a["xfp"] / a["games"]) if a.get("xfp") is not None else None,
                                "snap_pct": a.get("snap")})
    birth = d.birth
    picks = [p for p in b["picks"] if p["year"] <= (S if k == 0 else S)]
    curves = model.fit_age_curves([s for s in seasons if s["season"] < S], birth, S - 1, cfg, current_complete=True)
    share: dict = {}
    prior = model.fit_draft_prior(picks, [s for s in seasons if s["season"] < S], S - 1, current_complete=True,
                                  per_game=cfg.prior_per_game, share=share)
    from .backtest import players_as_of
    players = players_as_of(b, S - 1) if k == 0 else players_as_of_inseason(d, S, k)
    proj = model.project(players, seasons, curves, prior, through, cfg, current_games=k if k > 0 else None)
    out = {}
    for g, p in proj.items():
        if k == 0:
            out[g] = {"ros": p["ppg_y1"], "y1": p["ppg_y2"], "y2": p["ppg_y3"], "y3": p["ppg_y3"],
                      "play": p.get("play_share"), "_p": p}
        else:
            out[g] = {"ros": p["ppg_ros"], "y1": p["ppg_y1"], "y2": p["ppg_y2"], "y3": p["ppg_y3"],
                      "play": p.get("play_share"), "_p": p}
    return out, share


def players_as_of_inseason(d: Data, S: int, k: int) -> list[dict]:
    from .backtest import _player
    out = []
    for g in active_players(d, S, k):
        dr = d.draft.get(g)
        first = d.first_season.get(g, S)
        out.append(_player(d.b, g, d.pos[g], dr, years_exp=S - first))
    return out


# ---------------------------------------------------------------------------------------------
# Training and prediction
# ---------------------------------------------------------------------------------------------
@dataclass
class Learned:
    feature_names: list
    ppg: dict = field(default_factory=dict)        # horizon -> regressor
    games: dict = field(default_factory=dict)      # horizon -> regressor
    resid: dict = field(default_factory=dict)      # (pos, horizon, tier) -> sorted actual/pred ratios
    tiers: dict = field(default_factory=dict)      # (pos, horizon) -> tier cut points of predicted PPG


def _matrix(rows: list[dict], names: list[str]):
    import numpy as np
    X = np.full((len(rows), len(names)), np.nan, dtype=float)
    for i, r in enumerate(rows):
        f = r["f"]
        for j, n in enumerate(names):
            v = f.get(n)
            if v is not None:
                X[i, j] = float(v)
    return X


def _regressor(cfg_params: dict | None = None):
    from sklearn.ensemble import HistGradientBoostingRegressor
    p = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=40, l2_regularization=1.0,
             random_state=7)
    p.update(cfg_params or {})
    return HistGradientBoostingRegressor(**p)


def fit(rows: list[dict], params: dict | None = None) -> Learned:
    import numpy as np
    names = sorted({n for r in rows for n in r["f"]})
    L = Learned(feature_names=names)
    for h in HORIZONS:
        tr = [r for r in rows if h in r.get("t", {})]
        if len(tr) < 200:
            continue
        X = _matrix(tr, names)
        gy = np.array([r["t"][h][1] for r in tr])
        m = _regressor(params)
        m.fit(X, np.clip(gy, 0, 1))
        L.games[h] = m
        pr = [r for r in tr if r["t"][h][0] is not None and r["t"][h][2] >= MIN_TARGET_GAMES[h]]
        Xp = _matrix(pr, names)
        y = np.array([r["t"][h][0] for r in pr])
        w = np.array([r["t"][h][2] for r in pr], dtype=float)
        m2 = _regressor(params)
        m2.fit(Xp, y, sample_weight=w)
        L.ppg[h] = m2
    return L


def predict(L: Learned, rows: list[dict]) -> None:
    import numpy as np
    if not rows:
        return
    X = _matrix(rows, L.feature_names)
    for h in HORIZONS:
        if h not in L.ppg:
            continue
        p = L.ppg[h].predict(X)
        gm = np.clip(L.games[h].predict(X), 0.0, 1.0)
        for r, a, b in zip(rows, p, gm):
            r.setdefault("pred", {})[h] = (max(0.0, float(a)), float(b))


TIER_Q = (0.5, 0.8)   # tiers of projected PPG within position: bottom half, next 30%, top 20%


def _tier(L: Learned, pos: str, h: str, ppg: float) -> int:
    cuts = L.tiers.get((pos, h))
    if not cuts:
        return 0
    return sum(1 for c in cuts if ppg >= c)


def learn_residuals(L: Learned, rows: list[dict]) -> None:
    """Outcome ranges from out-of-sample rows (predicted before their outcomes were trained on)."""
    by = defaultdict(list)
    for r in rows:
        for h, (ppg, _) in (r.get("pred") or {}).items():
            by[(r["pos"], h)].append(ppg)
    for key, v in by.items():
        v = sorted(v)
        if len(v) >= 20:
            L.tiers[key] = [v[int(q * (len(v) - 1))] for q in TIER_Q]
    ratios = defaultdict(list)
    for r in rows:
        for h, (ppg, _) in (r.get("pred") or {}).items():
            t = r.get("t", {}).get(h)
            if not t or t[0] is None or t[2] < MIN_TARGET_GAMES[h] or ppg < 1:
                continue
            ratios[(r["pos"], h, _tier(L, r["pos"], h, ppg))].append(t[0] / ppg)
    for key, v in ratios.items():
        if len(v) >= 30:
            v = sorted(v)
            L.resid[key] = [v[int(i * (len(v) - 1) / 40)] for i in range(41)]


def expected_excess(L: Learned, pos: str, h: str, ppg: float, repl: float, fallback_cv: float) -> float:
    """E[max(0, PPG - repl)] over the real outcome range for this position, horizon and tier."""
    rs = L.resid.get((pos, h, _tier(L, pos, h, ppg)))
    if not rs:
        return model.expected_excess(ppg, fallback_cv * ppg, repl)
    return sum(max(0.0, ppg * x - repl) for x in rs) / len(rs)


# ---------------------------------------------------------------------------------------------
# Assembling training sets over history
# ---------------------------------------------------------------------------------------------
def history_rows(d: Data, cfg: ModelConfig, seasons: list[int], cuts=CUTS) -> list[dict]:
    rows = []
    for S in seasons:
        for k in cuts:
            if k > 0 and S not in d.seasons_have:
                continue
            base, _ = base_projections(d, S, k, cfg)
            rr = build_rows(d, S, k, base)
            attach_targets(d, rr)
            for r in rr:
                r["base"] = base.get(r["g"])
            rows.extend(rr)
    return rows

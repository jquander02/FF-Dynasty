"""Backtest the projection model against what actually happened.

For each past offseason (say before 2022), the model is refit using only seasons up to 2021
(age curves, draft prior, talent), then its projections for 2022, 2023 and 2024 are compared
with real points per game in this league's scoring.

Scores
  ppg accuracy   mean absolute error and bias of projected vs actual PPG, for players who
                 played 6+ games and were fantasy-relevant (projected or actual 8+ PPG), by
                 horizon (1, 2, 3 seasons out), position, age band and NFL year
  rank           Spearman rank correlation within position (does it order players right?)
  value          projected vs realised points above replacement over the three seasons
                 (missed games count as zero), rank correlation within position: the dynasty
                 question of who will produce the most over the window
  baselines      the same scores for "last season's PPG" and "recency-weighted PPG, no aging"
                 so it's clear whether the model's adjustments help

Usage
  python -m dynasty.backtest bundle <db> <out.json.gz>     # history bundle from the database
  python -m dynasty.backtest run <bundle.json.gz> [--json out.json] [--set key=value ...]
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import date

from . import model
from .model import POSITIONS, ModelConfig

MIN_GAMES = 6
RELEVANT_PPG = 8.0
REPL = {"QB": 14.0, "RB": 8.0, "WR": 8.0, "TE": 6.5}   # rough waiver-level PPG for the value test


# ---------------------------------------------------------------------------------------------
# History bundle (made in Actions where the full database lives)
# ---------------------------------------------------------------------------------------------
def make_bundle(store) -> dict:
    seasons = [
        {k: r[k] for k in ("gsis_id", "season", "position", "games", "pts", "ppg", "xppg", "snap_pct",
                           "target_share", "targets_pg", "rush_att_pg", "team") if k in r.keys()}
        for r in store.query("SELECT * FROM player_season WHERE position IN ('QB','RB','WR','TE')")
    ]
    picks = []
    if model._exists(store, "nfl_draft_picks"):
        dc = model._cols(store, "nfl_draft_picks")
        gcol = next((c for c in ("gsis_id", "player_id") if c in dc), None)
        pcol = "position" if "position" in dc else ("pos" if "pos" in dc else None)
        name = next((c for c in ("pfr_player_name", "player_name", "full_name") if c in dc), None)
        if gcol and pcol:
            for r in store.query(f'SELECT season, pick, "{gcol}" g, "{pcol}" pos'
                                 + (f', "{name}" n' if name else "") + " FROM nfl_draft_picks"):
                picks.append({"gsis_id": r["g"], "year": int(r["season"]), "pick": int(r["pick"]) if r["pick"] else None,
                              "position": r["pos"], "name": r["n"] if name else None})
    birth, names = {}, {}
    if model._exists(store, "nfl_rosters"):
        rc = model._cols(store, "nfl_rosters")
        bcol = next((c for c in ("birth_date", "birthdate") if c in rc), None)
        ncol = next((c for c in ("full_name", "player_name") if c in rc), None)
        if "gsis_id" in rc:
            cols = ", ".join(x for x in (f'"{bcol}" b' if bcol else None, f'"{ncol}" n' if ncol else None) if x)
            for r in store.query(f"SELECT gsis_id, {cols} FROM nfl_rosters"):
                d = model._date(r["b"]) if bcol else None
                if d:
                    birth[str(r["gsis_id"])] = d.isoformat()
                if ncol and r["n"]:
                    names[str(r["gsis_id"])] = r["n"]
    for r in store.query("SELECT x.gsis_id, p.birth_date, p.full_name FROM player p "
                         "JOIN player_xref x ON x.player_id=p.player_id WHERE x.gsis_id IS NOT NULL"):
        d = model._date(r["birth_date"])
        if d:
            birth.setdefault(r["gsis_id"], d.isoformat())
        if r["full_name"]:
            names.setdefault(r["gsis_id"], r["full_name"])
    # weekly rows, columnar (for noise floors, usage and availability features)
    wk_cols = ("gsis_id", "season", "week", "team", "pts", "targets", "rush_att", "rec_yd", "target_share",
               "air_yards_share", "wopr", "snap_pct", "xfp")
    weeks = {c: [] for c in wk_cols}
    if model._exists(store, "player_week"):
        have = model._cols(store, "player_week")
        use = [c for c in wk_cols if c in have]
        for r in store.query(f"SELECT {', '.join(use)} FROM player_week WHERE position IN ('QB','RB','WR','TE')"):
            for c in wk_cols:
                v = r[c] if c in use else None
                weeks[c].append(round(v, 3) if isinstance(v, float) else v)
    # combine measurements joined to gsis ids through the draft picks' pfr ids
    combine = []
    if model._exists(store, "nfl_combine") and model._exists(store, "nfl_draft_picks"):
        cc, dc = model._cols(store, "nfl_combine"), model._cols(store, "nfl_draft_picks")
        if "pfr_id" in cc and "pfr_player_id" in dc and "gsis_id" in dc:
            pfr = {r["p"]: r["g"] for r in store.query(
                "SELECT pfr_player_id p, gsis_id g FROM nfl_draft_picks WHERE gsis_id IS NOT NULL AND pfr_player_id IS NOT NULL")}
            keep = [c for c in ("season", "pos", "ht", "wt", "forty", "vertical", "broad_jump", "cone", "shuttle", "bench")
                    if c in cc]
            for r in store.query(f"SELECT pfr_id, {', '.join(keep)} FROM nfl_combine"):
                g = pfr.get(r["pfr_id"])
                if g:
                    combine.append({"gsis_id": g, **{c: r[c] for c in keep}})
    lg = store.query("SELECT season FROM league_season WHERE is_current=1")
    return {"made": date.today().isoformat(), "current_season": int(lg[0]["season"]) if lg else None,
            "seasons": seasons, "picks": picks, "birth": birth, "names": names, "weeks": weeks,
            "combine": combine}


def save_bundle(bundle: dict, path: str) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(bundle, f, separators=(",", ":"))


def load_bundle(path: str) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        b = json.load(f)
    add_late_ppg(b)
    return b


def add_late_ppg(b: dict, n: int = 6) -> None:
    """Each player-season gets ppg_late: PPG over his last `n` games that season (from weekly rows)."""
    w = b.get("weeks") or {}
    if not w.get("gsis_id"):
        return
    games = defaultdict(list)
    for g, season, week, pts in zip(w["gsis_id"], w["season"], w["week"], w["pts"]):
        if pts is not None:
            games[(g, season)].append((week, pts))
    for s in b["seasons"]:
        gl = sorted(games.get((s["gsis_id"], s["season"]), []))[-n:]
        if len(gl) >= 3:
            s["ppg_late"] = round(sum(p for _, p in gl) / len(gl), 2)


# ---------------------------------------------------------------------------------------------
# Projections as of an offseason
# ---------------------------------------------------------------------------------------------
def players_as_of(b: dict, through: int) -> list[dict]:
    """Everyone with an NFL season up to `through`, plus the next draft class (rookies)."""
    first = {}
    pos = {}
    for s in b["seasons"]:
        if s["season"] <= through:
            g = s["gsis_id"]
            first[g] = min(first.get(g, 9999), s["season"])
            pos[g] = s["position"]
    draft = {p["gsis_id"]: p for p in b["picks"] if p["gsis_id"]}
    recent = {s["gsis_id"] for s in b["seasons"] if through - 1 <= s["season"] <= through}
    out = []
    for g, fs in first.items():
        if g not in recent:
            continue   # out of the league for two seasons (retired, released): not on a dynasty roster
        d = draft.get(g)
        out.append(_player(b, g, pos[g], d, years_exp=through - fs + 0))
    for p in b["picks"]:
        if p["year"] == through + 1 and p["gsis_id"] and p["position"] in POSITIONS and p["gsis_id"] not in first:
            out.append(_player(b, p["gsis_id"], p["position"], p, years_exp=-1))
    return out


def _player(b, g, pos, d, years_exp):
    bd = b["birth"].get(g)
    return {"key": g, "gsis_id": g, "position": pos, "name": b["names"].get(g),
            "years_exp": years_exp, "birth": date.fromisoformat(bd) if bd else None,
            "draft": {"year": d["year"], "pick": d["pick"]} if d and d.get("pick") else None}


def project_as_of(b: dict, through: int, cfg: ModelConfig, share: dict | None = None) -> dict:
    seasons = [s for s in b["seasons"] if s["season"] <= through]
    birth = {g: date.fromisoformat(v) for g, v in b["birth"].items()}
    picks = [p for p in b["picks"] if p["year"] <= through]
    curves = model.fit_age_curves(seasons, birth, through, cfg, current_complete=True)
    prior = model.fit_draft_prior(picks, seasons, through, current_complete=True, per_game=cfg.prior_per_game,
                                  share=share)
    return model.project(players_as_of(b, through), seasons, curves, prior, through, cfg)


LINEUP = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX"]   # this league's starting lineup


def value_check(b: dict, cfg: ModelConfig, start: int, end: int) -> dict:
    """Did players produce what the model valued them at?

    As of each offseason with three finished seasons after it, every player's model value (the
    same formula as the app: expected points above a replacement starter over the next three
    seasons, undiscounted) is compared with what he actually produced: games x (PPG - that
    season's real replacement level), floored at zero per season. Replacement levels come from
    this league's lineup (12 teams, QB/2RB/2WR/TE/FLEX, 1.5x starters deep), from projections for
    the valuation and from actual PPG (6+ games) for the outcome."""
    actual = defaultdict(dict)
    for s in b["seasons"]:
        actual[s["season"]][s["gsis_id"]] = s
    real_repl = {}
    for season, rows in actual.items():
        pool = {g: (s["position"], s["ppg"]) for g, s in rows.items() if s["games"] >= MIN_GAMES}
        real_repl[season], _ = model.replacement_levels(pool, LINEUP, cfg.teams, cfg.replacement_depth)
    out = []
    for through in range(start - 1, end - 2):
        share: dict = {}
        proj = project_as_of(b, through, cfg, share)
        repl, _ = model.replacement_levels({k: (v["position"], v["ppg_y1"]) for k, v in proj.items()},
                                           LINEUP, cfg.teams, cfg.replacement_depth)
        for g, v in proj.items():
            pos = v["position"]
            if pos not in POSITIONS:
                continue
            parts = model.value_parts(v, repl.get(pos, 0.0), share, cfg, 0)
            pred = sum(parts[1:4])
            real = 0.0
            for h in (1, 2, 3):
                a = actual.get(through + h, {}).get(g)
                if a and a["games"]:
                    real += a["games"] * max(0.0, a["ppg"] - real_repl[through + h].get(pos, 0.0))
            if pred < 5 and real < 5:
                continue
            out.append({"g": g, "name": v.get("name"), "pos": pos, "through": through, "age": v["age"],
                        "nfl_year": v["nfl_year"], "pred": pred, "real": real})
    return summarize_values(out)


def luck_floor(b: dict, rows: list[dict]) -> float | None:
    """Average error a perfect forecaster of each player's true per-game level would still make,
    from week-to-week randomness alone: sqrt(2/pi) x weekly SD / sqrt(games), over the same rows."""
    w = b.get("weeks") or {}
    if not w.get("gsis_id"):
        return None
    pts = defaultdict(list)
    for g, season, p in zip(w["gsis_id"], w["season"], w["pts"]):
        if p is not None:
            pts[(g, season)].append(p)
    errs = []
    for r in rows:
        x = pts.get((r["g"], r["season"]))
        if x and len(x) >= 2:
            errs.append(math.sqrt(2 / math.pi) * statistics.stdev(x) / math.sqrt(len(x)))
    return round(statistics.mean(errs), 2) if errs else None


def explained(b: dict, rows: list[dict]) -> dict | None:
    """Share of the variation in next-season PPG the model explains (R^2), against the most any
    forecast could explain once week-to-week luck is removed (1 - luck variance / total variance)."""
    w = b.get("weeks") or {}
    if not w.get("gsis_id") or len(rows) < 30:
        return None
    pts = defaultdict(list)
    for g, season, p in zip(w["gsis_id"], w["season"], w["pts"]):
        if p is not None:
            pts[(g, season)].append(p)
    use = [(r, pts.get((r["g"], r["season"]))) for r in rows]
    use = [(r, x) for r, x in use if x and len(x) >= 2]
    act = [r["actual"] for r, _ in use]
    var = statistics.pvariance(act)
    luck = statistics.mean(statistics.variance(x) / len(x) for _, x in use)
    out = {"max_r2": round(1 - luck / var, 3)}
    for key in ("model", "avg", "last"):
        sub = [(r[key], r["actual"]) for r, _ in use if r.get(key) is not None]
        mse = statistics.mean((p - a) ** 2 for p, a in sub)
        out[key] = round(1 - mse / var, 3)
    out["share_of_possible"] = round(out["model"] / out["max_r2"], 3)
    return out


def _tier(rank: int) -> str:
    for cut, lab in ((12, "top 12"), (24, "13-24"), (48, "25-48"), (96, "49-96")):
        if rank <= cut:
            return lab
    return "97+"


def summarize_values(rows: list[dict]) -> dict:
    """Calibration (valued vs produced) by tier, position, age and NFL year, rank agreement, and
    the biggest misses either way."""
    by_year = defaultdict(list)
    for r in rows:
        by_year[r["through"]].append(r)
    for yr, sub in by_year.items():
        for i, r in enumerate(sorted(sub, key=lambda x: -x["pred"]), 1):
            r["rank"] = i
        for pos in POSITIONS:
            for i, r in enumerate(sorted((x for x in sub if x["pos"] == pos), key=lambda x: -x["pred"]), 1):
                r["pos_rank"] = i

    def cal(sub):
        if not sub:
            return {"n": 0}
        p, a = sum(r["pred"] for r in sub), sum(r["real"] for r in sub)
        return {"n": len(sub), "valued": round(p / len(sub)), "produced": round(a / len(sub)),
                "ratio": round(a / p, 2) if p else None,
                "beat": round(sum(1 for r in sub if r["real"] > r["pred"]) / len(sub), 2),
                "mae": round(statistics.mean(abs(r["real"] - r["pred"]) for r in sub))}

    top = [r for r in rows if r["rank"] <= 150]   # the dynasty-relevant pool each offseason
    rep = {"players": len(rows), "offseasons": sorted(by_year)}
    rep["overall_top150"] = cal(top)
    rep["by_tier"] = {t: cal([r for r in rows if _tier(r["rank"]) == t]) for t in ("top 12", "13-24", "25-48", "49-96", "97+")}
    rep["by_position"] = {pos: cal([r for r in top if r["pos"] == pos]) for pos in POSITIONS}
    rep["by_age"] = {f"{pos} {band}": cal([r for r in top if r["pos"] == pos and age_band(r["age"]) == band])
                     for pos in POSITIONS for band in ("<23", "23-25", "26-28", "29-31", "32+")}
    rep["by_nfl_year"] = {f"{pos} yr{n}": cal([r for r in top if r["pos"] == pos and r["nfl_year"] == n])
                          for pos in POSITIONS for n in (0, 1, 2)}
    cors, hits = [], []
    for yr, sub in by_year.items():
        s2 = [r for r in sub if r["rank"] <= 150]
        c = spearman([r["pred"] for r in s2], [r["real"] for r in s2])
        if c is not None:
            cors.append(c)
        real_top = {r["g"] for r in sorted(sub, key=lambda x: -x["real"])[:36]}
        hits.append(sum(1 for r in sub if r["rank"] <= 36 and r["g"] in real_top) / 36)
    rep["rank_corr_top150"] = round(statistics.mean(cors), 3) if cors else None
    rep["rank_corr_by_offseason"] = {yr: round(c, 3) for yr, c in zip(by_year, cors)} if len(cors) == len(by_year) else None
    rep["top36_hit_rate"] = round(statistics.mean(hits), 3) if hits else None
    rep["abs_error_top150"] = round(statistics.mean(abs(r["real"] - r["pred"]) for r in top)) if top else None
    fmt = lambda r: {"name": r["name"], "pos": r["pos"], "as_of": r["through"], "age": r["age"],
                     "valued": round(r["pred"]), "produced": round(r["real"])}
    rep["most_overvalued"] = [fmt(r) for r in sorted(top, key=lambda r: r["real"] - r["pred"])[:12]]
    rep["most_undervalued"] = [fmt(r) for r in sorted(rows, key=lambda r: r["pred"] - r["real"])[:12]]
    return rep


def baselines(b: dict, through: int, cfg: ModelConfig) -> dict:
    """last: last season's PPG.  avg: recency-weighted PPG (actual/expected blend), no aging."""
    by = defaultdict(dict)
    for s in b["seasons"]:
        if s["season"] <= through:
            by[s["gsis_id"]][s["season"]] = s
    out = {}
    for g, ss in by.items():
        last = ss.get(through)
        num = den = 0.0
        for i, w in enumerate(cfg.recency):
            s = ss.get(through - i)
            if s and s["games"]:
                ppg = s["ppg"] if s.get("xppg") is None else cfg.actual_vs_expected * s["ppg"] + (1 - cfg.actual_vs_expected) * s["xppg"]
                num += w * s["games"] * ppg
                den += w * s["games"]
        out[g] = {"last": last["ppg"] if last and last["games"] else None, "avg": num / den if den else None}
    return out


# ---------------------------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------------------------
def spearman(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 8:
        return None

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return cov / (vx * vy) if vx and vy else None


def age_band(a):
    if a is None:
        return "?"
    return "<23" if a < 23 else "23-25" if a < 26 else "26-28" if a < 29 else "29-31" if a < 32 else "32+"


def _summ(rows: list[dict], key: str) -> dict:
    """MAE / bias / count for prediction field `key` against 'actual'."""
    rows = [r for r in rows if r.get(key) is not None]
    if not rows:
        return {"n": 0}
    err = [r[key] - r["actual"] for r in rows]
    return {"n": len(rows), "mae": round(statistics.mean(abs(e) for e in err), 2),
            "bias": round(statistics.mean(err), 2)}


def run(b: dict, cfg: ModelConfig | None = None, start: int | None = None, end: int | None = None) -> dict:
    cfg = cfg or ModelConfig()
    have = sorted({s["season"] for s in b["seasons"]})
    cur = b.get("current_season") or have[-1]
    complete = [y for y in have if y < cur]
    start = start or (complete[0] + 2)            # need two seasons of history first
    end = end or complete[-1]
    actual = {(s["gsis_id"], s["season"]): s for s in b["seasons"]}

    rows = []      # one per (player, as-of, horizon)
    value_rows = []
    for target_first in range(start, end + 1):
        through = target_first - 1
        proj = project_as_of(b, through, cfg)
        base = baselines(b, through, cfg)
        for g, p in proj.items():
            pos = p["position"]
            if pos not in POSITIONS:
                continue
            bl = base.get(g, {})
            realised = projected = 0.0
            full = True
            for h, k in ((1, "ppg_y1"), (2, "ppg_y2"), (3, "ppg_y3")):
                season = through + h
                a = actual.get((g, season))
                games = a["games"] if a else 0
                if season > end:
                    full = False
                else:
                    realised += games * max(0.0, (a["ppg"] if a else 0.0) - REPL[pos])
                    projected += 17 * cfg.availability * max(0.0, p[k] - REPL[pos])
                if season > end or not a or games < MIN_GAMES:
                    continue
                pred = p[k]
                if max(pred, a["ppg"]) < RELEVANT_PPG:
                    continue
                rows.append({
                    "g": g, "pos": pos, "h": h, "through": through, "season": season,
                    "age": (p["age"] + h) if p["age"] else None, "nfl_year": p["nfl_year"] + h,
                    "actual": a["ppg"], "model": pred,
                    "last": bl.get("last") if bl.get("last") is not None else (p["prior_ppg"] if p["nfl_year"] <= 0 else None),
                    "avg": bl.get("avg") if bl.get("avg") is not None else (p["prior_ppg"] if p["nfl_year"] <= 0 else None),
                    "name": p.get("name"),
                })
            if full and (projected > 0 or realised > 0):
                value_rows.append({"g": g, "pos": pos, "through": through, "proj": projected, "real": realised,
                                   "age": p["age"], "nfl_year": p["nfl_year"], "name": p.get("name")})

    def block(sub):
        return {k: _summ(sub, k) for k in ("model", "avg", "last")}

    report = {"config": {k: v for k, v in cfg.__dict__.items() if k != "extra"},
              "as_of": [start - 1, end - 1], "rows": len(rows)}
    report["overall"] = {f"h{h}": block([r for r in rows if r["h"] == h]) for h in (1, 2, 3)}
    report["by_position"] = {pos: {f"h{h}": block([r for r in rows if r["h"] == h and r["pos"] == pos]) for h in (1, 2)}
                             for pos in POSITIONS}
    report["by_age"] = {f"{pos} {band}": _summ([r for r in rows if r["pos"] == pos and age_band(r["age"]) == band and r["h"] <= 2], "model")
                        for pos in POSITIONS for band in ("<23", "23-25", "26-28", "29-31", "32+")}
    report["by_nfl_year"] = {f"{pos} yr{n}": _summ([r for r in rows if r["pos"] == pos and r["nfl_year"] == n and r["h"] == 1], "model")
                             for pos in POSITIONS for n in (1, 2, 3)}
    # rank quality within position, horizon 1, per season
    rk = defaultdict(list)
    for (pos, season) in {(r["pos"], r["season"]) for r in rows if r["h"] == 1}:
        sub = [r for r in rows if r["h"] == 1 and r["pos"] == pos and r["season"] == season]
        for key in ("model", "avg", "last"):
            s2 = [r for r in sub if r[key] is not None]
            c = spearman([r[key] for r in s2], [r["actual"] for r in s2])
            if c is not None:
                rk[(pos, key)].append(c)
    report["rank_h1"] = {pos: {k: round(statistics.mean(rk[(pos, k)]), 3) for k in ("model", "avg", "last") if rk[(pos, k)]}
                         for pos in POSITIONS}
    # three-season value: does the model find who produces most over the window?
    vr = {}
    for pos in POSITIONS:
        cs = []
        for through in {r["through"] for r in value_rows}:
            sub = [r for r in value_rows if r["pos"] == pos and r["through"] == through]
            sub = sorted(sub, key=lambda r: -r["proj"])[:60]   # roughly the rosterable pool
            c = spearman([r["proj"] for r in sub], [r["real"] for r in sub])
            if c is not None:
                cs.append(c)
        vr[pos] = round(statistics.mean(cs), 3) if cs else None
    report["value_rank_3yr"] = vr
    report["value"] = value_check(b, cfg, start, end)
    report["luck_floor_h1"] = luck_floor(b, [r for r in rows if r["h"] == 1])
    report["explained_h1"] = explained(b, [r for r in rows if r["h"] == 1])
    # biggest misses, horizon 1, for a sanity read
    misses = sorted((r for r in rows if r["h"] == 1), key=lambda r: -abs(r["model"] - r["actual"]))[:15]
    report["biggest_misses_h1"] = [{"name": r["name"], "pos": r["pos"], "season": r["season"], "age": r["age"] and round(r["age"], 1),
                                    "proj": round(r["model"], 1), "actual": r["actual"]} for r in misses]
    return report


def _parse_set(items: list[str], cfg: ModelConfig) -> ModelConfig:
    upd = {}
    for it in items or []:
        k, v = it.split("=", 1)
        cur = getattr(cfg, k)
        upd[k] = tuple(float(x) for x in v.split(",")) if isinstance(cur, tuple) else type(cur)(v)
    return replace(cfg, **upd)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dynasty.backtest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bundle")
    b.add_argument("db")
    b.add_argument("out")
    r = sub.add_parser("run")
    r.add_argument("bundle")
    r.add_argument("--json")
    r.add_argument("--set", nargs="*", default=[])
    a = ap.parse_args(argv)
    if a.cmd == "bundle":
        from .store import Store
        bun = make_bundle(Store(a.db))
        save_bundle(bun, a.out)
        print(f"bundle: {len(bun['seasons'])} player-seasons, {len(bun['picks'])} draft picks, "
              f"{len(bun['birth'])} birthdates, seasons {min(s['season'] for s in bun['seasons'])}-"
              f"{max(s['season'] for s in bun['seasons'])}")
        return 0
    rep = run(load_bundle(a.bundle), _parse_set(a.set, ModelConfig()))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(rep, f, indent=1)
    o = rep["overall"]
    print(f"as of {rep['as_of'][0]}..{rep['as_of'][1]}  rows {rep['rows']}")
    for h in ("h1", "h2", "h3"):
        print(f"  {h}: " + "  ".join(f"{k} MAE {v.get('mae')} bias {v.get('bias')} (n {v['n']})" for k, v in o[h].items()))
    print(f"  luck floor (h1): {rep.get('luck_floor_h1')}  explained (R^2): {rep.get('explained_h1')}")
    print("  rank (h1, Spearman):", rep["rank_h1"])
    print("  3-season value rank:", rep["value_rank_3yr"])
    v = rep["value"]
    print(f"  value check ({len(v['offseasons'])} offseasons, top 150): {v['overall_top150']}  "
          f"rank corr {v['rank_corr_top150']}  top-36 hit rate {v['top36_hit_rate']}")
    print("  by tier:", {k: (x.get("ratio"), x.get("beat")) for k, x in v["by_tier"].items()})
    print("  by position:", {k: (x.get("ratio"), x.get("beat")) for k, x in v["by_position"].items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())

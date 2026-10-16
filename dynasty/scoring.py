"""Scores nflverse weekly stats with this league's Sleeper scoring_settings.

Each Sleeper scoring key maps to a function of an nflverse stat row. Column names have
changed across nflverse versions, so each stat lists candidate columns and uses the
first one present. Keys we can't reproduce (IDP, team defense, some kicker extras) are
reported by `unmapped_keys` so the gap is visible instead of silently ignored.
"""

from __future__ import annotations

from typing import Callable

# stat name -> candidate nflverse columns (first present wins)
COLS: dict[str, list[str]] = {
    "pass_yd": ["passing_yards"],
    "pass_td": ["passing_tds"],
    "pass_int": ["passing_interceptions", "interceptions"],
    "pass_2pt": ["passing_2pt_conversions"],
    "pass_cmp": ["completions"],
    "pass_att": ["attempts"],
    "pass_sack": ["sacks_suffered", "sacks"],
    "pass_fd": ["passing_first_downs"],
    "rush_yd": ["rushing_yards"],
    "rush_td": ["rushing_tds"],
    "rush_2pt": ["rushing_2pt_conversions"],
    "rush_att": ["carries"],
    "rush_fd": ["rushing_first_downs"],
    "rec": ["receptions"],
    "rec_yd": ["receiving_yards"],
    "rec_td": ["receiving_tds"],
    "rec_2pt": ["receiving_2pt_conversions"],
    "rec_tgt": ["targets"],
    "rec_fd": ["receiving_first_downs"],
    "st_td": ["special_teams_tds"],
    "sack_fum_lost": ["sack_fumbles_lost"],
    "rush_fum_lost": ["rushing_fumbles_lost"],
    "rec_fum_lost": ["receiving_fumbles_lost"],
    "sack_fum": ["sack_fumbles"],
    "rush_fum": ["rushing_fumbles"],
    "rec_fum": ["receiving_fumbles"],
    "fum_rec_td": ["fumble_recovery_tds"],
    "pr_yd": ["punt_return_yards"],
    "kr_yd": ["kickoff_return_yards"],
    # kicking
    "fgm_0_19": ["fg_made_0_19"],
    "fgm_20_29": ["fg_made_20_29"],
    "fgm_30_39": ["fg_made_30_39"],
    "fgm_40_49": ["fg_made_40_49"],
    "fgm_50_59": ["fg_made_50_59"],
    "fgm_60p": ["fg_made_60_", "fg_made_60"],
    "fgmiss_0_19": ["fg_missed_0_19"],
    "fgmiss_20_29": ["fg_missed_20_29"],
    "fgmiss_30_39": ["fg_missed_30_39"],
    "fgmiss_40_49": ["fg_missed_40_49"],
    "fgmiss_50_59": ["fg_missed_50_59"],
    "fgmiss_60p": ["fg_missed_60_", "fg_missed_60"],
    "fgm": ["fg_made"],
    "fgmiss": ["fg_missed"],
    "xpm": ["pat_made"],
    "xpmiss": ["pat_missed"],
}


def resolve_columns(columns) -> dict[str, str]:
    """stat name -> actual column present in the data."""
    have = set(columns)
    out = {}
    for stat, cands in COLS.items():
        for c in cands:
            if c in have:
                out[stat] = c
                break
    return out


def _v(row: dict, colmap: dict[str, str], stat: str) -> float:
    c = colmap.get(stat)
    if not c:
        return 0.0
    v = row.get(c)
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _bonus(stat: str, lo: float, hi: float | None = None) -> Callable:
    def f(row, cm, pos):
        x = _v(row, cm, stat)
        return 1.0 if x >= lo and (hi is None or x < hi) else 0.0
    return f


def _pos_rec(pos_want: str) -> Callable:
    return lambda row, cm, pos: _v(row, cm, "rec") if pos == pos_want else 0.0


def _stat(stat: str) -> Callable:
    return lambda row, cm, pos: _v(row, cm, stat)


# Sleeper scoring key -> f(row, colmap, position) giving the count the points multiply.
KEY_FUNCS: dict[str, tuple[Callable, list[str]]] = {
    # key: (function, stats it needs — used to decide if the key is reproducible)
    "pass_yd": (_stat("pass_yd"), ["pass_yd"]),
    "pass_td": (_stat("pass_td"), ["pass_td"]),
    "pass_int": (_stat("pass_int"), ["pass_int"]),
    "pass_2pt": (_stat("pass_2pt"), ["pass_2pt"]),
    "pass_cmp": (_stat("pass_cmp"), ["pass_cmp"]),
    "pass_att": (_stat("pass_att"), ["pass_att"]),
    "pass_inc": (lambda r, cm, p: _v(r, cm, "pass_att") - _v(r, cm, "pass_cmp"), ["pass_att", "pass_cmp"]),
    "pass_sack": (_stat("pass_sack"), ["pass_sack"]),
    "pass_fd": (_stat("pass_fd"), ["pass_fd"]),
    "rush_yd": (_stat("rush_yd"), ["rush_yd"]),
    "rush_td": (_stat("rush_td"), ["rush_td"]),
    "rush_2pt": (_stat("rush_2pt"), ["rush_2pt"]),
    "rush_att": (_stat("rush_att"), ["rush_att"]),
    "rush_fd": (_stat("rush_fd"), ["rush_fd"]),
    "rec": (_stat("rec"), ["rec"]),
    "rec_yd": (_stat("rec_yd"), ["rec_yd"]),
    "rec_td": (_stat("rec_td"), ["rec_td"]),
    "rec_2pt": (_stat("rec_2pt"), ["rec_2pt"]),
    "rec_tgt": (_stat("rec_tgt"), ["rec_tgt"]),
    "rec_fd": (_stat("rec_fd"), ["rec_fd"]),
    "bonus_rec_te": (_pos_rec("TE"), ["rec"]),
    "bonus_rec_rb": (_pos_rec("RB"), ["rec"]),
    "bonus_rec_wr": (_pos_rec("WR"), ["rec"]),
    "bonus_pass_yd_300": (_bonus("pass_yd", 300, 400), ["pass_yd"]),
    "bonus_pass_yd_400": (_bonus("pass_yd", 400), ["pass_yd"]),
    "bonus_rush_yd_100": (_bonus("rush_yd", 100, 200), ["rush_yd"]),
    "bonus_rush_yd_200": (_bonus("rush_yd", 200), ["rush_yd"]),
    "bonus_rec_yd_100": (_bonus("rec_yd", 100, 200), ["rec_yd"]),
    "bonus_rec_yd_200": (_bonus("rec_yd", 200), ["rec_yd"]),
    "bonus_rush_att_20": (_bonus("rush_att", 20), ["rush_att"]),
    "bonus_pass_cmp_25": (_bonus("pass_cmp", 25), ["pass_cmp"]),
    "st_td": (_stat("st_td"), ["st_td"]),
    "fum_lost": (lambda r, cm, p: _v(r, cm, "sack_fum_lost") + _v(r, cm, "rush_fum_lost") + _v(r, cm, "rec_fum_lost"),
                 ["rush_fum_lost"]),
    "fum": (lambda r, cm, p: _v(r, cm, "sack_fum") + _v(r, cm, "rush_fum") + _v(r, cm, "rec_fum"), ["rush_fum"]),
    "fum_rec_td": (_stat("fum_rec_td"), ["fum_rec_td"]),
    "pr_yd": (_stat("pr_yd"), ["pr_yd"]),
    "kr_yd": (_stat("kr_yd"), ["kr_yd"]),
    "fgm_0_19": (_stat("fgm_0_19"), ["fgm_0_19"]),
    "fgm_20_29": (_stat("fgm_20_29"), ["fgm_20_29"]),
    "fgm_30_39": (_stat("fgm_30_39"), ["fgm_30_39"]),
    "fgm_40_49": (_stat("fgm_40_49"), ["fgm_40_49"]),
    "fgm_50_59": (_stat("fgm_50_59"), ["fgm_50_59"]),
    "fgm_60p": (_stat("fgm_60p"), ["fgm_60p"]),
    "fgm_50p": (lambda r, cm, p: _v(r, cm, "fgm_50_59") + _v(r, cm, "fgm_60p"), ["fgm_50_59"]),
    "fgmiss_0_19": (_stat("fgmiss_0_19"), ["fgmiss_0_19"]),
    "fgmiss_20_29": (_stat("fgmiss_20_29"), ["fgmiss_20_29"]),
    "fgmiss_30_39": (_stat("fgmiss_30_39"), ["fgmiss_30_39"]),
    "fgmiss_40_49": (_stat("fgmiss_40_49"), ["fgmiss_40_49"]),
    "fgmiss_50_59": (_stat("fgmiss_50_59"), ["fgmiss_50_59"]),
    "fgmiss_60p": (_stat("fgmiss_60p"), ["fgmiss_60p"]),
    "fgmiss_50p": (lambda r, cm, p: _v(r, cm, "fgmiss_50_59") + _v(r, cm, "fgmiss_60p"), ["fgmiss_50_59"]),
    "fgm": (_stat("fgm"), ["fgm"]),
    "fgmiss": (_stat("fgmiss"), ["fgmiss"]),
    "xpm": (_stat("xpm"), ["xpm"]),
    "xpmiss": (_stat("xpmiss"), ["xpmiss"]),
}

# Keys that only apply to team defenses or IDP: not player stats, so never "missing".
DEFENSE_PREFIXES = ("pts_allow", "yds_allow", "def_", "idp_", "blk_kick", "tkl", "qb_hit",
                    "bonus_def", "bonus_sack", "bonus_tkl", "fum_ret", "int_ret", "def_st")
DEFENSE_KEYS = {"sack", "int", "ff", "fum_rec", "safe", "st_fum_rec", "st_ff", "st_tkl_solo",
                "def_td", "pts_allow", "yds_allow", "sack_yd", "fg_ret_yd", "blk_kick_ret_yd"}


def is_defense_key(key: str) -> bool:
    return key in DEFENSE_KEYS or key.startswith(DEFENSE_PREFIXES)


def unmapped_keys(scoring: dict, columns) -> list[str]:
    """Non-zero league scoring keys for players that we can't compute from these columns."""
    colmap = resolve_columns(columns)
    out = []
    for key, pts in scoring.items():
        if not pts or is_defense_key(key):
            continue
        spec = KEY_FUNCS.get(key)
        if spec is None or not all(s in colmap for s in spec[1]):
            out.append(key)
    return sorted(out)


def make_scorer(scoring: dict, columns):
    """Returns score(row, position) -> league fantasy points."""
    colmap = resolve_columns(columns)
    active = [
        (KEY_FUNCS[k][0], float(v))
        for k, v in scoring.items()
        if v and k in KEY_FUNCS and not is_defense_key(k)
    ]

    def score(row: dict, position: str | None = None) -> float:
        return round(sum(f(row, colmap, position) * pts for f, pts in active), 2)

    return score

"""Loads config.yaml, with environment-variable overrides for the cloud runner."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    league_id: str
    username: str
    db_path: Path = ROOT / "data" / "dynasty.db"
    cache_dir: Path = ROOT / "data" / "cache"
    site_dir: Path = ROOT / "site"
    # How many past NFL seasons of nflverse stats to keep (current season is always added).
    nfl_seasons_back: int = 5
    # Pull every owner's other Sleeper leagues (cross-league tendencies).
    owner_leagues: bool = True
    # Optional nflverse loaders that are large or slow; turn off if a run gets too long.
    nflverse_extras: list[str] = field(
        default_factory=lambda: ["nextgen", "pfr_advstats", "depth_charts", "injuries"]
    )
    # Blend weights for dynasty value: projection model vs market sources.
    value_weights: dict = field(
        default_factory=lambda: {"model": 0.6, "fantasycalc": 0.3, "dynastyprocess": 0.1}
    )


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else ROOT / "config.yaml"
    data: dict = {}
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}

    league_id = os.environ.get("SLEEPER_LEAGUE_ID") or str(data.get("league_id", "")).strip()
    username = os.environ.get("SLEEPER_USERNAME") or str(data.get("username", "")).strip()
    if not league_id or not username:
        raise SystemExit(
            "Missing league_id or username. Copy config.example.yaml to config.yaml and fill it in."
        )

    cfg = Config(league_id=league_id, username=username)
    for key in ("nfl_seasons_back", "owner_leagues", "nflverse_extras", "value_weights"):
        if key in data:
            setattr(cfg, key, data[key])
    for key in ("db_path", "cache_dir", "site_dir"):
        if key in data:
            setattr(cfg, key, (ROOT / data[key]).resolve())
    return cfg

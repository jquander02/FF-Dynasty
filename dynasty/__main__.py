"""Command line:  python -m dynasty refresh [--quick] [--no-nflverse]
                python -m dynasty export
                python -m dynasty status
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from . import export, idmap, ingest_sleeper, market, model, nflverse, picks, production, teams, values
from .config import load_config
from .sleeper import SleeperClient
from .store import Store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dynasty")
    ap.add_argument("command", choices=["refresh", "export", "status"])
    ap.add_argument("--config", help="path to config.yaml")
    ap.add_argument("--quick", action="store_true",
                    help="league data only: skip the player file, owners' other leagues and nflverse")
    ap.add_argument("--no-nflverse", action="store_true", help="skip nflverse stats")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = load_config(args.config)
    store = Store(cfg.db_path)
    started = time.time()

    try:
        if args.command == "status":
            for (k,) in [("league_season",), ("roster",), ("txn",), ("draft_pick_made",), ("player",),
                         ("player_xref",), ("owner_league",), ("ext_draft_pick",)]:
                if not store.query("SELECT 1 FROM sqlite_master WHERE name=?", (k,)):
                    continue
                n = store.query(f"SELECT COUNT(*) c FROM {k}")[0]["c"]
                print(f"{k:18} {n:>8}")
            for k in ("market_value", "player_week", "player_season", "player_projection", "player_value",
                      "team_window", "season_sim", "pick_value", "schedule"):
                if store.query("SELECT 1 FROM sqlite_master WHERE name=?", (k,)):
                    print(f"{k:18} {store.query(f'SELECT COUNT(*) c FROM {k}')[0]['c']:>8}")
            nfl = store.query("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'nfl_%'")
            for r in nfl:
                n = store.query(f"SELECT COUNT(*) c FROM {r['name']}")[0]["c"]
                print(f"{r['name']:18} {n:>8}")
            return 0

        if args.command == "refresh":
            if args.quick and not _has_values(store):
                print("No player values in the database yet (new or lost cache): running a full refresh.")
                args.quick = False
            client = SleeperClient(cache_dir=cfg.cache_dir)
            res = ingest_sleeper.run(
                store, client, cfg.league_id, cfg.username,
                owner_leagues=cfg.owner_leagues and not args.quick,
                players=not args.quick,
            )
            print(f"Sleeper: league {res.current_league_id}, seasons {', '.join(res.seasons)}, "
                  f"my roster {res.my_roster_id}, {client.calls} API calls")
            for k, v in res.counts.items():
                print(f"  {k:16} {v}")
            if not (args.quick or args.no_nflverse):
                counts = nflverse.run(store, int(res.season), cfg.nfl_seasons_back, cfg.nflverse_extras)
                print("nflverse:")
                for k, v in counts.items():
                    print(f"  {k:24} {'FAILED' if v < 0 else v}")
            if not args.quick:
                m = idmap.build(store)
                print("ID mapping: " + ", ".join(f"{k} {v}" for k, v in m.items()))
                lg = store.query("SELECT total_rosters, scoring_settings FROM league_season WHERE is_current=1")[0]
                ppr = json.loads(lg["scoring_settings"] or "{}").get("rec", 1.0)
                mk = market.run(store, teams=lg["total_rosters"] or 12, ppr=ppr)
                print("Market values: " + ", ".join(f"{k} {'FAILED' if v < 0 else v}" for k, v in mk.items()))
                pr = production.build(store)
                print("Production: " + ", ".join(f"{k} {v}" for k, v in pr.items()))
                mo = model.build(store)
                print("Model: " + ", ".join(f"{k} {v}" for k, v in mo.items()))
                va = values.build(store, cfg.value_weights)
                print("Values: " + ", ".join(f"{k} {v}" for k, v in va.items()))
            # Team windows, season simulation and pick values: cheap, so quick runs redo them too
            if store.query("SELECT 1 FROM sqlite_master WHERE name='player_value'"):
                tw = teams.build(store)
                print(f"Teams: {len(tw)} windows")
                pk = picks.build(store, cfg.value_weights)
                print("Picks: " + ", ".join(f"{k} {v}" for k, v in pk.items()))

        if args.command == "refresh" and not args.quick:
            store.commit()
            store.conn.execute("VACUUM")
        out = export.run(store, cfg.site_dir)
        print("Export: " + ", ".join(f"{k} {v}" for k, v in out.items()))
        print(f"Done in {time.time() - started:.0f}s")
        return 0
    finally:
        store.close()


def _has_values(store: Store) -> bool:
    if not store.query("SELECT 1 FROM sqlite_master WHERE name='player_value'"):
        return False
    return store.query("SELECT COUNT(*) c FROM player_value")[0]["c"] > 0


if __name__ == "__main__":
    sys.exit(main())

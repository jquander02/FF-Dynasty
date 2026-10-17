"""League-mate profiles, built from every transaction and draft this league has, plus each
manager's drafts in their other Sleeper leagues.

Per manager:
  trading      trades made, partners, hindsight result (what each side holds now, at
               today's values), positions they trade for, picks bought vs sold
  waivers      claims, free-agent adds, FAAB spent and biggest bid
  drafting     position mix in early rounds (rookie drafts here, all drafts elsewhere),
               compared with the league as a whole
  tags         short labels for what stands out
Also trade_log(): every completed trade with each side valued at today's values, used draft
picks shown as the player taken with them (exported to the app's Past trades view).

Writes owner_profile, trade_review.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict

from .store import Store

SKILL = ("QB", "RB", "WR", "TE")


SLOT_WINDOW = 3  # picks either side used to value a draft slot


def _pick_player_map(store: Store, val: dict | None = None) -> dict:
    """(season, round, original roster) of a used pick -> {player_id, pick, by_roster, league_id,
    slot_value} for every completed draft.

    The original roster comes from the draft's slot_to_roster_id, or from draft_order (user -> slot)
    when Sleeper leaves that out. Rookie drafts win over startup drafts for the same key.
    by_roster is the roster that made the pick. slot_value is what that draft slot turned out to be
    worth: today's average value of the players taken within SLOT_WINDOW picks of it in that draft,
    for teams that traded the pick on before the draft (the player taken wasn't their choice)."""
    val = val or {}
    owner_of = {(r["league_id"], r["owner_id"]): r["roster_id"]
                for r in store.query("SELECT league_id, roster_id, owner_id FROM roster")}
    out = {}
    drafts = store.query("SELECT * FROM draft WHERE status='complete' ORDER BY (player_type=1)")
    for d in drafts:  # startup drafts first, so rookie drafts overwrite on a clash
        s2r = {int(k): int(v) for k, v in json.loads(d["slot_to_roster_id"] or "{}").items() if v is not None}
        if not s2r:
            for uid, slot in json.loads(d["draft_order"] or "{}").items():
                rid = owner_of.get((d["league_id"], uid))
                if rid is not None:
                    s2r[int(slot)] = rid
        if not s2r:
            continue
        n = len(s2r)
        made = store.query("SELECT pick_no, round, draft_slot, roster_id, player_id FROM draft_pick_made "
                           "WHERE draft_id=?", (d["draft_id"],))
        by_no = {p["pick_no"]: val.get(p["player_id"], 0.0) for p in made if p["player_id"]}
        for p in made:
            orig = s2r.get(p["draft_slot"])
            if orig is None or not p["player_id"]:
                continue
            in_round = p["pick_no"] - (p["round"] - 1) * n
            near = [v for k, v in by_no.items() if abs(k - p["pick_no"]) <= SLOT_WINDOW]
            out[(str(d["season"]), p["round"], orig)] = {
                "player_id": p["player_id"], "pick": f"{p['round']}.{in_round:02d}", "pick_no": p["pick_no"],
                "by_roster": p["roster_id"], "league_id": d["league_id"],
                "slot_value": sum(near) / len(near) if near else 0.0}
    return out


def _package(values: list[float]) -> float:
    from .trade import package_value
    return package_value(values)


def trade_log(store: Store) -> list[dict]:
    """Every completed trade in this league's history, newest first, valued at today's values.

    Each side lists what it received. Players carry today's value and where they are now. Picks
    from completed drafts carry the player taken with them (and that player's value now); future
    picks carry their projected slot and pick value. Sides are scored with the trade lab's
    package rule, (sum of v^1.3)^(1/1.3), and a trade is even when every side's net is within 10%
    of the bigger package."""
    lg = store.query("SELECT * FROM league_season WHERE is_current=1")[0]
    cur = lg["league_id"]
    leagues = {r["league_id"]: r["season"] for r in store.query("SELECT league_id, season FROM league_season")}
    owner_of = {(r["league_id"], r["roster_id"]): r["owner_id"]
                for r in store.query("SELECT league_id, roster_id, owner_id FROM roster")}
    cur_rid = {r["owner_id"]: r["roster_id"] for r in store.query(
        "SELECT roster_id, owner_id FROM roster WHERE league_id=? AND owner_id IS NOT NULL", (cur,))}
    names = {}
    for r in store.query("""SELECT o.user_id, o.display_name, m.team_name FROM owner o
                            LEFT JOIN league_member m ON m.user_id=o.user_id AND m.league_id=?""", (cur,)):
        names[r["user_id"]] = {"owner": r["display_name"], "team": (r["team_name"] or r["display_name"] or "").strip()}

    vd = store.query("SELECT MAX(snapshot_date) d FROM player_value")[0]["d"]
    val = {r["player_id"]: r["value"] or 0.0 for r in store.query(
        "SELECT player_id, value FROM player_value WHERE snapshot_date=?", (vd,))}
    info = {r["player_id"]: r for r in store.query("SELECT player_id, full_name, position, team FROM player")}
    future = {(str(r["season"]), r["round"], r["original_roster_id"]): r for r in store.query("SELECT * FROM pick_value")}
    used = _pick_player_map(store, val)
    snap = store.query("SELECT MAX(snapshot_date) d FROM roster_snapshot WHERE league_id=?", (cur,))[0]["d"]
    now_on = {r["player_id"]: r["roster_id"] for r in store.query(
        "SELECT roster_id, player_id FROM roster_snapshot WHERE league_id=? AND snapshot_date=?", (cur, snap))}

    def player(pid):
        i = info.get(pid) or {}
        return {"id": pid, "name": i["full_name"] if i else f"Player {pid}", "pos": i["position"] if i else None,
                "nfl": i["team"] if i else None, "value": round(val.get(pid, 0.0)), "now_on": now_on.get(pid)}

    def team_of(lid, rid):
        uid = owner_of.get((lid, rid))
        return uid, cur_rid.get(uid), names.get(uid, {})

    out = []
    for t in store.query("SELECT * FROM txn WHERE status='complete' AND type='trade' "
                         "ORDER BY COALESCE(status_updated, created) DESC"):
        lid = t["league_id"]
        rids = json.loads(t["roster_ids"] or "[]")
        moves = store.query("SELECT * FROM txn_move WHERE transaction_id=? ORDER BY seq", (t["transaction_id"],))
        for m in moves:  # include any roster that only appears in the moves
            for r in (m["roster_id"], m["from_roster_id"]):
                if r is not None and r not in rids and m["kind"] != "drop":
                    rids.append(r)
        idx = {rid: i for i, rid in enumerate(rids)}
        sides = []
        for rid in rids:
            uid, crid, nm = team_of(lid, rid)
            sides.append({"roster_id": crid, "user_id": uid, "team": nm.get("team") or f"Team {rid}",
                          "owner": nm.get("owner"), "left_league": crid is None, "got": []})
        for m in moves:
            if m["kind"] == "drop" or m["roster_id"] not in idx:
                continue
            frm = idx.get(m["from_roster_id"])
            if m["kind"] == "add":
                a = {"k": "player", "from": frm, **player(m["player_id"])}
            elif m["kind"] == "pick":
                season, rnd, orig = str(m["pick_season"]), m["pick_round"], m["pick_original_roster"]
                _, orig_cur, orig_nm = team_of(lid, orig)
                a = {"k": "pick", "from": frm, "season": season, "round": rnd,
                     "orig": orig_cur, "orig_team": orig_nm.get("team")}
                hit = used.get((season, rnd, orig))
                fut = future.get((season, rnd, orig_cur if orig_cur is not None else orig))
                if hit and hit.get("by_roster") is not None and hit["by_roster"] != m["roster_id"]:
                    # traded on before the draft: another team chose the player, so this team is
                    # credited with what the slot was worth, not with that pick's hit or miss
                    _, by_cur, by_nm = team_of(hit["league_id"], hit["by_roster"])
                    a.update(status="flipped", pick=hit["pick"], became=player(hit["player_id"]),
                             drafted_by=by_cur, drafted_by_team=by_nm.get("team"),
                             value=round(hit["slot_value"]))
                elif hit:
                    b = player(hit["player_id"])
                    a.update(status="used", pick=hit["pick"], became=b, value=b["value"])
                elif fut:
                    a.update(status="future", exp_slot=fut["exp_slot"], value=round(fut["value"] or 0))
                else:
                    a.update(status="unknown", value=0)
            elif m["kind"] == "faab":
                a = {"k": "faab", "from": frm, "amount": m["amount"], "value": 0}
            else:
                continue
            sides[idx[m["roster_id"]]]["got"].append(a)
        for i, sd in enumerate(sides):
            got = [a["value"] for a in sd["got"] if a["k"] != "faab"]
            gave = [a["value"] for o in sides for a in o["got"] if a.get("from") == i and a["k"] != "faab"]
            sd["got_value"] = round(_package(got))
            sd["got_raw"] = round(sum(got))
            sd["gave_value"] = round(_package(gave))
            sd["net"] = sd["got_value"] - sd["gave_value"]
        big = max([sd["got_value"] for sd in sides] + [1])
        even = all(abs(sd["net"]) <= 0.10 * big for sd in sides)
        best = max(range(len(sides)), key=lambda i: sides[i]["net"]) if sides else None
        out.append({
            "id": t["transaction_id"], "season": leagues.get(lid), "week": t["week"],
            "ts": t["status_updated"] or t["created"], "sides": sides,
            "even": even, "winner": None if even or best is None else best,
            "margin": 0 if best is None else sides[best]["net"],
        })
    return out


def _scouting(store: Store, log: list[dict], owner_of: dict, names: dict) -> dict:
    """Per-manager scouting data: trade record and tendencies, activity, lineup efficiency,
    season finishes and rookie-draft results. Keyed by user id."""
    from datetime import datetime, timezone
    out = defaultdict(lambda: {"tr": {"won": 0, "lost": 0, "even": 0, "margins": [], "best": None, "worst": None,
                                      "got_pos": Counter(), "gave_pos": Counter(), "got_age": [], "gave_age": [],
                                      "got_picks": 0, "gave_picks": 0, "got_firsts": 0, "gave_firsts": 0,
                                      "pieces_got": [], "pieces_gave": [], "in_season": 0, "offseason": 0,
                                      "vs": defaultdict(lambda: [0, 0.0])}})
    age = {r["player_id"]: r["age"] for r in store.query("SELECT player_id, age FROM player_projection") if r["age"]}
    for r in store.query("SELECT player_id, age FROM player WHERE age IS NOT NULL"):
        age.setdefault(r["player_id"], r["age"])
    for tr in log:
        when = datetime.fromtimestamp((tr["ts"] or 0) / 1000, tz=timezone.utc) if tr.get("ts") else None
        in_season = bool(when and (when.month >= 9 or when.month == 1))
        for i, sd in enumerate(tr["sides"]):
            uid = sd["user_id"]
            if not uid:
                continue
            t = out[uid]["tr"]
            res = "even" if tr["even"] else ("won" if sd["net"] > 0 else "lost")
            t[res] += 1
            t["margins"].append(sd["net"])
            if t["best"] is None or sd["net"] > t["best"][1]:
                t["best"] = (tr["id"], sd["net"])
            if t["worst"] is None or sd["net"] < t["worst"][1]:
                t["worst"] = (tr["id"], sd["net"])
            gave = [a for o in tr["sides"] for a in o["got"] if a.get("from") == i]
            for lst, key_pos, key_age, key_pk, key_1st in ((sd["got"], "got_pos", "got_age", "got_picks", "got_firsts"),
                                                           (gave, "gave_pos", "gave_age", "gave_picks", "gave_firsts")):
                for a in lst:
                    if a["k"] == "player":
                        if a.get("pos") in SKILL:
                            t[key_pos][a["pos"]] += 1
                        if age.get(a["id"]):
                            t[key_age].append(age[a["id"]])
                    elif a["k"] == "pick":
                        t[key_pk] += 1
                        if a.get("round") == 1:
                            t[key_1st] += 1
            t["pieces_got"].append(sum(1 for a in sd["got"] if a["k"] != "faab"))
            t["pieces_gave"].append(sum(1 for a in gave if a["k"] != "faab"))
            t["in_season" if in_season else "offseason"] += 1
            for j, o in enumerate(tr["sides"]):
                if j != i and o["user_id"]:
                    t["vs"][o["user_id"]][0] += 1
                    t["vs"][o["user_id"]][1] += sd["net"]

    # activity: latest transaction of any kind, and moves in the last 30 days
    now_ms = datetime.now(tz=timezone.utc).timestamp() * 1000
    act = defaultdict(lambda: {"last": 0, "last_type": None, "recent": 0})
    for r in store.query("SELECT league_id, type, roster_ids, created, status_updated FROM txn WHERE status='complete'"):
        ts = r["status_updated"] or r["created"] or 0
        for rid in json.loads(r["roster_ids"] or "[]"):
            uid = owner_of.get((r["league_id"], rid))
            if not uid:
                continue
            a = act[uid]
            if ts > a["last"]:
                a["last"], a["last_type"] = ts, r["type"]
            if now_ms - ts < 30 * 86400 * 1000:
                a["recent"] += 1

    # seasons: record, points, lineup efficiency (points / best possible), regular-season rank, playoff place
    from .picks import bracket_places
    seasons = defaultdict(list)
    for lg in store.query("SELECT league_id, season, status FROM league_season ORDER BY season"):
        rows = store.query("SELECT roster_id, owner_id, wins, losses, ties, fpts, max_pf FROM roster WHERE league_id=?",
                           (lg["league_id"],))
        if not rows:
            continue
        order = sorted(rows, key=lambda r: (-(r["wins"] or 0), -(r["fpts"] or 0)))
        rank = {r["roster_id"]: i for i, r in enumerate(order, 1)}
        places = bracket_places(store, lg["league_id"]) if lg["status"] == "complete" else {}
        effs = [(r["fpts"] or 0) / r["max_pf"] for r in rows if r["max_pf"]]
        league_eff = sum(effs) / len(effs) if effs else None
        for r in rows:
            if not r["owner_id"]:
                continue
            seasons[r["owner_id"]].append({
                "season": lg["season"], "wins": r["wins"] or 0, "losses": r["losses"] or 0, "ties": r["ties"] or 0,
                "pf": round(r["fpts"] or 0, 1), "rank": rank[r["roster_id"]], "place": places.get(r["roster_id"]),
                "eff": round((r["fpts"] or 0) / r["max_pf"], 3) if r["max_pf"] else None,
                "league_eff": round(league_eff, 3) if league_eff else None, "complete": lg["status"] == "complete"})

    # rookie drafts in this league: what each manager's picks are worth now
    vd = store.query("SELECT MAX(snapshot_date) d FROM player_value")[0]["d"]
    val = {r["player_id"]: r["value"] or 0.0 for r in store.query(
        "SELECT player_id, value FROM player_value WHERE snapshot_date=?", (vd,))}
    pname = {r["player_id"]: r["full_name"] for r in store.query("SELECT player_id, full_name FROM player")}
    rookie = defaultdict(list)
    by_round = defaultdict(list)
    for d in store.query("SELECT draft_id, season FROM draft WHERE player_type=1 AND status='complete'"):
        for p in store.query("SELECT pick_no, round, picked_by, player_id FROM draft_pick_made WHERE draft_id=? ORDER BY pick_no",
                             (d["draft_id"],)):
            v = val.get(p["player_id"], 0.0)
            by_round[p["round"]].append(v)
            if p["picked_by"]:
                rookie[p["picked_by"]].append({"season": d["season"], "round": p["round"], "pick_no": p["pick_no"],
                                               "id": p["player_id"], "name": pname.get(p["player_id"]),
                                               "value": round(v)})
    round_avg = {r: sum(v) / len(v) for r, v in by_round.items() if v}

    result = {}
    uids = set(out) | set(act) | set(seasons) | set(rookie)
    for uid in uids:
        t = out[uid]["tr"]
        n = t["won"] + t["lost"] + t["even"]
        picks_made = rookie.get(uid, [])
        exp = sum(round_avg.get(p["round"], 0) for p in picks_made)
        got = sum(p["value"] for p in picks_made)
        result[uid] = {
            "trade_record": {"won": t["won"], "lost": t["lost"], "even": t["even"]},
            "avg_margin": round(sum(t["margins"]) / n) if n else None,
            "best_trade": t["best"][0] if t["best"] else None, "best_margin": t["best"][1] if t["best"] else None,
            "worst_trade": t["worst"][0] if t["worst"] else None, "worst_margin": t["worst"][1] if t["worst"] else None,
            "got_pos": dict(t["got_pos"]), "gave_pos": dict(t["gave_pos"]),
            "got_age": round(sum(t["got_age"]) / len(t["got_age"]), 1) if t["got_age"] else None,
            "gave_age": round(sum(t["gave_age"]) / len(t["gave_age"]), 1) if t["gave_age"] else None,
            "picks": {"got": t["got_picks"], "gave": t["gave_picks"], "firsts_got": t["got_firsts"], "firsts_gave": t["gave_firsts"]},
            "pieces": {"got": round(sum(t["pieces_got"]) / n, 2) if n else None,
                       "gave": round(sum(t["pieces_gave"]) / n, 2) if n else None},
            "timing": {"in_season": t["in_season"], "offseason": t["offseason"]},
            "vs": sorted(({"team": names.get(o, {}).get("team"), "owner": names.get(o, {}).get("owner"), "n": c,
                           "net": round(net)} for o, (c, net) in t["vs"].items()), key=lambda x: -x["n"]),
            "last_move": act[uid]["last"] or None, "last_move_type": act[uid]["last_type"],
            "moves_30d": act[uid]["recent"],
            "seasons": seasons.get(uid, []),
            "rookie_picks": sorted(picks_made, key=lambda p: (p["season"], p["pick_no"]), reverse=True)[:12],
            "rookie_score": round(got / exp, 2) if exp else None,
        }
    return result


def build(store: Store) -> dict:
    lg = store.query("SELECT * FROM league_season WHERE is_current=1")[0]
    cur = lg["league_id"]
    owner_of = {(r["league_id"], r["roster_id"]): r["owner_id"] for r in store.query("SELECT league_id, roster_id, owner_id FROM roster")}
    names = {}
    for r in store.query("""SELECT o.user_id, o.display_name, m.team_name FROM owner o
                            LEFT JOIN league_member m ON m.user_id=o.user_id AND m.league_id=?""", (cur,)):
        names[r["user_id"]] = {"owner": r["display_name"], "team": (r["team_name"] or r["display_name"] or "").strip()}
    current_owners = [r["owner_id"] for r in store.query("SELECT owner_id FROM roster WHERE league_id=? AND owner_id IS NOT NULL", (cur,))]
    roster_of_owner = {r["owner_id"]: r["roster_id"] for r in store.query("SELECT roster_id, owner_id FROM roster WHERE league_id=?", (cur,))}

    prof = defaultdict(lambda: {"trades": 0, "partners": Counter(), "net": 0.0, "acq_pos": Counter(),
                                "picks_in": 0, "picks_out": 0, "waiver_adds": 0, "fa_adds": 0,
                                "faab": 0, "max_bid": 0, "moves": 0})
    log = trade_log(store)
    for tr in log:
        for i, sd in enumerate(tr["sides"]):
            uid = sd["user_id"]
            if not uid:
                continue
            p = prof[uid]
            p["trades"] += 1
            p["net"] += sd["net"]
            for j, o in enumerate(tr["sides"]):
                if j != i and o["user_id"]:
                    p["partners"][o["user_id"]] += 1
                p["picks_out"] += sum(1 for a in o["got"] if a["k"] == "pick" and a.get("from") == i)
            for a in sd["got"]:
                if a["k"] == "pick":
                    p["picks_in"] += 1
                elif a["k"] == "player" and a.get("pos") in SKILL:
                    p["acq_pos"][a["pos"]] += 1
    reviews = [{"transaction_id": tr["id"], "season": tr["season"], "week": tr["week"], "ts": tr["ts"],
                "even": int(tr["even"]), "winner": tr["winner"], "margin": tr["margin"], "sides": json.dumps(tr["sides"])} for tr in log]
    for t in store.query("SELECT * FROM txn WHERE status='complete' ORDER BY COALESCE(status_updated, created)"):
        lid = t["league_id"]
        moves = store.query("SELECT * FROM txn_move WHERE transaction_id=?", (t["transaction_id"],))
        if t["type"] in ("waiver", "free_agent"):
            for m in moves:
                if m["kind"] != "add":
                    continue
                uid = owner_of.get((lid, m["roster_id"]))
                if not uid:
                    continue
                p = prof[uid]
                if t["type"] == "waiver":
                    p["waiver_adds"] += 1
                    if lid == cur and m["amount"]:
                        p["faab"] += m["amount"]
                        p["max_bid"] = max(p["max_bid"], m["amount"])
                else:
                    p["fa_adds"] += 1
        for m in moves:
            uid = owner_of.get((lid, m["roster_id"]))
            if uid and m["kind"] in ("add", "drop"):
                prof[uid]["moves"] += 1

    # drafting: early-round position mix (rounds 1-2 rookie drafts here; rounds 1-3 elsewhere)
    here = defaultdict(Counter)
    for r in store.query("""SELECT pm.picked_by, pm.position FROM draft_pick_made pm JOIN draft d ON d.draft_id=pm.draft_id
                            WHERE d.player_type=1 AND pm.round<=2"""):
        if r["position"] in SKILL:
            here[r["picked_by"]][r["position"]] += 1
    elsewhere = defaultdict(Counter)
    for r in store.query("SELECT user_id, position FROM ext_draft_pick WHERE round<=3"):
        if r["position"] in SKILL:
            elsewhere[r["user_id"]][r["position"]] += 1
    league_mix = Counter()
    for c in elsewhere.values():
        league_mix.update(c)
    base_total = sum(league_mix.values()) or 1
    base_share = {p: league_mix[p] / base_total for p in SKILL}
    other_leagues = Counter(r["user_id"] for r in store.query(
        "SELECT user_id FROM owner_league WHERE league_id NOT IN (SELECT league_id FROM league_season)"))
    windows = {r["roster_id"]: r["window"] for r in store.query("SELECT roster_id, window FROM team_window")}

    scout = _scouting(store, log, owner_of, names)
    rows = []
    trade_counts = [prof[u]["trades"] for u in current_owners]
    avg_trades = sum(trade_counts) / len(trade_counts) if trade_counts else 0
    faab_rank = sorted(current_owners, key=lambda u: -prof[u]["faab"])
    for uid in current_owners:
        p = prof[uid]
        mix = elsewhere.get(uid, Counter())
        n_mix = sum(mix.values())
        share = {k: round(mix[k] / n_mix, 2) for k in SKILL} if n_mix else {}
        tags = []
        if p["trades"] == 0:
            tags.append("Hasn't traded")
        elif p["trades"] >= max(3, 1.5 * avg_trades):
            tags.append("Active trader")
        if p["trades"] and p["net"] >= 1500:
            tags.append("Wins trades (by today's values)")
        elif p["trades"] and p["net"] <= -1500:
            tags.append("Loses trades (by today's values)")
        net_picks = p["picks_in"] - p["picks_out"]
        if net_picks >= 2:
            tags.append("Collects picks")
        elif net_picks <= -2:
            tags.append("Sells picks")
        if p["acq_pos"]:
            top, cnt = p["acq_pos"].most_common(1)[0]
            if cnt >= 3 and cnt / sum(p["acq_pos"].values()) >= 0.5:
                tags.append(f"Trades for {top}s")
        if uid in faab_rank[:3] and p["faab"] > 0:
            tags.append("Big FAAB spender")
        if n_mix >= 6:
            for k in SKILL:
                if share.get(k, 0) - base_share.get(k, 0) >= 0.12:
                    tags.append(f"Drafts {k}s early")
        sc = scout.get(uid, {})
        if sc.get("got_age") and sc.get("gave_age"):
            if sc["gave_age"] - sc["got_age"] >= 2:
                tags.append("Trades for youth")
            elif sc["got_age"] - sc["gave_age"] >= 2:
                tags.append("Trades for veterans")
        pcs = sc.get("pieces") or {}
        if p["trades"] >= 3 and pcs.get("got") and pcs.get("gave"):
            if pcs["gave"] - pcs["got"] >= 0.5:
                tags.append("Consolidates (2-for-1s)")
            elif pcs["got"] - pcs["gave"] >= 0.5:
                tags.append("Splits stars into depth")
        cur_season = next((x for x in reversed(sc.get("seasons", [])) if not x["complete"]), None)
        if cur_season and cur_season.get("eff") and cur_season.get("league_eff"):
            if cur_season["eff"] - cur_season["league_eff"] >= 0.04:
                tags.append("Sharp lineup setter")
            elif cur_season["league_eff"] - cur_season["eff"] >= 0.05:
                tags.append("Leaves points on the bench")
        if sc.get("moves_30d") == 0:
            tags.append("Quiet lately")
        rid = roster_of_owner.get(uid)
        rows.append({
            "user_id": uid, "roster_id": rid,
            "owner": names.get(uid, {}).get("owner"), "team": names.get(uid, {}).get("team"),
            "window": windows.get(rid),
            "trades": p["trades"], "trade_net": round(p["net"]),
            "partners": json.dumps([{"team": names.get(o, {}).get("team"), "n": n} for o, n in p["partners"].most_common(4)]),
            "acquired_positions": json.dumps(dict(p["acq_pos"])),
            "picks_in": p["picks_in"], "picks_out": p["picks_out"],
            "waiver_adds": p["waiver_adds"], "fa_adds": p["fa_adds"], "faab_spent": p["faab"], "max_bid": p["max_bid"],
            "moves": p["moves"],
            "rookie_early": json.dumps(dict(here.get(uid, Counter()))),
            "draft_mix_other_leagues": json.dumps(share), "other_draft_picks": n_mix,
            "other_leagues": other_leagues.get(uid, 0),
            "tags": json.dumps(tags),
            "scout": json.dumps(scout.get(uid, {})),
        })
    store.write_frame("owner_profile", rows)
    store.conn.execute("DROP TABLE IF EXISTS trade_review")  # shape changed: sides now carry full asset detail
    if reviews:
        store.write_frame("trade_review", reviews)
    store.commit()
    return {"profiles": len(rows), "trades_reviewed": len(reviews),
            "league_draft_mix": {k: round(v, 2) for k, v in base_share.items()}}

"""League-mate profiles, built from every transaction and draft this league has, plus each
manager's drafts in their other Sleeper leagues.

Per manager:
  trading      trades made, partners, hindsight result (what each side holds now, at
               today's values), positions they trade for, picks bought vs sold
  waivers      claims, free-agent adds, FAAB spent and biggest bid
  drafting     position mix in early rounds (rookie drafts here, all drafts elsewhere),
               compared with the league as a whole
  tags         short labels for what stands out
Also a review of every completed trade with today's value of each side.

Writes owner_profile, trade_review.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict

from .store import Store

SKILL = ("QB", "RB", "WR", "TE")


def _pick_player_map(store: Store) -> dict:
    """(season, round, original roster) of a used pick -> player taken with it."""
    out = {}
    for d in store.query("SELECT draft_id, season, slot_to_roster_id, player_type FROM draft WHERE status='complete'"):
        s2r = json.loads(d["slot_to_roster_id"] or "{}")
        if not s2r or d["player_type"] != 1:
            continue
        for p in store.query("SELECT round, draft_slot, player_id FROM draft_pick_made WHERE draft_id=?", (d["draft_id"],)):
            orig = s2r.get(str(p["draft_slot"]))
            if orig is not None:
                out[(str(d["season"]), p["round"], int(orig))] = p["player_id"]
    return out


def build(store: Store) -> dict:
    lg = store.query("SELECT * FROM league_season WHERE is_current=1")[0]
    cur = lg["league_id"]
    leagues = {r["league_id"]: r["season"] for r in store.query("SELECT league_id, season FROM league_season")}
    owner_of = {(r["league_id"], r["roster_id"]): r["owner_id"] for r in store.query("SELECT league_id, roster_id, owner_id FROM roster")}
    names = {}
    for r in store.query("""SELECT o.user_id, o.display_name, m.team_name FROM owner o
                            LEFT JOIN league_member m ON m.user_id=o.user_id AND m.league_id=?""", (cur,)):
        names[r["user_id"]] = {"owner": r["display_name"], "team": r["team_name"] or r["display_name"]}
    current_owners = [r["owner_id"] for r in store.query("SELECT owner_id FROM roster WHERE league_id=? AND owner_id IS NOT NULL", (cur,))]
    roster_of_owner = {r["owner_id"]: r["roster_id"] for r in store.query("SELECT roster_id, owner_id FROM roster WHERE league_id=?", (cur,))}

    vd = store.query("SELECT MAX(snapshot_date) d FROM player_value")[0]["d"]
    val = {r["player_id"]: r["value"] or 0.0 for r in store.query("SELECT player_id, value FROM player_value WHERE snapshot_date=?", (vd,))}
    pos = {r["player_id"]: r["position"] for r in store.query("SELECT player_id, position FROM player")}
    pname = {r["player_id"]: r["full_name"] for r in store.query("SELECT player_id, full_name FROM player")}
    future = {(r["season"], r["round"], r["original_roster_id"]): r["value"] or 0.0 for r in store.query("SELECT * FROM pick_value")}
    used = _pick_player_map(store)

    def pick_now(season, rnd, orig):
        k = (str(season), rnd, orig)
        if k in future:
            return future[k], f"{season} Rd {rnd} pick"
        pid = used.get(k)
        if pid:
            return val.get(pid, 0.0), f"{season} Rd {rnd} → {pname.get(pid, pid)}"
        return 0.0, f"{season} Rd {rnd} pick"

    prof = defaultdict(lambda: {"trades": 0, "partners": Counter(), "net": 0.0, "acq_pos": Counter(),
                                "picks_in": 0, "picks_out": 0, "waiver_adds": 0, "fa_adds": 0,
                                "faab": 0, "max_bid": 0, "moves": 0})
    reviews = []
    for t in store.query("SELECT * FROM txn WHERE status='complete' ORDER BY COALESCE(status_updated, created)"):
        lid = t["league_id"]
        moves = store.query("SELECT * FROM txn_move WHERE transaction_id=?", (t["transaction_id"],))
        if t["type"] == "trade":
            got, gave = defaultdict(float), defaultdict(float)
            items = defaultdict(list)
            for m in moves:
                if m["kind"] == "add":
                    v = val.get(m["player_id"], 0.0)
                    got[m["roster_id"]] += v
                    if m["from_roster_id"] is not None:
                        gave[m["from_roster_id"]] += v
                    items[m["roster_id"]].append(f"{pname.get(m['player_id'], m['player_id'])} ({round(v)})")
                    uid = owner_of.get((lid, m["roster_id"]))
                    if uid and pos.get(m["player_id"]) in SKILL:
                        prof[uid]["acq_pos"][pos[m["player_id"]]] += 1
                elif m["kind"] == "pick":
                    v, lab = pick_now(m["pick_season"], m["pick_round"], m["pick_original_roster"])
                    got[m["roster_id"]] += v
                    if m["from_roster_id"] is not None:
                        gave[m["from_roster_id"]] += v
                    items[m["roster_id"]].append(f"{lab} ({round(v)})")
                    to, fr = owner_of.get((lid, m["roster_id"])), owner_of.get((lid, m["from_roster_id"]))
                    if to:
                        prof[to]["picks_in"] += 1
                    if fr:
                        prof[fr]["picks_out"] += 1
                elif m["kind"] == "faab":
                    items[m["roster_id"]].append(f"${m['amount']} FAAB")
            rids = json.loads(t["roster_ids"] or "[]")
            sides = []
            for rid in rids:
                uid = owner_of.get((lid, rid))
                net = got[rid] - gave[rid]
                if uid:
                    p = prof[uid]
                    p["trades"] += 1
                    p["net"] += net
                    for other in rids:
                        if other != rid and owner_of.get((lid, other)):
                            p["partners"][owner_of[(lid, other)]] += 1
                sides.append({"roster_id": rid, "user_id": uid, "team": names.get(uid, {}).get("team"),
                              "received": items.get(rid, []), "value_now": round(got[rid]), "net": round(net)})
            reviews.append({"transaction_id": t["transaction_id"], "season": leagues.get(lid), "week": t["week"],
                            "ts": t["status_updated"] or t["created"], "sides": json.dumps(sides)})
        elif t["type"] in ("waiver", "free_agent"):
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
            if cnt >= 2 and cnt / sum(p["acq_pos"].values()) >= 0.4:
                tags.append(f"Trades for {top}s")
        if uid in faab_rank[:3] and p["faab"] > 0:
            tags.append("Big FAAB spender")
        if n_mix >= 6:
            for k in SKILL:
                if share.get(k, 0) - base_share.get(k, 0) >= 0.12:
                    tags.append(f"Drafts {k}s early")
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
        })
    store.write_frame("owner_profile", rows)
    if reviews:
        store.write_frame("trade_review", reviews)
    store.commit()
    return {"profiles": len(rows), "trades_reviewed": len(reviews),
            "league_draft_mix": {k: round(v, 2) for k, v in base_share.items()}}

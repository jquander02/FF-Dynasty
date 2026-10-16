/* Dynasty Analyzer — phase 1 app shell. Reads the JSON files the refresh job exports. */
(() => {
  "use strict";

  const D = {};
  const view = document.getElementById("view");
  const POS_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF"];
  let rosterFor = null;
  let pickFilter = "mine";
  let valPos = "ALL";
  let valScope = "all";
  let valLimit = 100;
  let tradePartner = null;
  const give = new Set();
  const get = new Set();

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n, d = 0) => (n == null ? "–" : Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  };

  async function load() {
    const files = ["meta", "teams", "rosters", "picks", "activity", "history", "values", "plan", "owners", "finder"];
    const got = await Promise.all(
      files.map((f) => fetch(`data/${f}.json`, { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).catch(() => null))
    );
    files.forEach((f, i) => (D[f] = got[i]));
  }

  function header() {
    const m = D.meta;
    if (!m) return;
    document.getElementById("league-name").textContent = m.league || "Dynasty";
    const when = new Date(m.refreshed_at);
    const wk = m.week ? `Week ${m.week}` : (m.season_type || "Offseason");
    document.getElementById("league-sub").textContent =
      `${m.season} · ${wk} · updated ${when.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`;
  }

  const teamName = (rid) => (D.teams || []).find((t) => t.roster_id === rid)?.team ?? `Team ${rid}`;
  const myId = () => D.meta?.my_roster_id;

  // ---------------------------------------------------------------- League
  function renderLeague() {
    const teams = [...(D.teams || [])].sort((a, b) => b.wins - a.wins || b.pf - a.pf);
    const me = teams.find((t) => t.is_me);
    const rank = me ? teams.indexOf(me) + 1 : null;
    const s = D.meta?.settings || {};
    const youngest = [...teams].filter((t) => t.avg_age).sort((a, b) => a.avg_age - b.avg_age);
    const ageRank = me ? youngest.indexOf(me) + 1 : null;
    return `
      <div class="stats">
        <div class="stat"><b>${rank ? `${rank} of ${teams.length}` : "–"}</b><span>Your standing</span></div>
        <div class="stat"><b>${me ? `${me.wins}-${me.losses}${me.ties ? "-" + me.ties : ""}` : "–"}</b><span>Your record</span></div>
        <div class="stat"><b>${me ? "$" + fmt(me.faab_left) : "–"}</b><span>FAAB left of $${fmt(s.faab)}</span></div>
        <div class="stat"><b>${me?.value_rank ? ordinal(me.value_rank) : "–"}</b><span>Roster value · ${me?.value ? fmt(me.value) : "–"}</span></div>
        <div class="stat"><b>${me?.avg_age ?? "–"}</b><span>Avg age (QB/RB/WR/TE)${ageRank ? ` · ${ordinal(ageRank)} youngest` : ""}</span></div>
      </div>
      <h2>Standings</h2>
      <div class="table-wrap"><table>
        <thead><tr><th>#</th><th>Team</th><th class="num">W-L</th><th class="num">PF</th><th class="num">Playoffs</th><th class="num">Value</th><th class="num">Age</th><th class="num">FAAB</th></tr></thead>
        <tbody>${teams.map((t, i) => `
          <tr class="${t.is_me ? "me" : ""}">
            <td>${i + 1}</td>
            <td class="team-cell"><a href="#team" data-roster="${t.roster_id}">${esc(t.team)}</a><div class="pmeta">${t.window ? `<span class="win ${winCls(t.window)}">${esc(t.window)}</span>` : esc(t.owner || "")}</div></td>
            <td class="num">${t.wins}-${t.losses}${t.ties ? "-" + t.ties : ""}</td>
            <td class="num">${fmt(t.pf, 1)}</td>
            <td class="num">${pct(t.p_playoffs)}</td>
            <td class="num">${t.value ? `${fmt(t.value / 1000, 1)}k <span class="pmeta">${ordinal(t.value_rank)}</span>` : "–"}</td>
            <td class="num">${t.avg_age ?? "–"}</td>
            <td class="num">$${fmt(t.faab_left)}</td>
          </tr>`).join("")}</tbody>
      </table></div>
      ${windowChart(teams)}
      <p class="foot"><a href="#owners">League-mates and trade history →</a> · <a href="#activity">Recent moves →</a> · <a href="#history">Champions →</a></p>
      <p class="foot">${s.teams} teams · ${s.ppr === 1 ? "full PPR" : s.ppr + " PPR"} · ${s.taxi_slots} taxi · trade deadline wk ${s.trade_deadline} · ${s.playoff_teams}-team playoffs from wk ${s.playoff_week_start}</p>`;
  }

  const winCls = (w) => ({ "Dynasty power": "w-power", "Win now": "w-now", "Rising": "w-rise", "Rebuild": "w-reb" }[w] || "w-mid");

  // Strength now (x) vs strength over the next two seasons (y), one dot per team.
  function windowChart(teams) {
    const pts = teams.filter((t) => t.strength && t.strength[0] != null);
    if (pts.length < 2) return "";
    const W = 340, H = 260, L = 34, B = 30, T = 14, R = 12;
    const xs = pts.map((t) => t.strength[0]);
    const ys = pts.map((t) => (t.strength[1] + t.strength[2]) / 2);
    const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
    const [y0, y1] = [Math.min(...ys), Math.max(...ys)];
    const px = (v) => L + ((v - x0) / (x1 - x0 || 1)) * (W - L - R);
    const py = (v) => H - B - ((v - y0) / (y1 - y0 || 1)) * (H - B - T);
    const med = (a) => { const b = [...a].sort((p, q) => p - q); return (b[(b.length - 1) >> 1] + b[b.length >> 1]) / 2; };
    const mx = px(med(xs)), my = py(med(ys));
    const short = (n) => (n.length > 11 ? n.slice(0, 10) + "…" : n);
    return `
      <h2>Contention windows</h2>
      <div class="card chart">
        <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Team strength now versus the next two seasons">
          <line x1="${mx}" y1="${T}" x2="${mx}" y2="${H - B}" class="grid"/>
          <line x1="${L}" y1="${my}" x2="${W - R}" y2="${my}" class="grid"/>
          <text x="${W - R}" y="${T + 10}" text-anchor="end" class="ql">Dynasty power</text>
          <text x="${L + 4}" y="${T + 10}" class="ql">Rising</text>
          <text x="${W - R}" y="${H - B - 6}" text-anchor="end" class="ql">Win now</text>
          <text x="${L + 4}" y="${H - B - 6}" class="ql">Rebuild</text>
          <text x="${(L + W - R) / 2}" y="${H - 6}" text-anchor="middle" class="ax">Lineup strength now →</text>
          <text x="10" y="${(T + H - B) / 2}" text-anchor="middle" class="ax" transform="rotate(-90 10 ${(T + H - B) / 2})">Next two seasons →</text>
          ${pts.map((t, i) => `<g class="${t.is_me ? "me" : ""}"><circle cx="${px(xs[i])}" cy="${py(ys[i])}" r="${t.is_me ? 6 : 4.5}"/>
            <text x="${px(xs[i]) + (px(xs[i]) > W - 90 ? -8 : 8)}" y="${py(ys[i]) + 3.5}" text-anchor="${px(xs[i]) > W - 90 ? "end" : "start"}" class="lbl">${esc(short(t.team))}</text></g>`).join("")}
        </svg>
      </div>`;
  }

  // ---------------------------------------------------------------- Team
  function renderTeam() {
    const teams = D.teams || [];
    if (rosterFor == null) rosterFor = Number(store.get("roster")) || myId() || teams[0]?.roster_id;
    const players = (D.rosters || {})[rosterFor] || [];
    const groups = POS_ORDER.map((p) => [p, players.filter((x) => x.pos === p)]).filter(([, l]) => l.length);
    const other = players.filter((x) => !POS_ORDER.includes(x.pos));
    if (other.length) groups.push(["Other", other]);
    const t = teams.find((x) => x.roster_id === rosterFor);
    const taxi = players.filter((p) => p.slot === "taxi").length;
    const under25 = players.filter((p) => p.age && p.age < 25 && POS_ORDER.slice(0, 4).includes(p.pos)).length;
    const over28 = players.filter((p) => p.age && p.age >= 28 && POS_ORDER.slice(0, 4).includes(p.pos)).length;
    const slotBadge = (p) =>
      p.slot === "taxi" ? `<span class="badge taxi">TAXI</span>` :
      p.slot === "reserve" ? `<span class="badge ir">IR</span>` :
      p.slot === "starter" ? `<span class="badge start">START</span>` : "";
    const inj = (p) => (p.inj && p.slot !== "reserve" ? `<span class="badge inj">${esc(p.inj)}</span>` : "");
    return `
      <div class="controls">
        <select id="roster-pick" aria-label="Team">
          ${teams.map((x) => `<option value="${x.roster_id}" ${x.roster_id === rosterFor ? "selected" : ""}>${esc(x.team)}${x.is_me ? " (you)" : ""}</option>`).join("")}
        </select>
      </div>
      <div class="stats">
        <div class="stat"><b>${t?.value ? fmt(t.value) : "–"}</b><span>Roster value${t?.value_rank ? ` · ${ordinal(t.value_rank)} of ${teams.length}` : ""}</span></div>
        <div class="stat"><b>${t?.avg_age ?? "–"}</b><span>Avg age (QB/RB/WR/TE)</span></div>
        <div class="stat"><b>${under25}</b><span>Under 25</span></div>
        <div class="stat"><b>${over28}</b><span>28 or older</span></div>
        <div class="stat"><b>${taxi} / ${D.meta?.settings?.taxi_slots ?? "–"}</b><span>Taxi slots used</span></div>
      </div>
      <div class="cols">
      ${groups.map(([pos, list]) => `
        <div class="card">
          <h2>${pos} <span class="muted">· ${list.length}</span></h2>
          ${list.sort((a, b) => (b.val ?? -1) - (a.val ?? -1) || (a.age ?? 99) - (b.age ?? 99)).map((p) => `
            <div class="player">
              <div class="pos ${esc(p.pos)}">${esc(p.pos)}</div>
              <div><div class="pname">${esc(p.name)}${slotBadge(p)}${inj(p)}</div>
                   <div class="pmeta">${esc(p.team || "FA")} · age ${p.age ?? "–"}${p.exp != null ? ` · ${p.exp === 0 ? "rookie" : "yr " + (p.exp + 1)}` : ""}${p.ppg != null ? ` · ${p.ppg} ppg` : ""}${signal(p)}</div></div>
              <div class="age"><b>${p.val != null ? fmt(p.val) : "–"}</b><span class="pmeta">${p.prank ? `${esc(p.pos)}${p.prank}` : "value"}</span></div>
            </div>`).join("")}
        </div>`).join("") || `<div class="empty">No players on this roster.</div>`}
      </div>`;
  }

  // ---------------------------------------------------------------- Values
  function signal(p) {
    const mkt = p.fc ?? p.dp;
    if (p.model == null || mkt == null) return "";
    const d = (p.model - mkt) / Math.max(mkt, 800);
    if (d > 0.25) return ` <span class="badge buy">MODEL LIKES</span>`;
    if (d < -0.25) return ` <span class="badge sell">MARKET LIKES</span>`;
    return "";
  }

  function renderValues() {
    const me = myId();
    let list = D.values || [];
    if (valPos !== "ALL") list = list.filter((p) => p.pos === valPos);
    if (valScope === "mine") list = list.filter((p) => p.owner === me);
    else if (valScope === "fa") list = list.filter((p) => p.owner == null);
    else if (valScope === "others") list = list.filter((p) => p.owner != null && p.owner !== me);
    const shown = list.slice(0, valLimit);
    const short = (rid) => (rid == null ? "FA" : rid === me ? "You" : teamName(rid));
    const trend = (t) => (t == null || Math.abs(t) < 50 ? "" : t > 0 ? ` <span class="up">▲${fmt(t)}</span>` : ` <span class="down">▼${fmt(-t)}</span>`);
    return `
      <div class="controls">
        <div class="seg" role="group" aria-label="Position">
          ${["ALL", "QB", "RB", "WR", "TE"].map((p) => `<button data-vp="${p}" class="${valPos === p ? "on" : ""}">${p === "ALL" ? "All" : p}</button>`).join("")}
        </div>
        <select id="val-scope" aria-label="Whose players">
          <option value="all" ${valScope === "all" ? "selected" : ""}>Everyone</option>
          <option value="mine" ${valScope === "mine" ? "selected" : ""}>Your team</option>
          <option value="others" ${valScope === "others" ? "selected" : ""}>Other teams</option>
          <option value="fa" ${valScope === "fa" ? "selected" : ""}>Free agents</option>
        </select>
      </div>
      <div class="card">
        ${shown.map((p) => `
          <div class="player">
            <div class="pos ${esc(p.pos)}">${esc(p.pos)}</div>
            <div><div class="pname">${p.rank ? `<span class="muted">${p.rank}.</span> ` : ""}${esc(p.name)}</div>
              <div class="pmeta">${esc(p.team || "FA")} · age ${p.age ?? "–"} · ${esc(short(p.owner))}${p.proj != null ? ` · proj ${p.proj}` : ""}${signal(p)}</div></div>
            <div class="age"><b>${fmt(p.val)}</b><span class="pmeta">${esc(p.pos)}${p.prank ?? ""}${trend(p.trend)}</span></div>
          </div>`).join("") || `<p class="muted">No players match.</p>`}
      </div>
      ${list.length > valLimit ? `<div class="controls"><button class="more" data-more="1">Show more (${list.length - valLimit} left)</button></div>` : ""}
      <p class="foot">Value = 60% projection model, 30% FantasyCalc, 10% DynastyProcess, on FantasyCalc's scale. “Proj” is projected points per game next season in this league's scoring. Model likes / Market likes flag a 25%+ gap between the two.</p>`;
  }

  // ---------------------------------------------------------------- Picks
  function renderPicks() {
    const all = D.picks || [];
    const me = myId();
    const seasons = [...new Set(all.map((p) => p.season))];
    const list = pickFilter === "mine" ? all.filter((p) => p.owner === me) : all;
    const mineBySeason = seasons.map((s) => [s, all.filter((p) => p.season === s && p.owner === me)]);
    return `
      <div class="stats">
        ${mineBySeason.map(([s, l]) => `<div class="stat"><b>${fmt(l.reduce((a, p) => a + (p.value || 0), 0))}</b><span>${s}: ${l.length} picks, ${l.filter((p) => p.round === 1).length} firsts</span></div>`).join("")}
      </div>
      <div class="controls"><div class="seg" role="group" aria-label="Show">
        <button data-pf="mine" class="${pickFilter === "mine" ? "on" : ""}">Yours</button>
        <button data-pf="all" class="${pickFilter === "all" ? "on" : ""}">All teams</button>
      </div></div>
      <div class="cols">
      ${seasons.map((s) => {
        const rows = list.filter((p) => p.season === s);
        return `<div class="card"><h2>${s} rookie draft</h2>
          ${rows.length ? rows.map((p) => `
            <div class="pick ${p.owner === me ? "mine" : ""}">
              <span class="lbl">${p.exp_slot ? `${p.round}.${String(Math.round(p.exp_slot)).padStart(2, "0")}` : `R${p.round}`}</span>
              <span class="from">${p.traded ? `via ${esc(p.from_team)}` : "own"}${pickFilter === "all" ? ` → ${esc(p.owner_team)}` : ""}<span class="pmeta">${p.p_top3 != null && p.round === 1 ? ` · top-3 ${pct(p.p_top3)}` : ""}</span></span>
              <span class="pv">${p.value != null ? fmt(p.value) : ""}</span>
            </div>`).join("") : `<p class="muted">No picks this year.</p>`}
        </div>`;
      }).join("")}
      </div>
      <p class="foot">Slot shown is the projected pick (e.g. 1.03). Next year's order comes from simulating the rest of this season on the real schedule, worst record picks first; later years from projected team strength. Value = 60% this league's past rookie picks, 40% market pick values.</p>`;
  }

  // ---------------------------------------------------------------- Plan
  function renderPlan() {
    const P = D.plan;
    if (!P || !P.roster_id) return `<div class="empty">The plan appears after the next full refresh.</div>`;
    const pcard = (c, extra = "") => `
      <div class="player">
        <div class="pos ${esc(c.pos)}">${esc(c.pos)}</div>
        <div><div class="pname">${esc(c.name)}${signal({ model: c.model, fc: c.market })}</div>
          <div class="pmeta">${esc(c.nfl || "FA")} · age ${c.age ?? "–"} · ${c.ppg_now ?? "–"} → ${c.ppg_2y ?? "–"} ppg${extra}</div></div>
        <div class="age"><b>${fmt(c.val)}</b><span class="pmeta">value</span></div>
      </div>`;
    const firsts = (P.my_picks || []).filter((p) => p.round === 1);
    return `
      <div class="stats">
        <div class="stat"><b>${esc(P.record)}</b><span>Record · ${P.exp_wins ?? "–"} projected wins</span></div>
        <div class="stat"><b>${pct(P.p_first_pick)}</b><span>Chance of worst record (1.01)</span></div>
        <div class="stat"><b>${P.exp_slot ? P.exp_slot.toFixed(1) : "–"}</b><span>Projected draft slot · top-3 ${pct(P.p_top3)}</span></div>
        <div class="stat"><b>${P.weeks_to_deadline ?? "–"}</b><span>Weeks to trade deadline (wk ${P.trade_deadline_week ?? "–"})</span></div>
      </div>
      <div class="card">
        <div class="txn-head"><span class="txn-type">Your window</span><span>${P.window ? `<span class="win ${winCls(P.window)}">${esc(P.window)}</span>` : ""}</span></div>
        <div class="move">Lineup strength rank: <b>${ordinal(P.ranks.rank_now)}</b> now, ${ordinal(P.ranks.rank_next)} next year, ${ordinal(P.ranks.rank_later)} in two years.</div>
        <div class="move">Assets: ${fmt(P.assets.players)} in players + ${fmt(P.assets.picks)} in picks = <b>${fmt(P.assets.total)}</b>.</div>
        ${firsts.length ? `<div class="move">Your firsts: ${firsts.map((p) => `${p.season} ${p.own ? "own" : `via ${esc(p.from)}`} (~${p.exp_slot ? p.exp_slot.toFixed(1) : "?"})`).join(", ")}.</div>` : `<div class="move">You own no first-round picks in the next three drafts.</div>`}
      </div>
      <h2>Sell before the cliff</h2>
      <div class="card">${(P.sell || []).map((c) => pcard(c,
        `${c.past_cliff ? " · past age cliff" : ""}${c.projected_drop > 0 ? ` · −${Math.round(c.projected_drop * 100)}% in 2 yrs` : ""}${c.buyers.length ? `<br>Upgrade for: ${c.buyers.map((b) => `${esc(b.team)} (+${b.upgrade_ppg})`).join(", ")}` : ""}`
      )).join("") || `<p class="muted">No veterans worth selling right now.</p>`}</div>
      <h2>Buy: young players on benches</h2>
      <div class="card">${(P.buy || []).map((c) => pcard(c, ` · ${esc(c.team)}${c.owner_window ? ` (${esc(c.owner_window)})` : ""}`)).join("") || `<p class="muted">No targets found.</p>`}</div>
      <h2>Your young core</h2>
      <div class="card">${(P.core || []).map((c) => pcard(c)).join("") || `<p class="muted">No players under 25 with value yet.</p>`}</div>
      <p class="foot">Sell: veterans at their position's age cliff (QB 32, RB 26, WR 28, TE 29) or projected to lose 15%+ in two seasons, with contenders and playoff-bubble teams whose starting lineup they'd upgrade most. Buy: players 24 or younger outside their team's best lineup, contenders' benches first. “ppg” shows now → two seasons out.</p>`;
  }

  // ---------------------------------------------------------------- Trade lab
  // Mirrors dynasty/trade.py: package value (sum v^1.3)^(1/1.3), best-lineup PPG for 3 horizons.
  const PKG = 1.3, FAIR = 0.10;
  const OUT = new Set(["IR", "Out", "PUP", "Sus", "NA", "COV", "DNR"]);
  const FLEX = { FLEX: ["RB", "WR", "TE"], SUPER_FLEX: ["QB", "RB", "WR", "TE"], REC_FLEX: ["WR", "TE"], WRRB_FLEX: ["WR", "RB"] };
  const SKILLPOS = ["QB", "RB", "WR", "TE"];
  const pkgValue = (vals) => {
    const v = vals.map((x) => Math.max(0, x || 0));
    if (!v.length) return 0;
    if (v.length === 1) return v[0];
    return Math.pow(v.reduce((a, x) => a + Math.pow(x, PKG), 0), 1 / PKG);
  };
  function bestLineup(players, h) {
    const slots = D.meta?.settings?.roster_positions || [];
    const key = ["p0", "proj", "p2"][h];
    const byPos = {};
    for (const p of players) {
      const v = p[key];
      if (!SKILLPOS.includes(p.pos) || v == null) continue;
      if (h === 0 && (p.slot === "reserve" || OUT.has(p.inj))) continue;
      (byPos[p.pos] ||= []).push(v);
    }
    Object.values(byPos).forEach((l) => l.sort((a, b) => b - a));
    const used = {};
    let total = 0;
    for (const s of slots) {
      if (!SKILLPOS.includes(s)) continue;
      const i = used[s] || 0;
      if (byPos[s] && i < byPos[s].length) { total += byPos[s][i]; used[s] = i + 1; }
    }
    for (const s of slots) {
      if (!FLEX[s]) continue;
      let best = null;
      for (const pos of FLEX[s]) {
        const i = used[pos] || 0;
        if (byPos[pos] && i < byPos[pos].length && (best === null || byPos[pos][i] > byPos[best][used[best] || 0])) best = pos;
      }
      if (best) { total += byPos[best][used[best] || 0]; used[best] = (used[best] || 0) + 1; }
    }
    return total;
  }
  const pickKey = (p) => `${p.season}-${p.round}-${p.from}`;
  function assetsOf(rid) {
    const players = ((D.rosters || {})[rid] || []).filter((p) => p.val > 0 || SKILLPOS.includes(p.pos));
    const picks = (D.picks || []).filter((p) => p.owner === rid);
    return { players, picks };
  }
  function assetValue(key) {
    const pk = (D.picks || []).find((p) => pickKey(p) === key);
    if (pk) return pk.value || 0;
    for (const l of Object.values(D.rosters || {})) { const p = l.find((x) => x.id === key); if (p) return p.val || 0; }
    return 0;
  }
  function assetLabel(key) {
    const pk = (D.picks || []).find((p) => pickKey(p) === key);
    if (pk) return `${pk.season} Rd ${pk.round}${pk.traded ? ` (${pk.from_team})` : ""}`;
    for (const l of Object.values(D.rosters || {})) { const p = l.find((x) => x.id === key); if (p) return `${p.name} (${p.pos})`; }
    return key;
  }
  function evaluateTrade(me, them) {
    const mineP = (D.rosters || {})[me] || [], theirP = (D.rosters || {})[them] || [];
    const gv = [...give].map(assetValue), tv = [...get].map(assetValue);
    const ga = pkgValue(gv), ta = pkgValue(tv);
    const moveOut = (list, out, inn) => list.filter((p) => !out.has(p.id)).concat(inn);
    const gotPlayers = theirP.filter((p) => get.has(p.id)), gavePlayers = mineP.filter((p) => give.has(p.id));
    const afterMe = moveOut(mineP, give, gotPlayers), afterThem = moveOut(theirP, get, gavePlayers);
    const delta = (before, after) => [0, 1, 2].map((h) => +(bestLineup(after, h) - bestLineup(before, h)).toFixed(1));
    const big = Math.max(ga, ta, 1), diff = ta - ga;
    return {
      giveRaw: gv.reduce((a, b) => a + b, 0), getRaw: tv.reduce((a, b) => a + b, 0), giveAdj: ga, getAdj: ta, diff,
      verdict: Math.abs(diff) <= FAIR * big ? "fair" : diff > 0 ? "win" : "lose",
      me: delta(mineP, afterMe), them: delta(theirP, afterThem),
    };
  }

  function renderTrade() {
    const me = myId();
    const others = (D.teams || []).filter((t) => t.roster_id !== me);
    if (tradePartner == null) tradePartner = Number(store.get("partner")) || null;
    const partner = others.find((t) => t.roster_id === tradePartner);
    const sugg = (D.finder || []).filter((f) => !partner || f.partner === tradePartner);
    const sgn = (x) => (x > 0 ? `+${x}` : `${x}`);
    const cls = (x) => (x > 0.05 ? "up" : x < -0.05 ? "down" : "muted");
    const assetRow = (key, label, meta, val, set, side) => `
      <label class="asset ${set.has(key) ? "on" : ""}">
        <input type="checkbox" data-asset="${esc(key)}" data-side="${side}" ${set.has(key) ? "checked" : ""}>
        <span class="aname">${label}<span class="pmeta">${meta}</span></span>
        <span class="aval">${fmt(val)}</span>
      </label>`;
    const list = (rid, set, side) => {
      const { players, picks } = assetsOf(rid);
      return players.sort((a, b) => (b.val || 0) - (a.val || 0)).map((p) =>
        assetRow(p.id, `<span class="pos-dot ${esc(p.pos)}">${esc(p.pos)}</span> ${esc(p.name)}`,
          ` · ${p.age ?? "–"} · ${p.p0 ?? "–"}→${p.p2 ?? "–"} ppg`, p.val || 0, set, side)).join("") +
        picks.map((p) => assetRow(pickKey(p), `◇ ${p.season} Rd ${p.round}`,
          ` · ${p.traded ? `via ${esc(p.from_team)}` : "own"}${p.exp_slot ? ` · ~${p.round}.${String(Math.round(p.exp_slot)).padStart(2, "0")}` : ""}`,
          p.value || 0, set, side)).join("");
    };
    let result = "";
    if (partner && (give.size || get.size)) {
      const ev = evaluateTrade(me, tradePartner);
      const verdict = ev.verdict === "fair" ? `<span class="badge buy">FAIR</span>` :
        ev.verdict === "win" ? `<span class="badge buy">YOU WIN BY ${fmt(ev.diff)}</span>` :
        `<span class="badge sell">THEY WIN BY ${fmt(-ev.diff)}</span>`;
      const contending = ["Dynasty power", "Win now"].includes(partner.window) || (partner.p_playoffs || 0) >= 0.4;
      const note = contending
        ? (ev.them[0] > 0.3 ? `They're in the hunt (${pct(partner.p_playoffs)} playoff odds) and their lineup gets better now (${sgn(ev.them[0])} ppg): a deal they can say yes to.`
                            : `They're in the hunt (${pct(partner.p_playoffs)} playoff odds), but this doesn't help their lineup now: a hard sell.`)
        : (ev.them[2] > 0.3 ? `They're ${(partner.window || "").toLowerCase()} and gain long-term (${sgn(ev.them[2])} ppg in 2 yrs).` : `Little in it for their window (${esc(partner.window || "?")}).`);
      result = `
        <div class="card result">
          <div class="txn-head"><span class="txn-type">Trade check</span>${verdict}</div>
          <div class="move">You give <b>${fmt(ev.giveAdj)}</b>${give.size > 1 ? ` <span class="pmeta">(${fmt(ev.giveRaw)} raw)</span>` : ""} · you get <b>${fmt(ev.getAdj)}</b>${get.size > 1 ? ` <span class="pmeta">(${fmt(ev.getRaw)} raw)</span>` : ""}</div>
          <table class="mini"><thead><tr><th>Lineup PPG</th><th class="num">Now</th><th class="num">Next yr</th><th class="num">In 2 yrs</th></tr></thead>
            <tbody><tr><td>You</td>${ev.me.map((x) => `<td class="num ${cls(x)}">${sgn(x)}</td>`).join("")}</tr>
                   <tr><td>${esc(partner.team)}</td>${ev.them.map((x) => `<td class="num ${cls(x)}">${sgn(x)}</td>`).join("")}</tr></tbody></table>
          <div class="move pmeta">${note}</div>
        </div>`;
    }
    const prof = partner ? (D.owners?.profiles || []).find((p) => p.roster_id === tradePartner) : null;
    return `
      <div class="controls">
        <select id="trade-partner" aria-label="Trade partner">
          <option value="">Pick a trade partner…</option>
          ${others.map((t) => `<option value="${t.roster_id}" ${t.roster_id === tradePartner ? "selected" : ""}>${esc(t.team)}${t.window ? ` · ${esc(t.window)}` : ""}</option>`).join("")}
        </select>
        ${give.size || get.size ? `<button class="more small" data-clear="1">Clear</button>` : ""}
      </div>
      ${result}
      ${prof ? profileCard(prof, true) : ""}
      ${partner ? `
        <div class="cols">
          <div class="card"><h2>You give</h2>${list(me, give, "give")}</div>
          <div class="card"><h2>You get</h2>${list(tradePartner, get, "get")}</div>
        </div>` : ""}
      <h2>Suggested trades${partner ? ` with ${esc(partner.team)}` : ""}</h2>
      <div class="card">${sugg.slice(0, partner ? 5 : 12).map((f) => `
        <div class="sugg">
          <div><div class="pname">Give ${esc(f.give_label)}</div><div class="pname">Get ${esc(f.get_label)}</div>
          <div class="pmeta">${partner ? "" : `${esc(f.partner_team)} (${esc(f.partner_window || "")}) · `}${fmt(f.give_value)} for ${fmt(f.get_value)} · you ${sgn(+(((f.my_delta[1] + f.my_delta[2]) / 2).toFixed(1)))} ppg later, them ${sgn(f.their_delta[0])} now</div></div>
          <button class="more small" data-load="${f.rank}">Try it</button>
        </div>`).join("") || `<p class="muted">No suggestions for this team.</p>`}</div>
      <p class="foot">Values: 2-for-1 packages count less than their sum (two equal pieces ≈ 1.7× one), since only so many players start. Fair = within 10%. Lineup PPG is the best possible starting lineup before vs after. Suggestions are fair deals that help your window and the partner's: teams in the playoff hunt get better now, everyone else over the next two seasons.</p>`;
  }

  // ---------------------------------------------------------------- Owners
  function profileCard(p, compact = false) {
    const pos = Object.entries(p.acquired_positions || {}).map(([k, v]) => `${k} ${v}`).join(", ");
    const mix = p.draft_mix_other_leagues || {};
    const mixTxt = Object.keys(mix).length ? Object.entries(mix).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${Math.round(v * 100)}%`).join(" · ") : "";
    return `
      <div class="card">
        <div class="txn-head"><span class="pname">${esc(p.team)}</span><span>${p.window ? `<span class="win ${winCls(p.window)}">${esc(p.window)}</span>` : ""}</span></div>
        ${(p.tags || []).length ? `<div class="tags">${p.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>` : ""}
        <div class="move">${p.trades} trades${p.trades ? ` · net ${p.trade_net >= 0 ? "+" : ""}${fmt(p.trade_net)} by today's values` : ""} · picks in ${p.picks_in} / out ${p.picks_out}</div>
        ${(p.partners || []).length ? `<div class="move pmeta">Trades most with: ${p.partners.map((x) => `${esc(x.team)} (${x.n})`).join(", ")}</div>` : ""}
        ${pos ? `<div class="move pmeta">Players acquired by trade: ${pos}</div>` : ""}
        <div class="move pmeta">Waivers: ${p.waiver_adds} claims, $${fmt(p.faab_spent)} FAAB this season (top bid $${fmt(p.max_bid)}) · ${p.fa_adds} free-agent adds</div>
        ${compact ? "" : `${mixTxt ? `<div class="move pmeta">Early-round picks in ${p.other_leagues} other leagues: ${mixTxt}</div>` : p.other_leagues ? `<div class="move pmeta">Plays ${p.other_leagues} other Sleeper leagues</div>` : ""}`}
      </div>`;
  }

  function renderOwners() {
    const O = D.owners || { profiles: [], trades: [] };
    return `
      <h2>League-mates</h2>
      <div class="cols">${O.profiles.map((p) => profileCard(p)).join("")}</div>
      <h2>Trade history, judged by today's values</h2>
      ${O.trades.map((t) => `
        <div class="card">
          <div class="txn-head"><span class="txn-type trade">Trade</span><span>${t.season}${t.week ? ` · wk ${t.week}` : ""}</span></div>
          ${t.sides.map((sd) => `<div class="move"><b>${esc(sd.team || "?")}</b> got ${sd.received.map(esc).join(", ") || "nothing"} <span class="${sd.net > 0 ? "up" : sd.net < 0 ? "down" : "muted"}">${sd.net > 0 ? "+" : ""}${fmt(sd.net)}</span></div>`).join("")}
        </div>`).join("") || `<p class="muted">No trades yet.</p>`}`;
  }

  // ---------------------------------------------------------------- Activity
  function renderActivity() {
    const items = D.activity || [];
    if (!items.length) return `<div class="empty">No transactions yet.</div>`;
    const label = { trade: "Trade", waiver: "Waiver", free_agent: "Free agent", commissioner: "Commish" };
    const line = (m) => {
      if (m.kind === "add") return `<div class="move"><span class="k add">+</span>${esc(m.player)} <span class="muted">${esc(m.pos || "")}${m.from ? ` ${esc(m.from)}` : ""} → ${esc(m.to)}${m.amount != null ? ` · $${m.amount}` : ""}</span></div>`;
      if (m.kind === "drop") return `<div class="move"><span class="k drop">−</span>${esc(m.player)} <span class="muted">${esc(m.pos || "")} dropped by ${esc(m.to)}</span></div>`;
      if (m.kind === "pick") return `<div class="move"><span class="k add">◇</span>${esc(m.pick)} <span class="muted">→ ${esc(m.to)}</span></div>`;
      if (m.kind === "faab") return `<div class="move"><span class="k add">$</span>$${m.amount} FAAB <span class="muted">→ ${esc(m.to)}</span></div>`;
      return "";
    };
    return items.map((t) => `
      <div class="card">
        <div class="txn-head"><span class="txn-type ${t.type}">${label[t.type] || esc(t.type)}</span>
          <span>${t.ts ? new Date(t.ts).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : ""}${t.week ? ` · wk ${t.week}` : ""}</span></div>
        ${t.moves.map(line).join("")}
      </div>`).join("");
  }

  // ---------------------------------------------------------------- History
  function renderHistory() {
    const h = D.history || [];
    return `
      <div class="table-wrap"><table>
        <thead><tr><th>Season</th><th>Champion</th><th class="num">Trades</th><th class="num">Moves</th></tr></thead>
        <tbody>${h.map((s) => `<tr><td>${esc(s.season)}</td><td class="team-cell">${s.champion ? esc(s.champion) : `<span class="muted">${s.status === "complete" ? "–" : "in progress"}</span>`}</td><td class="num">${s.trades}</td><td class="num">${s.transactions}</td></tr>`).join("")}</tbody>
      </table></div>
      <p class="foot">Owner tendencies and trade history by manager arrive in phase 4.</p>`;
  }

  const pct = (x) => (x == null ? "–" : x >= 0.995 ? ">99%" : x > 0 && x < 0.005 ? "<1%" : `${Math.round(x * 100)}%`);
  const ordinal = (n) => n + (["th", "st", "nd", "rd"][((n % 100) - 20) % 10] || ["th", "st", "nd", "rd"][n % 100] || "th");

  // ---------------------------------------------------------------- router
  const TABS = { league: renderLeague, team: renderTeam, values: renderValues, picks: renderPicks, plan: renderPlan, trade: renderTrade, owners: renderOwners, activity: renderActivity, history: renderHistory };
  function route() {
    const tab = (location.hash || "#league").slice(1);
    const fn = TABS[tab] || renderLeague;
    if (!D.meta) {
      view.innerHTML = `<div class="empty">No data yet. The first scheduled refresh fills this in.</div>`;
    } else {
      view.innerHTML = fn();
    }
    document.querySelectorAll("[data-tab]").forEach((a) => a.classList.toggle("on", a.dataset.tab === (TABS[tab] ? tab : "league")));
    window.scrollTo(0, 0);
  }

  document.addEventListener("click", (e) => {
    const r = e.target.closest("[data-roster]");
    if (r) { rosterFor = Number(r.dataset.roster); }
    const pf = e.target.closest("[data-pf]");
    if (pf) { pickFilter = pf.dataset.pf; route(); }
    const vp = e.target.closest("[data-vp]");
    if (vp) { valPos = vp.dataset.vp; valLimit = 100; rerender(); }
    if (e.target.closest("[data-more]")) { valLimit += 100; rerender(); }
    if (e.target.closest("[data-clear]")) { give.clear(); get.clear(); rerender(); }
    const ld = e.target.closest("[data-load]");
    if (ld) {
      const f = (D.finder || []).find((x) => x.rank === Number(ld.dataset.load));
      if (f) {
        tradePartner = f.partner; store.set("partner", f.partner);
        give.clear(); get.clear();
        f.give.forEach((k) => give.add(k)); f.get.forEach((k) => get.add(k));
        route();
      }
    }
  });
  document.addEventListener("change", (e) => {
    if (e.target.dataset && e.target.dataset.asset) {
      const set = e.target.dataset.side === "give" ? give : get;
      if (e.target.checked) set.add(e.target.dataset.asset); else set.delete(e.target.dataset.asset);
      rerender();
      return;
    }
    if (e.target.id === "trade-partner") {
      tradePartner = e.target.value ? Number(e.target.value) : null;
      store.set("partner", tradePartner ?? "");
      get.clear();
      rerender();
      return;
    }
    if (e.target.id === "val-scope") { valScope = e.target.value; valLimit = 100; rerender(); return; }
    if (e.target.id === "roster-pick") {
      rosterFor = Number(e.target.value);
      store.set("roster", rosterFor);
      route();
    }
  });
  window.addEventListener("hashchange", route);
  // re-render in place (keeps scroll position) for filter changes
  function rerender() {
    const y = window.scrollY;
    route();
    window.scrollTo(0, y);
  }

  load().then(() => { header(); route(); });

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
  }
})();

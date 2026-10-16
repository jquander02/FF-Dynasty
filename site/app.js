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

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n, d = 0) => (n == null ? "–" : Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  };

  async function load() {
    const files = ["meta", "teams", "rosters", "picks", "activity", "history", "values", "plan"];
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
      <p class="foot"><a href="#history">League history and champions →</a></p>
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
  const TABS = { league: renderLeague, team: renderTeam, values: renderValues, picks: renderPicks, plan: renderPlan, activity: renderActivity, history: renderHistory };
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
  });
  document.addEventListener("change", (e) => {
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

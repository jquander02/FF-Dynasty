/* Dynasty Analyzer — phase 1 app shell. Reads the JSON files the refresh job exports. */
(() => {
  "use strict";

  const D = {};
  const view = document.getElementById("view");
  const POS_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF"];
  let rosterFor = null;
  let pickFilter = "mine";

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n, d = 0) => (n == null ? "–" : Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  };

  async function load() {
    const files = ["meta", "teams", "rosters", "picks", "activity", "history"];
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
        <div class="stat"><b>${me?.avg_age ?? "–"}</b><span>Avg age (QB/RB/WR/TE)${ageRank ? ` · ${ordinal(ageRank)} youngest` : ""}</span></div>
      </div>
      <h2>Standings</h2>
      <div class="table-wrap"><table>
        <thead><tr><th>#</th><th>Team</th><th class="num">W-L</th><th class="num">PF</th><th class="num">Max PF</th><th class="num">Age</th><th class="num">FAAB</th></tr></thead>
        <tbody>${teams.map((t, i) => `
          <tr class="${t.is_me ? "me" : ""}">
            <td>${i + 1}</td>
            <td class="team-cell"><a href="#team" data-roster="${t.roster_id}">${esc(t.team)}</a><div class="pmeta">${esc(t.owner || "")}</div></td>
            <td class="num">${t.wins}-${t.losses}${t.ties ? "-" + t.ties : ""}</td>
            <td class="num">${fmt(t.pf, 1)}</td>
            <td class="num">${fmt(t.max_pf, 1)}</td>
            <td class="num">${t.avg_age ?? "–"}</td>
            <td class="num">$${fmt(t.faab_left)}</td>
          </tr>`).join("")}</tbody>
      </table></div>
      <p class="foot">${s.teams} teams · ${s.ppr === 1 ? "full PPR" : s.ppr + " PPR"} · ${s.taxi_slots} taxi · trade deadline wk ${s.trade_deadline} · ${s.playoff_teams}-team playoffs from wk ${s.playoff_week_start}</p>`;
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
        <div class="stat"><b>${t?.avg_age ?? "–"}</b><span>Avg age (QB/RB/WR/TE)</span></div>
        <div class="stat"><b>${under25}</b><span>Under 25</span></div>
        <div class="stat"><b>${over28}</b><span>28 or older</span></div>
        <div class="stat"><b>${taxi} / ${D.meta?.settings?.taxi_slots ?? "–"}</b><span>Taxi slots used</span></div>
      </div>
      <div class="cols">
      ${groups.map(([pos, list]) => `
        <div class="card">
          <h2>${pos} <span class="muted">· ${list.length}</span></h2>
          ${list.sort((a, b) => (a.slot === "starter" ? -1 : 0) - (b.slot === "starter" ? -1 : 0) || (a.age ?? 99) - (b.age ?? 99)).map((p) => `
            <div class="player">
              <div class="pos ${esc(p.pos)}">${esc(p.pos)}</div>
              <div><div class="pname">${esc(p.name)}${slotBadge(p)}${inj(p)}</div>
                   <div class="pmeta">${esc(p.team || "FA")}${p.exp != null ? ` · ${p.exp === 0 ? "rookie" : "yr " + (p.exp + 1)}` : ""}</div></div>
              <div class="age"><b>${p.age ?? "–"}</b><span class="pmeta">age</span></div>
            </div>`).join("")}
        </div>`).join("") || `<div class="empty">No players on this roster.</div>`}
      </div>`;
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
        ${mineBySeason.map(([s, l]) => `<div class="stat"><b>${l.length}</b><span>Your ${s} picks · ${l.filter((p) => p.round === 1).length} firsts</span></div>`).join("")}
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
              <span class="lbl">R${p.round}</span>
              <span class="from">${p.traded ? `via ${esc(p.from_team)}` : "own pick"}</span>
              ${pickFilter === "all" ? `<span>${esc(p.owner_team)}</span>` : ""}
            </div>`).join("") : `<p class="muted">No picks this year.</p>`}
        </div>`;
      }).join("")}
      </div>
      <p class="foot">Projected slots and pick values arrive in phase 3.</p>`;
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

  const ordinal = (n) => n + (["th", "st", "nd", "rd"][((n % 100) - 20) % 10] || ["th", "st", "nd", "rd"][n % 100] || "th");

  // ---------------------------------------------------------------- router
  const TABS = { league: renderLeague, team: renderTeam, picks: renderPicks, activity: renderActivity, history: renderHistory };
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
  });
  document.addEventListener("change", (e) => {
    if (e.target.id === "roster-pick") {
      rosterFor = Number(e.target.value);
      store.set("roster", rosterFor);
      route();
    }
  });
  window.addEventListener("hashchange", route);

  load().then(() => { header(); route(); });

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
  }
})();

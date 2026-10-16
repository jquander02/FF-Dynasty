/* Dynasty Analyzer — reads the JSON files the scheduled refresh exports (site/data). */
(() => {
  "use strict";

  const D = {};
  const view = document.getElementById("view");
  const sheet = document.getElementById("sheet");
  const panel = sheet.querySelector(".sheet-panel");
  const POS_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF"];
  const SKILLPOS = ["QB", "RB", "WR", "TE"];
  const FILES = ["meta", "teams", "rosters", "picks", "activity", "history", "values", "plan", "owners", "finder"];

  const state = {
    rosterFor: null, pickFilter: "mine", valPos: "ALL", valScope: "all", valQuery: "", valLimit: 100,
    partner: null, give: new Set(), get: new Set(), lastFocus: null,
  };
  let byId = {};

  // ---------------------------------------------------------------- helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n, d = 0) => (n == null || Number.isNaN(n) ? "–" : Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
  const pct = (x) => (x == null ? "–" : x >= 0.995 ? ">99%" : x > 0 && x < 0.005 ? "<1%" : `${Math.round(x * 100)}%`);
  const ordinal = (n) => (n == null ? "–" : n + (["th", "st", "nd", "rd"][((n % 100) - 20) % 10] || ["th", "st", "nd", "rd"][n % 100] || "th"));
  const sgn = (x) => (x > 0 ? `+${x}` : `${x}`);
  const compact = (n) => (n == null ? "–" : Math.abs(n) >= 10000 ? `${(n / 1000).toFixed(1)}k` : fmt(n));
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const f1 = (x) => (x == null || x === "" ? "–" : (+x).toFixed(1));
  const signCls = (x) => (x > 0.05 ? "up" : x < -0.05 ? "down" : "muted");
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private browsing */ } },
  };
  const myId = () => D.meta?.my_roster_id;
  const teamOf = (rid) => (D.teams || []).find((t) => t.roster_id === rid);
  const teamName = (rid) => (rid == null ? "Free agent" : rid === myId() ? "You" : teamOf(rid)?.team ?? `Team ${rid}`);
  const record = (t) => (t ? `${t.wins}–${t.losses}${t.ties ? `–${t.ties}` : ""}` : "–");
  const slotLabel = (round, slot) => (slot ? `${round}.${String(Math.round(slot)).padStart(2, "0")}` : `Rd ${round}`);

  function ago(iso) {
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    if (s < 60) return "just now";
    if (s < 3600) return rtf.format(-Math.round(s / 60), "minute");
    if (s < 86400) return rtf.format(-Math.round(s / 3600), "hour");
    return rtf.format(-Math.round(s / 86400), "day");
  }

  // ---------------------------------------------------------------- data
  async function load(bust = false) {
    const q = bust ? `?t=${Date.now()}` : "";
    const got = await Promise.all(FILES.map((f) =>
      fetch(`data/${f}.json${q}`, { cache: bust ? "reload" : "no-cache" }).then((r) => (r.ok ? r.json() : null)).catch(() => null)));
    FILES.forEach((f, i) => (D[f] = got[i]));
    byId = {};
    for (const p of D.values || []) byId[p.id] = { ...p };
    for (const [rid, list] of Object.entries(D.rosters || {})) {
      for (const p of list) byId[p.id] = { ...(byId[p.id] || {}), ...p, owner: Number(rid) };
    }
  }

  function header() {
    const m = D.meta;
    const sub = document.getElementById("league-sub");
    if (!m) { sub.textContent = "No data loaded"; return; }
    document.getElementById("league-name").textContent = m.league || "Dynasty";
    const wk = m.week ? `Week ${m.week}` : m.season_type === "off" ? "Offseason" : "Preseason";
    sub.textContent = `${m.season}, ${wk}. Updated ${ago(m.refreshed_at)}`;
    sub.title = new Date(m.refreshed_at).toLocaleString();
  }

  // ---------------------------------------------------------------- building blocks
  const board = (items) => `
    <div class="board" style="--n:${items.length}">
      ${items.map((i) => `<div class="${i.you ? "you" : ""}"><b>${i.v}</b><span>${i.l}</span></div>`).join("")}
    </div>`;
  const how = (title, paras) => `
    <details class="how"><summary>${esc(title)}</summary>${paras.map((p) => `<p>${p}</p>`).join("")}</details>`;
  const winCls = (w) => ({ "Dynasty power": "w-power", "Win now": "w-now", Rising: "w-rise", Rebuild: "w-reb" }[w] || "");
  const winChip = (w) => (w ? `<span class="win ${winCls(w)}">${esc(w)}</span>` : "");

  function lean(p) {
    const mkt = p.fc ?? p.dp ?? p.market;
    if (p.model == null || mkt == null) return "";
    const d = (p.model - mkt) / Math.max(mkt, 800);
    if (d > 0.25) return `<span class="lean model">Model likes</span>`;
    if (d < -0.25) return `<span class="lean market">Market likes</span>`;
    return "";
  }
  function chips(p) {
    let out = "";
    if (p.slot === "taxi") out += `<span class="chip taxi">Taxi</span>`;
    else if (p.slot === "reserve") out += `<span class="chip out">IR</span>`;
    if (p.inj && p.slot !== "reserve") out += `<span class="chip out">${esc(p.inj)}</span>`;
    return out;
  }
  // One player line: tap opens the player sheet.
  function playerRow(p, metaParts, right, lines = []) {
    const parts = metaParts.filter(Boolean);
    return `
      <button class="row" type="button" data-player="${esc(p.id)}">
        <span class="pos ${esc(p.pos)}">${esc(p.pos || "?")}</span>
        <span><span class="pname">${p.rank && right?.rank ? `<span class="rank">${p.rank}</span>` : ""}${esc(p.name)}${chips(p)}</span>
          <span class="meta">${parts.map((x) => `<span>${x}</span>`).join("")}</span>${lines.filter(Boolean).map((l) => `<span class="meta why">${l}</span>`).join("")}</span>
        <span class="val"><b>${p.val != null ? fmt(p.val) : "–"}</b><small>${right?.sub ?? (p.prank ? `${esc(p.pos)}${p.prank}` : "")}</small></span>
      </button>`;
  }

  // ---------------------------------------------------------------- League
  function renderLeague() {
    const teams = [...(D.teams || [])].sort((a, b) => b.wins - a.wins || b.pf - a.pf);
    const me = teams.find((t) => t.is_me);
    const s = D.meta?.settings || {};
    const stand = me ? teams.indexOf(me) + 1 : null;
    return `
      ${board([
        { v: record(me), l: `${ordinal(stand)} of ${teams.length}`, you: true },
        { v: pct(me?.p_playoffs), l: "Playoff odds" },
        { v: ordinal(me?.value_rank), l: `Roster value, ${fmt(me?.value)}` },
        { v: `$${compact(me?.faab_left)}`, l: `FAAB left of $${fmt(s.faab)}` },
      ])}
      <h2>Standings</h2>
      <div class="table"><table>
        <thead><tr><th>#</th><th>Team</th><th class="num">W–L</th><th class="num wide">PF</th><th class="num">Playoffs</th><th class="num">Value</th><th class="num wide">Age</th><th class="num wide">FAAB</th></tr></thead>
        <tbody>${teams.map((t, i) => `
          <tr class="${t.is_me ? "me" : ""}">
            <td>${i + 1}</td>
            <td><a class="tname" href="#team" data-roster="${t.roster_id}">${esc(t.team)}</a>${winChip(t.window)}</td>
            <td class="num">${record(t)}</td>
            <td class="num wide">${fmt(t.pf, 1)}</td>
            <td class="num">${pct(t.p_playoffs)}</td>
            <td class="num">${t.value ? `${fmt(t.value / 1000, 1)}k` : "–"}</td>
            <td class="num wide">${t.avg_age ?? "–"}</td>
            <td class="num wide">$${fmt(t.faab_left)}</td>
          </tr>`).join("")}</tbody>
      </table></div>
      ${windowChart(teams)}
      <div class="links"><a href="#owners">League-mates and past trades</a><a href="#activity">Recent moves</a><a href="#history">Champions</a></div>
      ${how("How these numbers work", [
        `Playoff odds come from simulating the rest of the regular season 10,000 times on Sleeper's schedule. Value is the sum of each roster's player values.`,
        `Windows: each team's best possible lineup is projected for the rest of this season and the next two. <i>Dynasty power</i> is strong now and later, <i>Win now</i> is strong now only, <i>Rising</i> is stronger later, <i>Rebuild</i> is weak both ways.`,
        `${s.teams} teams, ${s.ppr === 1 ? "full PPR" : `${s.ppr} PPR`}, ${s.taxi_slots} taxi spots, trade deadline week ${s.trade_deadline}, ${s.playoff_teams}-team playoffs from week ${s.playoff_week_start}.`,
      ])}`;
  }

  // Lineup strength now (x) vs the next two seasons (y), one dot per team.
  function windowChart(teams) {
    const pts = teams.filter((t) => t.strength && t.strength[0] != null);
    if (pts.length < 2) return "";
    const W = 340, H = 270, L = 26, B = 28, T = 10, R = 10;
    const xs = pts.map((t) => t.strength[0]);
    const ys = pts.map((t) => (t.strength[1] + t.strength[2]) / 2);
    const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
    const [y0, y1] = [Math.min(...ys), Math.max(...ys)];
    const px = (v) => L + 14 + ((v - x0) / (x1 - x0 || 1)) * (W - L - R - 28);
    const py = (v) => H - B - 26 - ((v - y0) / (y1 - y0 || 1)) * (H - B - T - 52);
    const med = (a) => { const b = [...a].sort((p, q) => p - q); return (b[(b.length - 1) >> 1] + b[b.length >> 1]) / 2; };
    const mx = px(med(xs)), my = py(med(ys));
    const short = (n) => (n.length > 13 ? `${n.slice(0, 12)}…` : n);
    // Greedy label placement: try spots around each dot, skip any that overlap a dot or a placed label.
    const pts2 = pts.map((t, i) => ({ me: t.is_me, x: px(xs[i]), y: py(ys[i]), full: t.team.trim(), text: t.is_me ? "You" : short(t.team.trim()) }));
    const boxes = pts2.map((p) => ({ x0: p.x - 6, x1: p.x + 6, y0: p.y - 6, y1: p.y + 6 }));
    const hit = (b) => boxes.some((o) => b.x0 < o.x1 && b.x1 > o.x0 && b.y0 < o.y1 && b.y1 > o.y0);
    const order = pts2.map((p, i) => i).sort((a, b) => (pts2[b].me - pts2[a].me));
    const labels = pts2.map((p) => ({ ...p }));
    for (const i of order) {
      const p = labels[i], w = p.text.length * 5.6 + 4;
      const spots = [[9, 3.5, "start"], [-9, 3.5, "end"], [7, -8, "start"], [7, 14, "start"], [-7, -8, "end"], [-7, 14, "end"], [0, -11, "middle"], [0, 18, "middle"]];
      for (const [dx, dy, anchor] of spots) {
        const lx = p.x + dx, ly = p.y + dy;
        const x0 = anchor === "start" ? lx : anchor === "end" ? lx - w : lx - w / 2;
        const b = { x0, x1: x0 + w, y0: ly - 9, y1: ly + 2 };
        if (b.x0 < 2 || b.x1 > W - 2 || b.y0 < T || b.y1 > H - B) continue;
        if (!hit(b)) { Object.assign(p, { lx, ly, anchor }); boxes.push(b); break; }
      }
    }
    const unlabeled = labels.filter((l) => l.lx == null).map((l) => l.full);
    return `
      <h2>Contention windows</h2>
      <div class="group pad chart">
        <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Lineup strength now against the next two seasons, by team">
          <line x1="${mx}" y1="${T}" x2="${mx}" y2="${H - B}" class="grid"/>
          <line x1="${L}" y1="${my}" x2="${W - R}" y2="${my}" class="grid"/>
          <text x="${W - R}" y="${T + 11}" text-anchor="end" class="ql">Dynasty power</text>
          <text x="${L + 2}" y="${T + 11}" class="ql">Rising</text>
          <text x="${W - R}" y="${H - B - 5}" text-anchor="end" class="ql">Win now</text>
          <text x="${L + 2}" y="${H - B - 5}" class="ql">Rebuild</text>
          <text x="${(L + W - R) / 2}" y="${H - 7}" text-anchor="middle" class="ax">Lineup strength now</text>
          <text x="9" y="${(T + H - B) / 2}" text-anchor="middle" class="ax" transform="rotate(-90 9 ${(T + H - B) / 2})">Next two seasons</text>
          ${labels.map((l) => `<g class="${l.me ? "me" : ""}"><circle class="dotc" cx="${l.x}" cy="${l.y}" r="${l.me ? 7 : 4.5}"/>
            ${l.lx != null ? `<text x="${l.lx}" y="${l.ly}" text-anchor="${l.anchor}" class="lbl">${esc(l.text)}</text>` : ""}</g>`).join("")}
        </svg>
        ${unlabeled.length ? `<p class="note">Unlabeled dots: ${unlabeled.map(esc).join(", ")}.</p>` : ""}
      </div>`;
  }

  // ---------------------------------------------------------------- Team
  function renderTeam() {
    const teams = D.teams || [];
    if (state.rosterFor == null) state.rosterFor = Number(store.get("roster")) || myId() || teams[0]?.roster_id;
    const t = teamOf(state.rosterFor);
    const players = (D.rosters || {})[state.rosterFor] || [];
    const groups = POS_ORDER.map((p) => [p, players.filter((x) => x.pos === p)]).filter(([, l]) => l.length);
    const other = players.filter((x) => !POS_ORDER.includes(x.pos));
    if (other.length) groups.push(["Other", other]);
    const taxi = players.filter((p) => p.slot === "taxi").length;
    const under25 = players.filter((p) => p.age && p.age < 25 && SKILLPOS.includes(p.pos)).length;
    const exp = (p) => (p.exp == null ? "" : p.exp === 0 ? "rookie" : `year ${p.exp + 1}`);
    return `
      <div class="controls">
        <select id="roster-pick" aria-label="Team">
          ${teams.map((x) => `<option value="${x.roster_id}" ${x.roster_id === state.rosterFor ? "selected" : ""}>${esc(x.team)}${x.is_me ? " (you)" : ""}</option>`).join("")}
        </select>
      </div>
      ${board([
        { v: compact(t?.value), l: `Roster value, ${ordinal(t?.value_rank)} of ${teams.length}`, you: t?.is_me },
        { v: t?.avg_age ?? "–", l: "Average age" },
        { v: under25, l: "Players under 25" },
        { v: `${taxi}/${D.meta?.settings?.taxi_slots ?? "–"}`, l: "Taxi spots used" },
      ])}
      ${t?.window ? `<p class="note">${winChip(t.window)} Lineup strength ranks ${ordinal(t.ranks?.[0])} now, ${ordinal(t.ranks?.[1])} next season and ${ordinal(t.ranks?.[2])} in two.</p>` : ""}
      <div class="cols" style="margin-top:14px">
      ${groups.map(([pos, list]) => `
        <div><h2>${pos}<span class="count">${list.length}</span></h2><div class="group">
          ${list.sort((a, b) => (b.val ?? -1) - (a.val ?? -1) || (a.age ?? 99) - (b.age ?? 99)).map((p) =>
            playerRow(p, [esc(p.team || "FA"), p.age != null ? `age ${p.age}` : "", exp(p), p.ppg != null ? `${f1(p.ppg)} ppg` : "", p.slot === "starter" ? "starting" : "", lean(p)])
          ).join("")}
        </div></div>`).join("") || `<div class="empty"><b>Empty roster</b>This team has no players yet.</div>`}
      </div>`;
  }

  // ---------------------------------------------------------------- Values
  function valuesList() {
    const me = myId();
    let list = D.values || [];
    if (state.valPos !== "ALL") list = list.filter((p) => p.pos === state.valPos);
    if (state.valScope === "mine") list = list.filter((p) => p.owner === me);
    else if (state.valScope === "fa") list = list.filter((p) => p.owner == null);
    else if (state.valScope === "others") list = list.filter((p) => p.owner != null && p.owner !== me);
    const q = state.valQuery.trim().toLowerCase();
    if (q) list = list.filter((p) => (p.name || "").toLowerCase().includes(q));
    const shown = list.slice(0, state.valLimit);
    const trend = (t) => (t == null || Math.abs(t) < 50 ? "" : t > 0 ? `<span class="up">▲ ${fmt(t)}</span>` : `<span class="down">▼ ${fmt(-t)}</span>`);
    return `
      <div class="group">${shown.map((p) => playerRow(p,
        [esc(p.team || "FA"), p.age != null ? `age ${p.age}` : "", esc(teamName(p.owner)), lean(p)],
        { rank: true, sub: `${esc(p.pos)}${p.prank ?? ""} ${trend(p.trend)}` })).join("") || `<div class="empty"><b>No players match</b>Try another position or clear the search.</div>`}
      </div>
      ${list.length > state.valLimit ? `<div class="controls" style="margin-top:12px"><button class="btn block" data-more="1">Show ${Math.min(100, list.length - state.valLimit)} more</button></div>` : ""}`;
  }
  function renderValues() {
    return `
      <div class="controls">
        <input type="search" id="val-search" placeholder="Search players" value="${esc(state.valQuery)}" aria-label="Search players" autocomplete="off">
        <div class="seg" role="group" aria-label="Position">
          ${["ALL", "QB", "RB", "WR", "TE"].map((p) => `<button data-vp="${p}" class="${state.valPos === p ? "on" : ""}" aria-pressed="${state.valPos === p}">${p === "ALL" ? "All" : p}</button>`).join("")}
        </div>
        <select id="val-scope" aria-label="Whose players">
          ${[["all", "Everyone"], ["mine", "Your team"], ["others", "Other teams"], ["fa", "Free agents"]].map(([k, l]) => `<option value="${k}" ${state.valScope === k ? "selected" : ""}>${l}</option>`).join("")}
        </select>
      </div>
      <div id="val-list">${valuesList()}</div>
      ${how("How values work", [
        `Each value blends three views: 60% this app's projection model, 30% FantasyCalc (built from real trades) and 10% DynastyProcess, all on FantasyCalc's scale.`,
        `The model projects points per game in this league's scoring for the rest of this season and the next three, adjusts for age and draft capital, and counts the expected points above a replacement-level starter.`,
        `<i>Model likes</i> means the model rates a player at least 25% above the market: a possible buy. <i>Market likes</i> is the reverse: a possible sell.`,
      ])}`;
  }

  // ---------------------------------------------------------------- Picks
  function renderPicks() {
    const all = D.picks || [];
    const me = myId();
    const seasons = [...new Set(all.map((p) => p.season))];
    const list = state.pickFilter === "mine" ? all.filter((p) => p.owner === me) : all;
    return `
      ${board(seasons.map((s) => {
        const l = all.filter((p) => p.season === s && p.owner === me);
        const firsts = l.filter((p) => p.round === 1).length;
        return { v: compact(l.reduce((a, p) => a + (p.value || 0), 0)), l: `${s}: ${plural(l.length, "pick")}, ${plural(firsts, "first")}` };
      }))}
      <div class="controls" style="margin-top:12px"><div class="seg" role="group" aria-label="Show picks">
        <button data-pf="mine" class="${state.pickFilter === "mine" ? "on" : ""}">Yours</button>
        <button data-pf="all" class="${state.pickFilter === "all" ? "on" : ""}">All teams</button>
      </div></div>
      <div class="cols">
      ${seasons.map((s) => {
        const rows = list.filter((p) => p.season === s).sort((a, b) => a.round - b.round || (a.exp_slot ?? 99) - (b.exp_slot ?? 99));
        return `<div><h2>${s} rookie draft<span class="count">${rows.length}</span></h2><div class="group">
          ${rows.length ? rows.map((p) => `
            <div class="pick ${p.owner === me ? "mine" : ""}">
              <span class="slot">${slotLabel(p.round, p.exp_slot)}</span>
              <span><span class="pname">${p.traded ? `From ${esc(p.from_team)}` : p.owner === me ? "Your own pick" : esc(p.owner_team)}</span>
                <span class="meta">${state.pickFilter === "all" && p.traded ? `<span>owned by ${esc(teamName(p.owner))}</span>` : ""}${p.round === 1 && p.p_top3 != null ? `<span>top-3 odds ${pct(p.p_top3)}</span>` : ""}</span></span>
              <span class="val"><b>${p.value != null ? fmt(p.value) : "–"}</b></span>
            </div>`).join("") : `<div class="empty">No picks in this draft.</div>`}
        </div></div>`;
      }).join("")}
      </div>
      ${how("How pick slots and values work", [
        `The slot is the projected pick. Next year's order comes from simulating the rest of this season and the playoffs: non-playoff teams pick by worst record, then playoff teams by finish, champion last. That rule matches 10 of 12 slots in this league's 2026 draft. Later drafts use projected team strength.`,
        `Value is 60% what this league's past rookie picks are worth today at each spot, 40% market pick values, averaged over every slot the pick could land in.`,
      ])}`;
  }

  // ---------------------------------------------------------------- Plan
  function oddsChart(pick) {
    const odds = pick?.odds || [];
    if (!odds.length) return "";
    const max = Math.max(...odds, 0.01);
    return `
      <div class="odds" role="img" aria-label="Chance of each draft slot for your ${pick.season} first">
        ${odds.map((o, i) => `<div class="${i < 3 ? "hi" : ""}" style="height:${Math.max(2, (o / max) * 100)}%" title="${slotLabel(1, i + 1)}: ${pct(o)}">${o >= 0.05 ? `<span>${pct(o)}</span>` : ""}</div>`).join("")}
      </div>
      <div class="odds-x">${odds.map((_, i) => `<span>${i + 1}</span>`).join("")}</div>`;
  }
  function planRow(c, lines = []) {
    const p = { ...c, inj: byId[c.id]?.inj, slot: byId[c.id]?.slot };
    const ppg = c.ppg_now != null ? `${f1(c.ppg_now)} → ${f1(c.ppg_2y)} ppg in 2 yrs` : "";
    return playerRow(p, [esc(c.nfl || "FA"), c.age != null ? `age ${c.age}` : "", ppg, lean({ model: c.model, market: c.market })], { sub: "value" }, lines);
  }
  function renderPlan() {
    const P = D.plan;
    if (!P || !P.roster_id) return `<div class="empty"><b>No plan yet</b>The plan fills in after the next full refresh.</div>`;
    const me = myId();
    const firstSeason = Math.min(...(D.picks || []).map((p) => Number(p.season)));
    const myFirst = (D.picks || []).find((p) => p.owner === me && p.round === 1 && Number(p.season) === firstSeason && p.odds?.length);
    const firsts = (P.my_picks || []).filter((p) => p.round === 1);
    return `
      ${board([
        { v: esc(P.record), l: `${P.exp_wins ?? "–"} wins projected`, you: true },
        { v: pct(P.p_first_pick), l: "Chance at the 1.01" },
        { v: P.exp_slot ? P.exp_slot.toFixed(1) : "–", l: `Projected slot, top-3 ${pct(P.p_top3)}` },
        { v: P.weeks_to_deadline ?? "–", l: `Weeks to the deadline (week ${P.trade_deadline_week ?? "–"})` },
      ])}
      <div class="group pad" style="margin-top:12px">
        <p class="lede">${winChip(P.window)}&nbsp; Your lineup ranks <b>${ordinal(P.ranks.rank_now)}</b> now, <b>${ordinal(P.ranks.rank_next)}</b> next season and <b>${ordinal(P.ranks.rank_later)}</b> in two. You hold ${fmt(P.assets.total)} in value: ${fmt(P.assets.players)} in players and ${fmt(P.assets.picks)} in picks.</p>
        ${myFirst ? `<h3 style="margin-top:12px">Where your ${myFirst.season} first lands</h3>${oddsChart(myFirst)}<p class="note">Chance of each slot. Yellow bars are top-3 picks.</p>` : ""}
        ${firsts.length ? `<p class="note">Your firsts: ${firsts.map((p) => `${p.season} ${p.own ? "own" : `from ${esc(p.from)}`} (about ${slotLabel(1, p.exp_slot)})`).join("; ")}.</p>` : `<p class="note">You own no first-round picks in the next three drafts.</p>`}
      </div>
      <h2>Sell before the cliff<span class="count">${(P.sell || []).length}</span></h2>
      <div class="group">${(P.sell || []).map((c) => planRow(c, [
        [c.past_cliff ? "Past his age cliff" : "", c.projected_drop > 0 ? `projected to lose ${Math.round(c.projected_drop * 100)}% by 2028` : ""].filter(Boolean).join(", ").replace(/^p/, "P"),
        c.buyers.length ? `Would start for ${c.buyers.map((b) => `${esc(b.team)} (+${b.upgrade_ppg})`).join(", ")}` : "No team in the hunt needs him",
      ])).join("") || `<div class="empty">No veterans worth selling right now.</div>`}</div>
      <h2>Buy young players off benches<span class="count">${(P.buy || []).length}</span></h2>
      <div class="group">${(P.buy || []).map((c) => planRow(c, [`On ${esc(c.team)}'s bench${c.owner_window ? ` (${esc(c.owner_window.toLowerCase())})` : ""}`])).join("") || `<div class="empty">No bench targets found.</div>`}</div>
      <h2>Your young core<span class="count">${(P.core || []).length}</span></h2>
      <div class="group">${(P.core || []).map((c) => planRow(c)).join("") || `<div class="empty">No players under 25 with value yet.</div>`}</div>
      ${how("How the plan is built", [
        `Sell: your veterans at their position's age cliff (QB 32, RB 26, WR 28, TE 29) or projected to lose 15% or more within two seasons. Buyers are contenders and teams with 40%+ playoff odds whose starting lineup the player would improve; the number is points per game added.`,
        `Buy: players 24 or younger who don't make their team's best lineup, contenders' benches first, since a contender is more willing to trade future value.`,
        `Tank odds come from the same season simulation as the Picks tab.`,
      ])}`;
  }

  // ---------------------------------------------------------------- Trade lab
  // Mirrors dynasty/trade.py: package value (sum v^1.3)^(1/1.3); best-lineup PPG for 3 horizons.
  const PKG = 1.3, FAIR = 0.10;
  const OUT = new Set(["IR", "Out", "PUP", "Sus", "NA", "COV", "DNR"]);
  const FLEX = { FLEX: ["RB", "WR", "TE"], SUPER_FLEX: ["QB", "RB", "WR", "TE"], REC_FLEX: ["WR", "TE"], WRRB_FLEX: ["WR", "RB"] };
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
  const pickByKey = (k) => (D.picks || []).find((p) => pickKey(p) === k);
  const assetValue = (k) => (pickByKey(k)?.value ?? byId[k]?.val ?? 0) || 0;
  function evaluateTrade(me, them) {
    const mineP = (D.rosters || {})[me] || [], theirP = (D.rosters || {})[them] || [];
    const gv = [...state.give].map(assetValue), tv = [...state.get].map(assetValue);
    const ga = pkgValue(gv), ta = pkgValue(tv);
    const swap = (list, out, inn) => list.filter((p) => !out.has(p.id)).concat(inn);
    const afterMe = swap(mineP, state.give, theirP.filter((p) => state.get.has(p.id)));
    const afterThem = swap(theirP, state.get, mineP.filter((p) => state.give.has(p.id)));
    const delta = (before, after) => [0, 1, 2].map((h) => +(bestLineup(after, h) - bestLineup(before, h)).toFixed(1));
    const big = Math.max(ga, ta, 1), diff = ta - ga;
    return {
      giveRaw: gv.reduce((a, b) => a + b, 0), getRaw: tv.reduce((a, b) => a + b, 0), giveAdj: ga, getAdj: ta, diff,
      verdict: Math.abs(diff) <= FAIR * big ? "fair" : diff > 0 ? "win" : "lose",
      me: delta(mineP, afterMe), them: delta(theirP, afterThem),
    };
  }
  function assetList(rid, set, side) {
    const sel = (k) => (set.has(k) ? 1 : 0);
    const players = ((D.rosters || {})[rid] || []).filter((p) => SKILLPOS.includes(p.pos))
      .sort((a, b) => sel(b.id) - sel(a.id) || (b.val || 0) - (a.val || 0));
    const picks = (D.picks || []).filter((p) => p.owner === rid).sort((a, b) => sel(pickKey(b)) - sel(pickKey(a)));
    const row = (key, label, meta, val) => `
      <label class="asset">
        <input type="checkbox" data-asset="${esc(key)}" data-side="${side}" ${set.has(key) ? "checked" : ""}>
        <span class="aname">${label}<span class="meta">${meta}</span></span>
        <span class="aval">${fmt(val)}</span>
      </label>`;
    return players.map((p) => row(p.id, `<span class="dot ${esc(p.pos)}">${esc(p.pos)}</span>${esc(p.name)}`,
        [p.age != null ? `age ${p.age}` : "", p.p0 != null ? `${f1(p.p0)} → ${f1(p.p2)} ppg` : ""].filter(Boolean).map((x) => `<span>${x}</span>`).join(""), p.val || 0)).join("") +
      picks.map((p) => row(pickKey(p), `<span class="dot PK">PK</span>${p.season} round ${p.round}`,
        `<span>${p.traded ? `from ${esc(p.from_team)}` : "own"}</span>${p.exp_slot ? `<span>about ${slotLabel(p.round, p.exp_slot)}</span>` : ""}`, p.value || 0)).join("");
  }
  function tradeResult(partner) {
    const ev = evaluateTrade(myId(), state.partner);
    const big = Math.max(ev.giveAdj, ev.getAdj, 1);
    const verdict = ev.verdict === "fair" ? "Fair trade" : ev.verdict === "win" ? `You win by ${fmt(ev.diff)}` : `They win by ${fmt(-ev.diff)}`;
    const buying = ["Dynasty power", "Win now"].includes(partner.window) || (partner.p_playoffs || 0) >= 0.4;
    const note = buying
      ? ev.them[0] > 0.3
        ? `They're chasing a title (${pct(partner.p_playoffs)} playoff odds) and this improves their lineup now by ${ev.them[0]} points a week. Expect interest.`
        : `They're chasing a title (${pct(partner.p_playoffs)} playoff odds), but this doesn't improve their lineup now. A hard sell.`
      : ev.them[2] > 0.3
        ? `They're building for later (${esc((partner.window || "").toLowerCase())}), and this makes them ${ev.them[2]} points a week better in two years.`
        : `They're building for later (${esc((partner.window || "").toLowerCase())}), and this doesn't help their future lineup.`;
    return `
      <div class="group pad" id="trade-result">
        <div class="verdict ${ev.verdict}">${verdict}</div>
        <div class="scale">
          <div><b>${fmt(ev.giveAdj)}</b>You give${state.give.size > 1 ? ` (${fmt(ev.giveRaw)} before the package discount)` : ""}<div class="bar"><i style="width:${(ev.giveAdj / big) * 100}%"></i></div></div>
          <div><b>${fmt(ev.getAdj)}</b>You get${state.get.size > 1 ? ` (${fmt(ev.getRaw)} before the package discount)` : ""}<div class="bar"><i style="width:${(ev.getAdj / big) * 100}%"></i></div></div>
        </div>
        <table class="mini"><thead><tr><th>Lineup pts/week</th><th class="num">Now</th><th class="num">Next yr</th><th class="num">In 2 yrs</th></tr></thead>
          <tbody><tr><td>You</td>${ev.me.map((x) => `<td class="num ${signCls(x)}">${sgn(x)}</td>`).join("")}</tr>
                 <tr><td>${esc(partner.team)}</td>${ev.them.map((x) => `<td class="num ${signCls(x)}">${sgn(x)}</td>`).join("")}</tr></tbody></table>
        <p class="note">${note}</p>
      </div>`;
  }
  function renderTrade() {
    const me = myId();
    const others = (D.teams || []).filter((t) => t.roster_id !== me);
    if (state.partner == null) state.partner = Number(store.get("partner")) || null;
    const partner = others.find((t) => t.roster_id === state.partner);
    const shopping = !partner && state.give.size > 0;
    const sugg = (D.finder || []).filter((f) => (partner ? f.partner === state.partner : !shopping || f.give.some((k) => state.give.has(k))));
    const prof = partner ? (D.owners?.profiles || []).find((p) => p.roster_id === state.partner) : null;
    const later = (f) => +(((f.my_delta[1] + f.my_delta[2]) / 2).toFixed(1));
    const change = (x, when) => (Math.abs(x) < 0.05 ? `even ${when}` : `${sgn(x)} ${when}`);
    return `
      <div class="controls">
        <select id="trade-partner" aria-label="Trade partner">
          <option value="">Choose a trade partner</option>
          ${others.map((t) => `<option value="${t.roster_id}" ${t.roster_id === state.partner ? "selected" : ""}>${esc(t.team)}${t.window ? ` (${esc(t.window.toLowerCase())})` : ""}</option>`).join("")}
        </select>
        ${state.give.size || state.get.size ? `<button class="btn" data-clear="1">Clear</button>` : ""}
      </div>
      ${partner && (state.give.size || state.get.size) ? tradeResult(partner) : partner ? `<p class="note">Tick players or picks on both sides to check a deal.</p>` : ""}
      ${shopping ? `<p class="note">Shopping ${[...state.give].map((k) => esc(byId[k]?.name || pickByKey(k)?.season || k)).join(" and ")}. Choose a trade partner to pick what you'd get back, or try a suggestion below.</p>
        <h2>You give</h2><div class="group">${assetList(me, state.give, "give")}</div>` : ""}
      ${prof ? `<h2>About ${esc(partner.team)}</h2>${profileCard(prof, true)}` : ""}
      ${partner ? `
        <div class="cols" style="margin-top:6px">
          <div><h2>You give</h2><div class="group">${assetList(me, state.give, "give")}</div></div>
          <div><h2>You get</h2><div class="group">${assetList(state.partner, state.get, "get")}</div></div>
        </div>` : ""}
      <h2>Suggested trades${partner ? ` with ${esc(partner.team)}` : shopping ? " including them" : ""}<span class="count">${sugg.length}</span></h2>
      <div class="group">${sugg.slice(0, partner ? 6 : 12).map((f) => `
        <div class="sugg">
          <div class="gv"><div>Give <b>${esc(f.give_label)}</b></div><div>Get <b>${esc(f.get_label)}</b></div>
            <div class="meta">${partner ? "" : `<span>${esc(f.partner_team)}</span>`}<span>${fmt(f.give_value)} for ${fmt(f.get_value)}</span><span>you ${change(later(f), "later")}</span><span>they ${change(f.partner_buying ? f.their_delta[0] : +(((f.their_delta[1] + f.their_delta[2]) / 2).toFixed(1)), f.partner_buying ? "now" : "later")}</span></div></div>
          <button class="btn" data-load="${f.rank}">Try it</button>
        </div>`).join("") || `<div class="empty">${shopping ? "No suggested deals include them. Choose a partner to build one." : "No fair deals found with this team."}</div>`}</div>
      ${state.give.size || state.get.size ? `<button class="tray" type="button" data-totop="1"><span>Give <b>${fmt(pkgValue([...state.give].map(assetValue)))}</b> · get <b>${fmt(pkgValue([...state.get].map(assetValue)))}</b></span><span>${partner ? "See result" : "Choose a partner"}</span></button>` : ""}
      ${how("How trades are judged", [
        `A package is worth less than the sum of its parts: two equal players count as about 1.7 of one, because only so many players start. A trade is fair within 10%.`,
        `Lineup change compares each team's best possible starting lineup before and after, in points per week: now (injured players left out), next season, and in two seasons.`,
        `Suggestions are fair deals that move you toward your window and help the partner's: teams chasing the playoffs must get better now, everyone else over the next two seasons.`,
      ])}`;
  }

  // ---------------------------------------------------------------- Owners
  function profileCard(p, compact = false) {
    const pos = Object.entries(p.acquired_positions || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${v}`).join(", ");
    const mix = Object.entries(p.draft_mix_other_leagues || {}).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${Math.round(v * 100)}%`).join(", ");
    return `
      <div class="group pad">
        ${compact ? "" : `<div class="entry-head"><b>${esc(p.team)}</b>${winChip(p.window)}</div>`}
        ${(p.tags || []).length ? `<div class="tags">${p.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>` : ""}
        <dl class="facts">
          <dt>Trades</dt><dd>${p.trades}${p.trades ? `, net <span class="${signCls(p.trade_net)}">${p.trade_net >= 0 ? "+" : ""}${fmt(p.trade_net)}</span> at today's values` : ""}</dd>
          ${(p.partners || []).length ? `<dt>Trades with</dt><dd>${p.partners.map((x) => `${esc(x.team)} (${x.n})`).join(", ")}</dd>` : ""}
          ${pos ? `<dt>Trades for</dt><dd>${pos}</dd>` : ""}
          <dt>Picks</dt><dd>${p.picks_in} acquired, ${p.picks_out} traded away</dd>
          <dt>Waivers</dt><dd>${p.waiver_adds} claims, $${fmt(p.faab_spent)} FAAB this season${p.max_bid ? ` (top bid $${fmt(p.max_bid)})` : ""}</dd>
          ${!compact && mix ? `<dt>Drafts early</dt><dd>${mix} in ${p.other_leagues} other leagues</dd>` : ""}
        </dl>
      </div>`;
  }
  function renderOwners() {
    const O = D.owners || { profiles: [], trades: [] };
    return `
      <h2>League-mates<span class="count">${O.profiles.length}</span></h2>
      <div class="cols">${O.profiles.map((p) => profileCard(p)).join("")}</div>
      <h2>Past trades at today's values<span class="count">${O.trades.length}</span></h2>
      <div class="group">${O.trades.map((t) => `
        <div class="entry">
          <div class="entry-head"><b>${t.season}${t.week ? `, week ${t.week}` : ""}</b></div>
          ${t.sides.map((sd) => `<div class="move"><b>${esc(sd.team || "?")}</b> got ${sd.received.map(esc).join(", ") || "nothing"} <span class="${signCls(sd.net)}">${sd.net > 0 ? "+" : ""}${fmt(sd.net)}</span></div>`).join("")}
        </div>`).join("") || `<div class="empty">No trades yet.</div>`}</div>
      ${how("How profiles are built", [
        `From every trade, waiver claim and draft in this league's history, plus each manager's early-round picks in their other Sleeper leagues. Trade results use what each side holds today at today's values, with used draft picks counted as the player taken.`,
      ])}`;
  }

  // ---------------------------------------------------------------- Activity + History
  function renderActivity() {
    const items = D.activity || [];
    if (!items.length) return `<div class="empty"><b>No moves yet</b>Trades and waiver claims show up here after the next refresh.</div>`;
    const label = { trade: "Trade", waiver: "Waiver claim", free_agent: "Free agent", commissioner: "Commissioner" };
    const line = (m) => {
      if (m.kind === "add") return `<div class="move"><span class="k add">+</span>${esc(m.player)} <span class="muted">${esc(m.pos || "")} to ${esc(m.to)}${m.from ? ` from ${esc(m.from)}` : ""}${m.amount != null ? `, $${m.amount}` : ""}</span></div>`;
      if (m.kind === "drop") return `<div class="move"><span class="k drop">−</span>${esc(m.player)} <span class="muted">${esc(m.pos || "")} dropped by ${esc(m.to)}</span></div>`;
      if (m.kind === "pick") return `<div class="move"><span class="k add">+</span>${esc(m.pick)} <span class="muted">to ${esc(m.to)}</span></div>`;
      if (m.kind === "faab") return `<div class="move"><span class="k add">+</span>$${m.amount} FAAB <span class="muted">to ${esc(m.to)}</span></div>`;
      return "";
    };
    return `<h2>Recent moves</h2><div class="group">${items.map((t) => `
      <div class="entry">
        <div class="entry-head"><b>${label[t.type] || esc(t.type)}</b><span>${t.ts ? new Date(t.ts).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : ""}${t.week ? `, week ${t.week}` : ""}</span></div>
        ${t.moves.map(line).join("")}
      </div>`).join("")}</div>`;
  }
  function renderHistory() {
    const h = D.history || [];
    return `
      <h2>Seasons</h2>
      <div class="table"><table>
        <thead><tr><th>Season</th><th>Champion</th><th class="num">Trades</th><th class="num">Moves</th></tr></thead>
        <tbody>${h.map((s) => `<tr><td>${esc(s.season)}</td><td>${s.champion ? `<span class="tname">${esc(s.champion)}</span>` : `<span class="muted">${s.status === "complete" ? "–" : "In progress"}</span>`}</td><td class="num">${s.trades}</td><td class="num">${s.transactions}</td></tr>`).join("")}</tbody>
      </table></div>`;
  }

  // ---------------------------------------------------------------- Player sheet
  function sparkline(h) {
    const pts = (h || []).filter((v) => v != null);
    if (pts.length < 2) return `<p class="note">The value trend fills in as daily refreshes build up history.</p>`;
    const W = 320, H = 60, lo = Math.min(...pts), hi = Math.max(...pts);
    const x = (i) => 4 + (i / (pts.length - 1)) * (W - 8);
    const y = (v) => H - 6 - ((v - lo) / (hi - lo || 1)) * (H - 12);
    const d = pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join("");
    const dates = D.meta?.value_dates || [];
    return `<svg class="spark" viewBox="0 0 ${W} ${H}" role="img" aria-label="Value over the last ${pts.length} days">
        <path d="${d}"/><circle cx="${x(pts.length - 1)}" cy="${y(pts[pts.length - 1])}" r="3.5"/></svg>
      <p class="note">${dates.length ? `Since ${new Date(dates[dates.length - pts.length] || dates[0]).toLocaleDateString(undefined, { month: "short", day: "numeric" })}` : ""}: ${fmt(pts[0])} → ${fmt(pts[pts.length - 1])}</p>`;
  }
  function openSheet(id) {
    const p = byId[id];
    if (!p) return;
    state.lastFocus = document.activeElement;
    const me = myId();
    const parts = [["Projection model", p.model, "model"], ["FantasyCalc", p.fc, "market"], ["DynastyProcess", p.dp, "market"]].filter(([, v]) => v != null);
    const top = Math.max(...parts.map(([, v]) => v), 1);
    const owner = p.owner == null ? "Free agent" : p.owner === me ? "On your team" : `On ${esc(teamOf(p.owner)?.team || "another team")}`;
    const exp = p.exp == null ? "" : p.exp === 0 ? "rookie" : `year ${p.exp + 1}`;
    panel.innerHTML = `
      <div class="sheet-head">
        <span class="pos ${esc(p.pos)}">${esc(p.pos || "?")}</span>
        <div><h2 id="sheet-title">${esc(p.name)}${chips(p)}</h2>
          <div class="meta">${[esc(p.team || "FA"), p.age != null ? `age ${p.age}` : "", exp, owner].filter(Boolean).map((x) => `<span>${x}</span>`).join("")}</div></div>
        <button class="icon-btn sheet-close" data-close="1" aria-label="Close">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg></button>
      </div>
      <div class="sheet-value"><b>${fmt(p.val)}</b><span class="muted">${p.prank ? `${esc(p.pos)}${p.prank}` : ""}${p.rank ? `, ${ordinal(p.rank)} overall` : ""}</span> ${lean(p)}</div>
      ${parts.length ? `<div class="parts">${parts.map(([l, v, k]) => `
        <div class="part ${k}"><span>${l}</span><div class="bar"><i style="width:${(v / top) * 100}%"></i></div><em>${fmt(v)}</em></div>`).join("")}</div>` : ""}
      <h3 style="margin-top:14px">Projected points per game</h3>
      <div class="ppg">
        <div><b>${f1(p.p0)}</b><span>Rest of this season</span></div>
        <div><b>${f1(p.proj)}</b><span>Next season</span></div>
        <div><b>${f1(p.p2)}</b><span>In two seasons</span></div>
      </div>
      ${p.slot === "reserve" || OUT.has(p.inj) ? `<p class="note">${p.slot === "reserve" ? "On IR" : esc(p.inj)}: left out of "now" lineup strength until he's back.</p>` : ""}
      ${p.ppg != null ? `<p class="note">This season: ${f1(p.ppg)} points per game over ${p.gp} games in this league's scoring.</p>` : ""}
      <h3 style="margin-top:14px">Value trend</h3>
      ${sparkline(p.h)}
      ${p.owner != null && p.owner !== me ? `<button class="btn primary block" style="margin-top:16px" data-tradefor="${esc(p.id)}">Build a trade for ${esc(p.name)}</button>` : ""}
      ${p.owner === me ? `<button class="btn block" style="margin-top:16px" data-shop="${esc(p.id)}">Shop ${esc(p.name)} in the trade lab</button>` : ""}`;
    sheet.hidden = false;
    document.body.style.overflow = "hidden";
    panel.focus();
  }
  function closeSheet() {
    if (sheet.hidden) return;
    sheet.hidden = true;
    document.body.style.overflow = "";
    state.lastFocus?.focus?.();
  }

  // ---------------------------------------------------------------- router
  const TABS = { league: renderLeague, team: renderTeam, values: renderValues, picks: renderPicks, plan: renderPlan, trade: renderTrade, owners: renderOwners, activity: renderActivity, history: renderHistory };
  const TITLES = { league: "League", team: "Team", values: "Values", picks: "Picks", plan: "Plan", trade: "Trade", owners: "Owners", activity: "Moves", history: "History" };
  function route(keepScroll = false) {
    const y = window.scrollY;
    const tab = (location.hash || "#league").slice(1);
    const key = TABS[tab] ? tab : "league";
    if (!D.meta) {
      view.innerHTML = `<div class="empty"><b>Couldn't load league data</b>Check your connection, then tap the reload button at the top.</div>`;
    } else {
      view.innerHTML = TABS[key]();
    }
    document.body.classList.toggle("has-tray", key === "trade" && (state.give.size > 0 || state.get.size > 0));
    document.querySelectorAll("[data-tab]").forEach((a) => {
      const on = a.dataset.tab === key;
      a.classList.toggle("on", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    document.title = `${TITLES[key]} · Dynasty Analyzer`;
    window.scrollTo(0, keepScroll ? y : 0);
  }
  const rerender = () => route(true);

  document.addEventListener("click", (e) => {
    const t = e.target;
    if (t.closest("[data-close]")) { closeSheet(); return; }
    const pl = t.closest("[data-player]");
    if (pl) { openSheet(pl.dataset.player); return; }
    const tf = t.closest("[data-tradefor]");
    if (tf) {
      const id = tf.dataset.tradefor;
      state.partner = byId[id]?.owner ?? null; store.set("partner", state.partner ?? "");
      state.get.clear(); state.get.add(id);
      closeSheet(); location.hash = "#trade"; route();
      return;
    }
    const sh = t.closest("[data-shop]");
    if (sh) { state.give.add(sh.dataset.shop); closeSheet(); location.hash = "#trade"; route(); return; }
    const r = t.closest("[data-roster]");
    if (r) { state.rosterFor = Number(r.dataset.roster); store.set("roster", state.rosterFor); }
    const pf = t.closest("[data-pf]");
    if (pf) { state.pickFilter = pf.dataset.pf; rerender(); }
    const vp = t.closest("[data-vp]");
    if (vp) { state.valPos = vp.dataset.vp; state.valLimit = 100; rerender(); }
    if (t.closest("[data-more]")) { state.valLimit += 100; document.getElementById("val-list").innerHTML = valuesList(); }
    if (t.closest("[data-clear]")) { state.give.clear(); state.get.clear(); rerender(); }
    if (t.closest("[data-totop]")) {
      const el = document.getElementById("trade-result") || document.getElementById("trade-partner");
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
      if (el?.id === "trade-partner") el.focus();
    }
    const ld = t.closest("[data-load]");
    if (ld) {
      const f = (D.finder || []).find((x) => x.rank === Number(ld.dataset.load));
      if (f) {
        state.partner = f.partner; store.set("partner", f.partner);
        state.give.clear(); state.get.clear();
        f.give.forEach((k) => state.give.add(k)); f.get.forEach((k) => state.get.add(k));
        route();
      }
    }
  });
  document.addEventListener("change", (e) => {
    const t = e.target;
    if (t.dataset?.asset) {
      const set = t.dataset.side === "give" ? state.give : state.get;
      if (t.checked) set.add(t.dataset.asset); else set.delete(t.dataset.asset);
      rerender();
      return;
    }
    if (t.id === "trade-partner") {
      state.partner = t.value ? Number(t.value) : null;
      store.set("partner", state.partner ?? "");
      state.get.clear();
      rerender();
      return;
    }
    if (t.id === "val-scope") { state.valScope = t.value; state.valLimit = 100; document.getElementById("val-list").innerHTML = valuesList(); return; }
    if (t.id === "roster-pick") { state.rosterFor = Number(t.value); store.set("roster", state.rosterFor); rerender(); }
  });
  document.addEventListener("input", (e) => {
    if (e.target.id === "val-search") {
      state.valQuery = e.target.value; state.valLimit = 100;
      document.getElementById("val-list").innerHTML = valuesList();
    }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });
  window.addEventListener("hashchange", () => { closeSheet(); route(); });

  const refreshBtn = document.getElementById("refresh");
  refreshBtn.addEventListener("click", async () => {
    refreshBtn.classList.add("busy");
    await load(true);
    header(); rerender();
    refreshBtn.classList.remove("busy");
  });

  view.innerHTML = `<div class="empty"><b>Loading your league</b></div>`;
  load().then(() => { header(); route(true); });
  setInterval(header, 60000);

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
  }
})();

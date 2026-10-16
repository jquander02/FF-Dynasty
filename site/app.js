/* Dynasty Analyzer — reads the JSON files the scheduled refresh exports (site/data).
   Neutral: every screen works for any team. The yellow marker follows the team being viewed. */
(() => {
  "use strict";

  const D = {};
  const view = document.getElementById("view");
  const sheet = document.getElementById("sheet");
  const panel = sheet.querySelector(".sheet-panel");
  const POS_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF"];
  const SKILLPOS = ["QB", "RB", "WR", "TE"];
  const FILES = ["meta", "teams", "rosters", "picks", "activity", "history", "values", "plan", "owners", "finder", "trades"];

  const state = {
    team: null,            // the team being viewed on Team / Plan, and "Team A" in the trade lab
    partner: null,         // "Team B" in the trade lab
    give: new Set(), get: new Set(),
    pickTeam: "all", valOwner: "all", valPos: "ALL", valQuery: "", valLimit: 100,
    valSort: "val", rosterSort: "val", pickSort: "slot",
    histTeam: "all", histWith: "all", histSort: "new",
    sort: {},              // per-table column sort: id -> {key, dir}
    lastFocus: null,
  };
  let byId = {};
  let byTeamName = {};

  // ---------------------------------------------------------------- helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n, d = 0) => (n == null || Number.isNaN(n) ? "–" : Number(n).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
  const pct = (x) => (x == null ? "–" : x >= 0.995 ? ">99%" : x > 0 && x < 0.005 ? "<1%" : `${Math.round(x * 100)}%`);
  const ordinal = (n) => (n == null ? "–" : n + (["th", "st", "nd", "rd"][((n % 100) - 20) % 10] || ["th", "st", "nd", "rd"][n % 100] || "th"));
  const sgn = (x) => (x > 0 ? `+${x}` : `${x}`);
  const signCls = (x) => (x > 0.05 ? "up" : x < -0.05 ? "down" : "muted");
  const compact = (n) => (n == null ? "–" : Math.abs(n) >= 10000 ? `${(n / 1000).toFixed(1)}k` : fmt(n));
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  const f1 = (x) => (x == null || x === "" ? "–" : (+x).toFixed(1));
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private browsing */ } },
  };
  const teams = () => D.teams || [];
  const teamOf = (rid) => teams().find((t) => t.roster_id === rid);
  const record = (t) => (t ? `${t.wins}–${t.losses}${t.ties ? `–${t.ties}` : ""}` : "–");
  const slotLabel = (round, slot) => (slot ? `${round}.${String(Math.round(slot)).padStart(2, "0")}` : `Rd ${round}`);
  const standings = () => [...teams()].sort((a, b) => b.wins - a.wins || b.pf - a.pf);

  // Team names always travel with the manager's username.
  const userTag = (u) => (u ? ` <span class="user">@${esc(u)}</span>` : "");
  const teamHTML = (rid) => {
    if (rid == null) return "Free agent";
    const t = teamOf(rid);
    return t ? `${esc(t.team)}${userTag(t.owner)}` : `Team ${rid}`;
  };
  const teamText = (rid) => {
    const t = teamOf(rid);
    return t ? `${t.team} (@${t.owner})` : rid == null ? "Free agent" : `Team ${rid}`;
  };
  const nameHTML = (name) => {             // for team names that arrive as plain strings
    const t = byTeamName[String(name || "").trim()];
    return t ? `${esc(t.team)}${userTag(t.owner)}` : esc(name);
  };
  const teamOptions = (selected, blank) =>
    (blank ? `<option value="">${blank}</option>` : "") +
    [...teams()].sort((a, b) => a.team.localeCompare(b.team)).map((t) =>
      `<option value="${t.roster_id}" ${t.roster_id === selected ? "selected" : ""}>${esc(teamText(t.roster_id))}</option>`).join("");

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
    byTeamName = {};
    for (const t of teams()) { t.team = (t.team || "").trim(); byTeamName[t.team] = t; }
    if (state.team == null) state.team = Number(store.get("team")) || null;
    if (!teamOf(state.team)) state.team = null;
  }
  const setTeam = (rid) => { state.team = rid; store.set("team", rid ?? ""); };
  const focusTeam = () => state.team ?? standings()[0]?.roster_id ?? null;

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
      ${items.map((i) => `<div class="${i.mark ? "you" : ""}"><b class="${String(i.v).replace(/&[lg]t;/g, "<").length > 5 || /[<>]|&[lg]t;/.test(String(i.v)) ? "long" : ""} ${i.cls || ""}">${i.v}</b><span>${i.l}</span></div>`).join("")}
    </div>`;
  const how = (title, paras) => `
    <details class="how"><summary>${esc(title)}</summary>${paras.map((p) => `<p>${p}</p>`).join("")}</details>`;
  const winCls = (w) => ({ "Dynasty power": "w-power", "Win now": "w-now", Rising: "w-rise", Rebuild: "w-reb" }[w] || "");
  const winChip = (w) => (w ? `<span class="win ${winCls(w)}">${esc(w)}</span>` : "");
  const sortSelect = (id, current, options) => `
    <label class="sortby">Sort by
      <select id="${id}">${options.map(([k, l]) => `<option value="${k}" ${current === k ? "selected" : ""}>${l}</option>`).join("")}</select>
    </label>`;

  // Sortable table: columns {key, label, num, wide, value(row), html(row)}
  function sortableTable(id, cols, rows, def, rowCls = () => "") {
    const s = (state.sort[id] ||= { ...def });
    const col = cols.find((c) => c.key === s.key) || cols[0];
    const sorted = [...rows].sort((a, b) => {
      const va = col.value(a), vb = col.value(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      const c = typeof va === "string" ? va.localeCompare(vb) : va - vb;
      return c * s.dir;
    });
    const th = (c) => {
      const on = c.key === s.key;
      const aria = on ? (s.dir === 1 ? "ascending" : "descending") : "none";
      return `<th class="${c.num ? "num" : ""} ${c.wide ? "wide" : ""}" aria-sort="${aria}">
        <button class="th-sort ${on ? "on" : ""}" data-sort="${id}:${c.key}">${c.label}<span class="arrow" aria-hidden="true">${on ? (s.dir === 1 ? "▲" : "▼") : ""}</span></button></th>`;
    };
    return `<div class="table"><table>
      <thead><tr>${cols.map(th).join("")}</tr></thead>
      <tbody>${sorted.map((r) => `<tr class="${rowCls(r)}">${cols.map((c) => `<td class="${c.num ? "num" : ""} ${c.wide ? "wide" : ""}">${c.html(r)}</td>`).join("")}</tr>`).join("")}</tbody>
    </table></div>`;
  }

  function lean(p) {
    const mkt = p.fc ?? p.dp ?? p.market;
    if (p.model == null || mkt == null) return "";
    const d = (p.model - mkt) / Math.max(mkt, 800);
    if (d > 0.25) return `<span class="lean model">Model likes</span>`;
    if (d < -0.25) return `<span class="lean market">Market likes</span>`;
    return "";
  }
  const OUT = new Set(["IR", "Out", "PUP", "Sus", "NA", "COV", "DNR"]);
  function chips(p) {
    let out = "";
    if (p.slot === "taxi") out += `<span class="chip taxi">Taxi</span>`;
    else if (p.slot === "reserve") out += `<span class="chip out">IR</span>`;
    if (p.inj && p.slot !== "reserve") out += `<span class="chip out">${esc(p.inj)}</span>`;
    return out;
  }
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
  const PLAYER_SORTS = {
    val: [(p) => p.val ?? -1, -1], age: [(p) => p.age ?? 99, 1], name: [(p) => p.name || "", 1],
    proj: [(p) => p.proj ?? -1, -1], ppg: [(p) => p.ppg ?? -1, -1], trend: [(p) => p.trend ?? -1e9, -1],
  };
  const sortPlayers = (list, key) => {
    const [f, dir] = PLAYER_SORTS[key] || PLAYER_SORTS.val;
    return [...list].sort((a, b) => {
      const va = f(a), vb = f(b);
      return (typeof va === "string" ? va.localeCompare(vb) : va - vb) * dir || (b.val ?? -1) - (a.val ?? -1);
    });
  };

  // ---------------------------------------------------------------- League
  function renderLeague() {
    const list = standings();
    const s = D.meta?.settings || {};
    const week = D.meta?.week || 0;
    const lastReg = (s.playoff_week_start || 15) - 1;
    const leader = list[0];
    const richest = [...list].sort((a, b) => (b.value || 0) - (a.value || 0))[0];
    const rankOf = new Map(list.map((t, i) => [t.roster_id, i + 1]));
    const cols = [
      { key: "rank", label: "#", value: (t) => rankOf.get(t.roster_id), html: (t) => rankOf.get(t.roster_id) },
      { key: "team", label: "Team", value: (t) => t.team.toLowerCase(),
        html: (t) => `<a class="tname" href="#team" data-roster="${t.roster_id}">${esc(t.team)}</a><span class="sub2"><span class="user">@${esc(t.owner)}</span>${winChip(t.window)}</span>` },
      { key: "wl", label: "W–L", num: 1, value: (t) => t.wins + 0.5 * (t.ties || 0) + t.pf / 1e5, html: record },
      { key: "pf", label: "PF", num: 1, wide: 1, value: (t) => t.pf, html: (t) => fmt(t.pf, 1) },
      { key: "po", label: "Playoffs", num: 1, value: (t) => t.p_playoffs, html: (t) => pct(t.p_playoffs) },
      { key: "value", label: "Value", num: 1, value: (t) => t.value, html: (t) => (t.value ? `${fmt(t.value / 1000)}k` : "–") },
      { key: "age", label: "Age", num: 1, wide: 1, value: (t) => t.avg_age, html: (t) => t.avg_age ?? "–" },
      { key: "faab", label: "FAAB", num: 1, wide: 1, value: (t) => t.faab_left, html: (t) => `$${fmt(t.faab_left)}` },
    ];
    return `
      ${board([
        { v: week ? `Wk ${week}` : "–", l: week ? `${plural(Math.max(0, lastReg - week + 1), "regular-season week")} left` : "Season not started" },
        { v: `Wk ${s.trade_deadline ?? "–"}`, l: "Trade deadline" },
        { v: record(leader), l: `Best record, ${esc(leader?.team || "–")}` },
        { v: richest?.value ? `${fmt(richest.value / 1000)}k` : "–", l: `Top value, ${esc(richest?.team || "–")}` },
      ])}
      <h2>Standings</h2>
      ${sortableTable("standings", cols, list, { key: "rank", dir: 1 }, (t) => (t.roster_id === state.team ? "me" : ""))}
      ${windowChart(list)}
      <div class="links"><a href="#trades" data-hist="all">Past trades</a><a href="#owners">Managers</a><a href="#activity">Recent moves</a><a href="#history">Champions</a></div>
      ${how("How these numbers work", [
        `Tap a column heading to sort; tap it again to reverse. Playoff odds come from simulating the rest of the regular season 10,000 times on Sleeper's schedule. Value is the sum of a roster's player values.`,
        `Windows: each team's best possible lineup is projected for the rest of this season and the next two. <i>Dynasty power</i> is strong now and later, <i>Win now</i> is strong now only, <i>Rising</i> is stronger later, <i>Rebuild</i> is weak both ways.`,
        `The highlighted row and dot is the team you're viewing on the Team and Plan tabs.`,
        `${s.teams} teams, ${s.ppr === 1 ? "full PPR" : `${s.ppr} PPR`}, ${s.taxi_slots} taxi spots, trade deadline week ${s.trade_deadline}, ${s.playoff_teams}-team playoffs from week ${s.playoff_week_start}.`,
      ])}`;
  }

  // Lineup strength now (x) vs the next two seasons (y), one dot per team.
  function windowChart(list) {
    const pts = list.filter((t) => t.strength && t.strength[0] != null);
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
    const pts2 = pts.map((t, i) => ({ me: t.roster_id === state.team, x: px(xs[i]), y: py(ys[i]), full: teamText(t.roster_id), text: short(t.team) }));
    const boxes = pts2.map((p) => ({ x0: p.x - 6, x1: p.x + 6, y0: p.y - 6, y1: p.y + 6 }));
    const hit = (b) => boxes.some((o) => b.x0 < o.x1 && b.x1 > o.x0 && b.y0 < o.y1 && b.y1 > o.y0);
    const order = pts2.map((p, i) => i).sort((a, b) => pts2[b].me - pts2[a].me);
    const labels = pts2.map((p) => ({ ...p }));
    for (const i of order) {
      const p = labels[i], w = p.text.length * 5.6 + 4;
      const spots = [[9, 3.5, "start"], [-9, 3.5, "end"], [7, -8, "start"], [7, 14, "start"], [-7, -8, "end"], [-7, 14, "end"], [0, -11, "middle"], [0, 18, "middle"]];
      for (const [dx, dy, anchor] of spots) {
        const lx = p.x + dx, ly = p.y + dy;
        const x0b = anchor === "start" ? lx : anchor === "end" ? lx - w : lx - w / 2;
        const b = { x0: x0b, x1: x0b + w, y0: ly - 9, y1: ly + 2 };
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
          ${labels.map((l) => `<g class="${l.me ? "me" : ""}"><circle class="dotc" cx="${l.x}" cy="${l.y}" r="${l.me ? 7 : 4.5}"><title>${esc(l.full)}</title></circle>
            ${l.lx != null ? `<text x="${l.lx}" y="${l.ly}" text-anchor="${l.anchor}" class="lbl">${esc(l.text)}</text>` : ""}</g>`).join("")}
        </svg>
        ${unlabeled.length ? `<p class="note">Unlabeled dots: ${unlabeled.map(esc).join(", ")}.</p>` : ""}
      </div>`;
  }

  // ---------------------------------------------------------------- Team
  const teamPicker = (id = "team-pick") => `
    <div class="controls"><select id="${id}" aria-label="Team">${teamOptions(focusTeam())}</select></div>`;
  function renderTeam() {
    const rid = focusTeam();
    const t = teamOf(rid);
    const players = (D.rosters || {})[rid] || [];
    const groups = POS_ORDER.map((p) => [p, players.filter((x) => x.pos === p)]).filter(([, l]) => l.length);
    const other = players.filter((x) => !POS_ORDER.includes(x.pos));
    if (other.length) groups.push(["Other", other]);
    const taxi = players.filter((p) => p.slot === "taxi").length;
    const under25 = players.filter((p) => p.age && p.age < 25 && SKILLPOS.includes(p.pos)).length;
    const exp = (p) => (p.exp == null ? "" : p.exp === 0 ? "rookie" : `year ${p.exp + 1}`);
    return `
      ${teamPicker()}
      ${board([
        { v: compact(t?.value), l: `Roster value, ${ordinal(t?.value_rank)} of ${teams().length}`, mark: true },
        { v: record(t), l: `Playoff odds ${pct(t?.p_playoffs)}` },
        { v: t?.avg_age ?? "–", l: "Average age" },
        { v: `${taxi}/${D.meta?.settings?.taxi_slots ?? "–"}`, l: `Taxi spots used, ${under25} under 25` },
      ])}
      ${t ? `<p class="note">${esc(t.team)}${userTag(t.owner)} ${winChip(t.window)}${t.window ? ":" : ""} lineup strength ranks ${ordinal(t.ranks?.[0])} now, ${ordinal(t.ranks?.[1])} next season and ${ordinal(t.ranks?.[2])} in two. <a href="#plan">See this team's plan</a> or <a href="#trades" data-hist="${t.roster_id}">its past trades</a>.</p>` : ""}
      <div class="controls" style="margin-top:12px">${sortSelect("roster-sort", state.rosterSort, [["val", "Value"], ["age", "Age, youngest first"], ["ppg", "Points per game this season"], ["proj", "Projection next season"], ["name", "Name"]])}</div>
      <div class="cols">
      ${groups.map(([pos, list]) => `
        <div><h2>${pos}<span class="count">${list.length}</span></h2><div class="group">
          ${sortPlayers(list, state.rosterSort).map((p) =>
            playerRow(p, [esc(p.team || "FA"), p.age != null ? `age ${p.age}` : "", exp(p), p.ppg != null ? `${f1(p.ppg)} ppg` : "", p.slot === "starter" ? "starting" : "", lean(p)])
          ).join("")}
        </div></div>`).join("") || `<div class="empty"><b>Empty roster</b>This team has no players yet.</div>`}
      </div>`;
  }

  // ---------------------------------------------------------------- Values
  function valuesList() {
    let list = D.values || [];
    if (state.valPos !== "ALL") list = list.filter((p) => p.pos === state.valPos);
    if (state.valOwner === "fa") list = list.filter((p) => p.owner == null);
    else if (state.valOwner === "rostered") list = list.filter((p) => p.owner != null);
    else if (state.valOwner !== "all") list = list.filter((p) => p.owner === Number(state.valOwner));
    const q = state.valQuery.trim().toLowerCase();
    if (q) list = list.filter((p) => (p.name || "").toLowerCase().includes(q));
    list = sortPlayers(list, state.valSort);
    const shown = list.slice(0, state.valLimit);
    const trend = (t) => (t == null || Math.abs(t) < 50 ? "" : t > 0 ? `<span class="up">▲ ${fmt(t)}</span>` : `<span class="down">▼ ${fmt(-t)}</span>`);
    return `
      <div class="group">${shown.map((p) => playerRow(p,
        [esc(p.team || "FA"), p.age != null ? `age ${p.age}` : "", teamHTML(p.owner), lean(p)],
        { rank: true, sub: `${esc(p.pos)}${p.prank ?? ""} ${trend(p.trend)}` })).join("") || `<div class="empty"><b>No players match</b>Try another filter or clear the search.</div>`}
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
        <select id="val-owner" aria-label="Whose players">
          ${[["all", "All players"], ["rostered", "Rostered"], ["fa", "Free agents"]].map(([k, l]) => `<option value="${k}" ${state.valOwner === k ? "selected" : ""}>${l}</option>`).join("")}
          <optgroup label="Team">${[...teams()].sort((a, b) => a.team.localeCompare(b.team)).map((t) => `<option value="${t.roster_id}" ${state.valOwner === String(t.roster_id) ? "selected" : ""}>${esc(teamText(t.roster_id))}</option>`).join("")}</optgroup>
        </select>
        ${sortSelect("val-sort", state.valSort, [["val", "Value"], ["age", "Age, youngest first"], ["proj", "Projection next season"], ["ppg", "Points per game this season"], ["trend", "Value change, 7 days"], ["name", "Name"]])}
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
    const seasons = [...new Set(all.map((p) => p.season))];
    const tid = state.pickTeam === "all" ? null : Number(state.pickTeam);
    const list = tid == null ? all : all.filter((p) => p.owner === tid);
    const sorters = {
      slot: (a, b) => a.round - b.round || (a.exp_slot ?? 99) - (b.exp_slot ?? 99),
      value: (a, b) => (b.value ?? -1) - (a.value ?? -1),
      owner: (a, b) => (teamOf(a.owner)?.team || "").localeCompare(teamOf(b.owner)?.team || "") || a.round - b.round,
      top3: (a, b) => (b.p_top3 ?? -1) - (a.p_top3 ?? -1),
    };
    const summary = tid == null
      ? seasons.map((s) => {
          const l = all.filter((p) => p.season === s && p.round === 1);
          const best = [...l].sort((a, b) => (b.value || 0) - (a.value || 0))[0];
          return { v: compact(best?.value), l: `${s}: top first is ${esc(teamOf(best?.from)?.team || "–")}'s` };
        })
      : seasons.map((s) => {
          const l = all.filter((p) => p.season === s && p.owner === tid);
          return { v: compact(l.reduce((a, p) => a + (p.value || 0), 0)), l: `${s}: ${plural(l.length, "pick")}, ${plural(l.filter((p) => p.round === 1).length, "first")}`, mark: true };
        });
    return `
      <div class="controls">
        <select id="pick-team" aria-label="Whose picks">
          <option value="all" ${tid == null ? "selected" : ""}>All teams</option>${teamOptions(tid)}
        </select>
        ${sortSelect("pick-sort", state.pickSort, [["slot", "Round and slot"], ["value", "Value"], ["top3", "Top-3 odds"], ["owner", "Owner"]])}
      </div>
      ${board(summary)}
      <div class="cols" style="margin-top:6px">
      ${seasons.map((s) => {
        const rows = list.filter((p) => p.season === s).sort(sorters[state.pickSort] || sorters.slot);
        return `<div><h2>${s} rookie draft<span class="count">${rows.length}</span></h2><div class="group">
          ${rows.length ? rows.map((p) => `
            <div class="pick ${tid != null ? "mine" : ""}">
              <span class="slot">${slotLabel(p.round, p.exp_slot)}</span>
              <span><span class="pname">${teamHTML(p.owner)}</span>
                <span class="meta">${p.traded ? `<span>from ${teamHTML(p.from)}</span>` : "<span>own pick</span>"}${p.round === 1 && p.p_top3 != null ? `<span>top-3 odds ${pct(p.p_top3)}</span>` : ""}</span></span>
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
      <div class="odds" role="img" aria-label="Chance of each draft slot for this ${pick.season} first">
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
    const rid = focusTeam();
    const P = (D.plan || {})[rid];
    const t = teamOf(rid);
    if (!P || !P.roster_id) return `${teamPicker()}<div class="empty"><b>No plan yet</b>Plans fill in after the next full refresh.</div>`;
    const firstSeason = Math.min(...(D.picks || []).map((p) => Number(p.season)));
    const ownFirst = (D.picks || []).find((p) => p.owner === rid && p.round === 1 && Number(p.season) === firstSeason && p.odds?.length);
    const firsts = (P.my_picks || []).filter((p) => p.round === 1);
    return `
      ${teamPicker()}
      ${board([
        { v: esc(P.record), l: `${P.exp_wins ?? "–"} wins projected`, mark: true },
        { v: pct(P.p_first_pick), l: "Chance at the 1.01" },
        { v: P.exp_slot ? P.exp_slot.toFixed(1) : "–", l: `Projected slot, top-3 ${pct(P.p_top3)}` },
        { v: pct(P.p_playoffs), l: `Playoff odds, deadline in ${plural(P.weeks_to_deadline ?? 0, "week")}` },
      ])}
      <div class="group pad" style="margin-top:12px">
        <p class="lede">${esc(t?.team)}${userTag(t?.owner)} ${winChip(P.window)}&nbsp; Lineup strength ranks <b>${ordinal(P.ranks.rank_now)}</b> now, <b>${ordinal(P.ranks.rank_next)}</b> next season and <b>${ordinal(P.ranks.rank_later)}</b> in two. Total value ${fmt(P.assets.total)}: ${fmt(P.assets.players)} in players and ${fmt(P.assets.picks)} in picks.</p>
        ${ownFirst ? `<h3 style="margin-top:12px">Where their ${ownFirst.season} first lands</h3>${oddsChart(ownFirst)}<p class="note">Chance of each slot. Yellow bars are top-3 picks.</p>` : ""}
        ${firsts.length ? `<p class="note">First-round picks: ${firsts.map((p) => `${p.season} ${p.own ? "own" : `from ${nameHTML(p.from)}`} (about ${slotLabel(1, p.exp_slot)})`).join("; ")}.</p>` : `<p class="note">No first-round picks in the next three drafts.</p>`}
      </div>
      <h2>Sell before the cliff<span class="count">${(P.sell || []).length}</span></h2>
      <div class="group">${(P.sell || []).map((c) => planRow(c, [
        [c.past_cliff ? "Past his age cliff" : "", c.projected_drop > 0 ? `projected to lose ${Math.round(c.projected_drop * 100)}% by 2028` : ""].filter(Boolean).join(", ").replace(/^p/, "P"),
        c.buyers.length ? `Would start for ${c.buyers.map((b) => `${nameHTML(b.team)} (+${b.upgrade_ppg})`).join(", ")}` : "No team in the hunt needs him",
      ])).join("") || `<div class="empty">No veterans worth selling right now.</div>`}</div>
      <h2>Buy young players off benches<span class="count">${(P.buy || []).length}</span></h2>
      <div class="group">${(P.buy || []).map((c) => planRow(c, [`On ${nameHTML(c.team)}'s bench${c.owner_window ? ` (${esc(c.owner_window.toLowerCase())})` : ""}`])).join("") || `<div class="empty">No bench targets found.</div>`}</div>
      <h2>Young core<span class="count">${(P.core || []).length}</span></h2>
      <div class="group">${(P.core || []).map((c) => planRow(c)).join("") || `<div class="empty">No players under 25 with value yet.</div>`}</div>
      ${how("How the plan is built", [
        `Sell: veterans at their position's age cliff (QB 32, RB 26, WR 28, TE 29) or projected to lose 15% or more within two seasons. Buyers are contenders and teams with 40%+ playoff odds whose starting lineup the player would improve; the number is points per game added.`,
        `Buy: players 24 or younger who don't make their team's best lineup, contenders' benches first, since a contender is more willing to trade future value.`,
        `Draft odds come from the same season simulation as the Picks tab.`,
      ])}`;
  }

  // ---------------------------------------------------------------- Trade lab
  // Mirrors dynasty/trade.py: package value (sum v^1.3)^(1/1.3); best-lineup PPG for 3 horizons.
  const PKG = 1.3, FAIR = 0.10;
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
  function evaluateTrade(a, b) {
    const aP = (D.rosters || {})[a] || [], bP = (D.rosters || {})[b] || [];
    const gv = [...state.give].map(assetValue), tv = [...state.get].map(assetValue);
    const ga = pkgValue(gv), ta = pkgValue(tv);
    const swap = (list, out, inn) => list.filter((p) => !out.has(p.id)).concat(inn);
    const afterA = swap(aP, state.give, bP.filter((p) => state.get.has(p.id)));
    const afterB = swap(bP, state.get, aP.filter((p) => state.give.has(p.id)));
    const delta = (before, after) => [0, 1, 2].map((h) => +(bestLineup(after, h) - bestLineup(before, h)).toFixed(1));
    const big = Math.max(ga, ta, 1), diff = ta - ga;
    return {
      giveRaw: gv.reduce((x, y) => x + y, 0), getRaw: tv.reduce((x, y) => x + y, 0), giveAdj: ga, getAdj: ta, diff,
      verdict: Math.abs(diff) <= FAIR * big ? "fair" : diff > 0 ? "win" : "lose",
      a: delta(aP, afterA), b: delta(bP, afterB),
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
        `<span>${p.traded ? `from ${esc(teamOf(p.from)?.team || "?")}` : "own"}</span>${p.exp_slot ? `<span>about ${slotLabel(p.round, p.exp_slot)}</span>` : ""}`, p.value || 0)).join("");
  }
  function tradeResult(A, B) {
    const ev = evaluateTrade(A.roster_id, B.roster_id);
    const big = Math.max(ev.giveAdj, ev.getAdj, 1);
    const verdict = ev.verdict === "fair" ? "Fair trade" : ev.verdict === "win" ? `${esc(A.team)} wins by ${fmt(ev.diff)}` : `${esc(B.team)} wins by ${fmt(-ev.diff)}`;
    const buying = (t) => ["Dynasty power", "Win now"].includes(t.window) || (t.p_playoffs || 0) >= 0.4;
    const fit = (t, d) => buying(t)
      ? d[0] > 0.3 ? `${esc(t.team)} is chasing a title (${pct(t.p_playoffs)} playoff odds) and gets ${d[0]} points a week better now.`
                   : `${esc(t.team)} is chasing a title (${pct(t.p_playoffs)} playoff odds) but gets no better now.`
      : d[2] > 0.3 ? `${esc(t.team)} is building for later (${esc((t.window || "").toLowerCase())}) and gets ${d[2]} points a week better in two years.`
                   : `${esc(t.team)} is building for later (${esc((t.window || "").toLowerCase())}) but gets no better in two years.`;
    return `
      <div class="group pad" id="trade-result">
        <div class="verdict ${ev.verdict}">${verdict}</div>
        <div class="scale">
          <div><b>${fmt(ev.giveAdj)}</b>${esc(A.team)} gives${state.give.size > 1 ? ` (${fmt(ev.giveRaw)} before the package discount)` : ""}<div class="bar"><i style="width:${(ev.giveAdj / big) * 100}%"></i></div></div>
          <div><b>${fmt(ev.getAdj)}</b>${esc(B.team)} gives${state.get.size > 1 ? ` (${fmt(ev.getRaw)} before the package discount)` : ""}<div class="bar"><i style="width:${(ev.getAdj / big) * 100}%"></i></div></div>
        </div>
        <table class="mini"><thead><tr><th>Lineup pts/week</th><th class="num">Now</th><th class="num">Next yr</th><th class="num">In 2 yrs</th></tr></thead>
          <tbody><tr><td>${teamHTML(A.roster_id)}</td>${ev.a.map((x) => `<td class="num ${signCls(x)}">${sgn(x)}</td>`).join("")}</tr>
                 <tr><td>${teamHTML(B.roster_id)}</td>${ev.b.map((x) => `<td class="num ${signCls(x)}">${sgn(x)}</td>`).join("")}</tr></tbody></table>
        <p class="note">${fit(A, ev.a)} ${fit(B, ev.b)}</p>
      </div>`;
  }
  function renderTrade() {
    const A = teamOf(state.team), B = teamOf(state.partner) && state.partner !== state.team ? teamOf(state.partner) : null;
    const selected = state.give.size || state.get.size;
    let sugg = D.finder || [];
    if (A) sugg = sugg.filter((f) => f.team === A.roster_id);
    if (A && B) sugg = sugg.filter((f) => f.partner === B.roster_id);
    if (A && !B && state.give.size) sugg = sugg.filter((f) => f.give.some((k) => state.give.has(k))).concat(sugg.filter((f) => !f.give.some((k) => state.give.has(k))));
    if (!A) sugg = [...sugg].sort((x, y) => y.score - x.score);
    const later = (d) => +(((d[1] + d[2]) / 2).toFixed(1));
    const change = (x, when) => (Math.abs(x) < 0.05 ? `even ${when}` : `${sgn(x)} ${when}`);
    const profA = A ? (D.owners?.profiles || []).find((p) => p.roster_id === A.roster_id) : null;
    const profB = B ? (D.owners?.profiles || []).find((p) => p.roster_id === B.roster_id) : null;
    return `
      <div class="controls pair">
        <label>Team A<select id="trade-a" aria-label="Team A">${teamOptions(A?.roster_id, "Choose a team")}</select></label>
        <label>Team B<select id="trade-b" aria-label="Team B">${teamOptions(B?.roster_id, "Choose a trade partner")}</select></label>
        ${selected ? `<button class="btn" data-clear="1">Clear</button>` : ""}
      </div>
      ${A && B && selected ? tradeResult(A, B) : A && B ? `<p class="note">Tick players or picks on both sides to check a deal.</p>` : `<p class="note">Choose two teams to build a trade, or try a suggestion below.</p>`}
      ${A && B ? `
        <div class="cols" style="margin-top:6px">
          <div><h2>${esc(A.team)} gives</h2><div class="group">${assetList(A.roster_id, state.give, "give")}</div></div>
          <div><h2>${esc(B.team)} gives</h2><div class="group">${assetList(B.roster_id, state.get, "get")}</div></div>
        </div>
        <div class="cols">
          <div><h2>About ${esc(A.team)}</h2>${profA ? profileCard(profA, true) : ""}</div>
          <div><h2>About ${esc(B.team)}</h2>${profB ? profileCard(profB, true) : ""}</div>
        </div>
        ${(() => { const n = (D.trades || []).filter((tr) => involves(tr, A.roster_id) && involves(tr, B.roster_id)).length;
          return `<p class="note">${n ? `<a href="#trades" data-hist="${A.roster_id}" data-with="${B.roster_id}">See their ${plural(n, "past trade")} with each other</a>.` : "They haven't traded with each other before."}</p>`; })()}` : A && state.give.size ? `<h2>${esc(A.team)} gives</h2><div class="group">${assetList(A.roster_id, state.give, "give")}</div>` : ""}
      <h2>Suggested trades${A ? ` for ${esc(A.team)}` : ""}${B ? ` with ${esc(B.team)}` : ""}<span class="count">${sugg.length}</span></h2>
      <div class="group">${sugg.slice(0, A ? 8 : 15).map((f) => `
        <div class="sugg">
          <div class="gv">
            ${A ? "" : `<div class="meta"><span>${teamHTML(f.team)} trades with ${teamHTML(f.partner)}</span></div>`}
            <div>${A ? "Give" : `${esc(teamOf(f.team)?.team)} gives`} <b>${esc(f.give_label)}</b></div>
            <div>${A ? "Get" : `${esc(teamOf(f.partner)?.team)} gives`} <b>${esc(f.get_label)}</b>${A && !B ? ` <span class="muted">from ${teamHTML(f.partner)}</span>` : ""}</div>
            <div class="meta"><span>${fmt(f.give_value)} for ${fmt(f.get_value)}</span><span>${esc(teamOf(f.team)?.team)} ${change(later(f.my_delta), "later")}</span><span>${esc(teamOf(f.partner)?.team)} ${change(f.partner_buying ? f.their_delta[0] : later(f.their_delta), f.partner_buying ? "now" : "later")}</span></div>
          </div>
          <button class="btn" data-load="${esc(f.id)}">Try it</button>
        </div>`).join("") || `<div class="empty">No fair deals found${A ? " for this team" : ""}.</div>`}</div>
      ${selected ? `<button class="tray" type="button" data-totop="1"><span>${esc(A?.team || "Team A")} <b>${fmt(pkgValue([...state.give].map(assetValue)))}</b>, ${esc(B?.team || "Team B")} <b>${fmt(pkgValue([...state.get].map(assetValue)))}</b></span><span>${A && B ? "See result" : "Choose teams"}</span></button>` : ""}
      ${how("How trades are judged", [
        `A package is worth less than the sum of its parts: two equal players count as about 1.7 of one, because only so many players start. A trade is fair within 10%.`,
        `Lineup change compares each team's best possible starting lineup before and after, in points per week: now (injured players left out), next season, and in two seasons.`,
        `Suggestions are worked out for every team: fair deals that move the team toward its window and help the partner's. Teams chasing the playoffs must get better now, everyone else over the next two seasons.`,
      ])}`;
  }

  // ---------------------------------------------------------------- Owners
  function profileCard(p, compactCard = false) {
    const pos = Object.entries(p.acquired_positions || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${v}`).join(", ");
    const mix = Object.entries(p.draft_mix_other_leagues || {}).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${Math.round(v * 100)}%`).join(", ");
    return `
      <div class="group pad">
        ${compactCard ? "" : `<div class="entry-head"><b>${esc(p.team)}${userTag(p.owner)}</b>${winChip(p.window)}</div>`}
        ${(p.tags || []).length ? `<div class="tags">${p.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>` : ""}
        <dl class="facts">
          <dt>Trades</dt><dd>${p.trades}${p.trades ? `, net <span class="${signCls(p.trade_net)}">${p.trade_net >= 0 ? "+" : ""}${fmt(p.trade_net)}</span> at today's values. <a href="#trades" data-hist="${p.roster_id}">See them</a>` : ""}</dd>
          ${(p.partners || []).length ? `<dt>Trades with</dt><dd>${p.partners.map((x) => `${nameHTML(x.team)} (${x.n})`).join(", ")}</dd>` : ""}
          ${pos ? `<dt>Trades for</dt><dd>${pos}</dd>` : ""}
          <dt>Picks</dt><dd>${p.picks_in} acquired, ${p.picks_out} traded away</dd>
          <dt>Waivers</dt><dd>${p.waiver_adds} claims, $${fmt(p.faab_spent)} FAAB this season${p.max_bid ? ` (top bid $${fmt(p.max_bid)})` : ""}</dd>
          ${!compactCard && mix ? `<dt>Drafts early</dt><dd>${mix} in ${p.other_leagues} other leagues</dd>` : ""}
        </dl>
      </div>`;
  }
  function renderOwners() {
    const O = D.owners || { profiles: [] };
    const cols = [
      { key: "team", label: "Team", value: (p) => (p.team || "").toLowerCase(),
        html: (p) => `<span class="tname">${esc(p.team)}</span><span class="sub2"><span class="user">@${esc(p.owner)}</span>${winChip(p.window)}</span>` },
      { key: "trades", label: "Trades", num: 1, value: (p) => p.trades, html: (p) => p.trades },
      { key: "net", label: "Net", num: 1, value: (p) => p.trade_net, html: (p) => `<span class="${signCls(p.trade_net)}">${p.trade_net > 0 ? "+" : ""}${compact(p.trade_net)}</span>` },
      { key: "pin", label: "Picks in", num: 1, wide: 1, value: (p) => p.picks_in, html: (p) => p.picks_in },
      { key: "pout", label: "Picks out", num: 1, wide: 1, value: (p) => p.picks_out, html: (p) => p.picks_out },
      { key: "claims", label: "Claims", num: 1, wide: 1, value: (p) => p.waiver_adds, html: (p) => p.waiver_adds },
      { key: "faab", label: "FAAB", num: 1, value: (p) => p.faab_spent, html: (p) => `$${fmt(p.faab_spent)}` },
    ];
    return `
      <h2>Managers<span class="count">${O.profiles.length}</span></h2>
      ${sortableTable("owners", cols, O.profiles, { key: "trades", dir: -1 }, (p) => (p.roster_id === state.team ? "me" : ""))}
      <div class="cols" style="margin-top:12px">${O.profiles.map((p) => profileCard(p)).join("")}</div>
      <p class="note" style="margin-top:12px"><a href="#trades" data-hist="all">Every past trade, judged at today's values</a></p>
      ${how("How profiles are built", [
        `From every trade, waiver claim and draft in this league's history, plus each manager's early-round picks in their other Sleeper leagues. Net is the total of every trade's result at today's values (see Past trades), with used draft picks counted as the player taken. Tap a column heading to sort.`,
      ])}`;
  }

  // ---------------------------------------------------------------- Past trades
  const involves = (tr, rid) => tr.sides.some((sd) => sd.roster_id === rid);
  const sideOf = (tr, rid) => tr.sides.findIndex((sd) => sd.roster_id === rid);
  const sideName = (sd) => (sd.roster_id != null ? teamHTML(sd.roster_id) : `${esc(sd.team)}${userTag(sd.owner)} <span class="muted">(left the league)</span>`);
  const tradeDate = (tr) => (tr.ts ? new Date(tr.ts).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "");
  const signed = (x) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${fmt(Math.abs(x))}`;
  // from one team's point of view: 1 won, -1 lost, 0 even
  const outcome = (tr, i) => (tr.even ? 0 : tr.sides[i].net > 0 ? 1 : -1);

  function whereNow(rid, receiver) {
    if (rid == null) return "free agent now";
    if (rid === receiver && receiver != null) return "still on the roster";
    return `now on ${teamHTML(rid)}`;
  }
  function pastAsset(a, receiver) {
    const clickable = (id) => (byId[id] ? `data-player="${esc(id)}"` : "");
    if (a.k === "faab") {
      return `<div class="past-row"><span class="dot PK">$</span><span><span class="aname">$${fmt(a.amount)} FAAB</span><span class="meta"><span>not counted in value</span></span></span><span class="aval muted">–</span></div>`;
    }
    if (a.k === "player") {
      const tag = byId[a.id] ? "button" : "div";
      return `<${tag} class="past-row" ${tag === "button" ? `type="button" ${clickable(a.id)}` : ""}>
        <span class="dot ${esc(a.pos || "")}">${esc(a.pos || "?")}</span>
        <span><span class="aname">${esc(a.name)}</span><span class="meta"><span>${whereNow(a.now_on, receiver)}</span></span></span>
        <span class="aval">${fmt(a.value)}</span></${tag}>`;
    }
    const owner = a.orig_team ? `${esc(a.orig_team)}${/s$/i.test(a.orig_team) ? "'" : "'s"}` : "";
    const head = `${a.season} round ${a.round}${a.status === "used" ? ` <span class="slotno">${esc(a.pick)}</span>` : ""}`;
    if (a.status === "used") {
      const b = a.became || {};
      const tag = byId[b.id] ? "button" : "div";
      return `<${tag} class="past-row pick" ${tag === "button" ? `type="button" ${clickable(b.id)}` : ""}>
        <span class="dot PK">PK</span>
        <span><span class="aname">${head}</span><span class="meta"><span>${owner} pick</span></span>
          <span class="became">Became <b>${esc(b.name || "?")}</b>${b.pos ? ` <span class="dot ${esc(b.pos)}">${esc(b.pos)}</span>` : ""} <span class="muted">${whereNow(b.now_on, receiver)}</span></span></span>
        <span class="aval">${fmt(a.value)}</span></${tag}>`;
    }
    const meta = a.status === "future"
      ? `${owner} pick, not drafted yet${a.exp_slot ? `, projected ${slotLabel(a.round, a.exp_slot)}` : ""}`
      : `${owner} pick, player taken not found`;
    return `<div class="past-row pick"><span class="dot PK">PK</span>
      <span><span class="aname">${head}</span><span class="meta"><span>${meta}</span></span></span>
      <span class="aval">${a.status === "future" ? fmt(a.value) : "–"}</span></div>`;
  }
  function tradeCard(tr, focus) {
    const fi = focus != null ? sideOf(tr, focus) : -1;
    const order = tr.sides.map((_, i) => i).sort((a, b) => (b === fi) - (a === fi));
    let verdict;
    if (fi >= 0) {
      const o = outcome(tr, fi), m = tr.sides[fi].net;
      verdict = o === 0 ? `<span class="verdict-chip even">Even</span>`
        : `<span class="verdict-chip ${o > 0 ? "won" : "lost"}">${o > 0 ? "Won" : "Lost"} by ${fmt(Math.abs(m))}</span>`;
    } else {
      verdict = tr.even ? `<span class="verdict-chip even">Even</span>`
        : `<span class="verdict-chip won">${esc(tr.sides[tr.winner]?.team || "")} won by ${fmt(tr.margin)}</span>`;
    }
    return `
      <article class="tcard">
        <header><span><b>${tr.season}${tr.week ? `, week ${tr.week}` : ""}</b> <span class="muted">${tradeDate(tr)}</span></span>${verdict}</header>
        <div class="tsides" style="--n:${tr.sides.length}">
          ${order.map((i) => {
            const sd = tr.sides[i];
            return `<section class="tside ${i === fi ? "focus" : ""}">
              <h3>${sideName(sd)} <span class="muted">got</span></h3>
              ${sd.got.map((a) => pastAsset(a, sd.roster_id)).join("") || `<p class="note">Nothing</p>`}
              <div class="tsum"><span>Worth ${fmt(sd.got_value)} today${sd.got.filter((a) => a.k !== "faab").length > 1 ? ` <span class="muted">(${fmt(sd.got_raw)} before the package discount)</span>` : ""}</span>
                <b class="${signCls(sd.net)}">${signed(sd.net)}</b></div>
            </section>`;
          }).join("")}
        </div>
      </article>`;
  }
  function renderPastTrades() {
    const all = D.trades || [];
    const tid = state.histTeam === "all" ? null : Number(state.histTeam);
    const wid = tid == null || state.histWith === "all" ? null : Number(state.histWith);
    let list = all;
    if (tid != null) list = list.filter((tr) => involves(tr, tid));
    if (wid != null) list = list.filter((tr) => involves(tr, wid));
    const net = (tr) => (tid != null ? tr.sides[sideOf(tr, tid)].net : Math.abs(tr.margin));
    const sorters = {
      new: (a, b) => (b.ts || 0) - (a.ts || 0),
      old: (a, b) => (a.ts || 0) - (b.ts || 0),
      swing: (a, b) => Math.abs(b.margin) - Math.abs(a.margin),
      best: (a, b) => net(b) - net(a),
      worst: (a, b) => net(a) - net(b),
    };
    const sortOpts = tid != null
      ? [["new", "Newest first"], ["old", "Oldest first"], ["best", "Best for this team"], ["worst", "Worst for this team"], ["swing", "Most lopsided"]]
      : [["new", "Newest first"], ["old", "Oldest first"], ["swing", "Most lopsided"]];
    if (!sortOpts.some(([k]) => k === state.histSort)) state.histSort = "new";
    list = [...list].sort(sorters[state.histSort]);

    let summary;
    if (tid != null) {
      const res = list.map((tr) => outcome(tr, sideOf(tr, tid)));
      const total = list.reduce((x, tr) => x + tr.sides[sideOf(tr, tid)].net, 0);
      const picksIn = list.reduce((x, tr) => x + tr.sides[sideOf(tr, tid)].got.filter((a) => a.k === "pick").length, 0);
      const picksOut = list.reduce((x, tr) => x + tr.sides.reduce((y, sd) => y + sd.got.filter((a) => a.k === "pick" && a.from === sideOf(tr, tid)).length, 0), 0);
      summary = board([
        { v: list.length, l: plural(list.length, "trade").replace(/^\d+ /, "") + (wid != null ? ` with ${esc(teamOf(wid)?.team || "")}` : ""), mark: true },
        { v: `${res.filter((x) => x > 0).length}–${res.filter((x) => x < 0).length}–${res.filter((x) => x === 0).length}`, cls: "long", l: "Won, lost, even" },
        { v: `${total > 0 ? "+" : total < 0 ? "−" : ""}${Math.abs(total) >= 1000 ? `${(Math.abs(total) / 1000).toFixed(1)}k` : fmt(Math.abs(total))}`, cls: `${signCls(total)} long`, l: "Net value today" },
        { v: `${picksIn}/${picksOut}`, l: "Picks got, picks given" },
      ]);
    } else {
      const evenN = list.filter((tr) => tr.even).length;
      const usedPicks = list.reduce((x, tr) => x + tr.sides.reduce((y, sd) => y + sd.got.filter((a) => a.k === "pick" && a.status === "used").length, 0), 0);
      summary = board([
        { v: list.length, l: "Trades in league history" },
        { v: pct(list.length ? evenN / list.length : null), l: "Even at today's values" },
        { v: usedPicks, l: "Traded picks already used" },
      ]);
    }
    return `
      <div class="controls">
        <select id="hist-team" aria-label="Team"><option value="all" ${tid == null ? "selected" : ""}>All teams</option>${teamOptions(tid)}</select>
        ${tid != null ? `<select id="hist-with" aria-label="Trades with"><option value="all" ${wid == null ? "selected" : ""}>With any team</option>${teams().filter((t) => t.roster_id !== tid).sort((a, b) => a.team.localeCompare(b.team)).map((t) => `<option value="${t.roster_id}" ${t.roster_id === wid ? "selected" : ""}>With ${esc(teamText(t.roster_id))}</option>`).join("")}</select>` : ""}
        ${sortSelect("hist-sort", state.histSort, sortOpts)}
      </div>
      ${summary}
      <div class="tlist">${list.map((tr) => tradeCard(tr, tid)).join("") || `<div class="empty"><b>No trades</b>${tid != null ? "This team hasn't made a trade" : "No trades yet"}${wid != null ? " with that team" : ""}.</div>`}</div>
      ${how("How past trades are judged", [
        `Everything is valued at today's values, not what it was worth on the day. Players count at their current value wherever they are now. A draft pick that has been used counts as the player taken with it; a pick not yet used counts at its pick value and projected slot.`,
        `Each side's haul is combined the same way as in the trade lab: two equal pieces count as about 1.7 of one, because only so many players start. The number on each side is what it got minus what it gave. A trade is even when every side is within 10%.`,
        `FAAB moved in a trade is shown but not valued. Tap a player to see his value breakdown.`,
      ])}`;
  }

  // ---------------------------------------------------------------- Activity + History
  function renderActivity() {
    const items = D.activity || [];
    if (!items.length) return `<div class="empty"><b>No moves yet</b>Trades and waiver claims show up here after the next refresh.</div>`;
    const label = { trade: "Trade", waiver: "Waiver claim", free_agent: "Free agent", commissioner: "Commissioner" };
    const line = (m) => {
      if (m.kind === "add") return `<div class="move"><span class="k add">+</span>${esc(m.player)} <span class="muted">${esc(m.pos || "")} to ${nameHTML(m.to)}${m.from ? ` from ${nameHTML(m.from)}` : ""}${m.amount != null ? `, $${m.amount}` : ""}</span></div>`;
      if (m.kind === "drop") return `<div class="move"><span class="k drop">−</span>${esc(m.player)} <span class="muted">${esc(m.pos || "")} dropped by ${nameHTML(m.to)}</span></div>`;
      if (m.kind === "pick") return `<div class="move"><span class="k add">+</span>${esc(m.pick)} <span class="muted">to ${nameHTML(m.to)}</span></div>`;
      if (m.kind === "faab") return `<div class="move"><span class="k add">+</span>$${m.amount} FAAB <span class="muted">to ${nameHTML(m.to)}</span></div>`;
      return "";
    };
    return `<h2>Recent moves</h2><div class="group">${items.map((t) => `
      <div class="entry">
        <div class="entry-head"><b>${label[t.type] || esc(t.type)}</b><span>${t.ts ? new Date(t.ts).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : ""}${t.week ? `, week ${t.week}` : ""}</span></div>
        ${t.moves.map(line).join("")}
      </div>`).join("")}</div>`;
  }
  function renderHistory() {
    const cols = [
      { key: "season", label: "Season", value: (s) => Number(s.season), html: (s) => esc(s.season) },
      { key: "champ", label: "Champion", value: (s) => s.champion || "", html: (s) => (s.champion ? nameHTML(s.champion) : `<span class="muted">${s.status === "complete" ? "–" : "In progress"}</span>`) },
      { key: "trades", label: "Trades", num: 1, value: (s) => s.trades, html: (s) => s.trades },
      { key: "moves", label: "Moves", num: 1, value: (s) => s.transactions, html: (s) => s.transactions },
    ];
    return `<h2>Seasons</h2>${sortableTable("history", cols, D.history || [], { key: "season", dir: -1 })}`;
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
    const parts = [["Projection model", p.model, "model"], ["FantasyCalc", p.fc, "market"], ["DynastyProcess", p.dp, "market"]].filter(([, v]) => v != null);
    const top = Math.max(...parts.map(([, v]) => v), 1);
    const owner = p.owner == null ? "Free agent" : `On ${teamHTML(p.owner)}`;
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
      ${p.owner != null ? `<button class="btn primary block" style="margin-top:16px" data-tradefor="${esc(p.id)}">Build a trade for ${esc(p.name)}</button>
        <button class="btn block" style="margin-top:8px" data-shop="${esc(p.id)}">Shop him for ${esc(teamOf(p.owner)?.team || "his team")}</button>` : ""}`;
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
  const TABS = { league: renderLeague, team: renderTeam, values: renderValues, picks: renderPicks, plan: renderPlan, trade: renderTrade, trades: renderPastTrades, owners: renderOwners, activity: renderActivity, history: renderHistory };
  const TITLES = { league: "League", team: "Team", values: "Values", picks: "Picks", plan: "Plan", trade: "Trade", trades: "Past trades", owners: "Managers", activity: "Moves", history: "History" };
  const FIRST_DIR = { rank: 1, team: 1, age: 1, champ: 1 };   // first tap: ascending for these, descending otherwise
  function route(keepScroll = false) {
    const y = window.scrollY;
    const tab = (location.hash || "#league").slice(1);
    const key = TABS[tab] ? tab : "league";
    view.innerHTML = D.meta ? TABS[key]() : `<div class="empty"><b>Couldn't load league data</b>Check your connection, then tap the reload button at the top.</div>`;
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
  const refreshValues = () => { document.getElementById("val-list").innerHTML = valuesList(); };

  document.addEventListener("click", (e) => {
    const t = e.target;
    if (t.closest("[data-close]")) { closeSheet(); return; }
    const pl = t.closest("[data-player]");
    if (pl) { openSheet(pl.dataset.player); return; }
    const srt = t.closest("[data-sort]");
    if (srt) {
      const [id, key] = srt.dataset.sort.split(":");
      const cur = state.sort[id];
      state.sort[id] = cur && cur.key === key ? { key, dir: -cur.dir } : { key, dir: FIRST_DIR[key] ?? -1 };
      rerender();
      return;
    }
    const tf = t.closest("[data-tradefor]");
    if (tf) {
      const id = tf.dataset.tradefor, owner = byId[id]?.owner ?? null;
      state.partner = owner;
      if (state.team === owner) setTeam(null);
      state.get.clear(); state.get.add(id); state.give.clear();
      closeSheet(); location.hash = "#trade"; route();
      return;
    }
    const sh = t.closest("[data-shop]");
    if (sh) {
      const id = sh.dataset.shop, owner = byId[id]?.owner ?? null;
      if (state.team !== owner) { setTeam(owner); state.give.clear(); }
      if (state.partner === owner) state.partner = null;
      state.give.add(id); state.get.clear();
      closeSheet(); location.hash = "#trade"; route();
      return;
    }
    const hl = t.closest("[data-hist]");
    if (hl) { state.histTeam = hl.dataset.hist; state.histWith = hl.dataset.with || "all"; if (location.hash === "#trades") route(); }
    const r = t.closest("[data-roster]");
    if (r) { setTeam(Number(r.dataset.roster)); }
    const vp = t.closest("[data-vp]");
    if (vp) { state.valPos = vp.dataset.vp; state.valLimit = 100; rerender(); }
    if (t.closest("[data-more]")) { state.valLimit += 100; refreshValues(); }
    if (t.closest("[data-clear]")) { state.give.clear(); state.get.clear(); rerender(); }
    if (t.closest("[data-totop]")) {
      const el = document.getElementById("trade-result") || document.getElementById(state.team ? "trade-b" : "trade-a");
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
      if (el?.tagName === "SELECT") el.focus();
    }
    const ld = t.closest("[data-load]");
    if (ld) {
      const f = (D.finder || []).find((x) => x.id === ld.dataset.load);
      if (f) {
        setTeam(f.team); state.partner = f.partner;
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
    const num = t.value ? Number(t.value) : null;
    switch (t.id) {
      case "trade-a":
        setTeam(num); state.give.clear();
        if (state.partner === num) { state.partner = null; state.get.clear(); }
        rerender(); break;
      case "trade-b":
        state.partner = num; state.get.clear(); rerender(); break;
      case "team-pick": setTeam(num); rerender(); break;
      case "pick-team": state.pickTeam = t.value; rerender(); break;
      case "pick-sort": state.pickSort = t.value; rerender(); break;
      case "hist-team": state.histTeam = t.value; state.histWith = "all"; rerender(); break;
      case "hist-with": state.histWith = t.value; rerender(); break;
      case "hist-sort": state.histSort = t.value; rerender(); break;
      case "roster-sort": state.rosterSort = t.value; rerender(); break;
      case "val-owner": state.valOwner = t.value; state.valLimit = 100; refreshValues(); break;
      case "val-sort": state.valSort = t.value; state.valLimit = 100; refreshValues(); break;
      default: break;
    }
  });
  document.addEventListener("input", (e) => {
    if (e.target.id === "val-search") { state.valQuery = e.target.value; state.valLimit = 100; refreshValues(); }
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

  view.innerHTML = `<div class="empty"><b>Loading the league</b></div>`;
  load().then(() => { header(); route(true); });
  setInterval(header, 60000);

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {}));
  }
})();

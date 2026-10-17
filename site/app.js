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
  const FILES = ["meta", "teams", "rosters", "picks", "activity", "history", "values", "plan", "owners", "finder", "trades", "weekly"];

  const state = {
    team: null,            // the team being viewed on Team / Plan, and "Team A" in the trade lab
    partner: null,         // "Team B" in the trade lab
    give: new Set(), get: new Set(),
    pickTeam: "all", valOwner: "all", valPos: "ALL", valQuery: "", valLimit: 100,
    valSort: "val", rosterSort: "val", pickSort: "slot",
    histTeam: "all", histWith: "all", histSort: "new", mgr: null, weekView: "lineup", tradeView: "total",
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
    judged.clear();
    byId = {};
    for (const p of D.values || []) byId[p.id] = { ...p };
    for (const [rid, list] of Object.entries(D.rosters || {})) {
      for (const p of list) byId[p.id] = { ...(byId[p.id] || {}), ...p, owner: Number(rid) };
    }
    byTeamName = {};
    for (const t of teams()) { t.team = (t.team || "").trim(); byTeamName[t.team] = t; }
    if (state.team == null) state.team = Number(store.get("team")) || null;
    state.weekView = store.get("weekView") || state.weekView;
    state.tradeView = store.get("tradeView") || state.tradeView;
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

  // ---------------------------------------------------------------- Week
  // This week: the viewed team's lineup check, every matchup's odds, and waiver targets with FAAB bids.
  const SLOT_LABEL = { FLEX: "FLEX", SUPER_FLEX: "SFLX", REC_FLEX: "R/T", WRRB_FLEX: "W/R" };
  const wk = () => D.weekly || {};
  const wp = (pid) => (wk().players || {})[pid] || {};
  const wt = (rid) => (wk().teams || {})[rid];
  function kickText(iso) {
    if (!iso) return "";
    return new Date(iso).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
  }
  function gameText(x) {
    if (!x || !x.team) return "No team";
    if (x.st === "bye") return "Bye";
    if (x.st === "final") return `${esc(x.opp || "")} final`;
    if (x.st === "live") return `${esc(x.opp || "")} live`;
    return `${esc(x.opp || "")} ${kickText(x.kick)}`;
  }
  // A player in a weekly list: name button (opens the sheet when we know him), team and game.
  function wkName(pid, extra = "") {
    const x = wp(pid);
    const inj = x.inj ? `<span class="chip out">${esc(x.inj)}</span>` : "";
    const nm = byId[pid] ? `<button class="linkish" type="button" data-player="${esc(pid)}">${esc(x.name || pid)}</button>` : esc(x.name || pid);
    return `<span class="dot ${esc(x.pos)}">${esc(x.pos || "?")}</span>${nm}${inj}${extra}
      <span class="meta"><span>${esc(x.pos === "DEF" ? "" : x.team || "FA") || "Defense"}</span><span>${gameText(x)}</span></span>`;
  }
  function wkPoints(pid) {
    const x = wp(pid);
    if (x.st === "final") return `<b>${f1(x.pts)}</b><small>scored</small>`;
    if (x.st === "live") return `<b>${f1(x.pts)}</b><small>so far, ${f1(x.proj)} exp.</small>`;
    return `<b>${f1(x.proj)}</b><small>proj</small>`;
  }
  function scoreboard(rid) {
    const T = wt(rid);
    if (!T) return "";
    const opp = T.opp != null ? wt(T.opp) : null;
    const side = (r, t, me) => `
      <div class="side ${me ? "me" : ""}">
        <span class="who">${teamHTML(r)}</span>
        <b>${f1(t.mean)}</b>
        <small>${t.now > 0 ? `${f1(t.now)} scored so far` : "projected"}</small>
      </div>`;
    if (!opp) return `<div class="score solo">${side(rid, T, true)}<p class="note">No opponent this week.</p></div>`;
    return `
      <div class="score">
        ${side(rid, T, true)}
        <span class="vs">vs</span>
        ${side(T.opp, opp, false)}
        <div class="tug" role="img" aria-label="Win odds ${pct(T.win)}"><i style="width:${Math.round(T.win * 100)}%"></i></div>
        <div class="odds2"><span>${pct(T.win)} to win</span><span>${pct(opp.win)}</span></div>
      </div>`;
  }
  function weekLineup(rid) {
    const T = wt(rid);
    if (!T) return `<div class="empty"><b>No lineup</b>This team isn't playing this week.</div>`;
    const B = T.best;
    const start = new Set(B.start), sit = new Set(B.sit);
    const name = (p) => esc(wp(p).name || p);
    const lede = B.gain >= 0.5
      ? `<p class="lede"><b>${B.start.length ? `Start ${B.start.map(name).join(", ")}` : "Fill the empty slot"}${B.sit.length ? ` over ${B.sit.map(name).join(", ")}` : ""}.</b>
           That's <b>+${f1(B.gain)} projected points</b>${T.win != null ? `, and win odds go from ${pct(T.win)} to ${pct(B.win)}` : ""}.</p>`
      : `<p class="lede"><b>Lineup is set.</b> No bench player projects for more than the starter he'd replace.</p>`;
    const minor = T.issues.filter((i) => i.minor);
    const slotRow = (s) => {
      const issue = T.issues.find((i) => i.pid === s.pid && !i.minor);
      const cls = s.pid == null || issue ? "bad" : sit.has(s.pid) ? "warnrow" : "";
      return `<div class="lrow ${cls}">
        <span class="slotl">${SLOT_LABEL[s.slot] || esc(s.slot)}</span>
        <span class="lname">${s.pid ? wkName(s.pid, sit.has(s.pid) ? `<span class="chip out">Bench him</span>` : "") : `<b class="down">Empty slot</b>`}</span>
        <span class="val">${s.pid ? wkPoints(s.pid) : ""}</span></div>`;
    };
    const bench = ((D.rosters || {})[rid] || []).filter((p) => p.slot === "bench" && wk().players?.[p.id])
      .sort((a, b) => (wp(b.id).proj || 0) - (wp(a.id).proj || 0));
    return `
      ${lede}
      ${minor.length ? `<p class="note">Game-time decisions: ${minor.map((i) => `${name(i.pid)} (${esc(i.why)})`).join(", ")}. Questionable players are projected at 80% of their usual output, doubtful at 20%.</p>` : ""}
      <h2>Starters</h2>
      <div class="group">${T.slots.map(slotRow).join("")}</div>
      <h2>Bench<span class="count">${bench.length}</span></h2>
      <div class="group">${bench.map((p) => `<div class="lrow ${start.has(p.id) ? "good" : ""}">
        <span class="slotl">BN</span>
        <span class="lname">${wkName(p.id, start.has(p.id) ? `<span class="chip start">Start him</span>` : "")}</span>
        <span class="val">${wkPoints(p.id)}</span></div>`).join("") || `<div class="empty"><b>Empty bench</b></div>`}</div>`;
  }
  function weekMatchups(rid) {
    const W = wk();
    const side = (r) => {
      const t = wt(r);
      return `<div class="mside ${r === rid ? "me" : ""}">
        <span class="who"><button class="linkish" type="button" data-wkteam="${r}">${teamHTML(r)}</button>
          <small>${t.best.gain >= 0.5 ? `${f1(t.best.gain)} pts left on the bench` : "Lineup set"}${t.now > 0 ? `, ${f1(t.now)} scored` : ""}</small></span>
        <b>${f1(t.mean)}</b><em class="${t.win >= 0.5 ? "up" : "muted"}">${pct(t.win)}</em></div>`;
    };
    return `
      <div class="mlist">${(W.matchups || []).map(([a, b]) => `
        <div class="mcard ${a === rid || b === rid ? "mine" : ""}">${side(a)}${side(b)}</div>`).join("")}</div>
      <p class="note">Projected final score and win odds with the lineups set right now. Tap a team to check its lineup.</p>`;
  }
  function weekWaivers(rid) {
    const W = wk();
    const recs = (W.waivers || {})[rid] || [];
    const drop = (W.drop || {})[rid];
    const left = (W.faab_left || {})[rid];
    const name = (p) => esc(wp(p).name || p);
    const rivalText = (r) => r.rivals?.length
      ? `Also wanted by ${r.rivals.map((x) => `${esc(teamOf(x.rid)?.team || "?")} (~$${fmt(x.worth)})`).join(", ")}`
      : "No other team gains much";
    const pos = POS_ORDER.filter((p) => (W.available || {})[p]?.length);
    return `
      ${board([
        { v: W.budget ? `$${fmt(left)}` : "–", l: W.budget ? `FAAB left of $${fmt(W.budget)}` : "No FAAB budget", mark: true },
        { v: W.bid_median != null ? `$${fmt(W.bid_median)}` : "–", l: `Typical winning bid (${plural(W.bid_count || 0, "claim")})` },
        { v: drop?.open ? "Open" : "Full", l: drop?.open ? "Roster spot free" : "Roster, add means drop" },
      ])}
      ${drop && !drop.open ? `<p class="note">To make room, drop <b>${name(drop.pid)}</b>: losing him costs this team the least (${f1(drop.loss)} points of lineup and future value).</p>` : ""}
      <h2>Best adds for this team</h2>
      <div class="group">${recs.map((r) => `<div class="lrow wv">
          <span class="lname">${wkName(r.pid)}
            <span class="meta why"><span>+${f1(r.gain)} lineup pts this season</span><span>${f1(r.future)} value beyond</span></span>
            <span class="meta"><span>${r.bid === 0 ? "No better than other free agents: claim for $0" : rivalText(r)}</span></span></span>
          <span class="val">${r.bid != null ? `<b>$${fmt(r.bid)}</b><small>bid</small>` : `<b>${f1(r.net)}</b><small>pts</small>`}</span></div>`).join("")
        || `<div class="empty"><b>Nobody worth a claim</b>No free agent beats what this roster already has. Check again after injuries.</div>`}</div>
      <h2>Best available this week</h2>
      <div class="cols">${pos.map((p) => `<div><h3 class="poshead"><span class="dot ${p}">${p}</span>${p === "DEF" ? "Defenses" : p === "K" ? "Kickers" : p}</h3>
        <div class="group">${W.available[p].slice(0, 5).map((id) => `<div class="lrow"><span class="lname">${wkName(id)}</span>
          <span class="val"><b>${f1(wp(id).proj)}</b><small>proj</small></span></div>`).join("")}</div></div>`).join("")}</div>
      ${(W.bids || []).length ? `<h2>Biggest winning bids this season</h2>
        <div class="group">${W.bids.map((b) => `<div class="lrow"><span class="lname">${wkName(b.pid)}<span class="meta"><span>${teamHTML(b.rid)}</span><span>week ${b.week}</span></span></span>
          <span class="val"><b>$${fmt(b.amount)}</b></span></div>`).join("")}</div>` : ""}`;
  }
  function renderWeek() {
    const W = wk();
    if (!W.week) return `<div class="empty"><b>No games this week</b>Lineup checks, matchup odds and waiver picks show up here during the season.</div>`;
    const rid = focusTeam();
    const v = state.weekView;
    const seg = [["lineup", "Lineup"], ["matchups", "Matchups"], ["waivers", "Waivers"]];
    return `
      ${teamPicker()}
      <p class="note wk-head">${W.phase === "playoffs" ? "Playoffs, w" : "W"}eek ${W.week}. Projections updated ${ago(W.made)}.</p>
      ${scoreboard(rid)}
      <div class="controls" style="margin-top:14px"><div class="seg" role="group" aria-label="View">
        ${seg.map(([k, l]) => `<button data-wv="${k}" class="${v === k ? "on" : ""}" aria-pressed="${v === k}">${l}</button>`).join("")}</div></div>
      ${v === "matchups" ? weekMatchups(rid) : v === "waivers" ? weekWaivers(rid) : weekLineup(rid)}
      ${how("How this week's numbers work", [
        `Each player's projection is the model's rest-of-season points per game, nudged by his team's betting-line point total for this game against its usual total (a team expected to score 10% more gets about 5% more). Kickers and defenses use their points so far, adjusted the same way; a defense facing a low-scoring offense projects higher. Byes and players ruled out count zero.`,
        `Tested on past seasons: the betting-line nudge cut weekly projection error slightly (7.28 to 7.26 points); the opponent's points allowed to the position added nothing, so it's left out. A single week is mostly noise: a 12-point projection lands anywhere from about 3 to 21.`,
        `Win odds assume each player's score varies around his projection by the amount seen in past games. Points already scored count as final; games in progress count what's scored plus the unplayed share. Players whose games have started are locked, as on Sleeper.`,
        `Waiver adds: the points a player would add to this team's best lineup in each remaining week (playoff weeks weighted by playoff odds), plus his model value beyond this season. The bid pays only for his edge over the next-best free agent, since that one costs nothing: a share of remaining FAAB that grows with the edge, kept just above what the most interested rival should pay. Injury news updates each morning; scores update every two hours on game days.`,
      ])}`;
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
  const GOAL_CLS = { "Win the title": "g-title", Contend: "g-win", "Aim to contend": "g-win", "Make the playoffs": "g-push",
    "Push or sell": "g-push", Build: "g-build", Develop: "g-build", "Sell and reload": "g-sell", "Sell and collect": "g-sell", Retool: "g-sell" };
  const SELLING = new Set(["reload", "rebuild"]);
  function yearCard(y, i) {
    const label = i === 0 ? `${y.season} <span class="muted">rest of season</span>` : `${y.season}`;
    const gap = (x) => `<span class="${signCls(x.gap)}">${x.gap > 0 ? "+" : ""}${f1(x.gap)}</span>`;
    const spot = (x) => `${esc(x.slot)} ${x.name ? `<span class="muted">${esc(x.name)}</span> ` : ""}${gap(x)}`;
    return `
      <section class="ycard">
        <header><h3>${label}</h3><span class="goal ${GOAL_CLS[y.goal] || ""}">${esc(y.goal)}</span></header>
        <p class="yrank">Lineup ranks <b>${ordinal(y.rank)}</b> as the roster stands <span class="muted">(${f1(y.strength)} ppg; league median ${f1(y.median_strength)})</span></p>
        ${y.moves.length ? `<ul class="moves">${y.moves.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : ""}
        <dl class="yfacts">
          ${y.holes.length ? `<dt>Weak spots</dt><dd>${y.holes.map(spot).join("; ")}</dd>` : ""}
          ${y.strong.length ? `<dt>Strengths</dt><dd>${y.strong.map(spot).join("; ")}</dd>` : ""}
          ${y.draft.length ? `<dt>${y.season} draft</dt><dd>${y.draft.map((d) => `${d.exp_slot ? slotLabel(d.round, d.exp_slot) : `Rd ${d.round}`}${d.own ? "" : ` <span class="muted">from ${esc(d.from)}</span>`}`).join(", ")}</dd>` : ""}
        </dl>
      </section>`;
  }
  function renderPlan() {
    const rid = focusTeam();
    const P = (D.plan || {})[rid];
    const t = teamOf(rid);
    if (!P || !P.roster_id || !P.strategy) return `${teamPicker()}<div class="empty"><b>No plan yet</b>Plans fill in after the next full refresh.</div>`;
    const S = P.strategy;
    const selling = SELLING.has(S.key);
    const firstSeason = Math.min(...(D.picks || []).map((p) => Number(p.season)));
    const ownFirst = (D.picks || []).find((p) => p.owner === rid && p.round === 1 && Number(p.season) === firstSeason && p.odds?.length);
    const deadline = P.weeks_to_deadline ? `Deadline in ${plural(P.weeks_to_deadline, "week")}` : "Trade deadline passed";
    const boardItems = selling
      ? [{ v: esc(P.record), l: `${P.exp_wins ?? "–"} wins projected`, mark: true },
         { v: pct(P.p_first_pick), l: "Chance at the 1.01" },
         { v: P.exp_slot ? P.exp_slot.toFixed(1) : "–", l: `Projected slot, top-3 ${pct(P.p_top3)}` },
         { v: P.weeks_to_deadline ? `Wk ${P.trade_deadline_week}` : "–", l: deadline }]
      : [{ v: esc(P.record), l: `${P.exp_wins ?? "–"} wins projected`, mark: true },
         { v: pct(P.p_playoffs), l: "Playoff odds" },
         { v: pct(P.p_title), l: "Title odds" },
         { v: P.weeks_to_deadline ? `Wk ${P.trade_deadline_week}` : "–", l: deadline }];
    const buyTitle = S.target_season === D.meta?.season ? `Starters to add for ${S.target_season}` : `Targets for ${S.target_season}`;
    const sellTitle = { chips: "Trade chips", shop: "Veterans to shop", sell: `Sell before ${S.target_season}` }[P.sell_kind] || "Sell";
    const buyers = (c) => (c.buyers?.length
      ? `Would start for ${c.buyers.map((b) => `${nameHTML(b.team)} (+${b.upgrade_ppg}${b.season !== D.meta?.season ? ` in ${b.season}` : ""})`).join(", ")}`
      : "No team needs him as a starter right now");
    return `
      ${teamPicker()}
      <section class="strategy">
        <p class="who">${esc(t?.team)}${userTag(t?.owner)} ${winChip(P.window)}</p>
        <h2>${esc(S.title)}</h2>
        <p class="lede">${esc(S.summary)}</p>
        <ul class="why">${S.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>
      </section>
      ${board(boardItems)}
      <h2>Three-season plan</h2>
      <div class="years">${P.years.map(yearCard).join("")}</div>
      ${selling && ownFirst ? `<div class="group pad" style="margin-top:12px"><h3>Where their ${ownFirst.season} first lands</h3>${oddsChart(ownFirst)}<p class="note">Chance of each slot. Yellow bars are top-3 picks.</p></div>` : ""}
      <div class="cols">
        <div>
          <h2>${buyTitle}<span class="count">${(P.buy || []).length}</span></h2>
          <div class="group">${(P.buy || []).map((c) => planRow(c, [esc(c.reason)])).join("") || `<div class="empty">No realistic upgrades found for ${S.target_season}.</div>`}</div>
        </div>
        <div>
          <h2>${sellTitle}<span class="count">${(P.sell || []).length + (P.chips || []).length}</span></h2>
          <div class="group">${(P.sell || []).map((c) => planRow(c, [esc(c.reason), buyers(c)])).join("")}
            ${(P.chips || []).map((c) => `
              <div class="pick"><span class="slot">${c.season}</span>
                <span><span class="pname">Round ${c.round}${c.own ? "" : ` <span class="muted">from ${esc(c.from)}</span>`}</span>
                <span class="meta"><span>${c.exp_slot ? `about ${slotLabel(c.round, c.exp_slot)}` : ""}</span><span>${esc(c.reason)}</span></span></span>
                <span class="val"><b>${fmt(c.value)}</b></span></div>`).join("")}
            ${!(P.sell || []).length && !(P.chips || []).length ? `<div class="empty">Nothing to move right now.</div>` : ""}
          </div>
        </div>
      </div>
      <h2>Build around<span class="count">${(P.keep || []).length}</span></h2>
      <div class="group">${(P.keep || []).map((c) => planRow(c, [esc(c.reason)])).join("") || `<div class="empty">No core players yet.</div>`}</div>
      ${how("How the plan is built", [
        `The strategy comes from this season's playoff and title odds (10,000 simulated seasons) and how the team's best possible lineup ranks now, next season and in two seasons as the roster stands. Contenders with a lasting lineup stay on top; contenders that fade push now or make a run and retool; teams that get stronger later build toward that season; teams out of the race reload or rebuild. Each strategy has a target season the plan aims at.`,
        `Each season card compares every starting spot with the league's median starter at that spot (weak spots and strengths), lists players reaching their position's age cliff (QB 32, RB 26, WR 28, TE 29) as their projection falls, young players stepping up, and the team's picks in that year's draft.`,
        `Targets are players who'd start for this team in the target season and who their own team doesn't need then: veterans on rebuilding teams, or bench players on contenders. Young core players on building teams are left out. Sell lists show what won't help in the target season (contenders: bench depth and distant picks to spend), with the teams each player would start for.`,
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
  const SELLERS = new Set(["reload", "rebuild"]);
  const BUYERS = new Set(["sustain", "allin", "run"]);
  const daysAgo = (ms) => (ms ? Math.max(0, Math.round((Date.now() - ms) / 86400000)) : null);
  const agoText = (d) => (d == null ? "no moves yet" : d === 0 ? "today" : d === 1 ? "yesterday" : `${d} days ago`);
  const posList = (o) => Object.entries(o || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${v}`).join(", ") || "none";
  const MOVE_NAME = { trade: "trade", waiver: "waiver claim", free_agent: "free-agent add", commissioner: "commissioner move" };

  // Plain-language advice for approaching a manager, from their plan, needs and trade history.
  function scoutTips(p) {
    const sc = p.scout || {};
    const P = (D.plan || {})[p.roster_id];
    const tips = [];
    const dl = D.meta?.settings?.trade_deadline;
    if (P?.strategy) {
      const key = P.strategy.key, tgt = P.strategy.target_season;
      const holes = (P.years?.[0]?.holes || []).map((h) => h.slot);
      const holesLater = ((P.years || []).find((y) => y.season === tgt)?.holes || []).map((h) => h.slot);
      const sells = (P.sell || []).slice(0, 2).map((c) => c.name);
      if (key === "run") {
        tips.push(`Making a run with a lineup that fades after this season: they'll add cheap veteran starters${holes.length ? ` at ${holes.join(", ")}` : ""} but shouldn't pay in firsts. Their future picks may be gettable.`);
      } else if (key === "climb") {
        tips.push(`Competing now but built for ${tgt}: open to cheap upgrades${holes.length ? ` at ${holes.join(", ")}` : ""}, holding on to picks and young players.`);
      } else if (BUYERS.has(key)) {
        tips.push(`Buying for ${tgt}${holes.length ? `, thinnest at ${holes.join(", ")}` : ""}: pitch a starter there for picks or young players${dl ? ` before the week ${dl} deadline` : ""}.`);
      } else if (SELLERS.has(key)) {
        tips.push(`Building for ${tgt}: likely to sell veterans${sells.length ? ` like ${sells.join(" and ")}` : ""}. Lead with picks or players 24 and under${holesLater.length ? `; they'll want help at ${holesLater.join(", ")}` : ""}.`);
      } else if (key === "deadline") {
        tips.push(`On the playoff bubble: they could buy or sell by week ${dl}. Offer both ways${sells.length ? ` (their veterans: ${sells.join(", ")})` : ""}.`);
      } else {
        tips.push(`Aiming at ${tgt}: they want young starters${holesLater.length ? ` at ${holesLater.join(", ")}` : ""} and won't spend their future on this season.`);
      }
    }
    if (sc.got_age && sc.gave_age) {
      const d = sc.gave_age - sc.got_age;
      if (d >= 2) tips.push(`Trades for youth: players they get are ${d.toFixed(1)} years younger on average than what they give. Young players open doors.`);
      else if (d <= -2) tips.push(`Trades for veterans: they take on players ${(-d).toFixed(1)} years older than they give. Your aging starters fit here.`);
    }
    const pc = sc.pieces || {};
    if (p.trades >= 3 && pc.got && pc.gave) {
      if (pc.gave - pc.got >= 0.5) tips.push(`Consolidates: gives ${pc.gave} pieces for ${pc.got} on average. Ask for their depth for one of your stars.`);
      else if (pc.got - pc.gave >= 0.5) tips.push(`Splits stars into depth: takes ${pc.got} pieces for ${pc.gave}. Offer two good players for one of theirs.`);
    }
    const pk = sc.picks || {};
    if ((pk.firsts_gave || 0) - (pk.firsts_got || 0) >= 2) tips.push(`Has traded away ${pk.firsts_gave} first-round picks (got ${pk.firsts_got}): their firsts are on the table.`);
    else if ((pk.firsts_got || 0) - (pk.firsts_gave || 0) >= 2) tips.push(`Collects first-round picks (got ${pk.firsts_got}, gave ${pk.firsts_gave}): expect them to ask for picks.`);
    if (sc.avg_margin != null && p.trades >= 3) {
      if (sc.avg_margin >= 400) tips.push(`Comes out ahead: +${fmt(sc.avg_margin)} per trade at today's values. Expect hard bargaining.`);
      else if (sc.avg_margin <= -400) tips.push(`Past trades have favored their partners (${fmt(sc.avg_margin)} per trade at today's values).`);
    }
    const d = daysAgo(sc.last_move);
    if (d != null && d >= 21) tips.push(`No moves in ${d} days: may be slow to answer.`);
    else if ((sc.moves_30d || 0) >= 8) tips.push(`Very active (${sc.moves_30d} moves in 30 days): likely to answer quickly.`);
    if (sc.timing && sc.timing.offseason + sc.timing.in_season >= 4 && sc.timing.offseason / (sc.timing.offseason + sc.timing.in_season) >= 0.7) {
      tips.push("Does most trading in the offseason.");
    }
    return tips.slice(0, 5);
  }

  // Compact card (trade lab "About" panels).
  function profileCard(p, compactCard = false) {
    const sc = p.scout || {};
    const r = sc.trade_record || {};
    const tips = scoutTips(p).slice(0, 3);
    return `
      <div class="group pad">
        ${compactCard ? "" : `<div class="entry-head"><b>${esc(p.team)}${userTag(p.owner)}</b>${winChip(p.window)}</div>`}
        ${(p.tags || []).length ? `<div class="tags">${p.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>` : ""}
        ${tips.length ? `<ul class="tips">${tips.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}
        <dl class="facts">
          <dt>Trades</dt><dd>${p.trades ? `${r.won}–${r.lost}–${r.even} (won, lost, even), net <span class="${signCls(p.trade_net)}">${signed(p.trade_net)}</span>. <a href="#trades" data-hist="${p.roster_id}">See them</a>` : "none yet"}</dd>
          <dt>Last move</dt><dd>${agoText(daysAgo(sc.last_move))}</dd>
        </dl>
        <p class="note"><a href="#owners" data-mgr="${p.roster_id}">Full scouting report</a></p>
      </div>`;
  }

  function mgrSection(title, body) {
    return body ? `<section class="mgr-sec"><h3>${title}</h3>${body}</section>` : "";
  }

  function scoutReport(p) {
    const sc = p.scout || {};
    const P = (D.plan || {})[p.roster_id];
    const t = teamOf(p.roster_id);
    const r = sc.trade_record || {};
    const pk = sc.picks || {};
    const pc = sc.pieces || {};
    const tm = sc.timing || {};
    const y0 = P?.years?.[0], ytgt = (P?.years || []).find((y) => y.season === P?.strategy?.target_season);
    const spot = (x) => `<li><b>${esc(x.slot)}</b> ${x.name ? `${esc(x.name)} ` : ""}<span class="muted">${f1(x.ppg)} ppg vs ${f1(x.median)} league median</span></li>`;
    const needs = [
      y0?.holes?.length ? `<p class="lbl">This season</p><ul class="spots">${y0.holes.map(spot).join("")}</ul>` : "",
      ytgt && ytgt !== y0 && ytgt.holes?.length ? `<p class="lbl">${ytgt.season} (their target)</p><ul class="spots">${ytgt.holes.map(spot).join("")}</ul>` : "",
      (!ytgt || ytgt === y0) && P?.years?.[1]?.holes?.length ? `<p class="lbl">Next season</p><ul class="spots">${P.years[1].holes.map(spot).join("")}</ul>` : "",
      y0?.strong?.length ? `<p class="lbl">Deep at</p><ul class="spots">${y0.strong.map((x) => `<li><b>${esc(x.slot)}</b> ${esc(x.name || "")} <span class="up">+${f1(x.gap)}</span></li>`).join("")}</ul>` : "",
    ].join("");
    const moves = (P?.sell || []).slice(0, 5);
    const chips = (P?.chips || []).slice(0, 3);
    const d = daysAgo(sc.last_move);
    const cur = (sc.seasons || []).find((x) => !x.complete);
    const statRow = (label, value, note = "") => `<div class="stat"><span>${label}</span><b>${value}</b>${note ? `<small>${note}</small>` : ""}</div>`;
    const ages = sc.got_age && sc.gave_age ? `${f1(sc.got_age)} / ${f1(sc.gave_age)}` : "–";
    return `
      <section class="mgr-head">
        <p class="who">${esc(t?.team || p.team)}${userTag(p.owner)} ${winChip(p.window)}</p>
        <h2>${esc(P?.strategy?.title || "Manager")}</h2>
        <p class="lede">${t ? `${record(t)}, ${pct(t.p_playoffs)} playoff odds. ` : ""}Last move ${agoText(d)}${sc.last_move_type ? ` (${MOVE_NAME[sc.last_move_type] || sc.last_move_type})` : ""}; ${plural(sc.moves_30d || 0, "move")} in the last 30 days.</p>
        ${(p.tags || []).length ? `<div class="tags">${p.tags.map((x) => `<span class="tag">${esc(x)}</span>`).join("")}</div>` : ""}
        <div class="mgr-actions">
          <button class="btn primary" data-tradewith="${p.roster_id}">Build a trade with them</button>
          <a class="btn" href="#trades" data-hist="${p.roster_id}">Their ${plural(p.trades, "trade")}</a>
          <a class="btn" href="#plan" data-roster="${p.roster_id}">Their plan</a>
        </div>
      </section>
      ${(() => { const tips = scoutTips(p); return tips.length ? `<h2>How to approach them</h2><div class="group pad"><ul class="tips big">${tips.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""; })()}
      <div class="cols">
        <div>
          <h2>What they need</h2>
          <div class="group pad">${needs || `<p class="note">No weak spots against the league median.</p>`}</div>
        </div>
        <div>
          <h2>What they'd move</h2>
          <div class="group">${moves.map((c) => planRow(c, [esc(c.reason)])).join("")}
            ${chips.map((c) => `<div class="pick"><span class="slot">${c.season}</span><span><span class="pname">Round ${c.round}${c.own ? "" : ` <span class="muted">from ${esc(c.from)}</span>`}</span><span class="meta"><span>${c.exp_slot ? `about ${slotLabel(c.round, c.exp_slot)}` : ""}</span></span></span><span class="val"><b>${fmt(c.value)}</b></span></div>`).join("")}
            ${!moves.length && !chips.length ? `<div class="empty">Nothing obvious to pry loose.</div>` : ""}</div>
        </div>
      </div>
      <h2>How they trade</h2>
      <div class="group pad">
        <div class="stats">
          ${statRow("Record", p.trades ? `${r.won}–${r.lost}–${r.even}` : "–", "won, lost, even")}
          ${statRow("Net value", p.trades ? signed(p.trade_net) : "–", sc.avg_margin != null ? `${signed(sc.avg_margin)} per trade` : "")}
          ${statRow("Pieces per deal", pc.got != null ? `${pc.got} / ${pc.gave}` : "–", "get / give")}
          ${statRow("Player age", ages, "get / give")}
          ${statRow("Picks", `${pk.got || 0} / ${pk.gave || 0}`, `firsts ${pk.firsts_got || 0} / ${pk.firsts_gave || 0}`)}
          ${statRow("When", `${tm.in_season || 0} / ${tm.offseason || 0}`, "in season / offseason")}
        </div>
        <dl class="facts">
          <dt>Gets</dt><dd>${posList(sc.got_pos)}</dd>
          <dt>Gives</dt><dd>${posList(sc.gave_pos)}</dd>
          ${(sc.vs || []).length ? `<dt>Partners</dt><dd><ul class="partners">${sc.vs.slice(0, 6).map((x) => `<li><span>${nameHTML(x.team)}</span><span class="muted">${plural(x.n, "trade")}</span><b class="${signCls(x.net)}">${signed(x.net)}</b></li>`).join("")}</ul></dd>` : ""}
          ${sc.best_trade ? `<dt>Best / worst</dt><dd><span class="up">${signed(sc.best_margin)}</span> / <span class="${signCls(sc.worst_margin)}">${signed(sc.worst_margin)}</span> <a href="#trades" data-hist="${p.roster_id}" data-histsort="best">see them</a></dd>` : ""}
        </dl>
      </div>
      <div class="cols">
        <div>
          <h2>Track record</h2>
          <div class="group pad">
            <table class="mini"><thead><tr><th>Season</th><th class="num">Record</th><th class="num">Finish</th><th class="num">Lineups</th></tr></thead>
              <tbody>${(sc.seasons || []).slice().reverse().map((x) => `<tr><td>${x.season}${x.complete ? "" : " <span class=\"muted\">so far</span>"}</td>
                <td class="num">${x.wins}–${x.losses}${x.ties ? `–${x.ties}` : ""}</td>
                <td class="num">${x.place ? (x.place === 1 ? "Champion" : ordinal(x.place)) : `${ordinal(x.rank)} in standings`}</td>
                <td class="num">${x.eff ? `<span class="${x.league_eff && x.eff >= x.league_eff ? "up" : "down"}">${Math.round(x.eff * 100)}%</span>` : "–"}</td></tr>`).join("")}</tbody></table>
            <p class="note">Lineups: points scored as a share of the best lineup they could have started${cur?.league_eff ? ` (league average ${Math.round(cur.league_eff * 100)}% this season)` : ""}.</p>
          </div>
        </div>
        <div>
          <h2>Rookie drafts</h2>
          <div class="group">${(sc.rookie_picks || []).slice(0, 8).map((x) => {
            const tag = byId[x.id] ? "button" : "div";
            return `<${tag} class="past-row" ${tag === "button" ? `type="button" data-player="${esc(x.id)}"` : ""}><span class="slotno">${x.season}</span><span><span class="aname">${esc(x.name || "?")}</span><span class="meta"><span>round ${x.round}, pick ${x.pick_no}</span></span></span><span class="aval">${fmt(x.value)}</span></${tag}>`; }).join("") || `<div class="empty">No rookie picks made here yet.</div>`}
          </div>
          ${sc.rookie_score != null ? `<p class="note">Their picks are worth ${Math.round(sc.rookie_score * 100)}% of what the league's picks in the same rounds are worth today.</p>` : ""}
          <dl class="facts" style="margin-top:8px">
            <dt>Waivers</dt><dd>${p.waiver_adds} claims, ${p.fa_adds} free-agent adds, $${fmt(p.faab_spent)} FAAB this season${p.max_bid ? ` (top bid $${fmt(p.max_bid)})` : ""}</dd>
            ${Object.keys(p.draft_mix_other_leagues || {}).length ? `<dt>Elsewhere</dt><dd>Early picks in ${p.other_leagues} other leagues: ${Object.entries(p.draft_mix_other_leagues).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${Math.round(v * 100)}%`).join(", ")}</dd>` : ""}
          </dl>
        </div>
      </div>`;
  }

  function renderOwners() {
    const O = D.owners || { profiles: [] };
    if (!O.profiles.length) return `<div class="empty"><b>No manager data yet</b>Profiles fill in after the next refresh.</div>`;
    const rid = state.mgr ?? state.team ?? O.profiles[0].roster_id;
    const p = O.profiles.find((x) => x.roster_id === rid) || O.profiles[0];
    const cols = [
      { key: "team", label: "Team", value: (x) => (x.team || "").toLowerCase(),
        html: (x) => `<a class="tname" href="#owners" data-mgr="${x.roster_id}">${esc(x.team)}</a><span class="sub2"><span class="user">@${esc(x.owner)}</span></span>` },
      { key: "plan", label: "Plan", wide: 1, value: (x) => (D.plan?.[x.roster_id]?.strategy?.title || ""), html: (x) => esc(D.plan?.[x.roster_id]?.strategy?.title || "–") },
      { key: "trades", label: "Trades", num: 1, value: (x) => x.trades, html: (x) => { const r = x.scout?.trade_record; return r ? `${r.won}–${r.lost}–${r.even}` : x.trades; } },
      { key: "net", label: "Net", num: 1, value: (x) => x.trade_net, html: (x) => `<span class="${signCls(x.trade_net)}">${x.trade_net > 0 ? "+" : ""}${compact(x.trade_net)}</span>` },
      { key: "last", label: "Last move", num: 1, value: (x) => -(x.scout?.last_move || 0), html: (x) => { const d = daysAgo(x.scout?.last_move); return d == null ? "–" : `${d}d`; } },
      { key: "eff", label: "Lineups", num: 1, wide: 1, value: (x) => (x.scout?.seasons || []).find((y) => !y.complete)?.eff ?? null,
        html: (x) => { const e = (x.scout?.seasons || []).find((y) => !y.complete)?.eff; return e ? `${Math.round(e * 100)}%` : "–"; } },
      { key: "faab", label: "FAAB", num: 1, wide: 1, value: (x) => x.faab_spent, html: (x) => `$${fmt(x.faab_spent)}` },
    ];
    return `
      <div class="controls"><select id="mgr-pick" aria-label="Manager">${teamOptions(p.roster_id)}</select></div>
      ${scoutReport(p)}
      <h2>All managers<span class="count">${O.profiles.length}</span></h2>
      ${sortableTable("owners", cols, O.profiles, { key: "trades", dir: -1 }, (x) => (x.roster_id === p.roster_id ? "me" : ""))}
      ${how("How the scouting reports work", [
        `Built from every trade, waiver claim and draft in this league's history. Trade results count each player's banked value (his starts for the team since the trade) plus what's left of his value, with used draft picks counted as the player taken (or, for picks flipped before the draft, the slot's worth); a trade is even within 10%. "Pieces per deal" and "player age" compare what they get with what they give.`,
        `Needs and players they'd move come from their team plan (the Plan tab): weak spots are starting slots below the league's median starter. Lineups is points scored as a share of the best lineup they could have started. Advice is generated from all of this; treat it as a starting point.`,
      ])}`;
  }

  // ---------------------------------------------------------------- Past trades
  // One toggle picks how every trade is valued; each asset then shows one number and each trade one verdict.
  const TRADE_VIEWS = [
    ["total", "Results so far", "What each team got out of the trade: points its new players scored in its starting lineup, plus the value they have left (or had when the team moved them on)."],
    ["today", "Today's value", "Everything each team got, at today's values, wherever those players are now."],
    ["day", "On the day", "Everything at the values of the day the trade was made: was it fair when it was struck? Only trades from October 16, 2026 on."],
  ];
  const involves = (tr, rid) => tr.sides.some((sd) => sd.roster_id === rid);
  const sideOf = (tr, rid) => tr.sides.findIndex((sd) => sd.roster_id === rid);
  const sideName = (sd) => (sd.roster_id != null ? teamHTML(sd.roster_id) : `${esc(sd.team)}${userTag(sd.owner)} <span class="muted">(left the league)</span>`);
  const tradeDate = (tr) => (tr.ts ? new Date(tr.ts).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "");
  const signed = (x) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${fmt(Math.abs(x))}`;
  const assetVal = (a, view) => (a.k === "faab" ? null : view === "today" ? (a.today ?? a.value) : view === "day" ? a.day : a.value);
  const pkgSum = (vals) => Math.pow(vals.reduce((x, v) => x + Math.pow(Math.max(0, v), PKG), 0), 1 / PKG);
  // Judge a trade under one view: per side got/gave/net, plus even/winner/margin. null if values are missing.
  const judged = new Map();
  function judge(tr, view) {
    const key = `${tr.id}:${view}`;
    if (judged.has(key)) return judged.get(key);
    const assets = tr.sides.flatMap((sd) => sd.got.filter((a) => a.k !== "faab"));
    let res = null;
    if (!assets.some((a) => assetVal(a, view) == null)) {
      const sides = tr.sides.map((sd, i) => {
        const got = Math.round(pkgSum(sd.got.filter((a) => a.k !== "faab").map((a) => assetVal(a, view))));
        const gave = Math.round(pkgSum(tr.sides.flatMap((o) => o.got.filter((a) => a.k !== "faab" && a.from === i)).map((a) => assetVal(a, view))));
        return { got, gave, net: got - gave };
      });
      const big = Math.max(1, ...sides.map((x) => x.got));
      const even = sides.every((x) => Math.abs(x.net) <= FAIR * big);
      const best = sides.reduce((b, x, i) => (x.net > sides[b].net ? i : b), 0);
      res = { sides, even, winner: even ? null : best, margin: sides[best].net };
    }
    judged.set(key, res);
    return res;
  }
  // from one team's point of view: 1 won, -1 lost, 0 even
  const outcome = (j, i) => (j.even ? 0 : j.sides[i].net > 0 ? 1 : -1);

  function whereNow(rid, receiver) {
    if (rid == null) return "free agent now";
    if (rid === receiver && receiver != null) return "still on the roster";
    return `now on ${teamHTML(rid)}`;
  }
  function pastAsset(a, receiver, view) {
    const clickable = (id) => (byId[id] ? `data-player="${esc(id)}"` : "");
    const v = assetVal(a, view);
    const num = `<span class="aval">${v == null ? "–" : fmt(v)}</span>`;
    const row = (id, dot, name, meta, extraCls = "") => {
      const tag = id && byId[id] ? "button" : "div";
      return `<${tag} class="past-row ${extraCls}" ${tag === "button" ? `type="button" ${clickable(id)}` : ""}>
        ${dot}<span><span class="aname">${name}</span><span class="meta"><span>${meta}</span></span></span>${num}</${tag}>`;
    };
    if (a.k === "faab") {
      return `<div class="past-row"><span class="dot PK">$</span><span><span class="aname">$${fmt(a.amount)} FAAB</span><span class="meta"><span>not counted in value</span></span></span><span class="aval muted">–</span></div>`;
    }
    const starts = view === "total" && a.starts ? `, ${plural(a.starts, "start")} for them` : "";
    if (a.k === "player") {
      return row(a.id, `<span class="dot ${esc(a.pos || "")}">${esc(a.pos || "?")}</span>`, esc(a.name), `${whereNow(a.now_on, receiver)}${starts}`);
    }
    const owner = a.orig_team ? `${esc(a.orig_team)}${/s$/i.test(a.orig_team) ? "'" : "'s"} pick` : "pick";
    const name = `${a.season} round ${a.round}${a.pick ? ` <span class="slotno">${esc(a.pick)}</span>` : ""}`;
    const dot = `<span class="dot PK">PK</span>`;
    if (view === "day") return row(null, dot, name, `${owner}, before the draft`, "pick");
    const b = a.became || {};
    if (a.status === "flipped") {
      const by = a.drafted_by != null ? esc(teamOf(a.drafted_by)?.team || a.drafted_by_team || "another team") : esc(a.drafted_by_team || "another team");
      return row(null, dot, name, `${owner}, passed on before the draft (${by} took ${esc(b.name || "?")}); valued as the slot`, "pick");
    }
    if (a.status === "used" || a.became) {
      return row(b.id, dot, `${name} <span class="muted">became</span> ${esc(b.name || "?")}`, `${owner}, ${whereNow(b.now_on, receiver)}${starts}`, "pick");
    }
    return row(null, dot, name, a.status === "future"
      ? `${owner}, not drafted yet${a.exp_slot ? `, projected ${slotLabel(a.round, a.exp_slot)}` : ""}`
      : `${owner}, player taken not found`, "pick");
  }
  function tradeCard(tr, focus, view) {
    const j = judge(tr, view);
    const fi = focus != null ? sideOf(tr, focus) : -1;
    const order = tr.sides.map((_, i) => i).sort((a, b) => (b === fi) - (a === fi));
    let verdict;
    if (fi >= 0) {
      const o = outcome(j, fi), m = j.sides[fi].net;
      verdict = o === 0 ? `<span class="verdict-chip even">Even</span>`
        : `<span class="verdict-chip ${o > 0 ? "won" : "lost"}">${o > 0 ? "Won" : "Lost"} by ${fmt(Math.abs(m))}</span>`;
    } else {
      verdict = j.even ? `<span class="verdict-chip even">Even</span>`
        : `<span class="verdict-chip won">${esc(tr.sides[j.winner]?.team || "")} won by ${fmt(j.margin)}</span>`;
    }
    return `
      <article class="tcard">
        <header><span><b>${tr.season}${tr.week ? `, week ${tr.week}` : ""}</b> <span class="muted">${tradeDate(tr)}</span></span>${verdict}</header>
        <div class="tsides" style="--n:${tr.sides.length}">
          ${order.map((i) => {
            const sd = tr.sides[i], js = j.sides[i];
            return `<section class="tside ${i === fi ? "focus" : ""}">
              <h3>${sideName(sd)} <span class="muted">got</span></h3>
              ${sd.got.map((a) => pastAsset(a, sd.roster_id, view)).join("") || `<p class="note">Nothing</p>`}
              <div class="tsum"><span>Got ${fmt(js.got)}, gave ${fmt(js.gave)}</span>
                <b class="${signCls(js.net)}">${signed(js.net)}</b></div>
            </section>`;
          }).join("")}
        </div>
      </article>`;
  }
  function renderPastTrades() {
    const view = TRADE_VIEWS.some(([k]) => k === state.tradeView) ? state.tradeView : "total";
    const viewInfo = TRADE_VIEWS.find(([k]) => k === view);
    const all = (D.trades || []).filter((tr) => judge(tr, view));
    const tid = state.histTeam === "all" ? null : Number(state.histTeam);
    const wid = tid == null || state.histWith === "all" ? null : Number(state.histWith);
    let list = all;
    if (tid != null) list = list.filter((tr) => involves(tr, tid));
    if (wid != null) list = list.filter((tr) => involves(tr, wid));
    const net = (tr) => (tid != null ? judge(tr, view).sides[sideOf(tr, tid)].net : Math.abs(judge(tr, view).margin));
    const sorters = {
      new: (a, b) => (b.ts || 0) - (a.ts || 0),
      old: (a, b) => (a.ts || 0) - (b.ts || 0),
      swing: (a, b) => Math.abs(judge(b, view).margin) - Math.abs(judge(a, view).margin),
      best: (a, b) => net(b) - net(a),
      worst: (a, b) => net(a) - net(b),
    };
    const sortOpts = tid != null
      ? [["new", "Newest first"], ["old", "Oldest first"], ["best", "Best for this team"], ["worst", "Worst for this team"], ["swing", "Most lopsided"]]
      : [["new", "Newest first"], ["old", "Oldest first"], ["swing", "Most lopsided"]];
    if (!sortOpts.some(([k]) => k === state.histSort)) state.histSort = "new";
    list = [...list].sort(sorters[state.histSort]);

    let summary = "";
    if (list.length && tid != null) {
      const res = list.map((tr) => outcome(judge(tr, view), sideOf(tr, tid)));
      const total = list.reduce((x, tr) => x + judge(tr, view).sides[sideOf(tr, tid)].net, 0);
      const picksIn = list.reduce((x, tr) => x + tr.sides[sideOf(tr, tid)].got.filter((a) => a.k === "pick").length, 0);
      const picksOut = list.reduce((x, tr) => x + tr.sides.reduce((y, sd) => y + sd.got.filter((a) => a.k === "pick" && a.from === sideOf(tr, tid)).length, 0), 0);
      summary = board([
        { v: list.length, l: plural(list.length, "trade").replace(/^\d+ /, "") + (wid != null ? ` with ${esc(teamOf(wid)?.team || "")}` : ""), mark: true },
        { v: `${res.filter((x) => x > 0).length}–${res.filter((x) => x < 0).length}–${res.filter((x) => x === 0).length}`, cls: "long", l: "Won, lost, even" },
        { v: `${total > 0 ? "+" : total < 0 ? "−" : ""}${Math.abs(total) >= 1000 ? `${(Math.abs(total) / 1000).toFixed(1)}k` : fmt(Math.abs(total))}`, cls: `${signCls(total)} long`, l: "Net value" },
        { v: `${picksIn}/${picksOut}`, l: "Picks got, picks given" },
      ]);
    } else if (list.length) {
      const evenN = list.filter((tr) => judge(tr, view).even).length;
      summary = board([
        { v: list.length, l: "Trades" },
        { v: pct(evenN / list.length), l: "Even (within 10%)" },
        { v: list.filter((tr) => !judge(tr, view).even).length, l: "Lopsided" },
      ]);
    }
    const empty = view === "day" && !all.length
      ? `<div class="empty"><b>No trades to judge yet</b>Values have been saved daily since October 16, 2026. Trades made from then on show here.</div>`
      : `<div class="empty"><b>No trades</b>${tid != null ? "This team hasn't made a trade" : "No trades yet"}${wid != null ? " with that team" : ""}.</div>`;
    return `
      <div class="controls">
        <div class="seg fill" role="group" aria-label="Value trades by">
          ${TRADE_VIEWS.map(([k, l]) => `<button data-tv="${k}" class="${view === k ? "on" : ""}" aria-pressed="${view === k}">${l}</button>`).join("")}
        </div>
      </div>
      <p class="note tv-note">${viewInfo[2]}</p>
      <div class="controls" style="margin-top:12px">
        <select id="hist-team" aria-label="Team"><option value="all" ${tid == null ? "selected" : ""}>All teams</option>${teamOptions(tid)}</select>
        ${tid != null ? `<select id="hist-with" aria-label="Trades with"><option value="all" ${wid == null ? "selected" : ""}>With any team</option>${teams().filter((t) => t.roster_id !== tid).sort((a, b) => a.team.localeCompare(b.team)).map((t) => `<option value="${t.roster_id}" ${t.roster_id === wid ? "selected" : ""}>With ${esc(teamText(t.roster_id))}</option>`).join("")}</select>` : ""}
        ${sortSelect("hist-sort", state.histSort, sortOpts)}
      </div>
      ${summary}
      <div class="tlist">${list.map((tr) => tradeCard(tr, tid, view)).join("") || empty}</div>
      ${how("How past trades are judged", [
        `<b>Results so far</b> counts what each team actually got out of the deal. For each player: the points he scored in that team's starting lineup after the trade, above a replacement starter at his position (playoff starts count double), turned into value at the rate the model uses; plus the value he has left, or had on the day the team traded or dropped him. A player who retires keeps what he banked, so trading for an aging star isn't marked as a loss once he's done.`,
        `<b>Today's value</b> counts everything at today's values, wherever the players are now. <b>On the day</b> uses the values saved on the day of the trade (daily since October 16, 2026), which shows whether the deal was fair when it was made.`,
        `Draft picks: a pick a team used counts as the player it took. A pick a team passed on before the draft counts at what that slot turned out to be worth (players taken within three picks of it), since another team made the choice. A pick not yet used counts at its pick value.`,
        `Each side's haul is combined as in the trade lab: two equal pieces count as about 1.7 of one. A trade is even when every side is within 10%. FAAB is shown but not valued.`,
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
  const TABS = { league: renderLeague, week: renderWeek, team: renderTeam, values: renderValues, picks: renderPicks, plan: renderPlan, trade: renderTrade, trades: renderPastTrades, owners: renderOwners, activity: renderActivity, history: renderHistory };
  const TITLES = { league: "League", week: "Week", team: "Team", values: "Values", picks: "Picks", plan: "Plan", trade: "Trade", trades: "Past trades", owners: "Managers", activity: "Moves", history: "History" };
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
    if (hl) {
      state.histTeam = hl.dataset.hist; state.histWith = hl.dataset.with || "all";
      if (hl.dataset.histsort) state.histSort = hl.dataset.histsort;
      if (location.hash === "#trades") route();
    }
    const mg = t.closest("[data-mgr]");
    if (mg) { state.mgr = Number(mg.dataset.mgr); if (location.hash === "#owners") { rerender(); window.scrollTo(0, 0); } }
    const tw = t.closest("[data-tradewith]");
    if (tw) {
      const rid = Number(tw.dataset.tradewith);
      state.partner = rid; if (state.team === rid) setTeam(null);
      state.give.clear(); state.get.clear();
      location.hash = "#trade";
      return;
    }
    const r = t.closest("[data-roster]");
    if (r) { setTeam(Number(r.dataset.roster)); }
    const tv = t.closest("[data-tv]");
    if (tv) { state.tradeView = tv.dataset.tv; store.set("tradeView", state.tradeView); rerender(); return; }
    const wkt = t.closest("[data-wkteam]");
    if (wkt) { setTeam(Number(wkt.dataset.wkteam)); state.weekView = "lineup"; rerender(); window.scrollTo(0, 0); return; }
    const wv = t.closest("[data-wv]");
    if (wv) { state.weekView = wv.dataset.wv; store.set("weekView", state.weekView); rerender(); }
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
      case "mgr-pick": state.mgr = num; rerender(); break;
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

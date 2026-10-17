# Dynasty Analyzer

A year-round analysis tool for the **Dynasty** league on Sleeper (league `1312163152787443712`).
It pulls everything Sleeper and nflverse publish into one database, values every player and
pick for this league's exact settings, refreshes itself on a schedule in GitHub's cloud, and
publishes an app you can install on your iPhone and Windows PC.

**App:** https://jquander02.github.io/FF-Dynasty/

## What's in the app

The app is neutral: nothing is tied to one manager. Every team name is shown with the
manager's Sleeper username (e.g. *WR's 4 Sale @jquander*). Tap any table heading to sort by
that column (tap again to reverse); player and pick lists have a "Sort by" menu.

- **League:** week, trade deadline, best record and most roster value; standings with playoff
  odds, value and each team's contention window, and a chart of every team's lineup strength
  now vs. the next two seasons
- **Week:** this week for any team, in three views. *Lineup*: the set lineup against the best
  one, with byes, players ruled out and game-time decisions flagged, what to start and sit,
  and how much the swap adds in points and win odds. *Matchups*: every game's projected score
  and win odds, live as games finish. *Waivers*: the free agents who'd add most to the team's
  lineup this season plus value beyond it, a FAAB bid for each, the rivals who'd want him,
  who to drop, the best kickers, defenses and fill-ins available this week, and the league's
  biggest winning bids
- **Team:** pick any team: roster with values, ages, injury and taxi tags, model-vs-market leans
- **Values:** every player ranked by dynasty value; search, filter by position and team, sort
  by value, age, projection, points per game or 7-day change
- **Picks:** every 2027–2029 pick with its projected slot, top-3 odds and value; filter by team
- **Plan:** the best way forward for any team. A strategy (Stay on top, Push for the title,
  Compete now and peak later, Make a run then retool, Build for next season, Decide at the
  deadline, Reload, Rebuild) with the reasons behind it, then a three-season plan for 2026, 2027
  and 2028: the goal for each season, where the lineup ranks as it stands, weak spots and
  strengths against the league's median starter, players aging out and stepping up, that
  year's draft picks, and the moves to make. Plus targets to buy for the target season, what
  to sell (or, for contenders, trade chips to spend), and who to build around
- **Trade:** pick Team A and Team B, tick players and picks on both sides, and see both sides'
  value and how each team's best lineup changes now, next year and in two years; suggested
  fair trades for every team; both managers' profiles. A bar at the bottom keeps the running
  totals in view.
- **Past trades:** every trade in the league's history, for all teams or one team (optionally
  only those with a given partner). Each side shows what it got, valued today and where each
  player is now. Each player counts as banked value (his points in that team's starting lineup
  since the trade, above a replacement starter at his position, playoff starts double, converted
  at the model's value-per-point rate) plus what's left (today's value while he's on the team, or
  his value on the day he was traded or dropped; a retired player keeps what he banked). Trades
  made since daily values began (October 2026) also get an "on the day" verdict at that day's
  values. A pick the team used shows the player it took ("2026 round 1, 1.03, became
  ..."); a pick the team traded on before the draft counts at what that slot was worth (today's
  average value of the players taken within three picks of it), since another team chose the
  player; future picks show their projected slot. Each trade gets a verdict (won, lost or even
  within 10%, using the trade lab's package math), and a team view adds its trade record, net
  value and picks in/out. Linked from League, Team, Managers and the trade lab.
- **Player sheet:** tap any player anywhere for his value breakdown (model, FantasyCalc,
  DynastyProcess), projected points per game for three seasons, this season's scoring, a value
  trend, and a one-tap "build a trade" or "shop him" into the trade lab
- **Managers:** a scouting report for any manager: their plan and how active they are; how to
  approach them (advice generated from their needs, plan and trade history); what they need
  this season and next and what they'd move (from their team plan); how they trade (won-lost-even
  at today's values, net and per-trade margin, pieces per deal, ages and positions they get vs
  give, picks and firsts bought and sold, in-season vs offseason, record with each partner, best
  and worst deals); track record (finishes, lineup efficiency vs the league); rookie drafts (what
  their picks are worth now vs the league's picks in the same rounds); waivers. Buttons open the
  trade lab with them, their past trades and their plan. A sortable table compares everyone.
- **Moves, History:**
  recent transactions; seasons and champions (linked from League; tabs on PC)

Works offline with the last data it loaded, follows your phone's light or dark mode, and the
reload button at the top fetches the newest refresh. Each screen has a "How this works" note.

## How player values work (phase 2)

1. **League scoring.** Every regular-season player-week from nflverse is re-scored with this
   league's exact Sleeper scoring settings, then checked against Sleeper's own points for
   the same weeks (the refresh summary reports the match rate and any scoring keys that
   can't be reproduced).
2. **Projection model.** Recent points per game (blended with expected fantasy points),
   weighted by recency and games played, adjusted with position age curves fit from
   nflverse history, with a draft-capital prior for rookies and young players. Projected
   for the rest of this season and the next three, then valued as points above a
   replacement-level player (sized from this league's lineup: 12 teams, 1 QB, 2 RB,
   2 WR, 1 TE, 1 FLEX), discounted by year.
3. **Market.** FantasyCalc (12-team, 1QB, full PPR dynasty, from real trades) and
   DynastyProcess values, stored daily.
4. **Blend.** All three are put on FantasyCalc's value scale by rank and blended
   60% model / 30% FantasyCalc / 10% DynastyProcess. Change the mix in `config.yaml`
   under `value_weights`.

**Backtest.** `dynasty/backtest.py` reruns the model as of each offseason from 2015 to 2024 using
only data available then and checks it two ways:
- *Points per game:* projections for the next three seasons against what happened, next to two
  baselines (last season's PPG, a recency-weighted average) and the luck floor (the error a
  perfect forecaster would still make from week-to-week randomness).
- *Value:* each player's model value against what he actually produced over the next three
  seasons (points above that season's replacement level, missed games counting as zero): how
  well value ranks production, how often the model's top 36 really finish top 36, and whether
  players beat or fall short of their valuation by position, age, NFL year and value tier.

Every refresh runs it and posts the scores as a "Backtest" annotation; the history bundle and
full report are saved to the `site-data` branch under `backtest/`. Locally:
`python -m dynasty.backtest run backtest-bundle.json.gz [--set key=value ...]`.

What the backtest changed (Oct 2026): the draft prior no longer counts seasons outside the data
as zeros and is now a smooth curve in draft slot (points per game played, with how often
draftees play feeding expected games); expected games come from each player's own playing time
(QBs: snaps, so backups' cameo games don't count as starts); projections are pulled slightly
toward the position average; the upside range used for value is narrower; and each position's
value is calibrated so a point of value means the same production at every position (QBs had
produced only about a third of their valuation). Tested on seasons the tuning never saw
(tuned on 2015-18, scored on 2019-21): value-to-production rank correlation 0.50 -> 0.62,
top-36 hit rate 49% -> 57%, next-season PPG error 3.45 -> 3.28 (luck floor about 1.5).
Per-position projection settings were tried and dropped: they won in-sample but not out of
sample. Late-season form and usage stats (target share, snaps) didn't improve the scores.

**Learned corrections (`dynasty/learned.py`).** Gradient-boosted trees (scikit-learn) trained on
the database's own history at four points each season (late August, after weeks 4, 8, 12). They
see workload and efficiency separately (targets, carries, pass attempts; yards per touch; TD
rates), role (snap, target and team shares), the season so far, team context as of that point
(new team, targets and carries that left, best teammate at the position, rookies drafted there,
the team's QB and volume), contract (cap share, guarantees, years left), games history, draft
slot, combine numbers, and the hand-built projection, and learn corrections to it. In
rolling-origin tests (each season predicted only from earlier ones) they improve the current
season: rest-of-season PPG error after week 4 3.42 -> 3.27, preseason 3.30 -> 3.25, and
current-season games-share error 0.27 -> 0.21. Applied to later seasons they made value rankings
worse, so the app uses them for this season only (`ModelConfig.learned_current`). Also tested
and left off: training trees directly on three-season value (overfits: 0.47-0.52 vs 0.60),
real outcome ranges instead of a bell curve (better-calibrated stars, slightly worse ranking),
touchdown regression, and extra weight on in-season games. Re-run with
`python -m dynasty.learned evaluate backtest-bundle.json.gz`.

Tables: `player_week`, `player_season`, `age_curve`, `draft_prior`, `player_projection`,
`market_value` (dated), `player_value` (dated, so value trends build up over time).

## Windows, draft odds and pick values (phase 3)

- **Team strength:** each roster's best starting lineup by projected points per game, for the
  rest of this season (injured players left out), next season, and two seasons out.
  Labels: *Dynasty power* (top third now and later), *Win now*, *Rising*, *Rebuild*, *Middle*.
- **Draft odds:** the rest of the regular season is simulated 10,000 times on Sleeper's real
  schedule. Weekly scores are drawn around a mix of points scored so far and projected lineup
  strength, with spread measured from this league's own matchups. The rookie draft is assumed
  linear, worst record first (ties by points for); the refresh summary checks that rule
  against the last rookie draft. Drafts after next use projected strength plus wide noise.
- **Pick value:** 60% from what this league's past rookie picks are worth now at each
  overall pick (fitted curve, discounted 10% per year out), 40% from FantasyCalc and
  DynastyProcess pick values; averaged over the slot odds.
- **Plan (built for every team):** the strategy comes from playoff and title odds this season
  and the team's lineup rank now, next season and in two seasons. Each strategy has a target
  season. Buy targets would start for the team in that season and aren't needed by their own
  team then (veterans on rebuilding teams, bench players on contenders; building teams' young
  cores are off-limits). Sells are players who won't help in the target season (age cliff: QB
  32, RB 26, WR 28, TE 29; falling projection; or not good enough), shown with the teams
  they'd start for. Season cards compare each starting spot with the league median.

Tables: `schedule`, `team_window`, `season_sim`, `pick_value`. Quick refreshes on game days
redo windows, odds and pick values with the latest scores.

## Trades and league-mates (phase 4)

- **Trade value:** a package counts as (sum of value^1.3)^(1/1.3): one piece counts in full,
  two equal pieces about 1.7x one. In a 12-team league the stud usually wins a 2-for-1.
  Fair = within 10%. The app's trade lab and the Python engine (`dynasty/trade.py`) use the
  same math.
- **Lineup impact:** best possible starting lineup before vs after, for both teams, now
  (injured players out), next season and in two seasons.
- **Trade finder:** run for every team. Searches 1-for-1 and 2-for-1 deals with every other
  team (its players for their players or picks), keeps fair ones that fit its window and help
  the partner's (contenders must improve now), and ranks them.
- **Owner profiles:** trades and partners, each past trade judged by what both sides hold now,
  positions traded for, picks bought vs sold, waiver claims and FAAB, early-round draft habits
  here and in their other Sleeper leagues, with short tags for what stands out.

Tables: `owner_profile`, `trade_review`, `trade_suggestion`.

## This week (dynasty/weekly.py)

- **Projection for one game:** the model's rest-of-season points per game, times (team's
  betting-line implied points this week ÷ its season-average implied points)^0.5, times the
  chance he plays (questionable 80%, doubtful 20%, out/IR 0); zero on a bye. Kickers use
  their league-scored points per game, defenses this league's points for them; a defense
  scales with the inverse of the opponent's implied total. Lines and kickoff times come from
  nflverse schedules (`nfl_schedules`).
- **Tested:** fit on 2015–20 player-weeks, checked on 2021–25. The relative betting-line
  factor cut weekly RMSE from 7.28 to 7.26; a raw implied total, larger exponents and the
  opponent's points allowed to the position were no better or worse.
- **Spread:** a player's score varies around his projection by a + b × projection (fit by
  position: QB 6.96 + 0.08p, RB 3.18 + 0.35p, WR 2.89 + 0.39p, TE 2.13 + 0.45p). Teammates'
  misses barely correlate (0.04), so team variances add. Win odds use a normal approximation.
- **Live:** finished games count actual points, games in progress count points so far plus
  the unplayed share of the projection, and started players are locked like on Sleeper.
  Quick refreshes every two hours on game days update scores; injury news updates each morning.
- **Waivers:** for every team and free agent, the points he adds to that team's best lineup in
  each remaining week (playoff weeks weighted by playoff odds) plus his model value beyond
  this season. Bids pay only for the edge over the next-best free agent (lineup gain over the
  best other free agent at his position, future value over the best other stash), since that
  one is free: bid = remaining FAAB × edge ÷ (edge + 150), capped at 75%, held to just above
  the most interested rival's worth when that's lower. Drop = the active player whose loss
  costs least.
- **Season sim:** this week's projected scores replace the flat team average for this week.

Tables: `week_team`; the full report is `raw_snapshot['weekly']`, exported as `weekly.json`.

---

## Where it lives

- **Code and scheduled refresh:** GitHub repo [jquander02/FF-Dynasty](https://github.com/jquander02/FF-Dynasty) (public)
- **App:** https://jquander02.github.io/FF-Dynasty/ (GitHub Pages, published by the refresh workflow)

No passwords, API keys or secrets are involved: Sleeper and nflverse are public, and GitHub
Pages deploys with the workflow's built-in permissions. Everything the app shows is already
public on Sleeper.

### Install the app
- **iPhone:** open the address in **Safari** → Share button → **Add to Home Screen**.
  It opens full screen with its own icon.
- **Windows PC:** open the address in **Edge** or **Chrome** → the install icon at the right
  of the address bar (or menu → **Apps → Install this site as an app**). Pin it to the taskbar.

### If Pages isn't switched on
The first deploy needs GitHub Pages set to use Actions: repo **Settings → Pages →
Build and deployment → Source: GitHub Actions**. Then re-run the workflow from the
**Actions** tab.

---

## How it refreshes

| When | What runs |
| --- | --- |
| Every day, 4:17am Central | Full refresh: league, players file, owners' other leagues, nflverse stats, ID mapping |
| Every 2 hours on Sun, Mon, Thu | Quick refresh: league data only (rosters, scores, transactions, picks) |
| Any time | Actions tab → Refresh and deploy → Run workflow |

The database is kept between runs in GitHub's Actions cache, and a copy is attached to each
run as a downloadable artifact for 7 days. If the cache is ever cleared, the next full run
rebuilds everything. Actions minutes are free and unlimited for public repos.

## Running it on your PC (optional)

Needs Python 3.11+ from [python.org](https://www.python.org/downloads/) (tick "Add to PATH").
In PowerShell, inside this folder:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m dynasty refresh -v          # full refresh into data\dynasty.db
python -m http.server -d site 8000    # then open http://localhost:8000
```

`python -m dynasty refresh --quick` skips the slow parts. `python -m dynasty status` shows
row counts. The database is plain SQLite, so you can also open `data\dynasty.db` in
[DB Browser for SQLite](https://sqlitebrowser.org) to explore it.

---

## What gets pulled

**Sleeper** (current season plus every past season through `previous_league_id`):

| Table | Contents |
| --- | --- |
| `league_season` | Settings, scoring, roster slots for each season |
| `owner`, `league_member` | Managers, team names, commissioner |
| `roster`, `roster_snapshot` | Record, PF/PA, max PF, FAAB used; daily snapshot of every roster slot |
| `matchup`, `matchup_player` | Weekly scores and each player's points, starter or bench |
| `txn`, `txn_move` | Every trade, waiver, FA move; one row per player, pick or FAAB moved |
| `traded_pick`, `draft_pick_owned` | Pick trades; current owner of every 2027–2029 pick |
| `draft`, `draft_pick_made` | Startup and rookie drafts, pick by pick |
| `bracket` | Playoff and consolation results |
| `player`, `trending` | Sleeper's full player file; daily add/drop trends |
| `owner_league`, `ext_draft_pick` | Each manager's other leagues and the picks they made there |
| `raw_snapshot`, `ingest_log` | Latest raw responses; what's been loaded |

**nflverse** (via `nflreadpy`, last 5 seasons + current):
weekly player stats, snap counts, expected fantasy points (`ff_opportunity`), rosters,
schedules with betting lines,
NFL draft picks and combine (12 seasons), depth charts, injuries, Next Gen Stats,
PFR advanced stats, contracts, consensus rankings, and the cross-site ID table.
Each lands in an `nfl_*` table.

**ID mapping:** `player_xref` links each Sleeper player to nflverse (gsis), PFR,
ESPN, KeepTradeCut and FantasyPros ids — first by nflverse's own Sleeper id column,
then Sleeper's gsis id, then name + birthdate.

## Code layout

```
dynasty/
  sleeper.py         Sleeper API client (caching, rate limiting, retries)
  ingest_sleeper.py  League history, rosters, matchups, transactions, picks, drafts, owners
  nflverse.py        nflverse datasets, one season at a time
  idmap.py           Sleeper ↔ nflverse id mapping
  store.py           SQLite schema and writers
  weekly.py          This week: projections, lineup checks, matchup odds, waiver bids
  export.py          JSON files the app reads (site/data/)
  __main__.py        Command line: refresh | export | status
site/                The installable web app (HTML/CSS/JS, offline-capable)
tests/               Offline tests (fixtures include this league's real settings)
.github/workflows/   Scheduled refresh + deploy
```

Run tests with `python -m unittest discover -s tests`.

## Known limits

- Built and tested offline: the build environment couldn't reach Sleeper or nflverse
  directly, so tests use this league's real settings and draft responses plus synthetic data
  in Sleeper's documented shapes. Watch the first Actions run's log for any warnings.
- nflverse datasets that aren't published yet (common early in a season) are skipped with a
  warning and retried next run.
- Rookie-draft lottery settings aren't exposed until the 2027 draft is created.


## Design

Game-day TV graphics: field green and chalk, with the yellow first-down line used for one thing
only, marking the team you're viewing (its row, its picks, its dot on the chart). Headings and
numbers use Big Shoulders Display (self-hosted in `site/fonts`, SIL Open Font License); body
text uses the phone's own font. No build step: `site/` is plain HTML, CSS and JavaScript.

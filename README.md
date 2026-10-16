# Dynasty Analyzer

A year-round analysis tool for the **Dynasty** league on Sleeper (league `1312163152787443712`).
Phase 1 is the data foundation: it pulls everything Sleeper and nflverse publish into one
database, refreshes it on a schedule in GitHub's cloud, and publishes an app you can install
on your iPhone and Windows PC.

**What's in the app now:**
- **League:** standings with playoff odds, roster value and each team's contention window, plus a
  chart of every team's strength now vs. the next two seasons
- **Team:** any roster with player values, ages and model-vs-market flags
- **Values:** every player ranked by dynasty value, filterable by position and owner
- **Picks:** every 2027–2029 pick with its projected slot, top-3 odds and value
- **Plan:** your rebuild planner: tank odds, your picks, who to sell before their age cliff
  (and which contenders need them), young buy targets on other teams' benches, your core
- **Trade:** pick a partner, tick players and picks on both sides, and see both sides' value
  and how each team's best lineup changes now, next year and in two years; plus suggested
  trades and the partner's profile
- **Owners:** every league-mate's trading, waiver and drafting habits, and every past trade
  judged by today's values
- **Activity / History:** recent transactions; seasons and champions (linked from League)

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
- **Plan:** sell candidates are your veterans at their position's age cliff (QB 32, RB 26,
  WR 28, TE 29) or projected to drop 15%+ in two seasons, with the contenders whose lineup
  they'd upgrade most; buy targets are players 24 or younger outside their team's best
  lineup, contenders' benches first.

Tables: `schedule`, `team_window`, `season_sim`, `pick_value`. Quick refreshes on game days
redo windows, odds and pick values with the latest scores.

## Trades and league-mates (phase 4)

- **Trade value:** a package counts as (sum of value^1.3)^(1/1.3): one piece counts in full,
  two equal pieces about 1.7x one. In a 12-team league the stud usually wins a 2-for-1.
  Fair = within 10%. The app's trade lab and the Python engine (`dynasty/trade.py`) use the
  same math.
- **Lineup impact:** best possible starting lineup before vs after, for both teams, now
  (injured players out), next season and in two seasons.
- **Trade finder:** searches 1-for-1 and 2-for-1 deals with every team (your players for
  their players or picks), keeps fair ones that fit your window and, for contenders, improve
  their lineup now, and ranks them.
- **Owner profiles:** trades and partners, each past trade judged by what both sides hold now,
  positions traded for, picks bought vs sold, waiver claims and FAAB, early-round draft habits
  here and in their other Sleeper leagues, with short tags for what stands out.

Tables: `owner_profile`, `trade_review`, `trade_suggestion`.

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

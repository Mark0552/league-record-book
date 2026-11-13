# 2 Many Chiefs Not Enough Indians — League Record Book

**Live site: https://mark0552.github.io/league-record-book/**

Complete league history **2014–present**: standings, live playoff + championship
odds, every game, every draft pick, career head-to-head, records (raw and
era-adjusted), money, luck, trades, and the halls of fame & shame.

## How it works
- `refresh.py` pulls live data (Sleeper API, the league Google Sheet, Sleeper +
  ESPN + FantasyPros weekly projections blended into an ensemble), runs a
  100k-season full-bracket simulation plus tie-exact clinch/elimination solvers,
  then calls `build.py`.
- `build.py` merges the full archive and renders `site/index.html` from
  `template.html`.
- `validate.py` asserts site-wide invariants after every build (game-log vs
  all-time consistency, champions, divisions, lineup sums, trade integrity,
  odds totals). **The workflow refuses to publish a build that fails it.**
- `.github/workflows/update.yml`: daily anchor run, 4-hourly interim runs, and
  5-minute gated live runs during game windows (dispatched by cron-job.org);
  news/injury ticks commit lighter updates.

## Where the data lives (everything needed to rebuild is in this repo)
| Path | Contents |
|---|---|
| `data/raw/sleeper/<year>/` | Sleeper era (2020+): league, rosters, matchups, transactions, drafts, brackets |
| `data/raw/espn/` | ESPN era (2014–2019): league/season JSONs, weekly box scores, drafts, player maps, nflverse rec/rush maps, 2014 season totals |
| `data/raw/sheet/` | The league record sheet (games 2015–2019, payouts, hall of fame, trades, drafts) |
| `data/espn2014.json` | The recovered 2014 season (games, draft, divisions, playoffs) |
| `data/espn_trades.json` | ESPN-era trade log recovered from league notification e-mails |
| `data/players_min.json` | Committed snapshot of the Sleeper player database — the pipeline's fallback if Sleeper ever disappears |
| `data/now.json`, `data/odds_history.json`, `data/values.json`, `data/feeds.json`, `data/fp_proj.json` | Live-state files refreshed by the pipeline |

A failed upstream fetch fails the run **before** the commit step, so cached
data can never be overwritten by an outage. A fresh clone rebuilds the entire
site with no network access to Sleeper or ESPN (only current-season odds need
live APIs). Immutable snapshot: the `archive-2026-11-13` release.

## Provenance notes
- **2014** predates ESPN's public history (they hide pre-2015 seasons from
  accounts that weren't league members that season). The season was recovered
  via a 2014 member's credentials and cross-validated against the
  commissioner's ESPN recap e-mails; see `tools/`.
- **ESPN-era trades (2014–2019)** come from the league's notification e-mails
  (ESPN's API serves no pre-2020 transactions), validated against the box-score
  record: an "accepted" e-mail is kept only if the players actually moved
  (three phantoms — cancelled or pre-draft offers — were excluded this way).
  Waiver logs never existed, so pre-2020 pickups remain forensic ("started a
  player they didn't draft", cross-checked against the trade log).
- **Ghost starters**: ESPN's archive drops players it no longer lists, leaving
  some 2014–2016 lineups short. 33 were restored (10 named by recap e-mails,
  23 reconstructed by matching missing points against nflverse weekly stats,
  unique matches only — `tools/ghostfill.py`). Remaining gaps are mostly
  K/D-ST, shown on the site as explicit "unrecorded starter" rows.
- Two-week 2014 playoff rounds (semis NFL wks 14–15, final 16–17) are stored
  as aggregates flagged `agg`, excluded from single-game records.
- Transaction weeks follow the league convention that a week ends after
  Monday night (Tuesday 6 AM ET boundary), not Sleeper's mid-Wednesday leg.

## Season rollover
Each new season, update `LEAGUE_ID` and the `OWNER`/`ROSTER_OWNER_*` maps in
`refresh.py` / `build.py`, and extend `WK1_THURSDAY` in `build.py`.

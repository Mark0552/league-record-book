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
| `data/preseason_proj.json` | Preseason season projections re-scored under league rules (FantasyPros consensus from Wayback Machine snapshots, averaged with ESPN's preseason projections where both exist), built by `tools/preseason_proj.py`. Run that tool once after each draft. 2016, 2017 and 2026 have no archived non-QB projections; the site estimates those from the same position and round in other seasons |
| `data/draft_teams.json` | NFL team each drafted player was on in week 1 of his draft season (nflverse weekly rosters), for the owner "Tendencies" block. Built by `tools/draft_teams.py` from the picks in the built page; run it once after each draft, then rebuild |
| `data/dnp.json` | NFL games missed by every round 1–3 draft pick (Luck tab), with reasons from roster status and the NFL injury reports, computed by `dnp.py` from nflverse weekly rosters, player stats, snap counts and the schedule. Finished seasons are computed once; the current season refreshes at most once every 20 hours and falls back to this file if nflverse is unreachable |
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
- **Ghost starters**: ESPN's archive silently drops some starters from its
  2014–2016 box scores, leaving lineups short. 53 have been restored: 10 named
  by recap e-mails, 23 matched against nflverse weekly stats
  (`tools/ghostfill.py`), and 20 more 2014 kickers, D/STs and skill players
  identified by re-scoring nflverse data at the league's exact 2014 settings
  (`tools/fill2014.py`; the model reproduces 99% of the starters the archive
  still holds). A slot is filled only when one answer fits; restored starters
  carry a dagger on the site, and anything unresolved is shown as an explicit
  "not in ESPN's archive" row naming the open slot.
- **2014 playoff rosters** come from the archive's two-week matchup periods:
  each player's points are his total across both weeks of the round.
- Two-week 2014 playoff rounds (semis NFL wks 14–15, final 16–17) are stored
  as aggregates flagged `agg`, excluded from single-game records.
- Transaction weeks follow the league convention that a week ends after
  Monday night (Tuesday 6 AM ET boundary), not Sleeper's mid-Wednesday leg.

## Season rollover
Each new season, update `LEAGUE_ID` and the `OWNER`/`ROSTER_OWNER_*` maps in
`refresh.py` / `build.py`, and extend `WK1_THURSDAY` in `build.py`.

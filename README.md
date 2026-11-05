# 2 Many Chiefs Not Enough Indians - League Record Book

**Live site: https://mark0552.github.io/league-record-book/**

Complete league history 2015-present: standings, playoff + championship odds,
every game, every draft pick, career head-to-head, records, money, luck,
trades, and the halls of fame & shame.

## How it works
- `refresh.py` pulls live data (Sleeper API, league Google Sheet, player
  projections), runs a 20k-season playoff simulation, a 100k-season full-bracket
  championship simulation, and the elimination solver, then calls `build.py`.
- `build.py` merges everything (including static ESPN 2015-2019 history in
  `data/raw/espn/`) and renders `site/index.html` from `template.html`.
- `.github/workflows/update.yml` runs the whole thing every day at 5am ET
  and publishes automatically. No secrets required.

## Season rollover
Each new season, update `LEAGUE_ID` and the `OWNER`/`ROSTER_OWNER_2026` maps in
`refresh.py` / `build.py` with the new Sleeper league id and roster assignments.

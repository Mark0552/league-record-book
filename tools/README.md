# Recovery tools (provenance)

One-off scripts that reconstructed the pre-Sleeper archive in November 2026.
They reference local paths (the commissioner's e-mail Takeout mboxes and this
repo on his machine) and are kept for provenance and re-use, not as part of
the pipeline.

- `extract2014.py` — parses the 2014 ESPN recap/draft/trade e-mails (mbox)
  into `data/espn2014.json`; validates weekly scores against the e-mailed
  standings. Superseded for games by the ESPN API archive, still the source
  of the e-mail-only details.
- `espn_trades.py` — parses ESPN trade notification e-mails (both the 2014
  format and the post-migration format) into `data/espn_trades.json`,
  resolving each season's team abbreviations and dropping vetoed trades.
- `ghostfill.py` — reconstructs starters ESPN purged from its archive by
  matching each lineup's missing points against nflverse weekly stats under
  that season's scoring, constrained to the open lineup slot and the team's
  own draft/other-week starters; fills unique matches only.

"""Top-pick availability: NFL games missed by each owner's early-round draft picks.

For every pick in rounds 1-ROUNDS, walk the weeks the fantasy season was played
and record each week the player's NFL team played and he did not, with the
reason taken from that week's NFL roster status (IR, suspended, holdout, ...).
A miss counts for the drafter whether or not the player was still on his squad.

Source: nflverse weekly rosters + weekly player stats + snap counts + schedule.
Finished seasons are computed once and kept in data/dnp.json; the season in
progress is refetched at most once every 20 hours. Any network failure falls
back to whatever is cached, so a build never breaks on this.
"""
import csv, io, json, os, re, time, urllib.request
from collections import defaultdict
from pathlib import Path

ROUNDS = 5
CACHE = Path("data/dnp.json")
BASE = "https://github.com/nflverse/nflverse-data/releases/download"
FILES = {
    "roster": ("weekly_rosters", "roster_weekly_{y}.csv"),
    "stats": ("stats_player", "stats_player_week_{y}.csv"),
    "snaps": ("snap_counts", "snap_counts_{y}.csv"),
}
GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
STALE_S = 20 * 3600
VERSION = 3   # bump to force every season to recompute

# roster files spell some clubs differently from the schedule file
TEAM = {"CLV": "CLE", "BLT": "BAL", "HST": "HOU", "ARZ": "ARI", "SL": "STL", "JAC": "JAX",
        "LAR": "LA", "SD": "SD", "OAK": "OAK"}
# roster status -> the reason shown on the site
REASON = {"RES": "IR", "PUP": "PUP", "RSN": "Non-football injury", "SUS": "Suspended",
          "NWT": "Holdout", "RET": "Retired", "RSR": "Retired",
          "EXE": "Exempt list", "E01": "Exempt list", "E14": "Exempt list",
          "DEV": "Practice squad", "INA": "Inactive",
          "CUT": "Not on a team", "UFA": "Not on a team", "RFA": "Not on a team",
          "TRC": "Not on a team", "TRT": "Not on a team", "": "Not on a team"}
ON_FIELD = {"ACT", "TRD"}
# the 2014-2016 roster files have holes around suspensions and retirements;
# these are the known ones among early picks: (season, player) -> {week: reason}
OVERRIDE = {
    (2015, "leveonbell"): {1: "Suspended", 2: "Suspended"},
    (2015, "odellbeckham"): {16: "Suspended"},
    (2016, "leveonbell"): {1: "Suspended", 2: "Suspended", 3: "Suspended"},
    (2016, "tombrady"): {1: "Suspended", 2: "Suspended", 3: "Suspended", 4: "Suspended"},
    (2016, "arianfoster"): {w: "Retired" for w in range(8, 18)},
}

def norm(s):
    return re.sub(r"[^a-z]", "", re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", (s or "").lower()))

def _rows(kind, y):
    rel, fn = FILES[kind]
    fn = fn.format(y=y)
    local = os.environ.get("DNP_LOCAL")
    if local and (Path(local) / fn).exists():
        text = (Path(local) / fn).read_text(encoding="utf-8")
    else:
        with urllib.request.urlopen(f"{BASE}/{rel}/{fn}", timeout=120) as r:
            text = r.read().decode("utf-8")
    return csv.DictReader(io.StringIO(text))

_games = None
def _schedule():
    global _games
    if _games is None:
        local = os.environ.get("DNP_LOCAL")
        if local and (Path(local) / "games.csv").exists():
            text = (Path(local) / "games.csv").read_text(encoding="utf-8")
        else:
            with urllib.request.urlopen(GAMES_URL, timeout=60) as r:
                text = r.read().decode("utf-8")
        _games = list(csv.DictReader(io.StringIO(text)))
    return _games

def _season(y, picks, last_week):
    """picks: [(key, name, pos)] -> ({key: {...}}, through_week)"""
    played_by_team = defaultdict(set)      # team -> weeks with a finished game
    open_weeks = set()                     # weeks with a game still to finish
    for g in _schedule():
        if g["season"] != str(y) or g["game_type"] != "REG":
            continue
        w = int(g["week"])
        if (g.get("home_score") or "") != "" and (g.get("away_score") or "") != "":
            played_by_team[g["home_team"]].add(w)
            played_by_team[g["away_team"]].add(w)
        elif g.get("gameday", "") <= time.strftime("%Y-%m-%d", time.gmtime(time.time() + 86400)):
            open_weeks.add(w)              # scheduled by now but no score yet (cancelled games are rare)

    stat_rows = defaultdict(set)           # gsis -> weeks with a stat line
    pts = defaultdict(float)
    info = {}                              # gsis -> (name, pos)
    stat_weeks = set()
    for r in _rows("stats", y):
        if r["season_type"] != "REG":
            continue
        w = int(r["week"])
        stat_weeks.add(w)
        stat_rows[r["player_id"]].add(w)
        try:
            pts[r["player_id"]] += float(r.get("fantasy_points") or 0)
        except ValueError:
            pass
        info.setdefault(r["player_id"], (r["player_display_name"], r["position"]))
    snap = set()                           # (normalized name, week) with a snap
    for r in _rows("snaps", y):
        if r["game_type"] != "REG":
            continue
        if int(float(r["offense_snaps"] or 0)) > 0 or (r["position"] == "K" and int(float(r["st_snaps"] or 0)) > 0):
            snap.add((norm(r["player"]), int(r["week"])))
    roster = defaultdict(dict)             # gsis -> week -> (team, status)
    has_ina = False
    for r in _rows("roster", y):
        if r["game_type"] != "REG" or not r["gsis_id"]:
            continue
        st = r["status"]
        has_ina = has_ina or st == "INA"
        roster[r["gsis_id"]][int(r["week"])] = (TEAM.get(r["team"], r["team"]), st)
        info[r["gsis_id"]] = (r["full_name"], r["position"])

    # the season in progress only counts weeks that are finished AND published
    through = last_week
    done = [w for w in range(1, last_week + 1)
            if w in stat_weeks and w not in open_weeks and any(w in ws for ws in played_by_team.values())]
    through = max(done) if done else 0

    byname = defaultdict(list)
    for gid, (nm, pos) in info.items():
        byname[norm(nm)].append(gid)

    out = {}
    for key, name, pos in picks:
        cands = byname.get(norm(name), [])
        same = [g for g in cands if info[g][1] == pos]
        cands = same or cands
        if not cands:
            out[key] = dict(u=1, g=0, miss=[])
            continue
        gid = max(cands, key=lambda g: (pts[g], len(roster[g])))
        rw = roster[gid]
        miss, poss = [], 0
        for w in range(1, through + 1):
            # which club was he with? this week's row, else the nearest earlier, else the nearest later
            if w in rw:
                team, st = rw[w]
            else:
                before = [k for k in rw if k < w]
                after = [k for k in rw if k > w]
                if before:
                    team, st0 = rw[max(before)]
                    st = st0 if st0 not in ON_FIELD and st0 != "INA" else None
                elif after:
                    team, st = rw[min(after)][0], None
                else:
                    team, st = None, ""
            if team is not None and w not in played_by_team.get(team, ()):
                continue                                   # bye (or a cancelled game)
            poss += 1
            if w in stat_rows[gid] or (norm(info[gid][0]), w) in snap:
                continue
            if st is None:
                why = "Inactive"                           # on the club, no roster row that week
            elif st in ON_FIELD:
                why = "Did not play" if has_ina else "Inactive"
            else:
                why = REASON.get(st, st)
            why = OVERRIDE.get((y, norm(name)), {}).get(w, why)
            miss.append([w, why])
        team_now = rw[max(rw)][0] if rw else ""
        out[key] = dict(g=poss, miss=miss, t=team_now)
    return out, through

def compute(picks, weeks, current):
    """picks: draft dicts (season, owner, player, pos, round, pick).
    weeks: {season: last NFL week of the fantasy season (for the season in
    progress: the last completed fantasy week)}.  Returns the site payload."""
    try:
        cache = json.load(open(CACHE, encoding="utf-8"))
    except Exception:
        cache = {}
    if cache.get("version") != VERSION:
        cache = dict(version=VERSION, seasons={})
    changed = False
    by_season = defaultdict(list)
    for d in picks:
        by_season[d["season"]].append(d)
    for y, ds in sorted(by_season.items()):
        keyed = [(f'{norm(d["player"])}|{d["pos"]}', d["player"], d["pos"]) for d in ds]
        c = cache["seasons"].get(str(y))
        last = weeks.get(y, 0)
        have = c and all(k in c["picks"] for k, _, _ in keyed)
        fresh = have and (c.get("final") and c.get("last") == last
                          or y == current and time.time() - c.get("fetched", 0) < STALE_S)
        if fresh or not last:
            continue
        try:
            res, through = _season(y, keyed, last)
            cache["seasons"][str(y)] = dict(final=y < current, last=last, through=through,
                                            fetched=int(time.time()), picks=res)
            changed = True
            print(f"dnp: {y} computed through week {through} "
                  f"({sum(len(v['miss']) for v in res.values())} missed games, "
                  f"{sum(1 for v in res.values() if v.get('u'))} unmatched)")
        except Exception as e:
            print(f"dnp: {y} not refreshed ({e!r}); using cached data" if c else f"dnp: {y} unavailable ({e!r})")
    if changed:
        json.dump(cache, open(CACHE, "w", encoding="utf-8"), separators=(",", ":"))
    seasons = {}
    for y, ds in sorted(by_season.items()):
        c = cache["seasons"].get(str(y))
        if not c:
            continue
        rows = []
        for d in sorted(ds, key=lambda d: d["pick"]):
            v = c["picks"].get(f'{norm(d["player"])}|{d["pos"]}')
            if not v:
                continue
            rows.append(dict(o=d["owner"], p=d["player"], pos=d["pos"], rd=d["round"], pick=d["pick"],
                             g=v["g"], miss=v["miss"], **({"u": 1} if v.get("u") else {})))
        seasons[str(y)] = dict(weeks=c["through"], picks=rows)
    return dict(rounds=ROUNDS, seasons=seasons)

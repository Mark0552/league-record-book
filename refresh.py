"""Weekly refresh: re-download live data, recompute now.json (standings, projections,
odds, eliminations), rebuild site/index.html.

Usage: python refresh.py [--fast]   (--fast skips the elimination solver)
"""
import json, sys, random, statistics, itertools, urllib.request
from pathlib import Path
from collections import defaultdict, deque

LEAGUE_ID = "1312131541249318912"   # 2026 season — update after rollover
SHEET_ID = "151RNijLbcy6RY6nPwK6x9BeZ-P-CMH2DYOzQnmtBBWA"
SHEET_TABS = {"h2h_scoreboard": 919879618, "career_h2h": 557795231, "trades": 1183097430,
              "wins_in_week": 1673354871, "wins_thru_week": 918251457, "power_rankings": 1218552485,
              "best": 0, "worst": 304980967, "draft": 9474436, "hall_of_fame": 1726632255,
              "hall_of_shame": 1930651639, "master": 486415761, "payouts": 1739004433}
OWNER = {1: "Mark", 2: "Clancy", 3: "Will", 4: "Sam", 5: "Steve", 6: "Timmy",
         7: "Bob", 8: "Lerch", 9: "Martin", 10: "Luke", 11: "Austin", 12: "Cliff"}
RAW = Path("data/raw")
FAST = "--fast" in sys.argv

def get(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as r:
        dest.write_bytes(r.read())

def api(path):
    with urllib.request.urlopen(f"https://api.sleeper.app/v1/{path}", timeout=60) as r:
        return json.load(r)

# ---------------- 1b. publications: news feeds for the Publications tab ----------------
# Fetched here because a static page can't read RSS cross-origin; a failed
# fetch keeps the feed's last good copy from data/feeds.json.
FEEDS = [
    ("ESPN NFL", "https://www.espn.com/espn/rss/nfl/news"),
    ("Yahoo Fantasy", "https://sports.yahoo.com/fantasy/rss.xml"),
    ("CBS NFL", "https://www.cbssports.com/rss/headlines/nfl"),
    ("RotoWire", "https://www.rotowire.com/rss/news.php?sport=NFL"),
    # one RotoBaller feed: the NFL category (their player-news wire feeds into
    # it anyway, and the standalone news feed mixes in other sports)
    ("RotoBaller", "https://www.rotoballer.com/category/nfl/feed"),
    ("PFF", "https://www.pff.com/feed"),
    ("PFT Rumors", "https://www.profootballrumors.com/feed"),
]
def refresh_feeds():
    import html as _html
    import re as _refx
    import xml.etree.ElementTree as _ET
    from email.utils import parsedate_to_datetime
    fp = Path("data/feeds.json")
    prev_feeds = []
    if fp.exists():
        try:
            prev_feeds = json.load(open(fp, encoding="utf-8")).get("feeds", [])
        except Exception:
            prev_feeds = []
    old = {f["name"]: f for f in prev_feeds}
    out = []
    for name, url in FEEDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                root = _ET.fromstring(r.read())
            items = []
            for it in root.iter("item"):
                t = (it.findtext("title") or "").strip()
                u = (it.findtext("link") or "").strip()
                if not t or not u.startswith("http"):
                    continue
                try:
                    ts = int(parsedate_to_datetime(it.findtext("pubDate").strip()).timestamp())
                except Exception:
                    ts = 0
                d = _refx.sub(r"\s+", " ", _refx.sub(r"<[^>]+>", " ",
                              _html.unescape(it.findtext("description") or ""))).strip()
                items.append(dict(t=t, u=u, ts=ts, d=(d[:177] + "…") if len(d) > 180 else d))
                if len(items) >= 25:
                    break
            if items:
                out.append(dict(name=name, items=items))
            elif name in old:
                out.append(old[name])
        except Exception as e:
            print(f"  feed failed ({name}): {e}")
            if name in old:
                out.append(old[name])
    # only rewrite the file when a headline actually changed, so news-only
    # runs produce a commit exactly when there is news
    if [(f["name"], f["items"]) for f in out] == [(f.get("name"), f.get("items")) for f in prev_feeds]:
        print(f"publications: no new headlines ({len(out)} feeds)")
        return
    json.dump(dict(at=int(__import__("time").time()), feeds=out),
              open(fp, "w", encoding="utf-8"))
    print(f"publications: {len(out)} feeds, {sum(len(f['items']) for f in out)} headlines")
print("fetching news feeds ...")
refresh_feeds()

if '--news-only' in sys.argv:
    # headlines-only run: the page fetches data/feeds.json live, so no rebuild
    print('news-only refresh complete')
    sys.exit(0)

# ---------------- 1. refresh live data ----------------
print("fetching league data ...")
lg = api(f"league/{LEAGUE_ID}")
season = lg["season"]
yr_dir = RAW / "sleeper" / season
get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}", yr_dir / "league.json")
for name in ("users", "rosters", "winners_bracket", "losers_bracket"):
    get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/{name}", yr_dir / f"{name}.json")
for w in range(1, 19):
    get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/matchups/{w}", yr_dir / f"matchups_{w}.json")
    get(f"https://api.sleeper.app/v1/league/{LEAGUE_ID}/transactions/{w}", yr_dir / f"transactions_{w}.json")
print("fetching players + sheet ...")
get("https://api.sleeper.app/v1/players/nfl", RAW / "players.json")
for name, gid in SHEET_TABS.items():
    get(f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={gid}",
        RAW / "sheet" / f"{name}.csv")

def refresh_trade_values(ros_vor, es_vor):
    """Trade-calculator value tables, ALL REDRAFT: FantasyCalc market values
    computed for this league's settings (which also provide the value curve),
    rest-of-season consensus ranks (Dynasty Daddy's redraft aggregate of
    ESPN/FantasyPros/FanDuel) and FantasyPros redraft ECR — both converted to
    values on the FantasyCalc positional curve — plus Sleeper and ESPN ROS
    projections as value-over-replacement. Keyed by Sleeper id; a failed
    fetch keeps the last copy."""
    fp = Path("data/values.json")
    old = {}
    if fp.exists():
        try:
            old = json.load(open(fp, encoding="utf-8"))
        except Exception:
            old = {}
    def grab(url):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    players_v = {}
    for e in (old.get("players") or []):
        players_v[e["id"]] = e
    try:
        fc = grab("https://api.fantasycalc.com/values/current?isDynasty=false&numQbs=1&numTeams=12&ppr=0.5")
        for e in players_v.values():
            e.pop("fc", None)
        for p in fc:
            sid = str(p["player"].get("sleeperId") or "")
            if not sid:
                continue
            e = players_v.setdefault(sid, dict(id=sid, n=p["player"]["name"],
                                               p=p["player"].get("position", "?"),
                                               t=p["player"].get("maybeTeam") or ""))
            e["fc"] = p["value"]
        print(f"trade values: FantasyCalc {len(fc)} players")
    except Exception as ex:
        print(f"  trade values failed (FantasyCalc): {ex}")
    # the FantasyCalc positional curve turns a redraft rank into a value
    curves = defaultdict(list)
    for e in players_v.values():
        if e.get("fc"):
            curves[e["p"]].append(e["fc"])
    for pos in curves:
        curves[pos].sort(reverse=True)
    def curve_val(pos, rank):
        vs = curves.get(pos)
        try:
            r = int(round(float(rank)))
        except (TypeError, ValueError):
            return None
        if not vs or r < 1:
            return None
        return vs[r - 1] if r <= len(vs) else max(0, int(vs[-1] * len(vs) / r))
    for e in players_v.values():
        for k in ("dd", "dp", "rc", "fp"):   # retired dynasty fields + rebuilt ranks
            e.pop(k, None)
    try:
        dd = grab("https://dynasty-daddy.com/api/v1/player/all/today")
        n = 0
        for p in dd:
            sid = str(p.get("sleeper_id") or "")
            e = players_v.get(sid)
            v = curve_val(p.get("position"), p.get("avg_ros"))
            if e is not None and v:
                e["rc"] = v; n += 1
        print(f"trade values: ROS consensus ranks for {n} players")
    except Exception as ex:
        print(f"  trade values failed (ROS consensus): {ex}")
    nrm = lambda s: "".join(ch for ch in s.lower() if ch.isalpha())
    try:
        # FantasyPros' own HALF-PPR rest-of-season boards (QB page is scoring-free)
        byname = {(nrm(e["n"]), e["p"]): e for e in players_v.values()}
        n = 0
        for pos, page in (("QB", "ros-qb"), ("RB", "ros-half-point-ppr-rb"),
                          ("WR", "ros-half-point-ppr-wr"), ("TE", "ros-half-point-ppr-te")):
            req = urllib.request.Request(f"https://www.fantasypros.com/nfl/rankings/{page}.php",
                                         headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                h = r.read().decode("utf-8", "ignore")
            k = h.find("var ecrData")
            if k < 0:
                continue
            d2, _ = json.JSONDecoder().raw_decode(h, h.index("{", k))
            for p in d2.get("players", []):
                e = byname.get((nrm(p.get("player_name") or ""), pos))
                v = curve_val(pos, p.get("rank_ecr"))
                if e is not None and v:
                    e["fp"] = v; n += 1
        print(f"trade values: FantasyPros half-PPR ROS ECR for {n} players")
    except Exception as ex:
        print(f"  trade values failed (FantasyPros): {ex}")
    for e in players_v.values():
        v = ros_vor.get(e["id"], 0.0)
        if v > 1:
            e["rs"] = round(v, 1)
        else:
            e.pop("rs", None)
        v = es_vor.get((nrm(e["n"]), e["p"]), 0.0)
        if v > 1:
            e["es"] = round(v, 1)
        else:
            e.pop("es", None)
    keep = [e for e in players_v.values() if any(k in e for k in ("fc", "rc", "fp", "rs", "es"))]
    json.dump(dict(at=int(__import__("time").time()),
                   players=sorted(keep, key=lambda e: -(e.get("fc", 0) + e.get("rc", 0)))),
              open(fp, "w", encoding="utf-8"))

scoring = lg["scoring_settings"]
PSTART = lg["settings"]["playoff_week_start"]       # regular season = weeks 1..PSTART-1
last_scored = lg["settings"].get("last_scored_leg") or 0
cur_week = min(last_scored + 1, PSTART - 1)
weeks_left = [w for w in range(cur_week, PSTART) if w > last_scored]
print(f"season {season}: {last_scored} weeks scored, current week {cur_week}")

rosters = json.load(open(yr_dir / "rosters.json"))
players = json.load(open(RAW / "players.json", encoding="utf-8"))

# ---------------- 1c. injury snapshot (lets the live gate spot changes) ----------------
def write_injury_snapshot():
    try:
        rostered = set()
        for r in rosters:
            rostered.update(r.get("players") or [])
        names = {"".join(ch for ch in (players.get(p, {}).get("full_name") or "").lower() if ch.isalpha())
                 for p in rostered}
        names.discard("")
        req = urllib.request.Request(
            "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            inj = json.load(r)
        statuses = {}
        for t in inj.get("injuries", []):
            for i in t.get("injuries", []):
                nm = "".join(ch for ch in ((i.get("athlete") or {}).get("displayName") or "").lower()
                             if ch.isalpha())
                if nm in names:
                    statuses[nm] = i.get("status") or ""
        json.dump(dict(names=sorted(names), statuses=dict(sorted(statuses.items()))),
                  open("data/injury_snapshot.json", "w", encoding="utf-8"))
        print(f"injury snapshot: {len(statuses)} rostered players on the report")
    except Exception as e:
        print(f"  injury snapshot failed: {e}")
write_injury_snapshot()
division = {r["roster_id"]: r["settings"].get("division") for r in rosters}
TEAMS = sorted(OWNER)
D = {1: [t for t in TEAMS if division[t] == 1], 2: [t for t in TEAMS if division[t] == 2]}
DIVNAME = {1: (lg.get("metadata") or {}).get("division_1", "Div 1"),
           2: (lg.get("metadata") or {}).get("division_2", "Div 2")}
users = {u["user_id"]: u for u in json.load(open(yr_dir / "users.json"))}
teamname = {r["roster_id"]: ((users.get(r["owner_id"], {}).get("metadata") or {}).get("team_name")
                             or users.get(r["owner_id"], {}).get("display_name", "?")).strip()
            for r in rosters}

games = {}
for w in range(1, 19):
    d = json.load(open(yr_dir / f"matchups_{w}.json"))
    pair = defaultdict(list)
    for x in d:
        if x.get("matchup_id") is not None:
            pair[x["matchup_id"]].append(x)
    games[w] = [(v[0]["roster_id"], v[1]["roster_id"], v[0]["points"], v[1]["points"])
                for _, v in sorted(pair.items()) if len(v) == 2]

wins0 = {t: 0 for t in TEAMS}; pf0 = {t: 0.0 for t in TEAMS}
h2h0 = {t: {u: [0, 0] for u in TEAMS} for t in TEAMS}
sc = {t: [] for t in TEAMS}
for w in range(1, cur_week):
    for a, b, p, q in games[w]:
        if p == 0 and q == 0:
            continue
        pf0[a] += p; pf0[b] += q; sc[a].append(p); sc[b].append(q)
        if p != q:
            wi, lo = (a, b) if p > q else (b, a)
            wins0[wi] += 1
            h2h0[wi][lo][0] += 1
        h2h0[a][b][1] += 1; h2h0[b][a][1] += 1
future = {w: [(a, b) for a, b, _, _ in games[w]] for w in weeks_left}
season_cnt = {t: {u: 0 for u in TEAMS} for t in TEAMS}
for w in range(1, PSTART):
    for a, b, *_ in games[w]:
        season_cnt[a][b] += 1; season_cnt[b][a] += 1

def contextual_rank(subset, wins, pf, h2h):
    groups = defaultdict(list)
    for t in subset:
        groups[wins[t]].append(t)
    out = []
    for wc in sorted(groups, reverse=True):
        g = groups[wc]
        if len(g) == 1:
            out.extend(g); continue
        if len({season_cnt[a][b] for a in g for b in g if a < b}) == 1:
            key = lambda t: (-sum(h2h[t][u][0] for u in g if u != t), -pf[t])
        else:
            key = lambda t: -pf[t]
        out.extend(sorted(g, key=key))
    return out

# ---------------- 2. projections + optimal lineups (no K/DEF, waiver fill) ----------------
print("fetching projections ...")
POS = {"QB", "RB", "WR", "TE"}
PLAYOFF_WEEKS = [PSTART, PSTART + 1, PSTART + 2]
proj = {}
for w in weeks_left + PLAYOFF_WEEKS:
    get(f"https://api.sleeper.app/v1/projections/nfl/regular/{season}/{w}", RAW / f"proj_cache_{w}.json")
    proj[w] = json.load(open(RAW / f"proj_cache_{w}.json"))
def lpoints(st):
    return sum(scoring.get(k, 0.0) * v for k, v in st.items() if k in scoring)
rostered = set()
for r in rosters:
    rostered.update(r["players"] or [])
repl = {}
for w in weeks_left + PLAYOFF_WEEKS:
    fa = defaultdict(list)
    for pid, st in proj[w].items():
        if pid in rostered or not st:
            continue
        info = players.get(pid)
        if info and info.get("position") in POS:
            fa[info["position"]].append(lpoints(st))
    repl[w] = {p: (sum(sorted(v, reverse=True)[:5]) / min(5, len(v)) if v else 0.0) for p, v in fa.items()}
pool = {r["roster_id"]: [p for p in (r["players"] or []) if p not in set(r.get("reserve") or [])
                         and players.get(p) and players[p].get("position") in POS] for r in rosters}
def wproj(rid, w):
    cand = sorted(((lpoints(proj[w].get(p) or {}), players[p]["position"]) for p in pool[rid]), reverse=True)
    lineup, used = [], []
    pools = {pos: [c for c in cand if c[1] == pos] for pos in POS}
    take = lambda pos, n: [pools[pos].pop(0)[0] if pools[pos] else 0.0 for _ in range(n)]
    vals = take("QB", 1) + take("RB", 2) + take("WR", 2) + take("TE", 1)
    flexpool = sorted(pools["RB"] + pools["WR"] + pools["TE"], reverse=True)
    vals.append(flexpool[0][0] if flexpool else 0.0)
    slots = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX"]
    out = 0.0
    for sl, v in zip(slots, vals):
        if v < 0.5:
            v = max(repl[w].get(p, 0) for p in ("RB", "WR", "TE")) if sl == "FLEX" else repl[w].get(sl, 0)
        out += v
    return out
WP = {t: {w: wproj(t, w) for w in weeks_left + PLAYOFF_WEEKS} for t in TEAMS}

# rest-of-season projection per player (for the trade calculator's projection columns)
ros_pts = defaultdict(float)
for w in weeks_left + PLAYOFF_WEEKS:
    for pid, st in proj[w].items():
        v = lpoints(st)
        if v:
            ros_pts[pid] += v

def espn_ros_half():
    """ESPN rest-of-season projections at 0.5 PPR: the mean of their standard
    and full-PPR league defaults, which differ only in reception points."""
    flt = json.dumps({"players": {"limit": 350,
                                  "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
                                  "filterStatsForSourceIds": {"value": [1]}}})
    out = defaultdict(float)
    meta = {}
    posmap = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF"}
    for dflt in (1, 3):
        url = (f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
               f"/segments/0/leaguedefaults/{dflt}?view=kona_player_info")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                                   "X-Fantasy-Filter": flt})
        with urllib.request.urlopen(req, timeout=40) as r:
            d = json.loads(r.read())
        for p in d.get("players", []):
            pl = p.get("player") or {}
            tot = sum((st.get("appliedTotal") or 0) for st in (pl.get("stats") or [])
                      if st.get("statSourceId") == 1 and st.get("statSplitTypeId") == 1
                      and cur_week <= (st.get("scoringPeriodId") or 0) <= 17)
            out[pl.get("id")] += tot / 2.0
            meta[pl.get("id")] = (pl.get("fullName", ""), posmap.get(pl.get("defaultPositionId"), "?"))
    return {meta[k]: v for k, v in out.items() if meta.get(k, ("", "?"))[1] != "?"}

# positional scarcity: projections become value over a replacement-level
# starter (12-team, 1QB/2RB/2WR/TE/flex lineups)
REPL_RANK = {"QB": 13, "RB": 29, "WR": 31, "TE": 14, "K": 13, "DEF": 13}
def vor_by_pos(items):
    """items: (key, pos, value) -> {key: value over replacement, positives only}"""
    by_pos = defaultdict(list)
    for _, pos, v in items:
        if pos in REPL_RANK:
            by_pos[pos].append(v)
    repl = {pos: sorted(vs, reverse=True)[min(REPL_RANK[pos], len(vs)) - 1]
            for pos, vs in by_pos.items()}
    return {k: v - repl[pos] for k, pos, v in items
            if pos in repl and v - repl[pos] > 0}

ESPN_POSMAP = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF"}
_nrm = lambda s: "".join(ch for ch in s.lower() if ch.isalpha())
es_vor = {}
try:
    es_raw = espn_ros_half()   # espn id -> (summed halves); meta captured below
    es_vor = vor_by_pos([((_nrm(nm), pos), pos, v) for (nm, pos), v in es_raw.items()])
    print(f"trade values: ESPN ROS projections for {len(es_raw)} players")
except Exception as ex:
    print(f"  trade values failed (ESPN ROS): {ex}")

ros_vor = vor_by_pos([(pid, (players.get(pid) or {}).get("position"), v)
                      for pid, v in ros_pts.items()])
print("fetching trade values ...")
refresh_trade_values(ros_vor, es_vor)

# ---------------- 3. one simulation to rule them all: 100k full seasons incl. bracket ----------------
sdv = {t: max(12.0, statistics.stdev(sc[t])) if len(sc[t]) > 1 else 20.0 for t in TEAMS}

# ---------------- 2a. lock in the part of the current week already played ----------------
# Starters whose NFL game has kicked off are locked into their slot at their
# actual points: a banked score can't be benched, a benched player can't come
# in, and only not-yet-kicked-off players fill the remaining slots (Sleeper's
# own lineup-lock rules). Variance shrinks to the unlocked share of the lineup.
WSD = {t: {w: sdv[t] for w in weeks_left + PLAYOFF_WEEKS} for t in TEAMS}
NFL_ALIAS = {"LA": "LAR"}   # nflverse -> Sleeper team codes
blended = False

def nfl_game_state():
    """Remaining-game fraction per NFL team whose current-week game has kicked
    off (absent = not started). Kickoffs are in ET (EST offset is close
    enough: no kickoff falls near the 5am run). Wall clock over a nominal
    3h10m game stands in for game progress: a live starter is then valued at
    his banked points plus the remaining share of his projection — the same
    shape as a live 'current projection' — and that reaches his actual final
    score when the game ends."""
    get("https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv",
        RAW / "nfl_games.csv")
    import csv as _csv
    from datetime import datetime, timedelta, timezone
    now_et = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=5)
    rem = {}
    with open(RAW / "nfl_games.csv", encoding="utf-8") as f:
        for r in _csv.DictReader(f):
            if r["season"] == str(season) and r["week"] == str(cur_week) and r["game_type"] == "REG":
                ko = datetime.strptime(r["gameday"] + " " + (r.get("gametime") or "13:00"), "%Y-%m-%d %H:%M")
                if ko <= now_et:
                    frac = max(0.0, 1.0 - (now_et - ko) / timedelta(minutes=190))
                    for s in ("home_team", "away_team"):
                        rem[NFL_ALIAS.get(r[s], r[s])] = frac
    return rem

if weeks_left:
    try:
        nfl_rem = nfl_game_state()
    except Exception as e:
        print(f"current-week blend skipped ({e}); using pure projections")
        nfl_rem = {}
    if nfl_rem:
        SKILL = {"QB", "RB", "WR", "TE"}
        slots_all = [s for s in lg["roster_positions"] if s not in ("BN", "IR")]
        mrows = {x["roster_id"]: x for x in json.load(open(yr_dir / f"matchups_{cur_week}.json"))}
        rposmap = {r["roster_id"]: r for r in rosters}
        for rid in TEAMS:
            m = mrows.get(rid)
            if not m:
                continue
            starters = m.get("starters") or []
            ppts = m.get("players_points") or {}
            locked_sum, open_slots, locked_pids, inplay_w = 0.0, [], set(), 0.0
            for slot, pid in zip(slots_all, starters):
                nflteam = (players.get(pid) or {}).get("team")
                if pid and pid != "0" and nflteam in nfl_rem:
                    # slot locked: banked points + remaining share of projection
                    r = nfl_rem[nflteam]
                    locked_sum += (ppts.get(pid) or 0.0) + r * lpoints(proj[cur_week].get(pid) or {})
                    locked_pids.add(pid)
                    inplay_w += r
                else:
                    open_slots.append((slot, pid))
            # unlocked candidates: active roster, game not yet kicked off
            r_ = rposmap[rid]
            resv = set(r_.get("reserve") or [])
            cand = sorted(((lpoints(proj[cur_week].get(p) or {}), players[p]["position"])
                           for p in (r_["players"] or [])
                           if p not in resv and p not in locked_pids
                           and (players.get(p) or {}).get("position") in SKILL
                           and (players.get(p) or {}).get("team") not in nfl_rem), reverse=True)
            pools = {pos: [c for c in cand if c[1] == pos] for pos in SKILL}
            open_proj, flex_n = 0.0, 0
            vals = []
            for slot, pid in open_slots:
                if slot in SKILL:
                    vals.append((pools[slot].pop(0)[0] if pools[slot] else 0.0, slot))
                elif slot == "FLEX":
                    flex_n += 1
                else:   # K, DEF etc: keep the current occupant, no mid-week streaming
                    open_proj += lpoints(proj[cur_week].get(pid) or {})
            flexpool = sorted(pools["RB"] + pools["WR"] + pools["TE"], reverse=True)
            for i in range(flex_n):
                vals.append((flexpool[i][0] if i < len(flexpool) else 0.0, "FLEX"))
            for v, sl in vals:
                if v < 0.5:
                    v = max(repl[cur_week].get(p, 0) for p in ("RB", "WR", "TE")) if sl == "FLEX" else repl[cur_week].get(sl, 0)
                open_proj += v
            WP[rid][cur_week] = locked_sum + open_proj
            WSD[rid][cur_week] = sdv[rid] * ((len(open_slots) + inplay_w) / max(1, len(slots_all))) ** 0.5
        blended = True
        nfinal = sum(1 for v in nfl_rem.values() if v == 0)
        print(f"current-week blend: {nfinal} NFL teams final, {len(nfl_rem) - nfinal} in progress; "
              f"week {cur_week} mixes banked points with projections for the rest")
status = {}   # owner clinch/elimination status, filled by the solver or by the settled field
N_TITLE = 100000
title_counts = {k: {t: 0 for t in TEAMS} for k in ("made", "bye", "finals", "champs")}
lose_odds = {}   # playoff odds conditioned on losing this week's game
if weeks_left:
    print("simulating championships (100k) ...")
    random.seed(7)
    gauss = random.gauss
    sched = [(w, a, b) for w in weeks_left for a, b in future[w]]
    lose_cnt = {t: 0 for t in TEAMS}
    lose_made = {t: 0 for t in TEAMS}
    for s in range(N_TITLE):
        wins = dict(wins0); pf = dict(pf0)
        h2h = {t: {u: list(v) for u, v in dd.items()} for t, dd in h2h0.items()}
        losers = []
        for w, a, b in sched:
            pa = gauss(WP[a][w], WSD[a][w]); pb = gauss(WP[b][w], WSD[b][w])
            if pa > pb:
                wins[a] += 1; h2h[a][b][1] += 1; h2h[b][a][1] += 1; h2h[a][b][0] += 1
                lo = b
            else:
                wins[b] += 1; h2h[a][b][1] += 1; h2h[b][a][1] += 1; h2h[b][a][0] += 1
                lo = a
            if w == cur_week:
                losers.append(lo)
            pf[a] += max(30, pa); pf[b] += max(30, pb)
        w1 = contextual_rank(D[1], wins, pf, h2h)[0]; w2 = contextual_rank(D[2], wins, pf, h2h)[0]
        byes_ = contextual_rank([w1, w2], wins, pf, h2h)
        nw = contextual_rank([t for t in TEAMS if t not in (w1, w2)], wins, pf, h2h)
        s1, s2 = byes_; s3, s4, s5, s6 = nw[:4]
        fieldset = (s1, s2, s3, s4, s5, s6)
        for t in losers:
            lose_cnt[t] += 1
            if t in fieldset:
                lose_made[t] += 1
        for t in fieldset:
            title_counts["made"][t] += 1
        title_counts["bye"][s1] += 1; title_counts["bye"][s2] += 1
        def game(x, y, w):
            return x if gauss(WP[x][w], WSD[x][w]) >= gauss(WP[y][w], WSD[y][w]) else y
        q1 = game(s3, s6, PLAYOFF_WEEKS[0]); q2 = game(s4, s5, PLAYOFF_WEEKS[0])
        f1 = game(s1, q2, PLAYOFF_WEEKS[1]); f2 = game(s2, q1, PLAYOFF_WEEKS[1])
        title_counts["finals"][f1] += 1; title_counts["finals"][f2] += 1
        title_counts["champs"][game(f1, f2, PLAYOFF_WEEKS[2])] += 1
    title_odds = sorted([dict(owner=OWNER[t],
                              playoffs=round(100 * title_counts["made"][t] / N_TITLE, 2),
                              bye=round(100 * title_counts["bye"][t] / N_TITLE, 2),
                              final=round(100 * title_counts["finals"][t] / N_TITLE, 2),
                              champ=round(100 * title_counts["champs"][t] / N_TITLE, 3)) for t in TEAMS],
                        key=lambda o: -o["champ"])
    json.dump(dict(asof=f"entering week {cur_week}, {season}", sims=N_TITLE, odds=title_odds),
              open("data/title_odds.json", "w", encoding="utf-8"), indent=1)
    print("wrote data/title_odds.json")
    made, N = title_counts["made"], N_TITLE
    lose_odds = {t: round(100 * lose_made[t] / lose_cnt[t], 1) for t in TEAMS if lose_cnt[t]}
else:
    # regular season over: field is settled; simulate what's LEFT of the live bracket
    w1 = contextual_rank(D[1], wins0, pf0, h2h0)[0]; w2 = contextual_rank(D[2], wins0, pf0, h2h0)[0]
    nw0 = contextual_rank([t for t in TEAMS if t not in (w1, w2)], wins0, pf0, h2h0)
    field = {w1, w2, *nw0[:4]}
    made = {t: (1 if t in field else 0) for t in TEAMS}
    N = 1
    for t in TEAMS:
        status[t] = "clinched" if t in field else "out"
    status[w1] = status[w2] = "bye"
    try:
        wb_raw = json.load(open(yr_dir / "winners_bracket.json"))
        if any(isinstance(mt.get("t1"), int) for mt in wb_raw):
            random.seed(7)
            gauss = random.gauss
            finals_c = {t: 0 for t in TEAMS}; champs_c = {t: 0 for t in TEAMS}
            byes_c = {t: 0 for t in TEAMS}
            NB = 100000
            for s in range(NB):
                res = {}  # match id -> (winner_roster, loser_roster)
                for mt in sorted(wb_raw, key=lambda m: (m.get("r", 1), m.get("m", 0))):
                    def resolve(side):
                        v = mt.get(side)
                        if isinstance(v, int):
                            return v
                        fr = mt.get(side + "_from") or {}
                        if "w" in fr and fr["w"] in res:
                            return res[fr["w"]][0]
                        if "l" in fr and fr["l"] in res:
                            return res[fr["l"]][1]
                        return None
                    a, b = resolve("t1"), resolve("t2")
                    if a is None or b is None:
                        continue
                    if isinstance(mt.get("w"), int):           # already played
                        res[mt["m"]] = (mt["w"], mt["l"])
                    else:
                        wk = min(PSTART + mt.get("r", 1) - 1, 17)
                        pa = gauss(WP[a].get(wk, 100), sdv[a]); pb = gauss(WP[b].get(wk, 100), sdv[b])
                        res[mt["m"]] = (a, b) if pa >= pb else (b, a)
                    if mt.get("p") == 1:
                        fa, fb = (res[mt["m"]][0], res[mt["m"]][1])
                        finals_c[fa] += 1; finals_c[fb] += 1
                        champs_c[fa] += 1
            r1_teams = {mt.get("t1") for mt in wb_raw if mt.get("r") == 1} | \
                       {mt.get("t2") for mt in wb_raw if mt.get("r") == 1}
            title_odds = sorted([dict(owner=OWNER[t],
                                      playoffs=100.0 if t in field else 0.0,
                                      bye=100.0 if (t in field and t not in {x for x in r1_teams if x}) else 0.0,
                                      final=round(100 * finals_c[t] / NB, 2),
                                      champ=round(100 * champs_c[t] / NB, 3)) for t in TEAMS],
                                key=lambda o: -o["champ"])
            json.dump(dict(asof=f"playoffs, {season} (through week {last_scored})", sims=NB, odds=title_odds),
                      open("data/title_odds.json", "w", encoding="utf-8"), indent=1)
            print("wrote data/title_odds.json (live bracket simulation)")
    except Exception as ex:
        print("playoff title sim skipped:", ex)

# ---------------- 3c. odds history (for the trend graphs) ----------------
# Entering-week playoff and championship odds for every week of the season.
# Weeks before the book went live are reconstructed once from scores-to-date
# (season averages as projection means) and cached; the current week is always
# refreshed with the live 100k-sim numbers.
ODDS_HIST = Path("data/odds_history.json")
odds_hist = {}
if ODDS_HIST.exists():
    _oh = json.load(open(ODDS_HIST, encoding="utf-8"))
    if str(_oh.get("season")) == str(season):
        odds_hist = {int(k): v for k, v in _oh.get("weeks", {}).items()}
if weeks_left:
    def retro_odds(w, nsims=20000):
        wi = {t: 0 for t in TEAMS}; pfi = {t: 0.0 for t in TEAMS}
        h2i = {t: {u: [0, 0] for u in TEAMS} for t in TEAMS}
        sci = {t: [] for t in TEAMS}
        for ww in range(1, w):
            for a, b, p, q in games[ww]:
                if p == 0 and q == 0:
                    continue
                pfi[a] += p; pfi[b] += q; sci[a].append(p); sci[b].append(q)
                if p != q:
                    x_, y_ = (a, b) if p > q else (b, a)
                    wi[x_] += 1; h2i[x_][y_][0] += 1
                h2i[a][b][1] += 1; h2i[b][a][1] += 1
        # regress scores-to-date toward the league average (a ~5-game prior):
        # one hot or cold week is mostly noise, not true talent
        allsc = [s for t in TEAMS for s in sci[t]]
        lg_avg = sum(allsc) / len(allsc) if allsc else 110.0
        lg_sd = statistics.stdev(allsc) if len(allsc) > 1 else 22.0
        K = 5.0
        mean = {t: (sum(sci[t]) + K * lg_avg) / (len(sci[t]) + K) for t in TEAMS}
        sd_ = {t: (max(12.0, statistics.stdev(sci[t])) if len(sci[t]) > 3 else max(12.0, lg_sd))
               for t in TEAMS}
        schedR = [(a, b) for ww in range(w, PSTART) for a, b, *_ in games[ww]]
        made_ = {t: 0 for t in TEAMS}; champ_ = {t: 0 for t in TEAMS}
        random.seed(5)
        g_ = random.gauss
        for _ in range(nsims):
            wn = dict(wi); pn = dict(pfi)
            hn = {t: {u: list(v) for u, v in dd.items()} for t, dd in h2i.items()}
            for a, b in schedR:
                pa = g_(mean[a], sd_[a]); pb = g_(mean[b], sd_[b])
                if pa > pb:
                    wn[a] += 1; hn[a][b][0] += 1
                else:
                    wn[b] += 1; hn[b][a][0] += 1
                hn[a][b][1] += 1; hn[b][a][1] += 1
                pn[a] += max(30, pa); pn[b] += max(30, pb)
            w1_ = contextual_rank(D[1], wn, pn, hn)[0]; w2_ = contextual_rank(D[2], wn, pn, hn)[0]
            b_ = contextual_rank([w1_, w2_], wn, pn, hn)
            nw_ = contextual_rank([t for t in TEAMS if t not in (w1_, w2_)], wn, pn, hn)
            s1, s2 = b_; s3, s4, s5, s6 = nw_[:4]
            for t in (s1, s2, s3, s4, s5, s6):
                made_[t] += 1
            gm = lambda x_, y_: x_ if g_(mean[x_], sd_[x_]) >= g_(mean[y_], sd_[y_]) else y_
            champ_[gm(gm(s1, gm(s4, s5)), gm(s2, gm(s3, s6)))] += 1
        return dict(playoffs={OWNER[t]: round(100 * made_[t] / nsims, 1) for t in TEAMS},
                    champ={OWNER[t]: round(100 * champ_[t] / nsims, 2) for t in TEAMS})
    _oh_changed = False
    for w in range(1, cur_week):   # week 1 = the post-draft, everyone-even baseline
        if w not in odds_hist:
            print(f"backfilling odds history for week {w} ...")
            odds_hist[w] = retro_odds(w)
            _oh_changed = True
    _live = dict(playoffs={OWNER[t]: round(100 * title_counts["made"][t] / N_TITLE, 1) for t in TEAMS},
                 champ={OWNER[t]: round(100 * title_counts["champs"][t] / N_TITLE, 2) for t in TEAMS})
    if odds_hist.get(cur_week) != _live:
        odds_hist[cur_week] = _live
        _oh_changed = True
    if _oh_changed:
        json.dump(dict(season=season, weeks={str(k): v for k, v in sorted(odds_hist.items())}),
                  open(ODDS_HIST, "w", encoding="utf-8"), indent=1)
        print(f"odds history: {len(odds_hist)} weeks on file")
else:
    # regular season over: pin the settled after-week-14 point once
    if odds_hist and PSTART not in odds_hist and "title_odds" in globals():
        odds_hist[PSTART] = dict(playoffs={OWNER[t]: round(100.0 * made[t] / N, 1) for t in TEAMS},
                                 champ={o["owner"]: o["champ"] for o in title_odds})
        json.dump(dict(season=season, weeks={str(k): v for k, v in sorted(odds_hist.items())}),
                  open(ODDS_HIST, "w", encoding="utf-8"), indent=1)
        print("odds history: final settled point recorded")

# ---------------- 4. elimination solver (house rule, witness-verified) ----------------
def maxflow_feasible(glist, budget):
    G = len(glist)
    if G == 0:
        return True
    if sum(budget.values()) < G:
        return False
    tidx = {y: i for i, y in enumerate(budget)}
    n = 2 + G + len(budget); s, t = 0, n - 1
    cap = [[0] * n for _ in range(n)]
    for i, (a, b) in enumerate(glist):
        cap[s][1 + i] = 1
        cap[1 + i][1 + G + tidx[a]] = 1
        cap[1 + i][1 + G + tidx[b]] = 1
    for y, i in tidx.items():
        cap[1 + G + i][t] = budget[y]
    flow = 0
    while True:
        par = [-1] * n; par[s] = s; q = deque([s])
        while q and par[t] == -1:
            u = q.popleft()
            for v in range(n):
                if par[v] == -1 and cap[u][v] > 0:
                    par[v] = u; q.append(v)
        if par[t] == -1:
            return flow == G
        v = t
        while v != s:
            u = par[v]; cap[u][v] -= 1; cap[v][u] += 1; v = u
        flow += 1

def qualifies(x, wins, pf, h2h):
    winners = {contextual_rank(D[dn], wins, pf, h2h)[0] for dn in (1, 2)}
    if x in winners:
        return True
    return x in contextual_rank([t for t in TEAMS if t not in winners], wins, pf, h2h)[:4]

def alive(x, wins_now, h2h_now, future_games, tie_ok=True):
    """Can x still make the playoffs if every tie breaks x's way? A tie is
    never lost in advance: a multiway unbalanced tie group falls to points-for
    under the house rule, which can rescue a team that trails every pairwise
    head-to-head (it happened in 2024 and 2025). With tie_ok=False, asks the
    opposite question: can x still get in OUTRIGHT, without needing any
    tiebreaker (every tie counts as lost)?"""
    allg = [g for w in sorted(future_games) for g in future_games[w]]
    xg = [(a, b) for a, b in allg if x in (a, b)]
    pl = [(a, b) for a, b in allg if x not in (a, b)]
    M = wins_now[x] + len(xg)
    others = sorted([t for t in TEAMS if t != x], key=lambda t: -wins_now[t])
    caps = {y: (M if tie_ok else M - 1) for y in others}
    forced = [t for t in others if wins_now[t] > caps[t]]
    otherdiv = frozenset(t for t in others if division[t] != division[x])
    cands = ([otherdiv] if all(f in otherdiv for f in forced) else [])
    for S in itertools.combinations(others, 5):
        if {division[t] for t in S} == {1, 2} and all(f in S for f in forced):
            cands.append(frozenset(S))
    for S in cands:
        budget, bad = {}, False
        for y in others:
            if y in S:
                continue
            room = caps[y] - wins_now[y]
            if room < 0:
                bad = True; break
            budget[y] = room
        if bad:
            continue
        glist = [(a, b) for a, b in pl if a in budget and b in budget]
        if maxflow_feasible(glist, budget):
            return True
    return False

def factor_conditions(combos, games_list):
    """Factor winner-combos into readable conditions: constant winners as AND lines,
    the remainder minimized into an OR-of-ANDs clause."""
    needs = []
    residual_idx = []
    for i, (a, b) in enumerate(games_list):
        winners_i = {c[i] for c in combos}
        if len(winners_i) == 1:
            wteam = winners_i.pop()
            needs.append(f"{OWNER[wteam]} beats {OWNER[b if wteam == a else a]}")
        else:
            residual_idx.append(i)
    if residual_idx:
        variants = {tuple(c[i] for i in residual_idx) for c in combos}
        if len(variants) < 2 ** len(residual_idx):
            terms, changed = set(variants), True
            while changed:
                changed = False
                tl = sorted(terms, key=str); new, used = set(), set()
                for ii in range(len(tl)):
                    for jj in range(ii + 1, len(tl)):
                        a_, b_ = tl[ii], tl[jj]
                        diff = [k for k in range(len(a_)) if a_[k] != b_[k]]
                        if len(diff) == 1:
                            m = tuple(None if k == diff[0] else a_[k] for k in range(len(a_)))
                            new.add(m); used.add(a_); used.add(b_); changed = True
                terms = (terms - used) | new
            alts = []
            for v in sorted(terms, key=str):
                parts = []
                for i, wteam in zip(residual_idx, v):
                    if wteam is None:
                        continue
                    a, b = games_list[i]
                    parts.append(f"{OWNER[wteam]} beats {OWNER[b if wteam == a else a]}")
                if parts:
                    alts.append(" AND ".join(parts))
            if alts and len(alts) <= 4:
                needs.append("Plus one of: " + "  |  ".join(alts))
            elif alts:
                needs.append("Plus specific combinations of the other games")
    return needs

# ---------------- 3a. games already decided on the field ----------------
# When every starter on both sides has a final score, the game's result is a
# fact even though Sleeper hasn't closed the week: promote it to a real
# win/loss for the solvers. Official standings and records still wait for the
# fully scored week.
decided = {}
if blended and weeks_left:
    for a, b in future.get(cur_week, []):
        if WSD[a][cur_week] == 0 and WSD[b][cur_week] == 0 and WP[a][cur_week] != WP[b][cur_week]:
            decided[frozenset((a, b))] = a if WP[a][cur_week] > WP[b][cur_week] else b
wins_eff = dict(wins0)
h2h_eff = {t: {u: list(v) for u, v in dd.items()} for t, dd in h2h0.items()}
for pair, wnr in decided.items():
    lsr = next(t for t in pair if t != wnr)
    wins_eff[wnr] += 1
    h2h_eff[wnr][lsr][0] += 1
    h2h_eff[wnr][lsr][1] += 1; h2h_eff[lsr][wnr][1] += 1
future_eff = {w: [g for g in gs if not (w == cur_week and frozenset(g) in decided)]
              for w, gs in future.items()}
if decided:
    print(f"on-the-field finals: {len(decided)} of this week's games already count for the solvers")

elim_cards = []
brink_cards = []
clinch_cards = []
elimnote = ""
if not FAST and future_eff.get(cur_week):
    print("running elimination solver ...")
    WEEKG = future_eff[cur_week]
    FUTA = {w: future_eff[w] for w in future_eff if w > cur_week}
    for x in TEAMS:
        other = [(a, b) for a, b in WEEKG if x not in (a, b)]
        xg = [(a, b) for a, b in WEEKG if x in (a, b)]
        if not xg:
            continue
        opp = xg[0][0] if xg[0][1] == x else xg[0][1]
        combos, brink_combos = [], []
        for combo in itertools.product(*[[a, b] for a, b in other]):
            w9 = dict(wins_eff); w9[opp] += 1
            hh = {t: {u: list(v) for u, v in dd.items()} for t, dd in h2h_eff.items()}
            def rec2(wi, lo):
                hh[wi][lo][1] += 1; hh[lo][wi][1] += 1; hh[wi][lo][0] += 1
            rec2(opp, x)
            for wt, (a, b) in zip(combo, other):
                w9[wt] += 1; rec2(wt, b if wt == a else a)
            if not alive(x, w9, hh, FUTA):
                combos.append(combo)
            elif not alive(x, w9, hh, FUTA, tie_ok=False):
                brink_combos.append(combo)
        if combos:
            needs = [f"Loses to {OWNER[opp]}"] + factor_conditions(combos, other)
            elim_cards.append(dict(owner=OWNER[x], needs=needs))
        if brink_combos:
            needs = [f"Loses to {OWNER[opp]}"] + factor_conditions(brink_combos, other)
            brink_cards.append(dict(owner=OWNER[x], needs=needs, odds=lose_odds.get(x)))
    elimnote = "Shown only when no combination of results and tiebreakers could save the team — a spot still reachable via a points-for tiebreak counts as alive."

# ---------------- 4a. clinched / eliminated right now ----------------
def can_miss(x, wins_now=None, h2h_now=None, future_games=None):
    """Does ANY remaining outcome leave x out of the playoffs? (x loses out = worst case)"""
    wins_now = wins_now or wins0
    h2h_now = h2h_now or h2h0
    future_games = future_games or future
    allg = [g for w in sorted(future_games) for g in future_games[w]]
    M = wins_now[x]
    others = [t for t in TEAMS if t != x]
    owndiv = [t for t in others if division[t] == division[x]]
    # a tie can always break against x (a multiway unbalanced tie group falls
    # to points-for), so reaching x's win total is enough to finish above x
    thresh = {y: M for y in others}
    def above_feasible(T):
        Tset = set(T)
        kk, glist = {}, []
        for a, b in allg:
            if a in Tset and b in Tset:
                glist.append((a, b))
        for y in T:
            free_wins = sum(1 for a, b in allg if (a == y) != (b == y)
                            and not (a in Tset and b in Tset))
            k = thresh[y] - (wins_now[y] + free_wins)
            if k > len([1 for a, b in glist if y in (a, b)]):
                return False
            kk[y] = max(0, k)
        need = sum(kk.values())
        if need == 0:
            return True
        # flow: each internal game gives 1 win; each y must collect >= kk[y]
        tidx = {y: i for i, y in enumerate(T)}
        n = 2 + len(glist) + len(T); s, t = 0, n - 1
        cap = [[0] * n for _ in range(n)]
        for i, (a, b) in enumerate(glist):
            cap[s][1 + i] = 1
            cap[1 + i][1 + len(glist) + tidx[a]] = 1
            cap[1 + i][1 + len(glist) + tidx[b]] = 1
        for y, i in tidx.items():
            cap[1 + len(glist) + i][t] = kk[y]
        flow = 0
        while True:
            par = [-1] * n; par[s] = s; q = deque([s])
            while q and par[t] == -1:
                u = q.popleft()
                for v in range(n):
                    if par[v] == -1 and cap[u][v] > 0:
                        par[v] = u; q.append(v)
            if par[t] == -1:
                return flow >= need
            v = t
            while v != s:
                u = par[v]; cap[u][v] -= 1; cap[v][u] += 1; v = u
            flow += 1
    # minimal "x misses" families: all 5 division rivals above, or any 6 others (>=1 rival) above
    if above_feasible(owndiv):
        return True
    for T in itertools.combinations(others, 6):
        if any(division[y] == division[x] for y in T) and above_feasible(T):
            return True
    return False

def can_lose_division(x, wins_now, h2h_now, future_games):
    """Can any division rival still catch x? A rival who can merely tie x
    can take the title (ties are never safe in advance under the house rule)."""
    allg = [g for w in sorted(future_games) for g in future_games[w]]
    M = wins_now[x]
    for y in (t for t in TEAMS if t != x and division[t] == division[x]):
        max_y = wins_now[y] + sum(1 for a, b in allg if y in (a, b))
        if max_y >= M:
            return True
    return False

if weeks_left:
    print("computing clinch/elimination status ...")
    for x in TEAMS:
        if not alive(x, wins_eff, h2h_eff, future_eff):
            status[x] = "out"
        elif not can_lose_division(x, wins_eff, h2h_eff, future_eff):
            status[x] = "bye"
        elif not can_miss(x, wins_eff, h2h_eff, future_eff):
            status[x] = "clinched"

    # clinch scenarios: can any combination of this week's results LOCK a playoff spot?
    if not FAST and future_eff.get(cur_week):
        print("running clinch-scenario solver ...")
        week_games = future_eff[cur_week]
        futa_c = {w: future_eff[w] for w in future_eff if w > cur_week}
        for x in TEAMS:
            if status.get(x) in ("bye", "out"):
                continue
            spot_combos, bye_combos = [], []
            for combo in itertools.product(*[[a, b] for a, b in week_games]):
                wC = dict(wins_eff)
                hh = {t: {u: list(v) for u, v in dd.items()} for t, dd in h2h_eff.items()}
                for wt, (a, b) in zip(combo, week_games):
                    wC[wt] += 1
                    lt = b if wt == a else a
                    hh[wt][lt][1] += 1; hh[lt][wt][1] += 1; hh[wt][lt][0] += 1
                if not can_lose_division(x, wC, hh, futa_c):
                    bye_combos.append(combo)
                elif status.get(x) != "clinched" and not can_miss(x, wC, hh, futa_c):
                    spot_combos.append(combo)
            if bye_combos:
                clinch_cards.append(dict(owner=OWNER[x], kind="bye",
                                         div=DIVNAME[division[x]],
                                         needs=factor_conditions(bye_combos, week_games)))
            elif spot_combos:
                clinch_cards.append(dict(owner=OWNER[x], kind="spot",
                                         needs=factor_conditions(spot_combos, week_games)))

# ---------------- 4a2. longshot survival paths ----------------
# For every team still mathematically alive but under 2% simulated odds, search
# the remaining schedule for its easiest surviving world and write it up: the
# win-out requirement, who has to collapse and how far, the final tie shape,
# and the points-for hill inside that tie.
paths_cards = []
if weeks_left and not FAST:
    print("writing longshot survival paths ...")
    REGW = PSTART - 1
    pre_wins = dict(wins_eff)   # on-the-field finals already count
    sched_open = [(w, a, b) for w in sorted(future_eff) for a, b in future_eff[w]]
    left = {t: sum(1 for _, a, b in sched_open if t in (a, b)) for t in TEAMS}

    def final_alive(x, fw):
        """Tie-agnostic qualification on a FINAL wins table."""
        others = [t for t in TEAMS if t != x]
        M = fw[x]
        forced = [t for t in others if fw[t] > M]
        if len(forced) > 5:
            return False
        otherdiv = frozenset(t for t in others if division[t] != division[x])
        cands = ([otherdiv] if all(f in otherdiv for f in forced) else [])
        for S in itertools.combinations(others, 5):
            if {division[t] for t in S} == {1, 2} and all(f in S for f in forced):
                cands.append(frozenset(S))
        return any(all(fw[y] <= M for y in others if y not in S) for S in cands)

    for x in TEAMS:
        pct = 100 * made[x] / N
        if status.get(x) == "out" or pct >= 2.0:
            continue
        maxw = pre_wins[x] + left[x]
        random.seed(11)
        best = None
        for _ in range(150000):
            fw = dict(pre_wins)
            fw[x] = maxw
            for w, a, b in sched_open:
                if x in (a, b):
                    continue
                lo_, hi_ = (a, b) if pre_wins[a] <= pre_wins[b] else (b, a)
                fw[lo_ if random.random() < 0.65 else hi_] += 1   # collapse-friendly worlds
            if final_alive(x, fw):
                above = sum(1 for t in TEAMS if t != x and fw[t] > fw[x])
                if best is None or above < best[0]:
                    best = (above, dict(fw))
        if not best:
            continue
        above, fw = best
        spots = 6 - above
        group = [t for t in TEAMS if t != x and fw[t] == fw[x]]
        rec = f"{maxw}-{REGW - maxw}"
        prec = lambda t: f"{pre_wins[t]}-{REGW - left[t] - pre_wins[t]}"
        collapse = []
        for t in sorted(group, key=lambda t: -pre_wins[t]):
            allow = maxw - pre_wins[t]
            collapse.append(f"{OWNER[t]} ({prec(t)}) can't win again" if allow <= 0
                            else f"{OWNER[t]} ({prec(t)}) can win at most {allow} more")
        text = f"Even winning out only gets {OWNER[x]} to {rec} — and the path needs the middle to collapse: " \
               + "; ".join(collapse) + ". "
        r_needed = len(group) - (spots - 1)
        if not group or r_needed <= 0:
            text += (f"That leaves a {len(group) + 1}-way tie at {rec} with room for everyone inside the field — "
                     "win out and the spot is there." if group else
                     f"In that world {OWNER[x]} slots straight into the field at {rec}.")
        else:
            text += (f"That leaves a {len(group) + 1}-way tie at {rec} for the last "
                     + (f"{spots} spots" if spots > 1 else "spot") + ". ")
            mixed = len({division[t] for t in group + [x]}) > 1
            rivals_pf = sorted((pf0[t], OWNER[t]) for t in group)
            binding_pf, binding_name = rivals_pf[r_needed - 1]
            gap = binding_pf - pf0[x]
            tiebk = ("The tie crosses divisions, so it falls to season points-for: "
                     if mixed else "The tie falls to the league tiebreakers: ")
            if gap > 0:
                text += (tiebk + f"{OWNER[x]} ({pf0[x]:.1f}) must out-score {r_needed} of the {len(group)} — "
                         f"at minimum overtaking {binding_name}, currently {gap:.1f} points ahead.")
            else:
                text += tiebk + f"{OWNER[x]} ({pf0[x]:.1f}) already leads the group's points race."
        text += f" Simulated odds: {pct:.1f}%."
        paths_cards.append(dict(owner=OWNER[x], rec=prec(x), text=text))

# ---------------- 4b. playoff brackets (appear automatically once seeded) ----------------
def bracket_view(bracket, final_label):
    if not bracket:
        return None
    maxp = max((mt.get("p") or 0) for mt in bracket)
    out = []
    for mt in sorted(bracket, key=lambda m: (m.get("r", 1), m.get("m", 0))):
        r = mt.get("r", 1)
        wk = PSTART + r - 1
        def ref(side):
            v = mt.get(side)
            if isinstance(v, int):
                return OWNER.get(v, "?")
            fr = mt.get(side + "_from") or {}
            # toilet bowl: Sleeper's "w" (advancer) is the game's score LOSER
            if "w" in fr:
                return f"Loser of M{fr['w']}" if final_label == "lerch" else f"Winner of M{fr['w']}"
            if "l" in fr:
                return f"Winner of M{fr['l']}" if final_label == "lerch" else f"Loser of M{fr['l']}"
            return "TBD"
        s1 = s2 = None
        if isinstance(mt.get("t1"), int) and isinstance(mt.get("t2"), int):
            for a, b, p_, q_ in games.get(wk, []):
                if {a, b} == {mt["t1"], mt["t2"]} and (p_ or q_):
                    s1, s2 = (p_, q_) if a == mt["t1"] else (q_, p_)
        p = mt.get("p")
        if p == 1:
            # losers bracket is a toilet bowl: the p=1 match decides LAST place
            label = "Buck Wong Bowl" if final_label == "bwb" else "Lerch Cup game"
        elif p:
            label = "Place game"
        else:
            label = "Semifinal" if r == 2 and final_label == "bwb" else f"Round {r}"
        if final_label == "bwb" and r == 1:
            label = "Quarterfinal"
        # display winner = who scored more (the bracket's own w field means
        # "advancer", which in the toilet bowl is the game's LOSER)
        disp_w = None
        if s1 is not None and s2 is not None:
            disp_w = ref("t1") if s1 > s2 else ref("t2")
        out.append(dict(m=mt.get("m"), r=r, week=wk, label=label,
                        t1=ref("t1"), t2=ref("t2"), s1=s1, s2=s2, w=disp_w))
    return out

# ---------------- 5. write now.json ----------------
order = contextual_rank(TEAMS, wins0, pf0, h2h0)
leaders = {contextual_rank(D[dn], wins0, pf0, h2h0)[0] for dn in (1, 2)}
nonw = contextual_rank([t for t in TEAMS if t not in leaders], wins0, pf0, h2h0)
seed_order = sorted(leaders, key=lambda t: order.index(t)) + nonw

brackets = None
if weeks_left:
    # projected brackets seeded by OUR standings (house tiebreak rules), using the
    # league's bracket template as verified against the actual 2025 brackets
    S = [None] + [OWNER[t] for t in seed_order]   # S[1]..S[12]
    def pm(m, r, wk, label, t1, t2):
        return dict(m=m, r=r, week=wk, label=label, t1=t1, t2=t2, s1=None, s2=None, w=None)
    W1, W2, W3 = PSTART, PSTART + 1, PSTART + 2
    brackets = dict(projected=True, bwb=[
        pm(1, 1, W1, "Quarterfinal", S[4], S[5]),
        pm(2, 1, W1, "Quarterfinal", S[3], S[6]),
        pm(3, 2, W2, "Semifinal", S[1], "Winner of M1"),
        pm(4, 2, W2, "Semifinal", S[2], "Winner of M2"),
        pm(5, 2, W2, "Place game", "Loser of M1", "Loser of M2"),
        pm(6, 3, W3, "Buck Wong Bowl", "Winner of M3", "Winner of M4"),
        pm(7, 3, W3, "Place game", "Loser of M3", "Loser of M4"),
    ], lerch=[
        pm(1, 1, W1, "Round 1", S[9], S[7]),
        pm(2, 1, W1, "Round 1", S[8], S[10]),
        pm(3, 2, W2, "Round 2", S[12], "Loser of M1"),
        pm(4, 2, W2, "Round 2", S[11], "Loser of M2"),
        pm(5, 2, W2, "Place game", "Winner of M1", "Winner of M2"),
        pm(6, 3, W3, "Lerch Cup game", "Loser of M3", "Loser of M4"),
        pm(7, 3, W3, "Place game", "Winner of M3", "Winner of M4"),
    ])
else:
    try:
        wb_raw = json.load(open(yr_dir / "winners_bracket.json"))
        lb_raw = json.load(open(yr_dir / "losers_bracket.json"))
        seeded = any(isinstance(mt.get("t1"), int) or isinstance(mt.get("t2"), int)
                     for mt in (wb_raw or []) + (lb_raw or []))
        if seeded:
            brackets = dict(bwb=bracket_view(wb_raw, "bwb"), lerch=bracket_view(lb_raw, "lerch"),
                            projected=False)
    except Exception as ex:
        print("bracket build skipped:", ex)
standings = []
for i, t in enumerate(seed_order, 1):
    standings.append(dict(seed=i, owner=OWNER[t], team=teamname[t],
                          rec=f"{wins0[t]}-{last_scored - wins0[t]}", pf=round(pf0[t], 1),
                          div=DIVNAME[division[t]],
                          **({"leader": True} if t in leaders else {}),
                          **({"status": status[t]} if t in status else {})))
matchup_cards = [dict(a=OWNER[a], pa=round(WP[a][cur_week], 1), b=OWNER[b], pb=round(WP[b][cur_week], 1))
                 for a, b in future.get(cur_week, [])]

# Playoff Machine payload: everything the client needs to run pick-conditioned sims
# with the exact house seeding (contextual_rank). Owner lists keep roster-id order
# so stable-sort tie handling matches Python.
machine = None
if weeks_left:
    machine = dict(
        weeks={str(w): [[OWNER[a], OWNER[b]] for a, b in future[w]] for w in weeks_left},
        pweeks=PLAYOFF_WEEKS,
        proj={OWNER[t]: {str(w): round(WP[t][w], 1) for w in weeks_left + PLAYOFF_WEEKS} for t in TEAMS},
        sd={OWNER[t]: round(sdv[t], 1) for t in TEAMS},
        sdw=({OWNER[t]: round(WSD[t][cur_week], 1) for t in TEAMS} if blended else None),
        wins={OWNER[t]: wins0[t] for t in TEAMS},
        pf={OWNER[t]: round(pf0[t], 2) for t in TEAMS},
        h2hw={OWNER[t]: {OWNER[u]: h2h0[t][u][0] for u in TEAMS if u != t} for t in TEAMS},
        scnt={OWNER[t]: {OWNER[u]: season_cnt[t][u] for u in TEAMS if u != t} for t in TEAMS},
        divs=[[OWNER[t] for t in D[1]], [OWNER[t] for t in D[2]]],
        divnames=[DIVNAME[1], DIVNAME[2]],
        torder=[OWNER[t] for t in TEAMS],
        played=last_scored,
    )
now = dict(
    asof=(f"entering week {cur_week}, {season}" if weeks_left
          else f"playoffs, {season} — regular season complete"),
    standings=standings,
    odds=sorted([dict(owner=OWNER[t], pct=round(100 * made[t] / N, 1)) for t in TEAMS],
                key=lambda o: -o["pct"]),
    oddsnote="100,000 full-season simulations (regular season + playoff bracket). Weekly player "
             "projections, optimized lineups (no K/DEF), bye and injury holes filled with a waiver "
             "add (top-5 free agent average). League tiebreakers applied."
             + (" This week's finished games are locked in: those starters count at their actual "
                "points in their actual slots, and only players who haven't kicked off are projected."
                if blended else ""),
    week=cur_week,
    matchups=matchup_cards,
    elim=elim_cards,
    elimnote=elimnote,
    brink=brink_cards,
    brinknote="On the brink: those results leave no path to the playoffs without winning a points-for tiebreak. The percentage is the team's playoff odds across 100,000 simulations if the loss happens; under 1% counts as effectively out." if brink_cards else "",
    paths=paths_cards,
    pathnote="The easiest surviving world from a search of the remaining schedule, shown for teams under 2% simulated playoff odds. Records include this week's finished games; points-for totals run through the last fully scored week." if paths_cards else "",
    oddshist=({str(k): v for k, v in sorted(odds_hist.items())} if odds_hist else None),
    clinch=clinch_cards,
    clinchnote="Lock up a playoff spot no matter what happens the rest of the season." if clinch_cards else "",
    brackets=brackets,
    machine=machine,
)
json.dump(now, open("data/now.json", "w", encoding="utf-8"), indent=1)
print("wrote data/now.json")

# ---------------- 6. rebuild site ----------------
import subprocess
subprocess.run([sys.executable, "-I", "build.py", "data/raw"], check=True)
print("done — site/index.html is current. Republish the artifact to update the shared page.")

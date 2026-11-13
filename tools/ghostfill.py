"""Fill ESPN-era lineup ghosts (players purged from ESPN's archive) by matching
the missing points against nflverse weekly stats, constrained by lineup slots
and team context. Patches pages/data/raw/espn/{yr}_box_{w}.json in place."""
import csv, io, json, re, urllib.request
from pathlib import Path
from collections import defaultdict

ESPN = Path(r"C:\Repositories\Sleeper\pages\data\raw\espn")
POSN = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF"}
PS = {"QB": 1, "RB": 2, "WR": 3, "TE": 4, "K": 5, "DEF": 16}

def norm(s):
    s = re.sub(r"[^a-z ]", "", (s or "").lower())
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    return s.replace(" ", "")

def f(r, k):
    try:
        return float(r.get(k) or 0)
    except ValueError:
        return 0.0

def week_pts(rows, ppr):
    out = defaultdict(lambda: defaultdict(float))   # norm name -> week -> pts
    names = {}
    for r in rows:
        if r.get("season_type") != "REG":
            continue
        pts = (f(r,"passing_yards")*0.04 + f(r,"passing_tds")*4 - f(r,"interceptions")*2
               + f(r,"rushing_yards")*0.1 + f(r,"rushing_tds")*6
               + f(r,"receiving_yards")*0.1 + f(r,"receiving_tds")*6 + f(r,"receptions")*ppr
               - (f(r,"sack_fumbles_lost")+f(r,"rushing_fumbles_lost")+f(r,"receiving_fumbles_lost"))*2
               + (f(r,"passing_2pt_conversions")+f(r,"rushing_2pt_conversions")+f(r,"receiving_2pt_conversions"))*2
               + f(r,"special_teams_tds")*6)
        k = norm(r.get("player_display_name"))
        out[k][int(r["week"])] += pts
        names[k] = r.get("player_display_name")
    return out, names

filled_total = 0
for yr in range(2014, 2020):
    url = f"https://github.com/nflverse/nflverse-data/releases/download/player_stats/player_stats_{yr}.csv"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        nfl, names = week_pts(list(csv.DictReader(io.StringIO(r.read().decode("utf-8", "ignore")))),
                              0.5 if yr == 2019 else 0.0)
    # candidate positions: every (name, pos) seen in this year's boxes or draft
    posof, team_pool, weekly_started = {}, defaultdict(set), defaultdict(set)
    boxes = {}
    for w in range(1, 18):
        fp = ESPN / f"{yr}_box_{w}.json"
        if not fp.exists():
            continue
        boxes[w] = json.load(open(fp, encoding="utf-8"))
        for row in boxes[w]:
            for side in ("home", "away"):
                s = row.get(side) or {}
                for p in s.get("players") or []:
                    if p.get("name"):
                        posof[p["name"]] = p.get("pos")
                        team_pool[s.get("teamId")].add(p["name"])
                        weekly_started[w].add(p["name"])
    df = ESPN / f"{yr}_draft.json"
    pmapf = ESPN / f"{yr}_players.json"
    if df.exists() and pmapf.exists():
        pmap = json.load(open(pmapf, encoding="utf-8"))
        for pick in json.load(open(df, encoding="utf-8")).get("picks") or []:
            info = pmap.get(str(pick.get("playerId"))) or {}
            if info.get("n"):
                posof.setdefault(info["n"], info.get("p"))
                team_pool[pick.get("teamId")].add(info["n"])
    tol = 0.6 if yr == 2019 else 1.01
    filled = 0
    for w, rows in boxes.items():
        changed = False
        for row in rows:
            for side in ("home", "away"):
                s = row.get(side) or {}
                pls = s.get("players") or []
                if not pls or len(pls) >= 9:
                    continue
                gap = (s.get("totalPoints") or 0) - sum(p["pts"] for p in pls)
                if abs(gap) < 0.5 or len(pls) < 7:
                    continue
                # which positions can the missing slot(s) hold?
                cnt = defaultdict(int)
                for p in pls:
                    cnt[p.get("pos")] += 1
                allowed = set()
                if cnt[1] < 1: allowed.add("QB")
                skill = cnt[2] + cnt[3] + cnt[4]
                if cnt[2] < 2 or skill < 6: allowed.add("RB")
                if cnt[3] < 2 or skill < 6: allowed.add("WR")
                if cnt[4] < 1 or skill < 6: allowed.add("TE")
                missing_n = 9 - len(pls)
                # tier 1: this team's other-week starters + their draft class
                cands = [nm for nm in team_pool[s.get("teamId")]
                         if nm not in {p["name"] for p in pls}
                         and nm not in weekly_started[w]
                         and POSN.get(posof.get(nm)) in allowed]
                def match(pool, tolerance):
                    hits = [nm for nm in pool if abs(nfl.get(norm(nm), {}).get(w, -999) - gap) <= tolerance]
                    return hits
                hit = None
                if missing_n == 1:
                    h = match(cands, tol)
                    if len(h) == 1:
                        hit = h[0]
                    else:
                        wide = [nm for nm in posof
                                if POSN.get(posof.get(nm)) in allowed
                                and nm not in weekly_started[w]]
                        h2 = match(wide, 0.35)
                        if len(h2) == 1:
                            hit = h2[0]
                if hit:
                    pts = round(nfl[norm(hit)][w], 1)
                    s["players"].append({"name": hit, "pos": posof[hit], "pts": round(gap, 1) if abs(pts-gap) <= tol else pts, "ghost": 1})
                    filled += 1
                    changed = True
        if changed:
            json.dump(rows, open(ESPN / f"{yr}_box_{w}.json", "w", encoding="utf-8"))
    print(f"{yr}: filled {filled} ghost starters")
    filled_total += filled
print("TOTAL:", filled_total)

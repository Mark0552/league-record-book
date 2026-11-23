"""Build data/draft_teams.json: the NFL team each drafted player was on in week 1
of the season he was drafted (nflverse weekly rosters), for the owner
"tendencies" block (favourite NFL team to draft from).

Reads the picks from the built page (site/index.html), so build first.
Run once per season after the draft (needs network, or DNP_LOCAL=<dir of
roster_weekly_<year>.csv files>):
    python tools/draft_teams.py [year ...]
Seasons already in the file are kept unless named on the command line.
Franchises that moved are folded into one code (STL->LA, SD->LAC, OAK->LV).
"""
import csv, io, json, os, re, sys, urllib.request
from pathlib import Path

OUT = Path("data/draft_teams.json")
BASE = "https://github.com/nflverse/nflverse-data/releases/download"
FIX = {"CLV": "CLE", "BLT": "BAL", "HST": "HOU", "ARZ": "ARI", "SL": "LA", "STL": "LA", "LAR": "LA",
       "JAC": "JAX", "SD": "LAC", "OAK": "LV"}
POSMAP = {"FB": "RB", "HB": "RB"}

def norm(s):
    return re.sub(r"[^a-z]", "", re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", (s or "").lower()))

def rosters(y):
    local = os.environ.get("DNP_LOCAL")
    if local and (Path(local) / f"roster_weekly_{y}.csv").exists():
        text = open(Path(local) / f"roster_weekly_{y}.csv", encoding="utf-8").read()
    else:
        req = urllib.request.Request(f"{BASE}/weekly_rosters/roster_weekly_{y}.csv", headers={"User-Agent": "Mozilla/5.0"})
        text = urllib.request.urlopen(req, timeout=120).read().decode("utf-8", "ignore")
    first = {}   # (name, pos) -> (week, team): the earliest regular-season row
    for r in csv.DictReader(io.StringIO(text)):
        if r.get("game_type") not in ("REG", "", None) or not (r.get("week") or "").isdigit():
            continue
        k = (norm(r["full_name"]), POSMAP.get(r["position"], r["position"]))
        w = int(r["week"])
        if k not in first or w < first[k][0]:
            first[k] = (w, FIX.get(r["team"], r["team"]))
    return first

def main():
    html = open("site/index.html", encoding="utf-8").read()
    data = json.JSONDecoder().raw_decode(html[html.index("{", html.index("const DATA")):])[0]
    picks = data["draft"]["all"]
    out = json.load(open(OUT, encoding="utf-8")) if OUT.exists() else {}
    want = [int(a) for a in sys.argv[1:]]
    years = want or sorted({p["season"] for p in picks if str(p["season"]) not in out})
    for y in years:
        try:
            first = rosters(y)
        except Exception as e:
            print(f"{y}: rosters unavailable ({e!r})"); continue
        byname = {}
        for (n, pos), (w, t) in first.items():
            byname.setdefault(n, []).append(t)
        res, miss = {}, []
        for p in picks:
            if p["season"] != y or p["pos"] == "DEF":
                continue
            n = norm(p["player"])
            hit = first.get((n, p["pos"]))
            tm = hit[1] if hit else (byname[n][0] if len(byname.get(n, [])) == 1 else None)
            if tm:
                res[f'{n}|{p["pos"]}'] = tm
            else:
                miss.append(p["player"])
        out[str(y)] = res
        print(f"{y}: {len(res)} picks matched, {len(miss)} unmatched {miss[:8]}")
    json.dump(out, open(OUT, "w", encoding="utf-8"), separators=(",", ":"), sort_keys=True)

if __name__ == "__main__":
    main()

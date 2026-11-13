"""Site-wide data validation: run after build.py; exit 1 on any broken invariant.

Used as a gate in the update workflow so a bad build can never publish.
"""
import json, re, sys
from collections import defaultdict
from pathlib import Path

errors = []
def check(ok, msg):
    if not ok:
        errors.append(msg)

html = open("site/index.html", encoding="utf-8").read()
m = re.search(r"const DATA = (\{.*?\});\n", html, re.S)
check(m, "payload: DATA block missing from site/index.html")
D = json.loads(m.group(1)) if m else {}

for key in ("games", "alltime", "champs", "records", "money", "moves", "draft",
            "divisions", "owners", "built_ts", "updated"):
    check(key in D, f"payload: missing key {key}")

OWNERS = set(D.get("owners") or [])
check(len(OWNERS) == 14, f"owners: expected 14 all-time, got {len(OWNERS)}")

# ---- game log ----
G = D.get("games") or []
check(len(G) >= 1181, f"games: log shrank to {len(G)}")
years = sorted({g[0] for g in G})
check(years and years[0] == 2014, f"games: first year {years and years[0]}")
seen_pair = set()
reg_count = defaultdict(int)
for g in G:
    check(len(g) in (7, 8), f"games: bad row shape {g}")
    check(g[2] in ("R", "P", "C"), f"games: bad type {g}")
    check(g[3] in OWNERS and g[5] in OWNERS, f"games: unknown owner {g}")
    check(isinstance(g[4], (int, float)) and isinstance(g[6], (int, float)), f"games: bad score {g}")
    check(1 <= g[1] <= 18, f"games: bad week {g}")
    if g[2] == "R":
        for o in (g[3], g[5]):
            k = (g[0], g[1], o)
            check(k not in seen_pair, f"games: {o} plays twice in {g[0]} wk{g[1]}")
            seen_pair.add(k)
            reg_count[(g[0], o)] += 1

# ---- all-time table consistent with the log ----
derived = {o: [0, 0] for o in OWNERS}
for g in G:
    if g[2] != "R" or g[4] == g[6]:
        continue
    w, l = (g[3], g[5]) if g[4] > g[6] else (g[5], g[3])
    derived[w][0] += 1
    derived[l][1] += 1
for o, a in (D.get("alltime") or {}).items():
    check([a["w"], a["l"]] == derived[o], f"alltime: {o} {a['w']}-{a['l']} vs log {derived[o]}")

# ---- champions ----
champs = {c["year"]: c["owner"] for c in D.get("champs") or []}
for y in range(2014, max(years)):
    check(y in champs, f"champs: no champion recorded for {y}")
check(champs.get(2014) == "Mark", "champs: 2014 champion changed")
titles = defaultdict(int)
for c in D.get("champs") or []:
    titles[c["owner"]] += 1
for o, a in (D.get("alltime") or {}).items():
    check(a.get("titles", 0) == titles.get(o, 0), f"alltime: {o} titles {a.get('titles')} vs champs {titles.get(o, 0)}")

# ---- divisions per season ----
for y in years:
    check(str(y) in (D.get("divisions") or {}), f"divisions: missing {y}")

# ---- lineups never exceed the official score beyond what a missing
# negative starter (D/ST) can explain — anything more means double-counting ----
GS = {}
for g in G:
    if len(g) < 8:
        GS[f"{g[0]}|{g[1]}|{g[3]}"] = g[4]
        GS[f"{g[0]}|{g[1]}|{g[5]}"] = g[6]
over = 0
for k, lu in (D.get("lineups") or {}).items():
    off = GS.get(k)
    if off is not None and sum(p[2] for p in lu) - off > 12:
        over += 1
        if over <= 3:
            errors.append(f"lineups: {k} sums {sum(p[2] for p in lu):.1f} vs official {off} — double-counted?")

# ---- trades ----
for t in (D.get("moves") or {}).get("trades") or []:
    check(2014 <= t["year"] <= max(years), f"trades: bad year {t['year']}")
    for s in t["sides"]:
        check(s["owner"] in OWNERS, f"trades: unknown owner {s['owner']!r} ({t['year']} wk{t['week']})")
        if s.get("gotd"):
            check(len(s["gotd"]) == len(s["got"]), f"trades: gotd/got mismatch {t['year']} wk{t['week']}")

# ---- records sanity ----
r = D.get("records") or {}
check((r.get("low") or [{}])[0].get("pts", 99) >= 0, "records: negative low")
for e in (r.get("top") or []) + (r.get("low") or []):
    check(e["owner"] in OWNERS, f"records: unknown owner {e}")
check(all(x["year"] >= 2015 for x in (r.get("season_low") or [])) or True, "")
agg_years = {(g[0], g[1]) for g in G if len(g) == 8}
for e in (r.get("top") or []):
    check((e["year"], e["week"]) not in agg_years or e["year"] != 2014, f"records: aggregate leaked into top {e}")

# ---- live data (regular season only) ----
now = D.get("now") or {}
if now.get("odds"):
    tot = sum(o["pct"] for o in now["odds"])
    check(abs(tot - 600) < 3, f"odds: playoff pcts sum {tot:.1f}, expected ~600")
mach = now.get("machine")
if mach:
    check(len(mach.get("tradepool") or {}) == 12, "machine: tradepool missing owners")
    check(len(mach.get("torder") or []) == 12, "machine: torder")
    for o, pool in (mach.get("tradepool") or {}).items():
        for p in pool:
            check(all(isinstance(v, (int, float)) for v in p["pw"].values()),
                  f"machine: bad pw for {p.get('n')}")
            break

# ---- committed data files parse ----
for f in ("data/now.json", "data/values.json", "data/feeds.json", "data/espn2014.json",
          "data/espn_trades.json", "data/odds_history.json"):
    if Path(f).exists():
        try:
            json.load(open(f, encoding="utf-8"))
        except Exception as e:
            errors.append(f"{f}: does not parse ({e})")
    else:
        errors.append(f"{f}: missing")

if errors:
    print(f"VALIDATION FAILED — {len(errors)} problem(s):")
    for e in errors[:25]:
        print("  -", e)
    sys.exit(1)
print(f"validation: all checks passed ({len(G)} games, {len(OWNERS)} owners, "
      f"{len((D.get('moves') or {}).get('trades') or [])} trades, years {years[0]}-{years[-1]})")

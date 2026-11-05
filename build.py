"""Build the league record book: merge sheet history + Sleeper API data -> site/index.html.

Usage: python build.py data/raw
"""
import csv, json, sys, html
from pathlib import Path
from collections import defaultdict

RAW = Path(sys.argv[1] if len(sys.argv) > 1 else "data/raw")
OUT = Path("site")
OUT.mkdir(exist_ok=True)

CUR_SEASON = 2026
SLEEPER_SEASONS = [2020, 2021, 2022, 2023, 2024, 2025, 2026]

# 2026 roster -> owner (confirmed by the commissioner's message)
ROSTER_OWNER_2026 = {1: "Mark", 2: "Clancy", 3: "Will", 4: "Sam", 5: "Steve", 6: "Timmy",
                     7: "Bob", 8: "Lerch", 9: "Martin", 10: "Luke", 11: "Austin", 12: "Cliff"}
# sheet alias -> canonical owner name (verified below by score-matching)
ALIAS = {"Tim": "Timmy", "Marty": "Martin", "Auggie": "Austin", "Cosmo": "Steve"}

def canon(name):
    return ALIAS.get(name, name)

# ---------------- sheet game log (2015-2025) ----------------
games = []  # dicts: year, week, type ('Regular'|'Playoff'|'Consolation'), a, sa, b, sb
sheet_2026 = []
with open(RAW / "sheet" / "career_h2h.csv", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        try:
            year = int(r["Year"]); week = int(r["Week"])
            sa = float(r["Score Home"]); sb = float(r["Score Away"])
        except (ValueError, KeyError):
            continue
        a, b = r["Owner Home"].strip(), r["Owner Away"].strip()
        if a in ("None", "") or b in ("None", "") or (sa == 0 and sb == 0):
            continue
        if r["Season"] == "Regular":
            gtype = "Regular"
        else:
            gtype = "Playoff" if r["Matchup Type"] == "Winner's Bracket" else "Consolation"
        rec = dict(year=year, week=week, type=gtype, a=canon(a), sa=sa, b=canon(b), sb=sb)
        if year <= 2025:
            games.append(rec)
        elif year == 2026:
            sheet_2026.append(rec)

# ---------------- Sleeper data ----------------
def load(season, name):
    p = RAW / "sleeper" / str(season) / f"{name}.json"
    return json.load(open(p, encoding="utf-8")) if p.exists() else None

# 2026 games from Sleeper (live source of truth for current season)
lg26 = load(2026, "league")
playoff_start_26 = lg26["settings"]["playoff_week_start"]
wb26 = load(2026, "winners_bracket") or []
po_pairs = set()
for mt in wb26:
    if isinstance(mt.get("t1"), int) and isinstance(mt.get("t2"), int):
        po_pairs.add((playoff_start_26 + (mt.get("r", 1) - 1), frozenset((mt["t1"], mt["t2"]))))
sleeper_2026 = []
for w in range(1, 19):
    m = load(2026, f"matchups_{w}")
    if not m:
        continue
    pair = defaultdict(list)
    for x in m:
        if x.get("matchup_id") is not None:
            pair[x["matchup_id"]].append(x)
    for mid, xs in pair.items():
        if len(xs) != 2:
            continue
        x, y = xs
        if x["points"] == 0 and y["points"] == 0:
            continue
        if w < playoff_start_26:
            gtype = "Regular"
        elif (w, frozenset((x["roster_id"], y["roster_id"]))) in po_pairs:
            gtype = "Playoff"
        else:
            gtype = "Consolation"
        sleeper_2026.append(dict(year=2026, week=w, type=gtype,
                                 a=ROSTER_OWNER_2026[x["roster_id"]], sa=round(x["points"], 2),
                                 b=ROSTER_OWNER_2026[y["roster_id"]], sb=round(y["points"], 2)))

# verify alias map: every played 2026 sheet game should match a Sleeper game with same owners
def gkey(g):
    return (g["week"], frozenset([(g["a"], round(g["sa"], 1)), (g["b"], round(g["sb"], 1))]))
sl_keys = {gkey(g) for g in sleeper_2026}
checked = matched = 0
for g in sheet_2026:
    checked += 1
    if gkey(g) in sl_keys:
        matched += 1
print(f"alias verification: {matched}/{checked} played 2026 sheet games match Sleeper under alias map")
games.extend(sleeper_2026)

games.sort(key=lambda g: (g["year"], g["week"]))
OWNERS = sorted({g["a"] for g in games} | {g["b"] for g in games})
print(f"game log: {len(games)} games, {len(OWNERS)} owners, {games[0]['year']}-{games[-1]['year']}")

# ---- ESPN (2015-2019) is the official record for its era: correct sheet typos ----
ESPN_NAME = {"Samuel Harrison": "Sam", "Cliff Perez": "Cliff", "Peter Mulholland": "Neville",
             "Austin Minogue": "Austin", "mart jimpson": "Martin", "Martin Linnane PHD": "Martin",
             "Mark Sobchuk": "Mark", "Bob Baggott": "Bob", "Christian Lerch": "Lerch",
             "Tim Guckavan": "Timmy", "Mike Clancy": "Clancy", "Steven Mastro": "Steve",
             "Will Ferraro": "Will", "Billy Beane Of Fantasy Baseball": "Luke",
             "Alex Sniatkowski": "Alex"}
ESPN_POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF"}
espn_dir = RAW / "espn"
espn_owner = {}
espn_totals = {}
for yr in range(2015, 2020):
    f = espn_dir / f"{yr}.json"
    if not f.exists():
        continue
    d = json.load(open(f, encoding="utf-8"))
    members = {m["id"]: (m.get("firstName", "") + " " + m.get("lastName", "")).strip()
               for m in d.get("members", [])}
    for t in d.get("teams") or []:
        nm = members.get((t.get("owners") or [None])[0], "")
        espn_owner[(yr, t["id"])] = ESPN_NAME.get(nm, nm or "?")
    for g in d.get("schedule") or []:
        h, a = g.get("home") or {}, g.get("away") or {}
        if not h.get("totalPoints") or not a.get("totalPoints"):
            continue  # unplayed or playoff bye
        oh, oa = espn_owner.get((yr, h.get("teamId"))), espn_owner.get((yr, a.get("teamId")))
        espn_totals[(yr, g.get("matchupPeriodId"), frozenset((oh, oa)))] = {oh: h["totalPoints"], oa: a["totalPoints"]}
fixes = 0
for g in games:
    if g["year"] < 2020:
        t = espn_totals.get((g["year"], g["week"], frozenset((g["a"], g["b"]))))
        if t and (round(t[g["a"]], 2) != round(g["sa"], 2) or round(t[g["b"]], 2) != round(g["sb"], 2)):
            g["sa"], g["sb"] = t[g["a"]], t[g["b"]]
            fixes += 1
print(f"ESPN score corrections applied to sheet game log: {fixes}")

# ---------------- aggregates ----------------
def blank():
    return dict(w=0, l=0, t=0, pf=0.0, pa=0.0, pw=0, pl=0, g=0)

alltime = {o: blank() for o in OWNERS}
h2h = {o: {p: dict(w=0, l=0, t=0, pf=0.0, pa=0.0, pw=0, pl=0) for p in OWNERS if p != o} for o in OWNERS}
seasons_played = defaultdict(set)
streak_seq = defaultdict(list)  # regular-season results in order

for g in games:
    for me, you, sm, sy in ((g["a"], g["b"], g["sa"], g["sb"]), (g["b"], g["a"], g["sb"], g["sa"])):
        seasons_played[me].add(g["year"])
        if g["type"] == "Consolation":
            continue
        res = "T" if sm == sy else ("W" if sm > sy else "L")
        if g["type"] == "Regular":
            a = alltime[me]
            a["g"] += 1; a["pf"] += sm; a["pa"] += sy
            a["w" if res == "W" else "l" if res == "L" else "t"] += 1
            streak_seq[me].append(res)
            hh = h2h[me][you]
            hh["pf"] += sm; hh["pa"] += sy
            hh["w" if res == "W" else "l" if res == "L" else "t"] += 1
        elif g["type"] == "Playoff":
            alltime[me]["pw" if res == "W" else "pl"] += 1
            h2h[me][you]["pw" if res == "W" else "pl"] += 1

def longest_run(seq, ch):
    best = cur = 0
    for r in seq:
        cur = cur + 1 if r == ch else 0
        best = max(best, cur)
    return best

# ---------------- champions / shame / payouts (sheet) ----------------
def read_transposed(path):
    rows = list(csv.reader(open(path, encoding="utf-8")))
    header = rows[0]
    out = {}
    for i, y in enumerate(header):
        if not y.strip().isdigit():
            continue
        col = {}
        for r in rows[1:]:
            if r and r[0].strip():
                col[r[0].strip()] = r[i].strip() if i < len(r) else ""
        out[int(y)] = col
    return out

hof = read_transposed(RAW / "sheet" / "hall_of_fame.csv")
# full championship rosters + the Multiple Rings table (duplicate row labels need a raw parse)
hof_rows = list(csv.reader(open(RAW / "sheet" / "hall_of_fame.csv", encoding="utf-8")))
hof_years = {i: int(y) for i, y in enumerate(hof_rows[0]) if y.strip().isdigit()}
ROSTER_SLOTS = {"QB", "RB", "WR", "TE", "FLEX", "D/ST", "K", "BENCH", "BENCH/IR"}
def roster_from_transposed(rows, years_map):
    out = defaultdict(list)
    for r in rows[1:]:
        slot = (r[0] or "").strip()
        if slot in ROSTER_SLOTS:
            label = "BN" if slot.startswith("BENCH") else slot
            for i, y in years_map.items():
                if i < len(r) and r[i].strip():
                    out[y].append([label, r[i].strip()])
    return out
champ_roster = roster_from_transposed(hof_rows, hof_years)
rings = []
for r in hof_rows:
    if len(r) > 17 and r[15].strip() and r[15] != "Multiple Rings" and r[17].strip().isdigit():
        rings.append(dict(player=r[15].strip(), pos=r[16].strip(), rings=int(r[17])))
champs = []
for y in sorted(hof):
    c = hof[y]
    if c.get("Owner"):
        champs.append(dict(year=y, owner=canon(c["Owner"]), team=c.get("Team Name", ""),
                           record=c.get("Record", ""), roster=champ_roster.get(y, [])))
# current-season champion straight from the bracket, the moment the final is scored
if CUR_SEASON not in {c["year"] for c in champs}:
    final = next((mt for mt in wb26 if mt.get("p") == 1 and isinstance(mt.get("w"), int)), None)
    if final:
        champs.append(dict(year=CUR_SEASON, owner=ROSTER_OWNER_2026[final["w"]],
                           team=team.get(final["w"], ""), record="", roster=[]))
champs.sort(key=lambda c: c["year"])
titles = defaultdict(int)
for c in champs:
    titles[c["owner"]] += 1

payouts = {}
with open(RAW / "sheet" / "payouts.csv", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        o = canon((r.get("Owner") or "").strip())
        if o and o in alltime and r.get("Net"):
            payouts[o] = r["Net"]

# ---------------- records ----------------
# record lists count every game played — regular, playoff, and consolation alike
# (the league's own Best tab does: see Neville's 182.22 in the 2023 fifth-place game)
expand = []
for g in games:
    expand.append((g["sa"], g["a"], g["sb"], g["b"], g))
    expand.append((g["sb"], g["b"], g["sa"], g["a"], g))
top_games = sorted(expand, key=lambda x: -x[0])[:10]
low_games = sorted(expand, key=lambda x: x[0])[:10]
blowouts = sorted(games, key=lambda g: -abs(g["sa"] - g["sb"]))[:10]
closest = sorted([g for g in games if g["sa"] != g["sb"]], key=lambda g: abs(g["sa"] - g["sb"]))[:10]
shootouts = sorted(games, key=lambda g: -(g["sa"] + g["sb"]))[:10]
# season PF totals (regular season only, completed seasons only)
season_pf = defaultdict(lambda: dict(pf=0.0, g=0))
reg_weeks_by_year = defaultdict(set)
for g in games:
    if g["type"] == "Regular":
        reg_weeks_by_year[g["year"]].add(g["week"])
        for o, pts in ((g["a"], g["sa"]), (g["b"], g["sb"])):
            e = season_pf[(o, g["year"])]
            e["pf"] += pts; e["g"] += 1
complete_years = {y for y, wks in reg_weeks_by_year.items()
                  if y < CUR_SEASON or len(wks) >= playoff_start_26 - 1}
season_rows = [dict(owner=o, year=y, pf=round(v["pf"], 2), g=v["g"], ppg=round(v["pf"] / v["g"], 1))
               for (o, y), v in season_pf.items() if y in complete_years]
season_high = sorted(season_rows, key=lambda r: -r["pf"])[:10]
season_low = sorted(season_rows, key=lambda r: r["pf"])[:10]

win_streaks = sorted(((longest_run(streak_seq[o], "W"), o) for o in OWNERS), reverse=True)[:5]
loss_streaks = sorted(((longest_run(streak_seq[o], "L"), o) for o in OWNERS), reverse=True)[:5]
loss_high = sorted([e for e in expand if e[0] < e[2]], key=lambda x: -x[0])[:10]   # heartbreakers
win_low = sorted([e for e in expand if e[0] > e[2]], key=lambda x: x[0])[:10]      # daylight robberies

# ---------------- player records (Sleeper 2020+, starters only) ----------------
players = json.load(open(RAW / "players.json", encoding="utf-8"))
# (week, rounded score) -> owner, per season, from the canonical game log
score_owner = defaultdict(dict)
for g in games:
    score_owner[g["year"]][(g["week"], round(g["sa"], 1))] = g["a"]
    score_owner[g["year"]][(g["week"], round(g["sb"], 1))] = g["b"]

uid26 = {}
for r in load(2026, "rosters"):
    uid26[r["owner_id"]] = ROSTER_OWNER_2026[r["roster_id"]]

player_games = []
for season in SLEEPER_SEASONS:
    rost = load(season, "rosters")
    if not rost:
        continue
    users = {u["user_id"]: u for u in load(season, "users")}
    # map roster -> canonical owner: 2026 account first, else score-match against the game log
    ro = {}
    for r in rost:
        owner = uid26.get(r["owner_id"])
        if not owner:
            votes = defaultdict(int)
            for w in range(1, 19):
                m = load(season, f"matchups_{w}")
                if not m:
                    continue
                for x in m:
                    if x["roster_id"] == r["roster_id"] and x["points"]:
                        hit = score_owner[season].get((w, round(x["points"], 1)))
                        if hit:
                            votes[hit] += 1
            owner = max(votes, key=votes.get) if votes else users.get(r["owner_id"], {}).get("display_name", "?")
        ro[r["roster_id"]] = owner
    lgs = load(season, "league")
    pstart = lgs["settings"]["playoff_week_start"]
    for w in range(1, 19):
        m = load(season, f"matchups_{w}")
        if not m:
            continue
        for x in m:
            pts_map = x.get("players_points") or {}
            for pid in (x.get("starters") or []):
                pts = pts_map.get(pid)
                if pts and pts > 0:
                    player_games.append((pts, pid, ro.get(x["roster_id"], "?"), season, w))
# ---- ESPN era (2015-2019): starters' weekly points ----
for yr in range(2015, 2020):
    for w in range(1, 17):
        f = espn_dir / f"{yr}_box_{w}.json"
        if not f.exists():
            continue
        for row in json.load(open(f, encoding="utf-8")):
            for side in ("home", "away"):
                s_ = row.get(side) or {}
                o = espn_owner.get((yr, s_.get("teamId")), "?")
                for p in s_.get("players") or []:
                    if p.get("pts") and p["pts"] > 0 and p.get("name"):
                        player_games.append((p["pts"], ("ESPN", p["name"], ESPN_POS.get(p["pos"], "?")), o, yr, w))

player_games.sort(key=lambda z: -z[0])
top_players = []
for pts, pid, owner, season, w in player_games[:15]:
    if isinstance(pid, tuple):
        nm, pos = pid[1], pid[2]
    else:
        info = players.get(pid) or {}
        nm = info.get("full_name") or (info.get("last_name") or pid)
        pos = info.get("position", "?")
    top_players.append(dict(pts=round(pts, 2), name=nm, pos=pos,
                            owner=owner, year=season, week=w))

# ================= MONEY =================
from collections import Counter
byweek = defaultdict(list)  # (year, week) -> [(score, owner)]
for g in games:
    if g["type"] == "Regular":
        byweek[(g["year"], g["week"])].append((g["sa"], g["a"]))
        byweek[(g["year"], g["week"])].append((g["sb"], g["b"]))
weekly_top = defaultdict(list)
for (y, w), lst in sorted(byweek.items()):
    pts, o = max(lst)
    weekly_top[y].append(dict(week=w, owner=o, pts=round(pts, 2)))
topcounts = Counter(e["owner"] for y in weekly_top for e in weekly_top[y])

SEAT_LABELS = {"Luke/Auggie/Ski": "Luke / Austin / Alex (one seat)",
               "Neville/Auggie2.0": "Neville / Austin (one seat)"}
payout_rows = []
with open(RAW / "sheet" / "payouts.csv", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        raw_o = (r.get("Owner") or "").strip()
        o = canon(raw_o)
        label = o if o in alltime else SEAT_LABELS.get(raw_o)
        if label:
            payout_rows.append(dict(owner=label, paid=r.get("Total Paid In", ""),
                                    earned=r.get("Total Earned", ""), net=r.get("Net", "")))

money = dict(
    year=CUR_SEASON, perweek=40,
    weekly=weekly_top[CUR_SEASON],
    counts=[dict(owner=o, n=n) for o, n in topcounts.most_common()],
    payouts=payout_rows,
)

# ================= LUCK =================
luckstats = {o: dict(apw=0, apl=0, exp=0.0, lucky=0, unlucky=0) for o in OWNERS}
luck26 = {o: dict(apw=0, apl=0, exp=0.0, w=0, l=0) for o in OWNERS}
res26 = {o: dict(w=0, l=0) for o in OWNERS}
for g in games:
    if g["type"] == "Regular" and g["year"] == CUR_SEASON:
        res26[g["a"] if g["sa"] > g["sb"] else g["b"]]["w"] += 1
        res26[g["b"] if g["sa"] > g["sb"] else g["a"]]["l"] += 1
for (y, w), lst in byweek.items():
    n = len(lst)
    for pts, o in lst:
        beat = sum(1 for q, _ in lst if q < pts)
        L = luckstats[o]
        L["apw"] += beat; L["apl"] += n - 1 - beat; L["exp"] += beat / (n - 1)
        if y == CUR_SEASON:
            luck26[o]["apw"] += beat; luck26[o]["apl"] += n - 1 - beat; luck26[o]["exp"] += beat / (n - 1)
for g in games:
    if g["type"] != "Regular":
        continue
    lst = byweek[(g["year"], g["week"])]
    n = len(lst)
    for me, you, sm, sy in ((g["a"], g["b"], g["sa"], g["sb"]), (g["b"], g["a"], g["sb"], g["sa"])):
        rank = sum(1 for q, _ in lst if q > sm)  # 0 = top score
        if sm > sy and rank >= n // 2:
            luckstats[me]["lucky"] += 1
        if sm < sy and rank < n // 2:
            luckstats[me]["unlucky"] += 1
luck = dict(
    career=[dict(owner=o, apw=luckstats[o]["apw"], apl=luckstats[o]["apl"],
                 luck=round(alltime[o]["w"] - luckstats[o]["exp"], 1),
                 lucky=luckstats[o]["lucky"], unlucky=luckstats[o]["unlucky"]) for o in OWNERS],
    season=[dict(owner=o, w=res26[o]["w"], l=res26[o]["l"],
                 apw=luck26[o]["apw"], apl=luck26[o]["apl"],
                 luck=round(res26[o]["w"] - luck26[o]["exp"], 1))
            for o in OWNERS if res26[o]["w"] + res26[o]["l"] > 0],
)

# ================= per-season roster->owner maps + player weekly points =================
season_ro = {}
pwk = {}  # season -> pid -> {week: pts}
for season in SLEEPER_SEASONS:
    rost = load(season, "rosters")
    if not rost:
        continue
    users = {u["user_id"]: u for u in load(season, "users")}
    ro = {}
    for r in rost:
        owner = uid26.get(r["owner_id"])
        if not owner:
            votes = defaultdict(int)
            for w in range(1, 19):
                m = load(season, f"matchups_{w}")
                if not m:
                    continue
                for x in m:
                    if x["roster_id"] == r["roster_id"] and x["points"]:
                        hit = score_owner[season].get((w, round(x["points"], 1)))
                        if hit:
                            votes[hit] += 1
            owner = max(votes, key=votes.get) if votes else users.get(r["owner_id"], {}).get("display_name", "?")
        ro[r["roster_id"]] = owner
    season_ro[season] = ro
    pw = defaultdict(dict)
    for w in range(1, 19):
        m = load(season, f"matchups_{w}")
        if not m:
            continue
        for x in m:
            for pid, pts in (x.get("players_points") or {}).items():
                pw[pid][w] = (pts, x["roster_id"])   # points + whose roster he was on
    pwk[season] = pw

# ================= BENCH WASTE (2020+) =================
FLEXPOS = {"RB", "WR", "TE"}
def optimal_points(pp, slots):
    cand = sorted(((pts, pid) for pid, pts in pp.items()), reverse=True)
    pos_of = lambda pid: (players.get(pid) or {}).get("position")
    used, total = set(), 0.0
    for slot in slots:
        best = None
        for pts, pid in cand:
            if pid in used:
                continue
            p = pos_of(pid)
            if (slot == "FLEX" and p in FLEXPOS) or p == slot:
                best = (pts, pid); break
        if best:
            total += best[0]; used.add(best[1])
    return total

bench_career = defaultdict(lambda: dict(left=0.0, act=0.0, opt=0.0, g=0))
bench_26 = defaultdict(lambda: dict(left=0.0, act=0.0, opt=0.0, g=0))
for season in SLEEPER_SEASONS:
    lgs = load(season, "league")
    slots = [s for s in lgs["roster_positions"] if s != "BN"]
    pstart = lgs["settings"]["playoff_week_start"]
    ro = season_ro[season]
    for w in range(1, pstart):
        m = load(season, f"matchups_{w}")
        if not m:
            continue
        for x in m:
            pp = x.get("players_points") or {}
            if not pp or not x.get("points"):
                continue
            act = x["points"]
            opt = optimal_points(pp, slots)
            left = max(0.0, opt - act)
            o = ro.get(x["roster_id"], "?")
            for agg in ([bench_career[o]] + ([bench_26[o]] if season == CUR_SEASON else [])):
                agg["left"] += left; agg["act"] += act; agg["opt"] += opt; agg["g"] += 1
bench = dict(
    career=[dict(owner=o, left=round(v["left"], 1), pg=round(v["left"] / v["g"], 2),
                 eff=round(100 * v["act"] / v["opt"], 1)) for o, v in bench_career.items() if v["g"]],
    season=[dict(owner=o, left=round(v["left"], 1), pg=round(v["left"] / v["g"], 2),
                 eff=round(100 * v["act"] / v["opt"], 1)) for o, v in bench_26.items() if v["g"]],
)

# ================= TRADES (sheet list; 2020+ scored via Sleeper points) =================
import re as _re
name_idx = {}
for pid, info in players.items():
    if not info:
        continue
    nm = (info.get("search_full_name") or "").lower()
    if nm:
        rank = info.get("search_rank") or 10 ** 9
        if nm not in name_idx or rank < name_idx[nm][1]:
            name_idx[nm] = (pid, rank)
def find_pid(name):
    key = _re.sub(r"[^a-z]", "", name.lower())
    return name_idx.get(key, (None,))[0]

trade_list = []
with open(RAW / "sheet" / "trades.csv", encoding="utf-8") as f:
    rows = list(csv.reader(f))
hdr = rows[0]
for r in rows[1:]:
    row = dict(zip(hdr, r))
    if not row.get("Year", "").strip():
        continue
    year = int(row["Year"]); week = int(row["Week"]) if row.get("Week", "").strip().isdigit() else 0
    towners = [canon(row.get(f"Owner {i}", "").strip()) for i in (1, 2, 3)]
    towners = [o for o in towners if o]
    gave = {}
    for i, o in enumerate(towners, 1):
        raw_g = row.get(f"From Owner {i}", "") or ""
        gave[o] = [p.strip() for p in raw_g.split(";") if p.strip()]
    sides = []
    for o in towners:
        got = [p for oo in towners if oo != o for p in gave[oo]]
        pts = None
        if year in pwk and len(towners) == 2:
            pts = 0.0
            for pname in got:
                pid = find_pid(pname)
                if pid:
                    pts += sum(v for wk2, (v, _) in pwk[year].get(pid, {}).items() if wk2 > week)
            pts = round(pts, 1)
        sides.append(dict(owner=o, gave=gave[o], got=got, pts=pts))
    trade_list.append(dict(year=year, week=week, sides=sides))
trade_list.sort(key=lambda t: (-t["year"], -t["week"]))

# Sleeper transactions are the authoritative trade record for 2020+ (the sheet runs behind);
# keep the sheet only for the pre-Sleeper years it alone remembers.
sleeper_trades = []
for season in SLEEPER_SEASONS:
    ro = season_ro.get(season)
    if not ro:
        continue
    for w in range(1, 19):
        tx = load(season, f"transactions_{w}")
        if not tx:
            continue
        for t in tx:
            if t.get("type") != "trade" or t.get("status") != "complete":
                continue
            adds = t.get("adds") or {}
            week = t.get("leg") or w
            sides = []
            for r in t.get("roster_ids") or []:
                got = []
                pts = 0.0
                for pid, rr in adds.items():
                    if rr == r:
                        info = players.get(pid) or {}
                        got.append(info.get("full_name") or info.get("last_name") or pid)
                        # only weeks the player was actually on the acquiring roster
                        pts += sum(v for wk2, (v, rid2) in pwk[season].get(pid, {}).items()
                                   if wk2 > week and rid2 == r)
                for dp in t.get("draft_picks") or []:
                    if dp.get("owner_id") == r:
                        got.append(f"{dp.get('season')} round-{dp.get('round')} pick")
                for b in t.get("waiver_budget") or []:
                    if b.get("receiver") == r:
                        got.append(f"${b.get('amount')} FAAB")
                sides.append(dict(owner=ro.get(r, "?"), gave=[], got=got, pts=round(pts, 1)))
            sleeper_trades.append(dict(year=season, week=week, sides=sides))
trade_list = [t for t in trade_list if t["year"] < 2020] + sleeper_trades
trade_list.sort(key=lambda t: (-t["year"], -t["week"]))
print(f"trades: {len(trade_list)} total ({len(sleeper_trades)} from Sleeper transactions)")

# ================= WAIVER STEALS (2020+) =================
steals = []
seen_adds = set()
for season in SLEEPER_SEASONS:
    if season not in season_ro:
        continue
    ro = season_ro[season]
    for w in range(1, 19):
        tx = load(season, f"transactions_{w}")
        if not tx:
            continue
        for t in tx:
            if t.get("type") not in ("waiver", "free_agent") or t.get("status") != "complete":
                continue
            for pid, rid in (t.get("adds") or {}).items():
                key = (season, pid, rid)
                if key in seen_adds:
                    continue
                seen_adds.add(key)
                pts = sum(v for wk2, (v, rid2) in pwk[season].get(pid, {}).items()
                          if wk2 >= w and rid2 == rid)
                if pts > 50:
                    info = players.get(pid) or {}
                    bid = (t.get("settings") or {}).get("waiver_bid")
                    steals.append(dict(owner=ro.get(rid, "?"), player=info.get("full_name", pid),
                                       pos=info.get("position", "?"), year=season, week=w,
                                       pts=round(pts, 1), bid=bid))
steals.sort(key=lambda s: -s["pts"])

# ---- ESPN-era forensic reconstruction: who played for a team that didn't draft them ----
espn_pickups, espn_swaps = [], []
for yr in range(2015, 2020):
    df = espn_dir / f"{yr}_draft.json"
    pf_ = espn_dir / f"{yr}_players.json"
    if not (df.exists() and pf_.exists()):
        continue
    pmap = json.load(open(pf_, encoding="utf-8"))
    drafted_by = {}
    for p in json.load(open(df, encoding="utf-8")).get("picks") or []:
        info = pmap.get(str(p.get("playerId"))) or {}
        if info.get("n"):
            drafted_by[info["n"]] = espn_owner.get((yr, p.get("teamId")), "?")
    starts = defaultdict(list)  # (name, pos, owner) -> [(week, pts)]
    for w in range(1, 17):
        f = espn_dir / f"{yr}_box_{w}.json"
        if not f.exists():
            continue
        for row in json.load(open(f, encoding="utf-8")):
            for side in ("home", "away"):
                s_ = row.get(side) or {}
                o = espn_owner.get((yr, s_.get("teamId")))
                for p in s_.get("players") or []:
                    if p.get("name") and o:
                        starts[(p["name"], ESPN_POS.get(p.get("pos"), "?"), o)].append((w, p.get("pts") or 0))
    for (nm, pos, o), wk in starts.items():
        src = drafted_by.get(nm)
        if src == o:
            continue
        first = min(w for w, _ in wk)
        pts = round(sum(x for _, x in wk), 1)
        entry = dict(year=yr, player=nm, pos=pos, owner=o, week=first, pts=pts,
                     games=len(wk), src=src or "")
        (espn_swaps if src else espn_pickups).append(entry)
# classify swaps: sheet-confirmed trade > reciprocal-swap heuristic > unknown
def normname(s):
    return _re.sub(r"[^a-z]", "", s.lower())
sheet_trade_gets = set()
for t in trade_list:
    for side in t["sides"]:
        for pname in side["got"]:
            sheet_trade_gets.add((t["year"], side["owner"], normname(pname)))
for e in espn_swaps:
    if (e["year"], e["owner"], normname(e["player"])) in sheet_trade_gets:
        e["how"] = "trade"
    elif any(o["year"] == e["year"] and o["src"] == e["owner"] and o["owner"] == e["src"]
             and abs(o["week"] - e["week"]) <= 2 for o in espn_swaps if o is not e):
        e["how"] = "likely trade"
    else:
        e["how"] = "trade or waiver"
espn_pickups.sort(key=lambda e: -e["pts"])
espn_swaps.sort(key=lambda e: -e["pts"])
moves = dict(trades=trade_list, waivers=steals[:15],
             espn_pickups=espn_pickups[:15], espn_swaps=espn_swaps[:15])

# ================= DRAFT REPORT CARDS (2020+) =================
draft_data = []
for season in SLEEPER_SEASONS:
    drafts = load(season, "drafts") or []
    best_picks = []
    for dr in drafts:
        pf = RAW / "sleeper" / str(season) / f"draftpicks_{dr['draft_id']}.json"
        if pf.exists():
            picks = json.load(open(pf, encoding="utf-8"))
            if len(picks) > len(best_picks):
                best_picks = picks
    ro = season_ro.get(season, {})
    for p in best_picks:
        pid = p.get("player_id")
        rid = p.get("roster_id")
        if not pid or rid not in ro:
            continue
        total = sum(v for v, _ in pwk[season].get(pid, {}).values())
        info = players.get(pid) or {}
        entry = dict(season=season, pick=p.get("pick_no"), owner=ro[rid],
                     player=info.get("full_name", pid), pos=info.get("position", "?"),
                     pts=round(total, 1))
        if p.get("is_keeper"):
            entry["keeper"] = True
        draft_data.append(entry)
# ESPN-era picks (2015-2019) with TRUE season totals under that year's league scoring
for yr in range(2015, 2020):
    df = espn_dir / f"{yr}_draft.json"
    pf_ = espn_dir / f"{yr}_players.json"
    tf = espn_dir / f"{yr}_season_totals.json"
    if not (df.exists() and pf_.exists()):
        continue
    pmap = json.load(open(pf_, encoding="utf-8"))
    totals = json.load(open(tf, encoding="utf-8")) if tf.exists() else {}
    for p in json.load(open(df, encoding="utf-8")).get("picks") or []:
        pid = str(p.get("playerId"))
        info = pmap.get(pid) or {}
        draft_data.append(dict(season=yr, pick=p.get("overallPickNumber"),
                               owner=espn_owner.get((yr, p.get("teamId")), "?"),
                               player=info.get("n") or f"player {pid}",
                               pos=ESPN_POS.get(info.get("p"), "?"),
                               pts=totals.get(pid, 0.0)))

# expected points: same season, same position, same round neighborhood
# (within-season comparison keeps a half-played season fair; within-position stops
#  late-round QBs from beating all-position slot averages on raw points)
N_TEAMS = 12
bygroup = defaultdict(list)  # (season, pos, round) -> [pts]
for d in draft_data:
    d["round"] = (d["pick"] - 1) // N_TEAMS + 1
    bygroup[(d["season"], d["pos"], d["round"])].append(d["pts"])
def expected(d):
    for spread in (1, 2, 4, 15):
        vals = [v for rd in range(d["round"] - spread, d["round"] + spread + 1)
                for v in bygroup.get((d["season"], d["pos"], rd), [])]
        if len(vals) >= 3:
            return sum(vals) / len(vals)
    return 0.0
for d in draft_data:
    d["value"] = round(d["pts"] - expected(d), 1)
grades = defaultdict(lambda: defaultdict(float))
for d in draft_data:
    grades[d["owner"]][d["season"]] += d["value"]
# keeper flags live only in the sheet's draft tab
keeper_set = set()
with open(RAW / "sheet" / "draft.csv", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if (r.get("Keeper?") or "").strip().lower() == "yes" and (r.get("Season") or "").strip().isdigit():
            keeper_set.add((int(r["Season"]), normname(r.get("Player") or "")))
for d in draft_data:
    nm = normname(d["player"])
    if any(s == d["season"] and (k == nm or k.startswith(nm) or nm.startswith(k))
           for s, k in keeper_set):
        d["keeper"] = True

all_picks = sorted([dict(season=d["season"], pick=d["pick"], owner=d["owner"],
                         player=d["player"], pos=d["pos"], pts=d["pts"], value=d["value"],
                         **({"keeper": True} if d.get("keeper") else {}))
                    for d in draft_data], key=lambda d: (d["season"], d["pick"]))

draft_payload = dict(
    grades=[dict(owner=o, years={str(y): round(v, 0) for y, v in ys.items()},
                 total=round(sum(ys.values()), 0)) for o, ys in grades.items()],
    steals=sorted(draft_data, key=lambda d: -d["value"])[:10],
    busts=sorted([d for d in draft_data if d["pick"] <= 60], key=lambda d: d["value"])[:10],
    all=all_picks,
)
draft_payload["grades"].sort(key=lambda g: -g["total"])

# ================= HALL OF SHAME =================
shame_raw = read_transposed(RAW / "sheet" / "hall_of_shame.csv")
shame_rows = list(csv.reader(open(RAW / "sheet" / "hall_of_shame.csv", encoding="utf-8")))
shame_years = {i: int(y) for i, y in enumerate(shame_rows[0]) if y.strip().isdigit()}
shame_roster = roster_from_transposed(shame_rows, shame_years)
shame = []
for y in sorted(shame_raw, reverse=True):
    c = shame_raw[y]
    if c.get("Owner"):
        shame.append(dict(year=y, owner=canon(c["Owner"]),
                          team=c.get("Team Name", ""), record=c.get("Record", ""),
                          roster=shame_roster.get(y, [])))
# current-season Lerch Cup from the losers bracket, once its last-place game is scored
if CUR_SEASON not in {s["year"] for s in shame}:
    lb26 = load(2026, "losers_bracket") or []
    placed = [mt for mt in lb26 if isinstance(mt.get("l"), int) and mt.get("p")]
    if placed:
        bottom = max(placed, key=lambda mt: mt["p"])
        shame.insert(0, dict(year=CUR_SEASON, owner=ROSTER_OWNER_2026[bottom["l"]],
                             team=team.get(bottom["l"], ""), record="", roster=[]))

# worst-game notes from the sheet
worst_notes = {}
with open(RAW / "sheet" / "worst.csv", encoding="utf-8") as f:
    for r in csv.reader(f):
        if len(r) > 6 and r[3].strip().isdigit():
            worst_notes[(int(r[3]), int(r[4]), canon(r[2].strip()))] = r[6].strip()

# ================= STARTING LINEUPS for every game =================
lineups = {}  # "year|week|owner" -> [[name, pos, pts], ...]
for yr in range(2015, 2020):
    for w in range(1, 17):
        f = espn_dir / f"{yr}_box_{w}.json"
        if not f.exists():
            continue
        for row in json.load(open(f, encoding="utf-8")):
            for side in ("home", "away"):
                s_ = row.get(side) or {}
                o = espn_owner.get((yr, s_.get("teamId")))
                pls = s_.get("players") or []
                if o and pls:
                    lineups[f"{yr}|{row.get('matchupPeriodId')}|{o}"] = [
                        [p.get("name") or "?", ESPN_POS.get(p.get("pos"), "?"), round(p.get("pts") or 0, 1)]
                        for p in pls]
for season in SLEEPER_SEASONS:
    ro = season_ro.get(season)
    if not ro:
        continue
    for w in range(1, 19):
        m = load(season, f"matchups_{w}")
        if not m:
            continue
        for x in m:
            starters = x.get("starters") or []
            pp = x.get("players_points") or {}
            if not starters or not x.get("points"):
                continue
            o = ro.get(x["roster_id"])
            lu = []
            for pid in starters:
                info = players.get(pid) or {}
                lu.append([info.get("full_name") or info.get("last_name") or pid,
                           info.get("position", "?"), round(pp.get(pid, 0) or 0, 1)])
            lineups[f"{season}|{w}|{o}"] = lu

# ---------------- 2026 "now" data ----------------
now = json.load(open("data/now.json", encoding="utf-8")) if Path("data/now.json").exists() else {}
if Path("data/title_odds.json").exists():
    now["title"] = json.load(open("data/title_odds.json", encoding="utf-8"))

# ---------------- payload ----------------
payload = dict(
    updated="through week 8, 2026",
    owners=OWNERS,
    active=list(ROSTER_OWNER_2026.values()),
    games=[[g["year"], g["week"], g["type"][0], g["a"], g["sa"], g["b"], g["sb"]] for g in games],
    alltime={o: dict(alltime[o],
                     seasons=len(seasons_played[o]),
                     titles=titles.get(o, 0),
                     net=payouts.get(o, ""),
                     bestw=longest_run(streak_seq[o], "W"),
                     bestl=longest_run(streak_seq[o], "L")) for o in OWNERS},
    champs=champs[::-1],
    records=dict(
        top=[dict(pts=e[0], owner=e[1], opp=e[3], opp_pts=e[2], year=e[4]["year"], week=e[4]["week"]) for e in top_games],
        low=[dict(pts=e[0], owner=e[1], opp=e[3], opp_pts=e[2], year=e[4]["year"], week=e[4]["week"],
                  note=worst_notes.get((e[4]["year"], e[4]["week"], e[1]), "")) for e in low_games],
        blowouts=[dict(margin=round(abs(g["sa"] - g["sb"]), 2), **g) for g in blowouts],
        closest=[dict(margin=round(abs(g["sa"] - g["sb"]), 2), **g) for g in closest],
        shootouts=[dict(total=round(g["sa"] + g["sb"], 2), **g) for g in shootouts],
        winstreaks=[dict(n=n, owner=o) for n, o in win_streaks],
        lossstreaks=[dict(n=n, owner=o) for n, o in loss_streaks],
        season_high=season_high,
        season_low=season_low,
        loss_high=[dict(pts=e[0], owner=e[1], opp=e[3], opp_pts=e[2], year=e[4]["year"], week=e[4]["week"]) for e in loss_high],
        win_low=[dict(pts=e[0], owner=e[1], opp=e[3], opp_pts=e[2], year=e[4]["year"], week=e[4]["week"]) for e in win_low],
        players=top_players,
    ),
    rings=rings,
    money=money,
    luck=luck,
    bench=bench,
    moves=moves,
    draft=draft_payload,
    shame=shame,
    lineups=lineups,
    now=now,
)

tpl = open("template.html", encoding="utf-8").read()
out_html = tpl.replace("/*__DATA__*/", "const DATA = " + json.dumps(payload) + ";")
open(OUT / "index.html", "w", encoding="utf-8").write(out_html)
print(f"wrote site/index.html ({len(out_html)//1024} KB)")

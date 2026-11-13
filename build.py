"""Build the league record book: merge sheet history + Sleeper API data -> site/index.html.

Usage: python build.py data/raw
"""
import csv, itertools, json, sys, html
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict, deque

BUILT_ET = datetime.now(timezone.utc) - timedelta(hours=5)   # ET date of this build

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

# 2014: the full 8-team season, recovered from the ESPN archive (hidden from
# public view since the platform migration; a 2014 member's credentials still
# unlock it, cached in data/raw/espn/2014.json). Playoff rounds were two NFL
# weeks each, so bracket entries carry agg=2 and stay out of single-game
# records. No player-level data survives, so the adjusted-era lists and the
# (12-team) clinch replay still start at 2015.
E14 = {}
_p14 = Path("data/espn2014.json")
if _p14.exists():
    E14 = json.load(open(_p14, encoding="utf-8"))

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

# 2026 games from Sleeper (live source of truth for current season).
# Only fully-scored weeks count: mid-week partial scores (some players played,
# some not) must never enter the record book.
lg26 = load(2026, "league")
playoff_start_26 = lg26["settings"]["playoff_week_start"]
last_scored_26 = lg26["settings"].get("last_scored_leg") or 0
wb26 = load(2026, "winners_bracket") or []
po_pairs = set()
for mt in wb26:
    if isinstance(mt.get("t1"), int) and isinstance(mt.get("t2"), int):
        po_pairs.add((playoff_start_26 + (mt.get("r", 1) - 1), frozenset((mt["t1"], mt["t2"]))))
sleeper_2026 = []
for w in range(1, min(18, last_scored_26) + 1):
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
games.extend(E14.get("games") or [])

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
for yr in range(2014, 2020):
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

def two_tiers(rows, key):
    """Keep only the rows holding the top two distinct values of key."""
    vals = sorted({key(r) for r in rows}, reverse=True)[:2]
    return [r for r in rows if key(r) in vals]

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

# modern franchise names for sheet-sourced rosters and rings
TEAM_RENAMES = {"Redskins D/ST": "Commanders D/ST", "Redskins": "Commanders",
                "Washington D/ST": "Commanders D/ST", "Washington Football Team": "Commanders",
                "Football Team D/ST": "Commanders D/ST"}
for c in champs:
    c["roster"] = [[sl, TEAM_RENAMES.get(nm, nm)] for sl, nm in (c.get("roster") or [])]
for ring in rings:
    ring["player"] = TEAM_RENAMES.get(ring["player"], ring["player"])

# which title teams was each multiple-ring player on? (name-match vs champ rosters)
def _ring_key(s):
    return "".join(ch for ch in s.lower() if ch.isalpha())
for ring in rings:
    k = _ring_key(ring["player"])
    ring["detail"] = [[c["year"], c["owner"], c.get("team", "")]
                      for c in champs
                      if any(_ring_key(nm) == k for _, nm in (c.get("roster") or []))]

payouts = {}   # owner -> net $ string; filled from the per-person split in the MONEY section

# ---------------- records ----------------
# record lists count every game played — regular, playoff, and consolation alike
# (the league's own Best tab does: see Neville's 182.22 in the 2023 fifth-place game).
# EXCEPT lowest-score records, which skip consolation: those rosters often
# weren't even set, so the lows would just be forfeits.
expand = []
for g in games:
    if g.get("agg"):   # two-week playoff aggregates aren't single-game scores
        continue
    expand.append((g["sa"], g["a"], g["sb"], g["b"], g))
    expand.append((g["sb"], g["b"], g["sa"], g["a"], g))
no_consol = [e for e in expand if e[4]["type"] != "Consolation"]
top_games = sorted(expand, key=lambda x: -x[0])[:10]
low_games = sorted(no_consol, key=lambda x: x[0])[:10]
single = [g for g in games if not g.get("agg")]
blowouts = sorted(single, key=lambda g: -abs(g["sa"] - g["sb"]))[:10]
closest = sorted([g for g in single if g["sa"] != g["sb"]], key=lambda g: abs(g["sa"] - g["sb"]))[:10]
shootouts = sorted(single, key=lambda g: -(g["sa"] + g["sb"]))[:10]
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

win_streaks = two_tiers(sorted(((longest_run(streak_seq[o], "W"), o) for o in OWNERS), reverse=True), key=lambda p: p[0])
loss_streaks = two_tiers(sorted(((longest_run(streak_seq[o], "L"), o) for o in OWNERS), reverse=True), key=lambda p: p[0])
loss_high = sorted([e for e in expand if e[0] < e[2]], key=lambda x: -x[0])[:10]   # heartbreakers
win_low = sorted([e for e in no_consol if e[0] > e[2]], key=lambda x: x[0])[:10]   # daylight robberies

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
bench_games = []   # best games left on the bench (Sleeper era only: ESPN kept no bench scores)
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
    slots_b = [s for s in (lgs.get("roster_positions") or []) if s not in ("BN", "IR")]
    for w in range(1, 19):
        if season == CUR_SEASON and w > last_scored_26:
            break   # mid-week partials never enter the record book
        m = load(season, f"matchups_{w}")
        if not m:
            continue
        opp_pts = {}
        pair_b = defaultdict(list)
        for x in m:
            if x.get("matchup_id") is not None:
                pair_b[x["matchup_id"]].append(x)
        for xs in pair_b.values():
            if len(xs) == 2:
                opp_pts[xs[0]["roster_id"]] = xs[1]["points"]
                opp_pts[xs[1]["roster_id"]] = xs[0]["points"]
        for x in m:
            pts_map = x.get("players_points") or {}
            st = x.get("starters") or []
            for pid in st:
                pts = pts_map.get(pid)
                if pts and pts > 0:
                    player_games.append((pts, pid, ro.get(x["roster_id"], "?"), season, w))
            # bench heartbreakers: what if the big bench game had started?
            op = opp_pts.get(x["roster_id"])
            if op is None or (x["points"] == 0 and op == 0):
                continue
            stset = set(st)
            for pid in (x.get("players") or []):
                if pid in stset:
                    continue
                bpts = pts_map.get(pid) or 0
                if bpts < 20:
                    continue
                pos = (players.get(pid) or {}).get("position")
                # cheapest legal swap: his position's slots, plus FLEX if eligible
                swap = [pts_map.get(st[i]) or 0 for i, sl in enumerate(slots_b) if i < len(st)
                        and (sl == pos or (sl == "FLEX" and pos in ("RB", "WR", "TE")))]
                if not swap:
                    continue
                gain = bpts - min(swap)
                verdict = ("won" if x["points"] > op else
                           "flip" if gain > 0 and x["points"] + gain > op else "short")
                info = players.get(pid) or {}
                bench_games.append(dict(
                    pts=round(bpts, 2), name=info.get("full_name") or info.get("last_name") or pid,
                    pos=pos or "?", owner=ro.get(x["roster_id"], "?"), year=season, week=w,
                    v=verdict, gain=round(gain, 2), margin=round(op - x["points"], 2)))
# ---- ESPN era (2014-2019): starters' weekly points ----
for yr in range(2014, 2020):
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
bench_games = sorted(bench_games, key=lambda b: -b["pts"])[:10]

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

# Shared-seat money is split to the person who held the seat each year.
# (2014 predates the game log; seat A's 2014 is attributed to Austin, its 2015 holder.)
SEAT_YEARS = {
    "Luke/Auggie/Ski": {2014: "Austin", 2015: "Austin", 2016: "Alex",
                        **{y: "Luke" for y in range(2017, 2027)}},
    "Neville/Auggie2.0": {**{y: "Neville" for y in range(2014, 2024)},
                          **{y: "Austin" for y in range(2024, 2027)}},
}
def money_f(v):
    v = (v or "").replace("$", "").replace(",", "").strip()
    try:
        return float(v)
    except ValueError:
        return 0.0
def money_s(v):
    return (f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}")

pay_rows_raw = list(csv.reader(open(RAW / "sheet" / "payouts.csv", encoding="utf-8")))
pay_hdr = pay_rows_raw[0]
year_cols = {}
for i, h in enumerate(pay_hdr):
    h = h.strip()
    if h[:4].isdigit():
        year_cols[i] = (int(h[:4]), "out" if "Out" in h else "in")
person_money = defaultdict(lambda: {"in": 0.0, "out": 0.0,
                                    "champ": 0.0, "second": 0.0, "weekly": 0.0, "other": 0.0})
# per (person, year): paid out, weekly-win count, finish code (1=champ, 2=runner-up, 3=neither);
# each year block in the sheet is [Y Paid Out][Y Paid In][Weekly Wins][Win?]
pay_cells = {}
for r in pay_rows_raw[1:]:
    raw_o = (r[0] or "").strip()
    for i, (yr, kind) in year_cols.items():
        if i >= len(r):
            continue
        p = SEAT_YEARS[raw_o].get(yr) if raw_o in SEAT_YEARS else canon(raw_o)
        if not p or (raw_o not in SEAT_YEARS and p not in alltime):
            continue
        person_money[p][kind] += money_f(r[i])
        if kind == "out":
            wins = code = 0
            if i + 3 < len(pay_hdr) and pay_hdr[i + 2].strip() == "Weekly Wins":
                try:
                    wins = int(r[i + 2] or 0); code = int(r[i + 3] or 3)
                except (ValueError, IndexError):
                    wins, code = 0, 3
            pay_cells[(p, yr)] = dict(out=money_f(r[i]), wins=wins, code=code)
# the weekly rate changed over the years: infer it per year from people who
# earned weekly money but no finish money (out / wins, mode across owners)
rate_votes = defaultdict(Counter)
for (p, yr), d in pay_cells.items():
    if d["code"] == 3 and d["wins"] > 0 and d["out"] > 0:
        rv = d["out"] / d["wins"]
        if abs(rv - round(rv)) < 0.01:
            rate_votes[yr][round(rv)] += 1
wk_rate = {yr: v.most_common(1)[0][0] for yr, v in rate_votes.items()}
champ2014 = next((c["owner"] for c in champs if c["year"] == 2014), None)
for (p, yr), d in pay_cells.items():
    out = d["out"]
    if yr == 2014:   # no weekly/finish columns in the ledger; finishes per the
        # commissioner: Mark beat Cliff in the 2014 final
        person_money[p]["champ" if p == champ2014 else
                        ("second" if p == "Cliff" else "other")] += out
        continue
    wk = min(out, wk_rate.get(yr, 20) * d["wins"])
    if d["code"] in (1, 2):
        person_money[p]["weekly"] += wk
        person_money[p]["champ" if d["code"] == 1 else "second"] += max(0.0, out - wk)
    else:
        person_money[p]["weekly"] += out
print("weekly payout rates by year:", {y: wk_rate[y] for y in sorted(wk_rate)})
for o, v in person_money.items():
    drift = v["out"] - (v["champ"] + v["second"] + v["weekly"] + v["other"])
    if abs(drift) > 0.01:
        print(f"  !! payout breakdown drift for {o}: {drift:+.2f}")
payout_rows = [dict(owner=o, paid=money_s(v["in"]), champ=money_s(v["champ"]),
                    second=money_s(v["second"]), weekly=money_s(v["weekly"]),
                    other=money_s(v["other"]), earned=money_s(v["out"]),
                    net=money_s(v["out"] - v["in"]))
               for o, v in sorted(person_money.items(), key=lambda kv: kv[1]["in"] - kv[1]["out"])]
payouts.update({o: money_s(v["out"] - v["in"]) for o, v in person_money.items()})
print(f"payouts split: Austin {payouts.get('Austin')}, Alex {payouts.get('Alex')}, "
      f"Luke {payouts.get('Luke')}, Neville {payouts.get('Neville')}")

highs_detail = defaultdict(list)
for y in sorted(weekly_top):
    for e in weekly_top[y]:
        highs_detail[e["owner"]].append([y, e["week"], e["pts"]])
money = dict(
    year=CUR_SEASON, perweek=40,
    weekly=weekly_top[CUR_SEASON],
    counts=[dict(owner=o, n=n) for o, n in topcounts.most_common()],
    detail=highs_detail,
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
    season_maxw = min(18, last_scored_26) if season == CUR_SEASON else 18
    for w in range(1, season_maxw + 1):
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
    bench_maxw = min(pstart - 1, last_scored_26) if season == CUR_SEASON else pstart - 1
    for w in range(1, bench_maxw + 1):
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
# ESPN-era trades recovered from the league's notification e-mails (the API has
# no pre-2020 transactions). The sheet keeps precedence where it recorded one.
_etf = Path("data/espn_trades.json")
if _etf.exists():
    _nn = lambda s: _re.sub(r"[^a-z]", "", _re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s.lower()))
    _known = {(t["year"], frozenset(_nn(p) for s in t["sides"] for p in s["gave"]))
              for t in trade_list}
    _mailed = 0
    for t in json.load(open(_etf, encoding="utf-8")):
        k = (t["year"], frozenset(_nn(p) for s in t["sides"] for p in s["gave"]))
        if k not in _known:
            _known.add(k)
            trade_list.append(t)
            _mailed += 1
    print(f"trades: {_mailed} ESPN-era trades added from the e-mail archive")
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
                        # only weeks the player was actually on the acquiring roster;
                        # the trade week itself counts when his points landed there
                        # (a pre-kickoff trade means he plays that week for the buyer)
                        pts += sum(v for wk2, (v, rid2) in pwk[season].get(pid, {}).items()
                                   if wk2 >= week and rid2 == r)
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
for yr in range(2014, 2020):
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

def by_position(items, per_pos=5):
    out = {}
    for it in items:   # already sorted by points desc
        out.setdefault(it["pos"], [])
        if len(out[it["pos"]]) < per_pos:
            out[it["pos"]].append(it)
    return {p: out[p] for p in ("QB", "RB", "WR", "TE", "K", "DEF") if out.get(p)}

moves = dict(trades=trade_list,
             waivers=by_position(steals),
             espn_pickups=by_position(espn_pickups, 4),
             espn_swaps=espn_swaps[:15])

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
# 2014 draft from the e-mail archive; season totals re-scored from nflverse
# weekly stats at ESPN-standard 2014 scoring (8-team league -> its own rounds)
_t14f = espn_dir / "2014_totals.json"
if E14.get("draft") and _t14f.exists():
    _t14 = json.load(open(_t14f, encoding="utf-8"))
    _n14 = lambda s: _re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", _re.sub(r"[^a-z ]", "", s.lower())).replace(" ", "")
    for p in E14["draft"]:
        draft_data.append(dict(season=2014, pick=p["pick"], owner=p["owner"],
                               player=p["player"], pos=p["pos"],
                               pts=_t14.get(_n14(p["player"]), 0.0),
                               round=(p["pick"] - 1) // 8 + 1))

# expected points: same season, same position, same round neighborhood
# (within-season comparison keeps a half-played season fair; within-position stops
#  late-round QBs from beating all-position slot averages on raw points)
N_TEAMS = 12
bygroup = defaultdict(list)  # (season, pos, round) -> [pts]
for d in draft_data:
    d["round"] = d.get("round") or (d["pick"] - 1) // N_TEAMS + 1
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
# current-season Lerch Cup from the losers bracket, once its last-place game is scored.
# The consolation is a toilet bowl: losers advance, so the "winner" (advancer) of the
# p=1 match IS last place (verified against the 2025 bracket -> Timmy, matching the sheet).
if CUR_SEASON not in {s["year"] for s in shame}:
    lb26 = load(2026, "losers_bracket") or []
    final_l = next((mt for mt in lb26 if mt.get("p") == 1 and isinstance(mt.get("w"), int)), None)
    if final_l:
        shame.insert(0, dict(year=CUR_SEASON, owner=ROSTER_OWNER_2026[final_l["w"]],
                             team=team.get(final_l["w"], ""), record="", roster=[]))

# worst-game notes from the sheet
worst_notes = {}
with open(RAW / "sheet" / "worst.csv", encoding="utf-8") as f:
    for r in csv.reader(f):
        if len(r) > 6 and r[3].strip().isdigit():
            worst_notes[(int(r[3]), int(r[4]), canon(r[2].strip()))] = r[6].strip()

# ================= STARTING LINEUPS for every game =================
lineups = {}  # "year|week|owner" -> [[name, pos, pts], ...]
for yr in range(2014, 2020):
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
    lu_maxw = min(18, last_scored_26) if season == CUR_SEASON else 18
    for w in range(1, lu_maxw + 1):
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
                if not pid or pid == "0":   # Sleeper's marker for an EMPTY starter slot
                    continue
                info = players.get(pid) or {}
                lu.append([info.get("full_name") or info.get("last_name") or pid,
                           info.get("position", "?"), round(pp.get(pid, 0) or 0, 1)])
            lineups[f"{season}|{w}|{o}"] = lu

# ================= PLAYER NAME UNIFICATION =================
# The same human appears under era-dependent names ("Aaron Jones" vs "Aaron Jones Sr.",
# "Ravens D/ST" vs "Ravens"). Merge variants; the most-used spelling wins.
def player_key(name, pos):
    s = _re.sub(r"[^a-z ]", "", name.lower())
    s = _re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    if pos == "DEF":
        s = s.replace("dst", "").replace(" ", "")
        # franchise renames: one team, one identity (Washington is the only
        # nickname change in our window; city moves kept their nicknames)
        return {"redskins": "commanders", "washington": "commanders",
                "footballteam": "commanders"}.get(s, s)
    return s.replace(" ", "")

variant_counts = defaultdict(Counter)
for lu in lineups.values():
    for nm, pos, pts in lu:
        variant_counts[player_key(nm, pos)][nm] += 1
canon_name = {}
for k, c in variant_counts.items():
    if len(c) > 1:
        best = max(c.items(), key=lambda kv: (kv[1], len(kv[0])))[0]
        for nm in c:
            if nm != best:
                canon_name[nm] = best
if canon_name:
    print(f"name unification: {len(canon_name)} variant spellings merged into "
          f"{len(set(canon_name.values()))} identities")
    for lu in lineups.values():
        for p in lu:
            if p[0] in canon_name:
                p[0] = canon_name[p[0]]
    def _remap(items, field="player"):
        for it in items:
            if it.get(field) in canon_name:
                it[field] = canon_name[it[field]]
    _remap(top_players, "name")
    _remap(bench_games, "name")
    _remap(espn_pickups)
    _remap(espn_swaps)
    for lst in moves["waivers"].values():
        _remap(lst)
    _remap(draft_payload["all"])
    _remap(draft_payload["steals"])
    _remap(draft_payload["busts"])

# ================= INFLATION-ADJUSTED RECORDS =================
# Scoring rules changed over the years (verified from ESPN mSettings + Sleeper
# scoring_settings): 0.5 PPR arrived in 2019; rush 100-199 (+2) / 200+ (+4)
# game bonuses arrived in 2024 (rec equivalents existed every year; pass
# bonuses never did). Adjusted mode re-scores 2014-2018 at 0.5 PPR and
# retro-applies the rush bonuses to 2014-2023 using nflverse weekly stats,
# and ranks seasons per-game. XP-miss -1 (added 2025) is NOT retro-applied
# (no per-week kicking source; worth ~1-2 pts/season league-wide).
def norm_infl(s):
    s = _re.sub(r"[^a-z ]", "", s.lower())
    s = _re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    return s.replace(" ", "")
recmaps = {}
for yr in range(2014, 2019):
    fp = espn_dir / f"rec_{yr}.json"
    if fp.exists():
        recmaps[yr] = json.load(open(fp, encoding="utf-8"))
def rec_of(yr, w, name):
    return (recmaps.get(yr) or {}).get(f"{w}|{norm_infl(name)}", 0.0)
rushmaps = {}
for yr in range(2014, 2024):
    fp = espn_dir / f"rush_{yr}.json"
    if fp.exists():
        rushmaps[yr] = json.load(open(fp, encoding="utf-8"))
def rush_bonus_of(yr, w, name):
    ry = (rushmaps.get(yr) or {}).get(f"{w}|{norm_infl(name)}", 0.0)
    return 4.0 if ry >= 200 else (2.0 if ry >= 100 else 0.0)

adj_player_entries = []
team_delta = defaultdict(float)   # (yr, w, owner) -> +points at 0.5 PPR
for key, lu in lineups.items():
    yr_s, w_s, o = key.split("|")
    yr, w = int(yr_s), int(w_s)
    for nm, pos, pts in lu:
        bump = 0.5 * rec_of(yr, w, nm) if yr <= 2018 else 0.0
        if yr <= 2023:
            bump += rush_bonus_of(yr, w, nm)
        team_delta[(yr, w, o)] += bump
        if pts + bump > 30:
            adj_player_entries.append((pts + bump, nm, pos, o, yr, w))
adj_player_entries.sort(key=lambda e: -e[0])
adj_players = [dict(pts=round(e[0], 1), name=e[1], pos=e[2], owner=e[3], year=e[4], week=e[5])
               for e in adj_player_entries[:15]]

adj_expand = []
for g in games:
    if g.get("agg"):   # two-week playoff totals aren't single-game scores
        continue
    da = team_delta.get((g["year"], g["week"], g["a"]), 0.0)
    db = team_delta.get((g["year"], g["week"], g["b"]), 0.0)
    adj_expand.append((g["sa"] + da, g["a"], g["sb"] + db, g["b"], g))
    adj_expand.append((g["sb"] + db, g["b"], g["sa"] + da, g["a"], g))
adj_top = sorted(adj_expand, key=lambda x: -x[0])[:10]
adj_low = sorted([e for e in adj_expand if e[4]["type"] != "Consolation"], key=lambda x: x[0])[:10]

adj_season = defaultdict(lambda: dict(pf=0.0, g=0))
for g in games:
    if g["type"] != "Regular":
        continue
    for o, pts in ((g["a"], g["sa"] + team_delta.get((g["year"], g["week"], g["a"]), 0.0)),
                   (g["b"], g["sb"] + team_delta.get((g["year"], g["week"], g["b"]), 0.0))):
        e = adj_season[(o, g["year"])]
        e["pf"] += pts; e["g"] += 1
adj_season_rows = [dict(owner=o, year=y, pf=round(v["pf"], 1), g=v["g"], ppg=round(v["pf"] / v["g"], 2))
                   for (o, y), v in adj_season.items() if y in complete_years]
adj = dict(
    top=[dict(pts=round(e[0], 2), owner=e[1], opp=e[3], opp_pts=round(e[2], 2),
              year=e[4]["year"], week=e[4]["week"]) for e in adj_top],
    low=[dict(pts=round(e[0], 2), owner=e[1], opp=e[3], opp_pts=round(e[2], 2),
              year=e[4]["year"], week=e[4]["week"],
              note=worst_notes.get((e[4]["year"], e[4]["week"], e[1]), "")) for e in adj_low],
    players=adj_players,
    season_high=sorted(adj_season_rows, key=lambda r: -r["ppg"])[:10],
    season_low=sorted(adj_season_rows, key=lambda r: r["ppg"])[:10],
)

# ---------------- clinch / elimination history ----------------
# Replay every regular season week by week and find the first week each owner
# locked a playoff spot, locked a first-round bye (division title), or was
# mathematically eliminated. Strict math: a clinch counts only if it holds no
# matter how every remaining game AND every tiebreaker falls; an elimination
# only if no combination could save them. Spots settled by points-for
# tiebreaks never mathematically lock (multiway unbalanced ties fall to PF),
# so ties always count as undecided in both directions.

def _ch_divmap(yr):
    """(owner -> division 1/2, [division names]) for a season."""
    if yr <= 2019:
        f = espn_dir / f"{yr}.json"
        if not f.exists():
            return None, None
        d = json.load(open(f, encoding="utf-8"))
        if isinstance(d, list):
            d = d[0]
        members = {m["id"]: (m.get("firstName", "") + " " + m.get("lastName", "")).strip()
                   for m in d.get("members", [])}
        log_teams = {g["a"] for g in games if g["year"] == yr} | {g["b"] for g in games if g["year"] == yr}
        dmap = {}
        for t in d.get("teams", []):
            # the rotating seat lists co-owners; pick the one in this year's game log
            cands = [ESPN_NAME.get(members.get(o, ""), members.get(o, ""))
                     for o in (t.get("owners") or [t.get("primaryOwner")])]
            owner = next((c for c in cands if c in log_teams), cands[0] if cands else "?")
            dmap[owner] = t.get("divisionId", 0) + 1
        dvs = sorted((d.get("settings", {}).get("scheduleSettings", {}).get("divisions") or []),
                     key=lambda v: v.get("id", 0))
        return dmap, [v.get("name", f"Division {i+1}") for i, v in enumerate(dvs)][:2] or ["Division 1", "Division 2"]
    rost = load(yr, "rosters")
    if not rost:
        return None, None
    users = {u["user_id"]: u for u in (load(yr, "users") or [])}
    dmap = {}
    for r in rost:
        owner = uid26.get(r["owner_id"])
        if not owner:
            votes = defaultdict(int)
            for w in range(1, 19):
                m = load(yr, f"matchups_{w}")
                if not m:
                    continue
                for x in m:
                    if x["roster_id"] == r["roster_id"] and x["points"]:
                        hit = score_owner[yr].get((w, round(x["points"], 1)))
                        if hit:
                            votes[hit] += 1
            owner = max(votes, key=votes.get) if votes else users.get(r["owner_id"], {}).get("display_name", "?")
        dmap[owner] = (r.get("settings") or {}).get("division")
    md = (load(yr, "league") or {}).get("metadata") or {}
    return dmap, [md.get("division_1", "Division 1"), md.get("division_2", "Division 2")]

def _ch_flow(glist, budget):
    """Can the games in glist be distributed so each team wins <= budget[team]?"""
    Gn = len(glist)
    if Gn == 0:
        return True
    if sum(budget.values()) < Gn:
        return False
    tidx = {y: i for i, y in enumerate(budget)}
    n = 2 + Gn + len(budget); s, t = 0, n - 1
    cap = [[0] * n for _ in range(n)]
    for i, (a, b) in enumerate(glist):
        cap[s][1 + i] = 1
        cap[1 + i][1 + Gn + tidx[a]] = 1
        cap[1 + i][1 + Gn + tidx[b]] = 1
    for y, i in tidx.items():
        cap[1 + Gn + i][t] = budget[y]
    flow = 0
    while True:
        par = [-1] * n; par[s] = s; q = deque([s])
        while q and par[t] == -1:
            u = q.popleft()
            for v in range(n):
                if par[v] == -1 and cap[u][v] > 0:
                    par[v] = u; q.append(v)
        if par[t] == -1:
            return flow == Gn
        v = t
        while v != s:
            u = par[v]; cap[u][v] -= 1; cap[v][u] += 1; v = u
        flow += 1

def _ch_alive(x, wins, future, teams, div):
    """Can x still make the playoffs if every tie breaks x's way?"""
    allg = [g for w in sorted(future) for g in future[w]]
    xg = [(a, b) for a, b in allg if x in (a, b)]
    pl = [(a, b) for a, b in allg if x not in (a, b)]
    M = wins[x] + len(xg)
    others = sorted([t for t in teams if t != x], key=lambda t: -wins[t])
    forced = [t for t in others if wins[t] > M]
    otherdiv = frozenset(t for t in others if div[t] != div[x])
    cands = ([otherdiv] if all(f in otherdiv for f in forced) else [])
    for S in itertools.combinations(others, 5):
        if {div[t] for t in S} == {1, 2} and all(f in S for f in forced):
            cands.append(frozenset(S))
    for S in cands:
        budget, bad = {}, False
        for y in others:
            if y in S:
                continue
            room = M - wins[y]
            if room < 0:
                bad = True; break
            budget[y] = room
        if bad:
            continue
        if _ch_flow([(a, b) for a, b in pl if a in budget and b in budget], budget):
            return True
    return False

def _ch_can_miss(x, wins, future, teams, div):
    """Can any remaining outcome leave x out, if every tie breaks against x?"""
    allg = [g for w in sorted(future) for g in future[w]]
    M = wins[x]
    others = [t for t in teams if t != x]
    owndiv = [t for t in others if div[t] == div[x]]
    def above_feasible(T):
        Tset = set(T)
        kk, glist = {}, []
        for a, b in allg:
            if a in Tset and b in Tset:
                glist.append((a, b))
        for y in T:
            free_wins = sum(1 for a, b in allg if (a == y) != (b == y)
                            and not (a in Tset and b in Tset))
            k = M - (wins[y] + free_wins)
            if k > len([1 for a, b in glist if y in (a, b)]):
                return False
            kk[y] = max(0, k)
        need = sum(kk.values())
        if need == 0:
            return True
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
    if above_feasible(owndiv):
        return True
    for T in itertools.combinations(others, 6):
        if any(div[y] == div[x] for y in T) and above_feasible(T):
            return True
    return False

def _ch_can_lose_division(x, wins, future, teams, div):
    allg = [g for w in sorted(future) for g in future[w]]
    for y in (t for t in teams if t != x and div[t] == div[x]):
        if wins[y] + sum(1 for a, b in allg if y in (a, b)) >= wins[x]:
            return True
    return False

clinch_rows = dict(spot=[], bye=[], elim=[])
divhist = {}   # year -> division names + owner assignments, for division standings
_ch_warn = []
for yr in sorted({g["year"] for g in games}):
    if yr == 2014:   # 8-team season; the solver assumes 12 teams. Divisions are known.
        if E14.get("divisions"):
            divhist[str(yr)] = dict(names=E14["divisions"]["names"], owners=E14["divisions"]["owners"])
        continue
    dmap, dnames = _ch_divmap(yr)
    reg = [g for g in games if g["year"] == yr and g["type"] == "Regular"]
    weeks = sorted({g["week"] for g in reg})
    teams = sorted({g["a"] for g in reg} | {g["b"] for g in reg})
    if not dmap or len(teams) != 12 or any(dmap.get(t) not in (1, 2) for t in teams):
        _ch_warn.append(f"{yr}: missing division data, skipped")
        continue
    div = {t: dmap[t] for t in teams}
    divhist[str(yr)] = dict(names=dnames, owners=div)
    byweek = {w: [(g["a"], g["b"], g["sa"], g["sb"]) for g in reg if g["week"] == w] for w in weeks}
    extra = {}
    if yr == CUR_SEASON:   # schedule of unplayed weeks, so mid-season clinches are exact
        for w in range(1, playoff_start_26):
            if w in byweek:
                continue
            pair = defaultdict(list)
            for x in (load(yr, f"matchups_{w}") or []):
                if x.get("matchup_id") is not None:
                    pair[x["matchup_id"]].append(x["roster_id"])
            prs = [(ROSTER_OWNER_2026[p[0]], ROSTER_OWNER_2026[p[1]]) for p in pair.values() if len(p) == 2]
            if prs:
                extra[w] = prs
    regweeks = max(list(weeks) + list(extra.keys()))
    wins = {t: 0 for t in teams}
    res = {t: {} for t in teams}
    for w in weeks:
        for a, b, sa, sb in byweek[w]:
            if sa > sb:
                wins[a] += 1
            elif sb > sa:
                wins[b] += 1
        future = {fw: [(a, b) for a, b, _, _ in byweek[fw]] for fw in weeks if fw > w}
        for fw, prs in extra.items():
            if fw > w:
                future[fw] = prs
        for x in teams:
            r = res[x]
            if "elim" in r:
                continue
            if "spot" not in r and not _ch_alive(x, wins, future, teams, div):
                r["elim"] = w
                continue
            if "bye" not in r and not _ch_can_lose_division(x, wins, future, teams, div):
                r["bye"] = w
                r.setdefault("spot", w)
            if "spot" not in r and not _ch_can_miss(x, wins, future, teams, div):
                r["spot"] = w
    for t, v in res.items():
        for kind, wk in v.items():
            clinch_rows[kind].append(dict(owner=t, year=yr, week=wk, of=regweeks, spare=regweeks - wk))
    # regression tripwire: strict clinches/eliminations must agree with reality
    po = [g for g in games if g["year"] == yr and g["type"] == "Playoff"]
    if po:
        field = {g["a"] for g in po} | {g["b"] for g in po}
        w1 = min(g["week"] for g in po)
        rd1 = {g["a"] for g in po if g["week"] == w1} | {g["b"] for g in po if g["week"] == w1}
        if {t for t, v in res.items() if "spot" in v} - field:
            _ch_warn.append(f"{yr}: clinched team missed the field")
        if {t for t, v in res.items() if "bye" in v} - (field - rd1):
            _ch_warn.append(f"{yr}: bye-clinched team did not get the bye")
        if {t for t, v in res.items() if "elim" in v} & field:
            _ch_warn.append(f"{yr}: eliminated team made the field")
for k in clinch_rows:
    clinch_rows[k].sort(key=lambda r: (-r["spare"], r["year"], r["owner"]))
print("clinch history: " + ("; ".join(_ch_warn) if _ch_warn else
      f"{len({r['year'] for r in clinch_rows['spot']})} seasons replayed, all consistent with actual fields"))

# ---------------- 2026 "now" data ----------------
now = json.load(open("data/now.json", encoding="utf-8")) if Path("data/now.json").exists() else {}
if Path("data/title_odds.json").exists():
    now["title"] = json.load(open("data/title_odds.json", encoding="utf-8"))

# ---------------- payload ----------------
payload = dict(
    updated=(lambda cw: f"through week {max(cw)}, {CUR_SEASON}" if cw else f"preseason {CUR_SEASON}")(
        [g["week"] for g in games if g["year"] == CUR_SEASON]),
    built=f"{BUILT_ET.strftime('%B')} {BUILT_ET.day}, {BUILT_ET.year}",
    built_ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    owners=OWNERS,
    active=list(ROSTER_OWNER_2026.values()),
    games=[[g["year"], g["week"], g["type"][0], g["a"], g["sa"], g["b"], g["sb"]]
           + ([1] if g.get("agg") else []) for g in games],
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
        bench_games=bench_games,
        adj=adj,
        clinches={k: two_tiers(v, key=lambda r: r["spare"]) for k, v in clinch_rows.items()},
    ),
    partial=({"2014": dict(note=E14.get("note", ""), playoffs=E14.get("playoffs") or [])}
             if E14 else {}),
    rings=rings,
    money=money,
    luck=luck,
    bench=bench,
    moves=moves,
    draft=draft_payload,
    shame=shame,
    lineups=lineups,
    divisions=divhist,
    pubs=(json.load(open("data/feeds.json", encoding="utf-8")).get("feeds", [])
          if Path("data/feeds.json").exists() else []),
    trade=(json.load(open("data/values.json", encoding="utf-8")).get("players", [])
           if Path("data/values.json").exists() else []),
    now=now,
)

tpl = open("template.html", encoding="utf-8").read()
out_html = tpl.replace("/*__DATA__*/", "const DATA = " + json.dumps(payload) + ";")
open(OUT / "index.html", "w", encoding="utf-8").write(out_html)
print(f"wrote site/index.html ({len(out_html)//1024} KB)")

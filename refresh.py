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

scoring = lg["scoring_settings"]
PSTART = lg["settings"]["playoff_week_start"]       # regular season = weeks 1..PSTART-1
last_scored = lg["settings"].get("last_scored_leg") or 0
cur_week = min(last_scored + 1, PSTART - 1)
weeks_left = [w for w in range(cur_week, PSTART) if w > last_scored]
print(f"season {season}: {last_scored} weeks scored, current week {cur_week}")

rosters = json.load(open(yr_dir / "rosters.json"))
players = json.load(open(RAW / "players.json", encoding="utf-8"))
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

# ---------------- 3. one simulation to rule them all: 100k full seasons incl. bracket ----------------
sdv = {t: max(12.0, statistics.stdev(sc[t])) if len(sc[t]) > 1 else 20.0 for t in TEAMS}
status = {}   # owner clinch/elimination status, filled by the solver or by the settled field
N_TITLE = 100000
title_counts = {k: {t: 0 for t in TEAMS} for k in ("made", "bye", "finals", "champs")}
if weeks_left:
    print("simulating championships (100k) ...")
    random.seed(7)
    gauss = random.gauss
    sched = [(w, a, b) for w in weeks_left for a, b in future[w]]
    for s in range(N_TITLE):
        wins = dict(wins0); pf = dict(pf0)
        h2h = {t: {u: list(v) for u, v in dd.items()} for t, dd in h2h0.items()}
        for w, a, b in sched:
            pa = gauss(WP[a][w], sdv[a]); pb = gauss(WP[b][w], sdv[b])
            if pa > pb:
                wins[a] += 1; h2h[a][b][1] += 1; h2h[b][a][1] += 1; h2h[a][b][0] += 1
            else:
                wins[b] += 1; h2h[a][b][1] += 1; h2h[b][a][1] += 1; h2h[b][a][0] += 1
            pf[a] += max(30, pa); pf[b] += max(30, pb)
        w1 = contextual_rank(D[1], wins, pf, h2h)[0]; w2 = contextual_rank(D[2], wins, pf, h2h)[0]
        byes_ = contextual_rank([w1, w2], wins, pf, h2h)
        nw = contextual_rank([t for t in TEAMS if t not in (w1, w2)], wins, pf, h2h)
        s1, s2 = byes_; s3, s4, s5, s6 = nw[:4]
        for t in (s1, s2, s3, s4, s5, s6):
            title_counts["made"][t] += 1
        title_counts["bye"][s1] += 1; title_counts["bye"][s2] += 1
        def game(x, y, w):
            return x if gauss(WP[x][w], sdv[x]) >= gauss(WP[y][w], sdv[y]) else y
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

def alive(x, wins_now, h2h_now, future_games):
    """'Practical' survival: a tied rival who holds the head-to-head edge ranks ahead
    (no points-for-miracle escape). Division title or top-4 non-winner qualifies."""
    allg = [g for w in sorted(future_games) for g in future_games[w]]
    xg = [(a, b) for a, b in allg if x in (a, b)]
    pl = [(a, b) for a, b in allg if x not in (a, b)]
    M = wins_now[x] + len(xg)
    others = sorted([t for t in TEAMS if t != x], key=lambda t: -wins_now[t])
    caps = {}
    for y in others:
        xw, g = h2h_now[x][y]
        fm = sum(1 for a, b in allg if {a, b} == {x, y})
        xw += fm; g += fm                       # x wins any future meetings
        caps[y] = M - 1 if (g and xw * 2 < g) else M
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

elim_cards = []
clinch_cards = []
elimnote = ""
if not FAST and future.get(cur_week):
    print("running elimination solver ...")
    WEEKG = future[cur_week]
    FUTA = {w: future[w] for w in future if w > cur_week}
    for x in TEAMS:
        other = [(a, b) for a, b in WEEKG if x not in (a, b)]
        xg = [(a, b) for a, b in WEEKG if x in (a, b)]
        if not xg:
            continue
        opp = xg[0][0] if xg[0][1] == x else xg[0][1]
        combos = []
        for combo in itertools.product(*[[a, b] for a, b in other]):
            w9 = dict(wins0); w9[opp] += 1
            hh = {t: {u: list(v) for u, v in dd.items()} for t, dd in h2h0.items()}
            def rec2(wi, lo):
                hh[wi][lo][1] += 1; hh[lo][wi][1] += 1; hh[wi][lo][0] += 1
            rec2(opp, x)
            for wt, (a, b) in zip(combo, other):
                w9[wt] += 1; rec2(wt, b if wt == a else a)
            if not alive(x, w9, hh, FUTA):
                combos.append(combo)
        if combos:
            needs = [f"Loses to {OWNER[opp]}"] + factor_conditions(combos, other)
            elim_cards.append(dict(owner=OWNER[x], needs=needs))
    elimnote = "Under league tiebreak rules; a points-for miracle technically keeps a team mathematically alive."

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
    # threshold to finish above x (adversary-optimistic on ties)
    thresh = {}
    for y in others:
        xw, g = h2h_now[x][y]
        fm = sum(1 for a, b in allg if {a, b} == {x, y})   # x loses these
        x_strictly_ahead = xw * 2 > (g + fm)
        thresh[y] = M + 1 if x_strictly_ahead else M
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
    """Can any division rival still finish above x? (x loses out; rival optimism on ties)"""
    allg = [g for w in sorted(future_games) for g in future_games[w]]
    M = wins_now[x]
    for y in (t for t in TEAMS if t != x and division[t] == division[x]):
        xw, g = h2h_now[x][y]
        fm = sum(1 for a, b in allg if {a, b} == {x, y})
        thresh = M + 1 if xw * 2 > (g + fm) else M
        max_y = wins_now[y] + sum(1 for a, b in allg if y in (a, b))
        if max_y >= thresh:
            return True
    return False

if weeks_left:
    print("computing clinch/elimination status ...")
    for x in TEAMS:
        if not alive(x, wins0, h2h0, future):
            status[x] = "out"
        elif not can_lose_division(x, wins0, h2h0, future):
            status[x] = "bye"
        elif not can_miss(x):
            status[x] = "clinched"

    # clinch scenarios: can any combination of this week's results LOCK a playoff spot?
    if not FAST and future.get(cur_week):
        print("running clinch-scenario solver ...")
        week_games = future[cur_week]
        futa_c = {w: future[w] for w in future if w > cur_week}
        for x in TEAMS:
            if status.get(x) in ("bye", "out"):
                continue
            spot_combos, bye_combos = [], []
            for combo in itertools.product(*[[a, b] for a, b in week_games]):
                wC = dict(wins0)
                hh = {t: {u: list(v) for u, v in dd.items()} for t, dd in h2h0.items()}
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
now = dict(
    asof=(f"entering week {cur_week}, {season}" if weeks_left
          else f"playoffs, {season} — regular season complete"),
    standings=standings,
    odds=sorted([dict(owner=OWNER[t], pct=round(100 * made[t] / N, 1)) for t in TEAMS],
                key=lambda o: -o["pct"]),
    oddsnote="100,000 full-season simulations (regular season + playoff bracket). Weekly player "
             "projections, optimized lineups (no K/DEF), bye and injury holes filled with a waiver "
             "add (top-5 free agent average). League tiebreakers applied.",
    week=cur_week,
    matchups=matchup_cards,
    elim=elim_cards,
    elimnote=elimnote,
    clinch=clinch_cards,
    clinchnote="Lock up a playoff spot no matter what happens the rest of the season." if clinch_cards else "",
    brackets=brackets,
)
json.dump(now, open("data/now.json", "w", encoding="utf-8"), indent=1)
print("wrote data/now.json")

# ---------------- 6. rebuild site ----------------
import subprocess
subprocess.run([sys.executable, "-I", "build.py", "data/raw"], check=True)
print("done — site/index.html is current. Republish the artifact to update the shared page.")

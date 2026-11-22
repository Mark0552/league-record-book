"""Build data/preseason_proj.json: preseason season-long projections, re-scored
under the league's own rules, for every season since 2014.

Sources, averaged per player where more than one lists him:
  * FantasyPros consensus projections (itself an average of several projection
    sites: CBS, ESPN, numberFire, FFToday, NFL.com, ... the mix varies by year)
    as archived by the Wayback Machine in the weeks before each season's kickoff;
  * ESPN's own preseason season projections (2018 on, finished seasons only).
Both carry raw projected stats, so points are recomputed with the league's
scoring instead of trusting either site's fantasy-point column:
  2014-2018 standard (no points per reception), 2019+ half-PPR;
  pass 0.04/yd, 4/TD, -2/INT; rush+rec 0.1/yd, 6/TD; -2 per fumble lost.

Run once per season after the draft (needs network):
    python tools/preseason_proj.py [year ...]
Seasons already in the file are kept unless named on the command line.
"""
import json, os, re, sys, time, urllib.request
from pathlib import Path

OUT = Path("data/preseason_proj.json")
POS = ("qb", "rb", "wr", "te")

def norm(s):
    return re.sub(r"[^a-z]", "", re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", (s or "").lower()))

def get(url):
    err = None
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.geturl(), r.read().decode("utf-8", "ignore")
        except Exception as e:
            err = e
            time.sleep(3)
    raise err

def parse(html):
    m = re.search(r'<table[^>]*id="data"[^>]*>(.*?)</table>', html, re.S)
    if not m:
        return None, []
    rows = [[re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).replace("&nbsp;", " ").strip()
             for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S)]
    hdr = next((r for r in rows if r and r[0] == "Player"), None)
    return hdr, [r for r in rows if hdr and len(r) == len(hdr) and r[0] != "Player"]

def snapshot(y, pos):
    """The archived season-projection page closest to kickoff: (timestamp, header, rows)."""
    for ts in (f"{y}0901", f"{y}0822", f"{y}0808", f"{y}0907", f"{y}0725"):
        for u in (f"https://www.fantasypros.com/nfl/projections/{pos}.php",
                  f"http://www.fantasypros.com/nfl/projections/{pos}.php",
                  f"https://www.fantasypros.com/nfl/projections/{pos}.php?week=draft"):
            try:
                final, html = get(f"https://web.archive.org/web/{ts}000000/{u}")
            except Exception:
                continue
            m = re.search(r"/web/(\d{14})", final)
            snap = m.group(1) if m else ""
            t = re.search(r"<title>(.*?)</title>", html, re.S)
            hdr, rows = parse(html)
            # must be the season page ("2015 RB Projections"), captured before week 1 was over
            if f"{y}0701" <= snap[:8] <= f"{y}0912" and t and t.group(1).strip().startswith(str(y)) and len(rows) > 20:
                return snap, hdr, rows
    return None, None, []

def score(pos, hdr, row, ppr):
    v = [float(x.replace(",", "") or 0) if re.fullmatch(r"-?[\d,]*\.?\d*", x) and x else 0.0 for x in row]
    h = hdr[1:]
    def take(i, n): return v[1 + i:1 + i + n]
    pts = 0.0
    if pos == "qb":            # ATT CMP YDS TDS INTS | ATT YDS TDS | FL
        _, _, pyd, ptd, pint = take(0, 5)
        _, ryd, rtd = take(5, 3)
        pts = pyd * 0.04 + ptd * 4 - pint * 2 + ryd * 0.1 + rtd * 6 - take(8, 1)[0] * 2
    elif pos == "te":          # REC YDS TDS | FL
        rec, yd, td = take(0, 3)
        pts = rec * ppr + yd * 0.1 + td * 6 - take(3, 1)[0] * 2
    else:                      # RB/WR: rushing (ATT YDS TDS) and receiving (REC YDS TDS) in either order | FL
        a, b = (0, 3) if h[0] == "ATT" else (3, 0)
        _, ryd, rtd = take(a, 3)
        rec, yd, td = take(b, 3)
        pts = ryd * 0.1 + rtd * 6 + rec * ppr + yd * 0.1 + td * 6 - take(6, 1)[0] * 2
    return round(pts, 1)

def player_name(cell):
    cell = re.sub(r"\(.*?\)", "", cell)                    # "Jamaal Charles (KC)"
    cell = re.sub(r"\s+(?:[A-Z]{2,3}|FA)\b.*$", "", cell)  # "Jamaal Charles KC ..."
    return cell.strip()

ESPN_POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE"}
def espn(y, ppr):
    """ESPN's own preseason season projection (2018+), re-scored from its raw stat lines.
    Returns {} when ESPN's numbers for that season aren't a usable preseason set
    (the season in progress is re-projected weekly; 2023 is mostly blank)."""
    filt = {"players": {"limit": 450, "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
                        "filterStatsForTopScoringPeriodIds": {"value": 1, "additionalValue": [f"10{y}"]}}}
    req = urllib.request.Request(
        f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{y}/segments/0/leaguedefaults/3?view=kona_player_info",
        headers={"User-Agent": "Mozilla/5.0", "X-Fantasy-Filter": json.dumps(filt)})
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.load(r)
    out = {}
    for p in d.get("players", []):
        pl = p["player"]
        pos = ESPN_POS.get(pl.get("defaultPositionId"))
        st = next((x.get("stats") for x in pl.get("stats", []) if x.get("id") == f"10{y}"), None)
        if not pos or not st:
            continue
        g = lambda k: float(st.get(str(k), 0) or 0)
        pts = (g(3) * 0.04 + g(4) * 4 - g(20) * 2 + g(24) * 0.1 + g(25) * 6
               + g(42) * 0.1 + g(43) * 6 + g(53) * ppr - g(72) * 2)
        out[f"{norm(pl['fullName'])}|{pos}"] = round(pts, 1)
    return out if sum(1 for v in out.values() if v > 150) >= 50 else {}

def fp(y, ppr):
    """FantasyPros consensus from the archived preseason pages: ({player: pts}, {pos: snapshot date})"""
    players, snaps = {}, {}
    local = os.environ.get("PROJ_LOCAL")          # pre-downloaded snapshots: fp_<year>_<pos>.json
    for pos in POS:
        cached = Path(local) / f"fp_{y}_{pos}.json" if local else None
        if cached and cached.exists():
            c = json.load(open(cached))
            snap, hdr, rows = c["snap"], c["hdr"], c["rows"]
        elif local:
            snap, hdr, rows = None, None, []
        else:
            snap, hdr, rows = snapshot(y, pos)
        if not snap:
            continue
        snaps[pos] = snap[:8]
        for r in rows:
            players[f"{norm(player_name(r[0]))}|{pos.upper()}"] = score(pos, hdr, r, ppr)
    return players, snaps

def main():
    want = [int(a) for a in sys.argv[1:]]
    data = json.load(open(OUT, encoding="utf-8")) if OUT.exists() else {}
    this_year = time.gmtime().tm_year
    years = want or [y for y in range(2014, this_year + 1) if str(y) not in data]
    for y in years:
        ppr = 0.0 if y <= 2018 else 0.5
        a, snaps = fp(y, ppr)
        b = {}
        if 2018 <= y < this_year:
            try:
                b = espn(y, ppr)
            except Exception as e:
                print(f"{y}: ESPN projections unavailable ({e!r})")
        if not a and not b:
            print(f"{y}: no preseason projections found")
            continue
        # the projection used on the site: the mean of the sources that list the player
        players = {k: round(sum(v) / len(v), 1)
                   for k in set(a) | set(b) for v in [[s[k] for s in (a, b) if k in s]]}
        data[str(y)] = dict(sources=dict(**({"fantasypros_consensus": snaps} if a else {}),
                                         **({"espn": len(b)} if b else {})), players=players)
        print(f"{y}: {len(players)} players — FantasyPros consensus {len(a)} {snaps}, ESPN {len(b)}")
    json.dump(data, open(OUT, "w", encoding="utf-8"), separators=(",", ":"), sort_keys=True)

if __name__ == "__main__":
    main()

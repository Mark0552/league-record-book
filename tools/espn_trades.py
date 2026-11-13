import mailbox, sys, email.utils, html, re, json, datetime
from pathlib import Path

NAME = {"Samuel Harrison": "Sam", "Cliff Perez": "Cliff", "Peter Mulholland": "Neville",
        "Austin Minogue": "Austin", "mart jimpson": "Martin", "Martin Linnane PHD": "Martin",
        "Martin Linnane": "Martin", "Mark Sobchuk": "Mark", "Bob Baggott": "Bob",
        "Christian Lerch": "Lerch", "Tim Guckavan": "Timmy", "Mike Clancy": "Clancy",
        "Steven Mastro": "Steve", "Will Ferraro": "Will",
        "Billy Beane Of Fantasy Baseball": "Luke", "Luke Minogue": "Luke",
        "Alex Sniatkowski": "Alex"}
WK1_THU = {2014: "2014-09-04", 2015: "2015-09-10", 2016: "2016-09-08",
           2017: "2017-09-07", 2018: "2018-09-06", 2019: "2019-09-05"}
def nfl_week(d):
    w1 = datetime.date.fromisoformat(WK1_THU[d.year]) - datetime.timedelta(days=2)
    return max(1, min(17, (d.date() - w1).days // 7 + 1))

# per-year abbrev -> owner from the cached ESPN API files
espn_dir = Path(r"C:\Repositories\Sleeper\pages\data\raw\espn")
ABBR = {}
for yr in range(2014, 2020):
    f = espn_dir / f"{yr}.json"
    if not f.exists():
        continue
    d = json.load(open(f, encoding="utf-8"))
    d = d[0] if isinstance(d, list) else d
    mem = {m["id"]: (m.get("firstName", "") + " " + m.get("lastName", "")).strip()
           for m in d.get("members", [])}
    for t in d.get("teams") or []:
        nm = mem.get((t.get("owners") or [None])[0], "")
        if t.get("abbrev"):
            ABBR[(yr, t["abbrev"].strip())] = NAME.get(nm, nm or "?")

def body_text(m):
    for part in m.walk():
        if part.get_content_type() in ("text/html", "text/plain"):
            b = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
            t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", b, flags=re.S | re.I)
            t = re.sub(r"<br[^>]*>|</tr>|</p>|</div>|</h\d>", "\n", t, flags=re.I)
            t = re.sub(r"</t[dh]>", " ", t, flags=re.I)
            t = re.sub(r"<[^>]+>", "", t)
            return html.unescape(t)
    return ""

mb = mailbox.mbox(sys.argv[1])
events = []
for m in mb:
    subj = m.get("Subject") or ""
    kind = ("accept" if "Has Been Accepted" in subj else
            "uphold" if "Has Been Upheld" in subj else
            "veto" if "Has Been Vetoed" in subj else None)
    if not kind:
        continue
    txt = body_text(m)
    if "many chiefs" not in txt.lower():
        continue
    d = email.utils.parsedate_to_datetime(m.get("Date"))
    ab = {}
    for mm in re.finditer(r"(?:To|From):\s*.+?\((\S+?)\)\s*-\s*([^\n]+)", txt):
        persons = [p.strip() for p in mm.group(2).split(",")]
        ab[mm.group(1)] = next((NAME[p] for p in persons if p in NAME), persons[0].strip())
    def owner_of(abbr):
        return ab.get(abbr) or ABBR.get((d.year, abbr)) or abbr
    lines = [l.strip() for l in txt.splitlines() if l.strip()]
    moves, cur = [], None
    for l in lines:
        t_old = re.match(r"(\S+) trade[sd] (.+?), \S+ (QB|RB|WR|TE|K|D/ST)\s+to (\S+)\s*$", l)
        if t_old:
            moves.append((t_old.group(1), t_old.group(2).replace("*", "").strip()))
            cur = None
            continue
        t_hdr = re.match(r"(\S+) trades?\s*$", l)
        if t_hdr:
            cur = t_hdr.group(1)
            continue
        t_pl = re.match(r"(.+?), \S+ (QB|RB|WR|TE|K|D/ST)\s*$", l)
        if t_pl and cur:
            moves.append((cur, t_pl.group(1).replace("*", "").strip()))
            continue
        if l.startswith(("This trade offer", "ESPN Fantasy App", "A message from")):
            cur = None
    if moves:
        events.append((d, kind, owner_of, moves))

events.sort(key=lambda e: e[0])
vetoed = {frozenset(p for _, p in mv) for d, k, oo, mv in events if k == "veto"}
out, seen = [], set()
for d, k, oo, mv in events:
    if k == "veto":
        continue
    key = frozenset(p for _, p in mv)
    if key in seen:
        continue
    if key in vetoed and k == "accept":
        print(f"  VETOED {d.date()}: {sorted(key)}")
        continue
    seen.add(key)
    gave = {}
    for frm, player in mv:
        gave.setdefault(oo(frm), []).append(player)
    owners = list(gave)
    sides = [dict(owner=o, gave=gave[o],
                  got=[p for x in owners if x != o for p in gave[x]], pts=None)
             for o in owners]
    out.append(dict(year=d.year, week=nfl_week(d), sides=sides))
    print(f"{d.date()} wk{nfl_week(d):2d}: " + " | ".join(f'{s["owner"]} gets {", ".join(s["got"])}' for s in sides))
json.dump(out, open(r"C:\Repositories\Sleeper\pages\data\espn_trades.json", "w", encoding="utf-8"), indent=1)
print(len(out), "completed trades written")

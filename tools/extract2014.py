"""Extract the 2014 season from the ESPN email mbox -> espn2014.json (stdout path arg2)."""
import mailbox, sys, email.utils, html, re, json

OWNER = {  # every 2014 team name (incl. mid-season renames) -> canonical owner
    "dont talk back to ray": "Mark", "montee's balls": "Mark",
    "its not a tumor": "Martin",
    "team redskinheads": "Lerch", "the pad dawgs": "Lerch",
    "the pivot pervert": "Bob",
    "mary was a virgin?": "Neville", "team mulholland": "Neville",
    "i miss chunk more than u": "Austin", "team minogue": "Austin",
    "just outside the city": "Cliff", "the bull nuts": "Cliff", "team perez": "Cliff",
    "head honchos": "Sam", "head hanchos": "Sam", "willie and the poor boys": "Sam",
    "at least im better than bob": "Sam",
}
def own(name):
    key = re.sub(r"\s+", " ", name).strip().lower()
    return OWNER.get(key)

def body_text(m):
    for part in m.walk():
        if part.get_content_type() in ("text/html", "text/plain"):
            b = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
            t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", b, flags=re.S | re.I)
            t = re.sub(r"<br[^>]*>|</tr>|</p>|</div>|</h\d>", "\n", t, flags=re.I)
            t = re.sub(r"</t[dh]>", " | ", t, flags=re.I)
            t = re.sub(r"<[^>]+>", "", t)
            return html.unescape(t)
    return ""

mb = mailbox.mbox(sys.argv[1])
games, draft, trades, stand_txt = [], [], [], {}
seen_weeks, seen_trades = set(), set()
for m in mb:
    subj = (m.get("Subject") or "")
    txt = body_text(m)
    if "many chiefs" not in txt.lower() and "many chiefs" not in subj.lower():
        continue
    d0 = email.utils.parsedate_to_datetime(m.get("Date")) if m.get("Date") else None
    if d0 and d0.year != 2014:
        continue
    if "Draft Results" in subj:
        for rm in re.finditer(r"Round:\s*(\d+)\s*\n(.*?)(?=Round:\s*\d+|\Z)", txt, re.S):
            for ln in rm.group(2).splitlines():
                p = re.match(r"\**\s*\((\d+)\)\s*(.+?)\s+-\s+(.+?)\s+(QB|RB|WR|TE|K|D/ST)\s*$", ln.strip())
                if p:
                    o = own(p.group(2))
                    draft.append(dict(pick=int(p.group(1)), owner=o or p.group(2),
                                      player=p.group(3).strip(), pos=p.group(4).replace("D/ST", "DEF")))
        continue
    if "Trade" in subj and "Upheld" in subj:
        lines = [l.strip() for l in txt.splitlines() if "traded" in l]
        moves = []
        for l in lines:
            t2 = re.match(r"(\S+) traded (.+?), \S+ (QB|RB|WR|TE|K|D/ST) to (\S+)", l)
            if t2:
                moves.append((t2.group(1), t2.group(2), t2.group(4)))
        key = frozenset(mv[1] for mv in moves)
        if moves and key not in seen_trades:
            seen_trades.add(key)
            d = email.utils.parsedate_to_datetime(m.get("Date"))
            ab = {"MJL": "Martin", "8==D": "Sam", "SLAP": "Mark", "CLKR": "Cliff",
                  "LRCH": "Lerch", "Perv": "Bob", "DODL": "Austin"}
            gave = {}
            for frm, player, to in moves:
                gave.setdefault(ab.get(frm, frm), []).append(player)
            owners = list(gave)
            week = 9 if d.month == 10 else 10
            sides = [dict(owner=o, gave=gave[o],
                          got=[p for oo in owners if oo != o for p in gave[oo]], pts=None)
                     for o in owners]
            trades.append(dict(year=2014, week=week, sides=sides))
        continue
    # weekly recap: box score + scoreboard
    mw = re.search(r"WEEK (\d+) SCOREBOARD\s*(.*?)LEAGUE STANDINGS", txt, re.S)
    if not mw:
        continue
    wk = int(mw.group(1))
    if wk in seen_weeks:
        continue
    seen_weeks.add(wk)
    pairs = []
    for l in mw.group(2).splitlines():
        m2 = re.match(r"(.+?)\s+([\d.]+)\s*$", l.strip())
        if m2 and own(m2.group(1)):
            pairs.append((own(m2.group(1)), float(m2.group(2))))
    assert len(pairs) == 6, (wk, pairs)
    for i in range(0, 6, 2):
        games.append(dict(year=2014, week=wk, type="Regular",
                          a=pairs[i][0], sa=pairs[i][1], b=pairs[i+1][0], sb=pairs[i+1][1]))
    bx = re.search(r"WEEK \d+ MATCHUP BOX SCORE\s*\n(.+?)\n.*?TOTALS\s*:\s*([\d.]+).*?TOTALS\s*:\s*([\d.]+)", txt, re.S)
    head = re.sub(r"\s+", " ", bx.group(1)).strip()
    words = head.split(" ")
    split_at = next(i for i in range(1, len(words)) if own(" ".join(words[:i])) and own(" ".join(words[i:])))
    games.append(dict(year=2014, week=wk, type="Regular",
                      a=own(" ".join(words[:split_at])), sa=float(bx.group(2)),
                      b=own(" ".join(words[i:] if False else words[split_at:])), sb=float(bx.group(3))))
    ms = re.search(r"LEAGUE STANDINGS AS OF[^\n]*\n(.*?)(?:[A-Z ]{8,}PLAYER NEWS|\Z)", txt, re.S)
    stand_txt[wk] = ms.group(1) if ms else ""

games.sort(key=lambda g: (g["week"],))
# validate: replay wins vs the emailed standings each week
ok = True
wins = {o: [0, 0] for o in set(OWNER.values())}
for wk in sorted(seen_weeks):
    for g in [g for g in games if g["week"] == wk]:
        w, l = (g["a"], g["b"]) if g["sa"] > g["sb"] else (g["b"], g["a"])
        wins[w][0] += 1; wins[l][1] += 1
    for ln in stand_txt.get(wk, "").splitlines():
        m3 = re.match(r"\*?\s*(.+?)\s+(\d+)-(\d+)-0", ln.strip())
        if m3 and own(m3.group(1)):
            o = own(m3.group(1))
            if wins[o] != [int(m3.group(2)), int(m3.group(3))]:
                ok = False
                print(f"MISMATCH wk{wk} {o}: replay {wins[o]} vs email {m3.group(2)}-{m3.group(3)}")
print("standings validation:", "ALL WEEKS MATCH" if ok else "MISMATCHES FOUND")
print(f"{len(games)} games, weeks {sorted(seen_weeks)}; draft picks {len(draft)}; trades {len(trades)}")
out = dict(
    note="Recovered from ESPN league e-mails (ESPN purged pre-2015 seasons). "
         "Weeks 1-7 scored; weeks 8-13 and playoff point totals were lost. "
         "Playoffs: Mark beat Neville in the semifinal and Cliff in the championship.",
    divisions=dict(names=["East", "West"],
                   owners={"Cliff": 1, "Neville": 1, "Sam": 1, "Austin": 1,
                           "Mark": 2, "Martin": 2, "Lerch": 2, "Bob": 2}),
    games=games, draft=draft, trades=trades,
    playoffs=[dict(round="Semifinal", winner="Mark", loser="Neville"),
              dict(round="Championship", winner="Mark", loser="Cliff")])
json.dump(out, open(sys.argv[2], "w", encoding="utf-8"), indent=1)
print("wrote", sys.argv[2])

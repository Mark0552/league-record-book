"""2014 lineup reconstruction, pass 2 (November 2026): kickers, D/STs and the
remaining skill slots that ESPN's archive drops from its 2014 box scores, plus
the two-week playoff rosters (2014_box_14/15.json, fetched with a 2014 member's
cookies from leagueHistory mBoxscore at scoringPeriodId 14 and 16).

Scores are nflverse stats re-scored at the league's exact 2014 ESPN settings
(integer yardage, standard D/ST and K tiers) -- validated against every
starter the archive still holds: 687/689 skill, 75/77 D/ST, 76/77 K.
A slot is filled ONLY when the evidence singles out one answer:
  - one player/defense league-wide with exactly the missing points, or
  - the same K or D/ST the team started the week before/after, or
  - one kicker whose scores match the team's whole run of missing-K weeks.
Fills carry "ghost": 2 and show on the site with a dagger. Everything else
stays an explicit unrecorded slot. Expects nflverse player_stats_2014.csv,
player_stats_kicking_2014.csv and play_by_play_2014.csv.gz under ./nfl/.
Kept for provenance; not part of the pipeline."""
import sys
import pandas as pd, numpy as np, json, re, math
from collections import defaultdict
NFL='nfl/'
ESPN=r'C:\Repositories\Sleeper\pages\data\raw\espn'
def norm(s):
    s=re.sub(r"[^a-z ]","",(s or "").lower()); s=re.sub(r"\b(jr|sr|ii|iii|iv|v)\b","",s); return s.replace(" ","")
tr=lambda v,d: int(v/d) if v>=0 else -int((-v)/d)   # truncate toward zero
# ---------- offense ----------
ps=pd.read_csv(NFL+'player_stats_2014.csv',low_memory=False); ps=ps[ps.season_type=='REG'].fillna(0)
OFF=defaultdict(dict); OPOS={}
for r in ps.itertuples():
    pts=(tr(r.passing_yards,25)+4*r.passing_tds-2*r.interceptions+tr(r.rushing_yards,10)+6*r.rushing_tds
         +tr(r.receiving_yards,10)+6*r.receiving_tds
         -2*(r.sack_fumbles_lost+r.rushing_fumbles_lost+r.receiving_fumbles_lost)
         +2*(r.passing_2pt_conversions+r.rushing_2pt_conversions+r.receiving_2pt_conversions)+6*r.special_teams_tds)
    k=norm(r.player_display_name); OFF[k][int(r.week)]=OFF[k].get(int(r.week),0)+pts
    OPOS[k]=(r.player_display_name,r.position,r.recent_team if hasattr(r,'recent_team') else getattr(r,'team',''))
# ---------- kickers ----------
kk=pd.read_csv(NFL+'player_stats_kicking_2014.csv'); kk=kk[kk.season_type=='REG'].fillna(0)
KP=defaultdict(dict); KN={}
for r in kk.itertuples():
    pts=3*(r.fg_made_0_19+r.fg_made_20_29+r.fg_made_30_39)+4*r.fg_made_40_49+5*(r.fg_made_50_59+r.fg_made_60_)-r.fg_missed+r.pat_made
    k=norm(r.player_display_name); KP[k][int(r.week)]=pts; KN[k]=(r.player_display_name,r.team)
# ---------- D/ST from pbp ----------
pb=pd.read_csv(NFL+'play_by_play_2014.csv.gz',low_memory=False); pb=pb[pb.season_type=='REG']
NICK={'ARI':'Cardinals','ATL':'Falcons','BAL':'Ravens','BUF':'Bills','CAR':'Panthers','CHI':'Bears','CIN':'Bengals','CLE':'Browns','DAL':'Cowboys','DEN':'Broncos','DET':'Lions','GB':'Packers','HOU':'Texans','IND':'Colts','JAX':'Jaguars','KC':'Chiefs','LA':'Rams','STL':'Rams','LAC':'Chargers','SD':'Chargers','LV':'Raiders','OAK':'Raiders','MIA':'Dolphins','MIN':'Vikings','NE':'Patriots','NO':'Saints','NYG':'Giants','NYJ':'Jets','PHI':'Eagles','PIT':'Steelers','SEA':'Seahawks','SF':'49ers','TB':'Buccaneers','TEN':'Titans','WAS':'Redskins'}
DST=defaultdict(dict); DDET={}
for (gid,wk),g in pb.groupby(['game_id','week']):
    home,away=g.home_team.iloc[0],g.away_team.iloc[0]
    fin={home:g.total_home_score.max(),away:g.total_away_score.max()}
    for T,O in ((home,away),(away,home)):
        d=g[g.defteam==T]   # plays where T is on defense (incl. T kicking? no: defteam on kickoffs = receiving team)
        o=g[g.posteam==T]
        sacks=d.sack.sum()
        ints=d.interception.sum()
        # fumble recoveries by T on any play where the fumbling side was the opponent
        fr=0
        for q in g[(g.fumble_lost==1)].itertuples():
            rec=q.fumble_recovery_1_team
            if rec==T and q.fumbled_1_team!=T: fr+=1
        saf=g[(g.safety==1)&(g.posteam==O)].shape[0]   # O tackled in own endzone
        blk=d[(d.field_goal_result=='blocked')|(d.extra_point_result=='blocked')|(d.punt_blocked==1)].shape[0]
        # return TDs: any TD scored by T while T is not the offense (def TDs) + KR/PR TDs
        td=g[(g.touchdown==1)&(g.td_team==T)]
        dtd=td[(td.posteam!=T)|(td.kickoff_attempt==1)|(td.punt_attempt==1)|(td.field_goal_attempt==1)]
        # exclude offensive TDs: plays where posteam==T and play is pass/run and no turnover
        rtd=0
        for q in td.itertuples():
            off_play = (q.posteam==T) and q.play_type in ('pass','run','qb_kneel','qb_spike') and q.interception!=1 and q.fumble_lost!=1
            if q.play_type=='kickoff':   # posteam = receiving team in nflverse
                off_play=False
            if not off_play: rtd+=1
        pa=fin[O]
        ya=int(o.shape[0]*0) # placeholder
        oo=g[(g.posteam==O)&(g.play_type.isin(['pass','run','qb_kneel','qb_spike']))]
        ya=oo.yards_gained.sum()
        pap=5 if pa==0 else 4 if pa<=6 else 3 if pa<=13 else 1 if pa<=17 else 0 if pa<=27 else -1 if pa<=34 else -3 if pa<=45 else -5
        yap=5 if ya<100 else 3 if ya<200 else 2 if ya<300 else 0 if ya<350 else -1 if ya<400 else -3 if ya<450 else -5 if ya<500 else -6 if ya<550 else -7
        pts=sacks+2*ints+2*fr+2*saf+2*blk+6*rtd+pap+yap
        DST[NICK[T]][int(wk)]=int(pts); DDET[(NICK[T],int(wk))]=dict(sk=int(sacks),int=int(ints),fr=fr,saf=saf,blk=blk,td=rtd,pa=int(pa),ya=int(ya))
import itertools
OWN={1:'Sam',2:'Cliff',3:'Neville',4:'Austin',5:'Martin',6:'Mark',7:'Bob',8:'Lerch'}
E=json.load(open(r'C:\Repositories\Sleeper\pages\data\espn2014.json',encoding='utf-8'))
draft=defaultdict(list)
for p in E['draft']: draft[p['owner']].append((p['player'],p.get('pos')))
POSN={1:"QB",2:"RB",3:"WR",4:"TE",5:"K",16:"DEF"}
boxes={w:json.load(open(ESPN+f'/2014_box_{w}.json')) for w in range(1,14)}
started=defaultdict(set); teamseen=defaultdict(lambda: defaultdict(set))
for w,b in boxes.items():
    for row in b:
        for side in ('home','away'):
            s=row[side]
            for p in s['players']:
                started[w].add(norm(p['name'].replace(' D/ST','')))
                teamseen[s['teamId']][norm(p['name'].replace(' D/ST',''))].add(w)
def score(k,pos,w):
    if pos=='DEF': return DST.get(k,{}).get(w)
    if pos=='K': return KP.get(k,{}).get(w)
    return OFF.get(k,{}).get(w)
NPOS={k:v[1] for k,v in OPOS.items()}
def gaps():
    for w,b in boxes.items():
        for row in b:
            for side in ('home','away'):
                s=row[side]; pl=s['players']
                gap=s['totalPoints']-sum(p['pts'] for p in pl)
                if abs(gap)>0.5 or len(pl)<9:
                    c=defaultdict(int)
                    for p in pl: c[POSN[p['pos']]]+=1
                    miss=[pos for pos in ('QB','K','DEF') if c[pos]<1]
                    need=['RB']*max(0,2-c['RB'])+['WR']*max(0,2-c['WR'])+['TE']*max(0,1-c['TE'])
                    fl=6-(c['RB']+c['WR']+c['TE'])-len(need)
                    yield w,s,round(gap),miss+need+['FLEX']*max(0,fl)

import sys
APPLY='--apply' in sys.argv
PID={v:k for k,v in POSN.items()}
def kd_name(t,w,pid):
    for row in boxes.get(w,[]):
        for side in ('home','away'):
            s=row[side]
            if s['teamId']==t:
                n=[p['name'] for p in s['players'] if p['pos']==pid]
                return n[0] if len(n)==1 else None
    return None
def neighbors(t,w,pid):
    out=set()
    for d in (-1,1):
        n=kd_name(t,w+d,pid)
        if n: out.add(n)
    return out
def key(name): return norm(name.replace(' D/ST',''))
def sc(name,pos,w):
    v=score(key(name) if pos!='DEF' else name.replace(' D/ST',''),pos,w)
    return v
def ctx_pool(t,o):
    pool=set(teamseen[t].keys())|{norm(d[0].replace(' D/ST','')) for d in draft[o]}
    return pool
def skill_cands(t,o,w,slot,r,cur):
    allowed={'FLEX':('RB','WR','TE')}.get(slot,(slot,))
    uni=[k for k in OFF if NPOS.get(k) in allowed and OFF[k].get(w)==r and k not in started[w] and k not in cur] if r!=0 else []
    ctx=[k for k in ctx_pool(t,o) if NPOS.get(k) in allowed and k not in started[w] and k not in cur and (OFF[k].get(w,0)==r)]
    if len(uni)==1: return uni[0],'unique score match league-wide'
    if len(ctx)==1 and r>=5: return ctx[0],'only player from this team\'s roster history with that score'
    return None,None
log=[]
def fill(s,w,t,o,name,pid,pts,why):
    s['players'].append({'name':name,'pos':pid,'pts':float(pts),'ghost':2})
    started[w].add(key(name)); teamseen[t][key(name)].add(w)
    log.append((w,o,name,POSN[pid],pts,why))
# sequence rule: one K (or D/ST) explains a team's whole run of single-slot gaps
for sl in ('K','DEF'):
    byteam=defaultdict(dict)
    for w,s,gap,slots in gaps():
        if slots==[sl]: byteam[s['teamId']][w]=(s,gap)
    for t,wk in byteam.items():
        if len(wk)<4: continue
        src=KP if sl=='K' else DST
        rank=[]
        for k,row in src.items():
            exact=sum(1 for w,(s,g) in wk.items() if row.get(w)==g)
            near=sum(1 for w,(s,g) in wk.items() if row.get(w) is not None and abs(row[w]-g)<=1)
            rank.append((exact,near,k))
        rank.sort(reverse=True)
        (e1,n1,k1),(e2,n2,k2)=rank[0],rank[1]
        if n1==len(wk) and e1>=len(wk)-1 and e2<=len(wk)//2:
            nm=KN[k1][0] if sl=='K' else k1+' D/ST'
            for w,(s,g) in wk.items():
                if key(nm) in started[w]: continue
                fill(s,w,t,OWN[t],nm,PID[sl],g,f'matches the team {sl} score in {e1} of {len(wk)} weeks; no other {sl} matches more than {e2}')
def bracket(t,w,pid):
    a=b=None
    for x in range(w-1,0,-1):
        a=kd_name(t,x,pid)
        if a: break
    for x in range(w+1,14):
        b=kd_name(t,x,pid)
        if b: break
    return a if a and a==b else None
changed=True
while changed:
    changed=False
    for w,s,gap,slots in list(gaps()):
        t=s['teamId']; o=OWN[t]; cur={key(p['name']) for p in s['players']}
        add=[]
        if len(slots)==1:
            sl=slots[0]
            if sl in ('K','DEF'):
                pid=PID[sl]
                if sl=='DEF': c=[k+' D/ST' for k in DST if DST[k].get(w)==gap and norm(k) not in started[w]]
                else: c=[KN[k][0] for k in KP if KP[k].get(w)==gap and k not in started[w]]
                nb=[n for n in neighbors(t,w,pid) if key(n) not in started[w] and sc(n,sl,w) is not None and abs(sc(n,sl,w)-gap)<=(1 if sl=='K' else 0)]
                if len(c)==1: add=[(c[0],pid,gap,'unique score match league-wide')]
                elif len(nb)==1: add=[(nb[0],pid,gap,'same '+sl+' this team started the week before/after')]
            elif sl!='QB':
                k,why=skill_cands(t,o,w,sl,gap,cur)
                if k: add=[(OPOS[k][0],PID[NPOS[k]],gap,why)]
        elif len(slots)==2 and sum(x in ('K','DEF') for x in slots)==1:
            kdsl=[x for x in slots if x in ('K','DEF')][0]; osl=[x for x in slots if x!=kdsl][0]
            sols=[]
            if osl!='QB':
                for n in neighbors(t,w,PID[kdsl]):
                    if key(n) in started[w]: continue
                    v=sc(n,kdsl,w)
                    if v is None or gap-v<0: continue
                    k,why=skill_cands(t,o,w,osl,gap-v,cur)
                    if k: sols.append((n,v,k,why))
            if not sols:
                n=bracket(t,w,PID[kdsl])
                if n and key(n) not in started[w] and sc(n,kdsl,w) is not None and gap-sc(n,kdsl,w)>=0:
                    add=[(n,PID[kdsl],sc(n,kdsl,w),'same '+kdsl+' this team started in the weeks on both sides')]
            if len(sols)==1:
                n,v,k,why=sols[0]
                add=[(n,PID[kdsl],v,'same '+kdsl+' this team started the week before/after'),(OPOS[k][0],PID[NPOS[k]],gap-v,why)]
        for name,pid,pts,why in add:
            s['players'].append({'name':name,'pos':pid,'pts':float(pts),'ghost':2})
            started[w].add(key(name)); teamseen[t][key(name)].add(w)
            log.append((w,o,name,POSN[pid],pts,why)); changed=True
for l in sorted(log): print(l)
print(len(log),'fills')
left=list(gaps())
print(len(left),'lineups still incomplete:')
for w,s,gap,slots in left: print('  wk',w,OWN[s['teamId']],gap,slots)
if APPLY:
    for w,b in boxes.items(): json.dump(b,open(ESPN+f'/2014_box_{w}.json','w',encoding='utf-8'))
    print('applied')

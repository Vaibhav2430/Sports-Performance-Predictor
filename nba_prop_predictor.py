#!/usr/bin/env python3
import sys, os, json, math, time, argparse, datetime
from typing import Optional

for pkg in ["requests","nba_api","tabulate","colorama"]:
    try: __import__(pkg)
    except ImportError: print(f"pip install {pkg}"); sys.exit(1)

import requests
from nba_api.stats.endpoints import playergamelog, commonplayerinfo
from nba_api.stats.static import players as nba_players, teams as nba_teams
from tabulate import tabulate
from colorama import init, Fore, Style
init(autoreset=True)

def green(s):  return Fore.GREEN+str(s)+Style.RESET_ALL
def yellow(s): return Fore.YELLOW+str(s)+Style.RESET_ALL
def red(s):    return Fore.RED+str(s)+Style.RESET_ALL
def bold(s):   return Style.BRIGHT+str(s)+Style.RESET_ALL
def cyan(s):   return Fore.CYAN+str(s)+Style.RESET_ALL
def dim(s):    return Style.DIM+str(s)+Style.RESET_ALL
def ecol(e):   return green if e>=5 else (yellow if e>=1 else red)

def find_player(name):
    all_p = nba_players.get_players()
    nl = name.lower()
    for p in all_p:
        if p["full_name"].lower()==nl: return p
    for p in all_p:
        if nl in p["full_name"].lower(): return p
    last = name.split()[-1].lower()
    m = [p for p in all_p if last in p["full_name"].lower() and p["is_active"]]
    return m[0] if m else None

def get_info(pid):
    try:
        row = commonplayerinfo.CommonPlayerInfo(player_id=pid,timeout=30).get_data_frames()[0].iloc[0]
        return {"team_name":row.get("TEAM_NAME",""),"team_abbr":row.get("TEAM_ABBREVIATION",""),
                "position":row.get("POSITION",""),"jersey":str(row.get("JERSEY","?"))}
    except Exception as e:
        print(f"  Warning: {e}"); return {}

def get_log(pid, season="2024-25", n=20):
    try:
        df = playergamelog.PlayerGameLog(player_id=pid,season=season,timeout=30).get_data_frames()[0]
        games = []
        for _,row in df.head(n).iterrows():
            ms = str(row.get("MIN","0"))
            try: mins = float(ms.split(":")[0]) if ":" in ms else float(ms)
            except: mins = 0.0
            if mins < 5: continue
            games.append({"date":row.get("GAME_DATE",""),"matchup":row.get("MATCHUP",""),
                "min":mins,"pts":float(row.get("PTS",0)),"reb":float(row.get("REB",0)),
                "ast":float(row.get("AST",0)),"stl":float(row.get("STL",0)),
                "blk":float(row.get("BLK",0)),"fg3m":float(row.get("FG3M",0))})
        games.reverse()
        return games
    except Exception as e:
        print(f"  Game log error: {e}"); return []

def get_ctx(games, team_name):
    today = datetime.date.today()
    yday = today - datetime.timedelta(days=1)
    is_b2b = False
    if games:
        try:
            ld = datetime.datetime.strptime(games[-1]["date"],"%b %d, %Y").date()
            is_b2b = (ld==yday)
        except: pass
    opp_name="Unknown"; opp_abbr=""; is_home=True
    if games:
        m = games[-1].get("matchup",""); parts = m.split()
        if len(parts)>=3:
            opp_abbr = parts[-1]
            is_home = "vs." in m
        t = next((t for t in nba_teams.get_teams() if t["abbreviation"]==opp_abbr),None)
        if t: opp_name = t["full_name"]
    rec = games[-10:] if len(games)>=10 else games
    avg_min = sum(g["min"] for g in rec)/len(rec) if rec else 32.0
    return {"game_date":str(today),"is_home":is_home,"is_b2b":is_b2b,
            "opponent_name":opp_name,"opponent_abbreviation":opp_abbr,
            "home_team":team_name if is_home else opp_name,
            "away_team":opp_name if is_home else team_name,"avg_minutes":avg_min}

DEF = {
    "Boston Celtics":108.2,"Oklahoma City Thunder":108.6,"Cleveland Cavaliers":109.1,
    "Minnesota Timberwolves":109.4,"New York Knicks":110.1,"Denver Nuggets":110.5,
    "Los Angeles Lakers":111.0,"Golden State Warriors":111.3,"Milwaukee Bucks":111.7,
    "Phoenix Suns":112.1,"Memphis Grizzlies":112.4,"Indiana Pacers":112.8,
    "Miami Heat":113.2,"Dallas Mavericks":113.5,"Sacramento Kings":113.9,
    "Los Angeles Clippers":114.2,"Philadelphia 76ers":114.6,"Atlanta Hawks":114.9,
    "Brooklyn Nets":115.4,"Toronto Raptors":115.8,"Orlando Magic":116.2,
    "Chicago Bulls":116.5,"Houston Rockets":116.9,"New Orleans Pelicans":117.3,
    "Utah Jazz":117.8,"San Antonio Spurs":118.2,"Charlotte Hornets":118.7,
    "Washington Wizards":119.1,"Portland Trail Blazers":119.6,"Detroit Pistons":120.1}
AVG_DEF = 113.5

def ctx_mult(opp, is_home, is_b2b, stat):
    mult=1.0; d=DEF.get(opp,AVG_DEF); diff=(d-AVG_DEF)/AVG_DEF
    if stat in ("pts","pra","fg3m","pr","pa"):
        mult *= (1+diff*0.6); mult *= 1.025 if is_home else 0.980
    else:
        mult *= (1+diff*0.2); mult *= 1.010 if is_home else 0.992
    if is_b2b: mult *= 0.94
    return mult

EX = {
    "pts": lambda g:g["pts"], "reb":lambda g:g["reb"], "ast":lambda g:g["ast"],
    "stl":lambda g:g["stl"], "blk":lambda g:g["blk"], "fg3m":lambda g:g["fg3m"],
    "pra":lambda g:g["pts"]+g["reb"]+g["ast"],
    "pr": lambda g:g["pts"]+g["reb"], "pa":lambda g:g["pts"]+g["ast"]}
LBL = {"pts":"Points","reb":"Rebounds","ast":"Assists","stl":"Steals","blk":"Blocks",
       "fg3m":"3-Pointers","pra":"Pts+Reb+Ast","pr":"Pts+Reb","pa":"Pts+Ast"}

def wavg(v,d=0.92):
    if not v: return 0.0
    w=[d**(len(v)-1-i) for i in range(len(v))]
    return sum(x*wi for x,wi in zip(v,w))/sum(w)

def sdev(v):
    if len(v)<2: return 1.0
    a=sum(v)/len(v)
    return max(math.sqrt(sum((x-a)**2 for x in v)/(len(v)-1)),0.5)

def ncdf(z): return 0.5*(1+math.erf(z/math.sqrt(2)))
def pover(proj,s,line): return 1-ncdf((line-proj)/s)
def hr(v,line): return sum(1 for x in v if x>line)/len(v) if v else 0.5
def a2i(o): return 100/(o+100) if o>0 else abs(o)/(abs(o)+100)
def edge(mp,ip): return ((mp-ip)/ip)*100 if ip>0 else 0.0
def kelly(mp,o,f=0.25):
    b=o/100 if o>0 else 100/abs(o)
    return max((b*mp-(1-mp))/b*f,0.0)

def build_proj(sk,games,ctx,n=10):
    rec=games[-n:] if len(games)>=n else games
    ex=EX.get(sk)
    if not ex or not rec: return None
    v=[ex(g) for g in rec]; wa=wavg(v); sd2=sdev(v)
    mult=ctx_mult(ctx.get("opponent_name",""),ctx.get("is_home",True),ctx.get("is_b2b",False),sk)
    am=ctx.get("avg_minutes",32.0); pm=ctx.get("projected_minutes",am)
    return {"projection":wa*mult*(pm/am if am>0 else 1),"weighted_avg":wa,
            "sd":sd2,"values":v,"adj_mult":mult,"avg_min":am}

def analyze(prop,pr):
    line=prop["line"]; p=pr["projection"]; sd2=pr["sd"]; v=pr["values"]
    no=pover(p,sd2,line); eo=hr(v,line); bo=no*0.6+eo*0.4; bu=1-bo
    r={**prop,"projection":p,"model_prob_over":bo,"model_prob_under":bu,
       "empirical_hr_over":eo,"best_edge":-999,"best_side":None,"best_odds":None,"best_kelly":0.0}
    edges=[]
    for side,mp,ov,ek in [("Over",bo,prop.get("over_odds"),"edge_over"),
                           ("Under",bu,prop.get("under_odds"),"edge_under")]:
        if ov is not None:
            ip=a2i(ov); e2=edge(mp,ip); k=kelly(mp,ov)
            r[ek]=e2; r[f"implied_{side.lower()}"]=ip; r[f"kelly_{side.lower()}"]=k
            edges.append((side,e2,ov,k))
    if edges:
        best=max(edges,key=lambda x:x[1])
        r["best_side"]=best[0]; r["best_edge"]=best[1]; r["best_odds"]=best[2]; r["best_kelly"]=best[3]
    return r

ODDS_BASE="https://api.the-odds-api.com/v4"
MKT={"pts":["player_points","player_points_alternate"],
     "reb":["player_rebounds","player_rebounds_alternate"],
     "ast":["player_assists","player_assists_alternate"],
     "fg3m":["player_threes"],"stl":["player_steals"],"blk":["player_blocks"],
     "pra":["player_points_rebounds_assists"],
     "pr":["player_points_rebounds"],"pa":["player_points_assists"]}
BOOKS=["draftkings","fanduel","betmgm","caesars","pointsbet"]

def norm(s): return s.lower().replace(".","").replace("-"," ").strip()

def find_event(events, team_name, team_abbr):
    abbr_l = team_abbr.lower()
    keywords = [w for w in team_name.lower().split() if len(w)>3]
    for ev in events:
        ht = ev.get("home_team","").lower()
        at = ev.get("away_team","").lower()
        if abbr_l and (abbr_l in ht or abbr_l in at):
            return ev
        for kw in keywords:
            if kw in ht or kw in at:
                return ev
    return None

def fetch_props(key, player_name, team_name, team_abbr):
    try:
        evs = requests.get(f"{ODDS_BASE}/sports/basketball_nba/events",
                           params={"apiKey":key},timeout=15).json()
        print(f"  {len(evs)} events available")
    except Exception as e:
        print(f"  Events error: {e}"); return []

    ev = find_event(evs, team_name, team_abbr)
    if not ev:
        print(f"  No event found for {team_name}")
        print("  Available: "+(", ".join(f"{e.get('away_team')} @ {e.get('home_team')}" for e in evs)))
        return []

    print(f"  Matched: {ev.get('away_team')} @ {ev.get('home_team')}")
    all_m=list({m for mv in MKT.values() for m in mv})
    pn=norm(player_name); results=[]

    for i in range(0,len(all_m),4):
        batch=all_m[i:i+4]
        try:
            resp=requests.get(f"{ODDS_BASE}/sports/basketball_nba/events/{ev['id']}/odds",
                params={"apiKey":key,"regions":"us","markets":",".join(batch),"oddsFormat":"american"},timeout=15)
            print(f"  Odds API remaining: {resp.headers.get('x-requests-remaining','?')}")
            data=resp.json()
            books=sorted(data.get("bookmakers",[]),key=lambda b:BOOKS.index(b["key"]) if b["key"] in BOOKS else 99)
            for bk in books:
                for mkt in bk.get("markets",[]):
                    sk=next((k for k,v in MKT.items() if mkt.get("key","") in v),None)
                    if not sk: continue
                    for oc in mkt.get("outcomes",[]):
                        if pn not in norm(oc.get("description","")): continue
                        side=oc.get("name","").lower(); pt=oc.get("point"); pr2=oc.get("price")
                        if pt is None or pr2 is None: continue
                        ex2=next((x for x in results if x["stat_key"]==sk and x["line"]==pt),None)
                        if ex2 is None:
                            ex2={"stat_key":sk,"line":pt,"over_odds":None,"under_odds":None,"book":bk.get("title",bk["key"])}
                            results.append(ex2)
                        if "over" in side: ex2["over_odds"]=pr2
                        elif "under" in side: ex2["under_odds"]=pr2
            time.sleep(0.4)
        except Exception as e:
            print(f"  Batch error: {e}")
    return [x for x in results if x["over_odds"] is not None or x["under_odds"] is not None]

def synth(projs):
    return [{"stat_key":sk,"line":round(p["projection"]-0.5,1),
             "over_odds":-110,"under_odds":-110,"book":"synthetic"}
            for sk,p in projs.items() if p]

def run(player_name, odds_key, n_games, proj_min):
    print(); print("="*62); print(f"  NBA PROP PREDICTOR — {player_name.upper()}"); print("="*62)
    print(f"\nSearching: {player_name}")
    pl=find_player(player_name)
    if not pl: print("  Not found."); sys.exit(1)
    pid=pl["id"]; fname=pl["full_name"]
    info=get_info(pid); tname=info.get("team_name","?"); tabbr=info.get("team_abbr","")
    print(f"  Found: {fname} — {tname} #{info.get('jersey','?')} | {info.get('position','?')}")
    print(f"\nGame log (2024-25)...")
    games=get_log(pid,"2024-25",max(n_games+5,20))
    if not games: print("  Trying 2023-24..."); games=get_log(pid,"2023-24",max(n_games+5,20))
    if not games: print("  No stats."); sys.exit(1)
    print(f"  {len(games)} games — using last {min(n_games,len(games))}")
    print("\nContext...")
    ctx=get_ctx(games,tname)
    if proj_min: ctx["projected_minutes"]=proj_min
    opp=ctx.get("opponent_name","?"); od=DEF.get(opp,AVG_DEF)
    dr="Elite" if od<110 else "Above avg" if od<112 else "Average" if od<115 else "Weak"
    loc="Home" if ctx.get("is_home") else "Away"
    b2b="YES" if ctx.get("is_b2b") else "No"
    print(f"  Date:              {ctx.get('game_date','TBD')}")
    print(f"  Matchup:           {ctx.get('away_team','?')} @ {ctx.get('home_team','?')}")
    print(f"  Location:          {loc}")
    print(f"  Back-to-back:      {b2b}")
    print(f"  Opponent:          {opp}  (DefRtg {od:.1f} — {dr})")
    print(f"  Avg minutes (L10): {ctx.get('avg_minutes',0):.1f} min")
    print("\nProjections...")
    projs={sk:build_proj(sk,games,ctx,n_games) for sk in EX}
    rows=[[LBL.get(sk,sk),f"{p['projection']:.1f}",f"{p['weighted_avg']:.1f}",
           f"+-{p['sd']:.1f}",f"{(p['adj_mult']-1)*100:+.1f}%"]
          for sk,p in projs.items() if p]
    print(tabulate(rows,headers=["Stat","Projection","Wtd Avg","Std Dev","Adj"],tablefmt="simple",stralign="left"))
    if odds_key:
        print("\nSportsbook lines...")
        props=fetch_props(odds_key,fname,tname,tabbr)
        if not props: print("  No props found — using synthetic."); props=synth(projs)
        else: print(f"  {len(props)} lines found")
    else:
        print("\nNo --odds-key — using synthetic lines.")
        props=synth(projs)
    analyzed=[analyze(p,projs[p["stat_key"]]) for p in props if projs.get(p["stat_key"])]
    # Keep only the single best line per stat (avoid alternate line noise)
    seen_stats = {}
    deduped = []
    for a in sorted(analyzed, key=lambda x: abs(x["projection"] - x["line"])):
        sk = a["stat_key"]
        if sk not in seen_stats:
            seen_stats[sk] = a
            deduped.append(a)
    # Also add any alternate lines that are clearly better edge on main stats
    analyzed = deduped
    analyzed.sort(key=lambda x:x["best_edge"],reverse=True)
    print("\nPROP LINES — RANKED BY EDGE")
    rows2=[]
    for i,r in enumerate(analyzed,1):
        e2=r["best_edge"]
        badge="STRONG" if e2>=8 else "VALUE" if e2>=5 else "MARGINAL" if e2>=1 else "AVOID"
        o=r["best_odds"]; os2=f"+{int(o)}" if o and o>0 else str(int(o)) if o else "N/A"
        mp=r["model_prob_over"] if r["best_side"]=="Over" else r["model_prob_under"]
        rows2.append([f"#{i}",f"{LBL.get(r['stat_key'],r['stat_key'])} {r['best_side']} {r['line']}",
            f"{r['projection']:.1f}",f"{e2:+.1f}%",badge,f"{mp*100:.0f}%",os2,f"{r['best_kelly']*100:.1f}%",r.get("book","")])
    print(tabulate(rows2,headers=["Rank","Prop","Proj","Edge","Signal","Model P","Odds","Kelly%","Book"],tablefmt="simple",stralign="left"))
    print("\nTOP 3 DETAILED")
    for r in analyzed[:3]:
        p2=projs.get(r["stat_key"]); lbl=LBL.get(r["stat_key"],r["stat_key"])
        print(f"\n  {lbl} {r['best_side']} {r['line']}")
        print(f"  Projection:            {r['projection']:.1f}")
        print(f"  Model P(over):         {r['model_prob_over']*100:.1f}%")
        ng=len(p2["values"]) if p2 else "?"
        print(f"  Empirical HR (over):   {r['empirical_hr_over']*100:.0f}%  ({ng} games)")
        print(f"  Edge:                  {r['best_edge']:+.1f}%")
        if r.get("edge_over") is not None:
            print(f"  Over  model vs implied: {r['model_prob_over']*100:.1f}% vs {r.get('implied_over',0)*100:.1f}%  ({r['edge_over']:+.1f}%)")
        if r.get("edge_under") is not None:
            print(f"  Under model vs implied: {r['model_prob_under']*100:.1f}% vs {r.get('implied_under',0)*100:.1f}%  ({r['edge_under']:+.1f}%)")
        if p2:
            v=p2["values"]
            print(f"  Last 5:                {', '.join(f'{x:.0f}' for x in v[-5:])}")
            print(f"  Above line:            {sum(1 for x in v if x>r['line'])}/{len(v)}")
    print("\nFor research use only. Gamble responsibly."); print()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("player")
    ap.add_argument("--odds-key",default=os.environ.get("ODDS_API_KEY"))
    ap.add_argument("--games",type=int,default=10)
    ap.add_argument("--minutes",type=float,default=None)
    a=ap.parse_args()
    run(a.player,a.odds_key,a.games,a.minutes)

if __name__=="__main__": main()

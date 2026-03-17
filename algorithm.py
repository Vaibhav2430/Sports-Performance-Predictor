#!/usr/bin/env python3
"""
NBA Prop Predictor
==================
Automatically fetches player stats and sportsbook lines,
then ranks prop bets by model edge.

APIs used:
  - nba_api (free, no key) — player stats, game logs from NBA.com
  - The Odds API (the-odds-api.com) — live sportsbook prop lines
    Free tier: 500 requests/month — get a key at https://the-odds-api.com

Usage:
    python nba_prop_predictor.py "LeBron James"
    python nba_prop_predictor.py "LeBron James" --odds-key YOUR_KEY
    python nba_prop_predictor.py "Stephen Curry" --games 15
    python nba_prop_predictor.py "LeBron James" --minutes 34.0
"""

import sys
import os
import json
import math
import time
import argparse
import datetime
from typing import Optional

for pkg, imp in [("requests","requests"),("nba_api","nba_api"),("tabulate","tabulate"),("colorama","colorama")]:
    try:
        __import__(imp)
    except ImportError:
        print(f"Missing dependency: pip install {pkg}")
        sys.exit(1)

import requests
from nba_api.stats.endpoints import playergamelog, commonplayerinfo
from nba_api.stats.static import players as nba_players, teams as nba_teams
from tabulate import tabulate
from colorama import init, Fore, Style
init(autoreset=True)

def green(s):  return Fore.GREEN  + str(s) + Style.RESET_ALL
def yellow(s): return Fore.YELLOW + str(s) + Style.RESET_ALL
def red(s):    return Fore.RED    + str(s) + Style.RESET_ALL
def bold(s):   return Style.BRIGHT + str(s) + Style.RESET_ALL
def cyan(s):   return Fore.CYAN   + str(s) + Style.RESET_ALL
def dim(s):    return Style.DIM   + str(s) + Style.RESET_ALL
def edge_color(e): return green if e >= 5 else yellow if e >= 1 else red


# ── NBA API ──────────────────────────────────────────────────

def find_player(name: str) -> Optional[dict]:
    all_p = nba_players.get_players()
    nl = name.lower()
    for p in all_p:
        if p["full_name"].lower() == nl:
            return p
    for p in all_p:
        if nl in p["full_name"].lower():
            return p
    last = name.split()[-1].lower()
    matches = [p for p in all_p if last in p["full_name"].lower() and p["is_active"]]
    return matches[0] if matches else None


def get_player_info(player_id: int) -> dict:
    try:
        info = commonplayerinfo.CommonPlayerInfo(player_id=player_id, timeout=30)
        row = info.get_data_frames()[0].iloc[0]
        return {
            "team_id":   int(row.get("TEAM_ID", 0)),
            "team_name": row.get("TEAM_NAME", ""),
            "team_abbr": row.get("TEAM_ABBREVIATION", ""),
            "position":  row.get("POSITION", ""),
            "jersey":    str(row.get("JERSEY", "?")),
        }
    except Exception as e:
        print(yellow(f"  Warning: {e}"))
        return {}


def get_game_log(player_id: int, season: str = "2024-25", last_n: int = 20) -> list:
    try:
        log = playergamelog.PlayerGameLog(player_id=player_id, season=season, timeout=30)
        df = log.get_data_frames()[0]
        games = []
        for _, row in df.head(last_n).iterrows():
            ms = str(row.get("MIN", "0"))
            try:
                mins = float(ms.split(":")[0]) if ":" in ms else float(ms)
            except Exception:
                mins = 0.0
            if mins < 5:
                continue
            games.append({
                "date":    row.get("GAME_DATE", ""),
                "matchup": row.get("MATCHUP", ""),
                "min":     mins,
                "pts":     float(row.get("PTS", 0)),
                "reb":     float(row.get("REB", 0)),
                "ast":     float(row.get("AST", 0)),
                "stl":     float(row.get("STL", 0)),
                "blk":     float(row.get("BLK", 0)),
                "fg3m":    float(row.get("FG3M", 0)),
            })
        games.reverse()
        return games
    except Exception as e:
        print(yellow(f"  Warning fetching game log: {e}"))
        return []


def get_context(games: list, team_name: str) -> dict:
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    is_b2b = False
    if games:
        try:
            last_date = datetime.datetime.strptime(games[-1]["date"], "%b %d, %Y").date()
            is_b2b = (last_date == yesterday)
        except Exception:
            pass

    opponent_name = "Unknown"
    opp_abbr = ""
    is_home = True
    if games:
        matchup = games[-1].get("matchup", "")
        parts = matchup.split()
        if len(parts) >= 3:
            opp_abbr = parts[-1]
            is_home = "vs." in matchup
        all_t = nba_teams.get_teams()
        opp_team = next((t for t in all_t if t["abbreviation"] == opp_abbr), None)
        if opp_team:
            opponent_name = opp_team["full_name"]

    recent = games[-10:] if len(games) >= 10 else games
    avg_min = sum(g["min"] for g in recent) / len(recent) if recent else 32.0

    return {
        "game_date":    str(today),
        "is_home":      is_home,
        "is_b2b":       is_b2b,
        "opponent_name": opponent_name,
        "opponent_abbreviation": opp_abbr,
        "home_team":    team_name if is_home else opponent_name,
        "away_team":    opponent_name if is_home else team_name,
        "avg_minutes":  avg_min,
    }


# ── Defensive ratings (2024-25) ──────────────────────────────

TEAM_DEF_RATINGS = {
    "Boston Celtics": 108.2,         "Oklahoma City Thunder": 108.6,
    "Cleveland Cavaliers": 109.1,    "Minnesota Timberwolves": 109.4,
    "New York Knicks": 110.1,        "Denver Nuggets": 110.5,
    "Los Angeles Lakers": 111.0,     "Golden State Warriors": 111.3,
    "Milwaukee Bucks": 111.7,        "Phoenix Suns": 112.1,
    "Memphis Grizzlies": 112.4,      "Indiana Pacers": 112.8,
    "Miami Heat": 113.2,             "Dallas Mavericks": 113.5,
    "Sacramento Kings": 113.9,       "Los Angeles Clippers": 114.2,
    "Philadelphia 76ers": 114.6,     "Atlanta Hawks": 114.9,
    "Brooklyn Nets": 115.4,          "Toronto Raptors": 115.8,
    "Orlando Magic": 116.2,          "Chicago Bulls": 116.5,
    "Houston Rockets": 116.9,        "New Orleans Pelicans": 117.3,
    "Utah Jazz": 117.8,              "San Antonio Spurs": 118.2,
    "Charlotte Hornets": 118.7,      "Washington Wizards": 119.1,
    "Portland Trail Blazers": 119.6, "Detroit Pistons": 120.1,
}
LEAGUE_AVG_DEF = 113.5


def context_multiplier(opponent: str, is_home: bool, is_b2b: bool, stat: str) -> float:
    mult = 1.0
    opp_def = TEAM_DEF_RATINGS.get(opponent, LEAGUE_AVG_DEF)
    def_diff = (opp_def - LEAGUE_AVG_DEF) / LEAGUE_AVG_DEF
    if stat in ("pts", "pra", "fg3m", "pr", "pa"):
        mult *= (1 + def_diff * 0.6)
        mult *= 1.025 if is_home else 0.980
    else:
        mult *= (1 + def_diff * 0.2)
        mult *= 1.010 if is_home else 0.992
    if is_b2b:
        mult *= 0.94
    return mult


# ── Statistical model ────────────────────────────────────────

STAT_EXTRACTORS = {
    "pts":  lambda g: g["pts"],
    "reb":  lambda g: g["reb"],
    "ast":  lambda g: g["ast"],
    "stl":  lambda g: g["stl"],
    "blk":  lambda g: g["blk"],
    "fg3m": lambda g: g["fg3m"],
    "pra":  lambda g: g["pts"] + g["reb"] + g["ast"],
    "pr":   lambda g: g["pts"] + g["reb"],
    "pa":   lambda g: g["pts"] + g["ast"],
}

STAT_LABELS = {
    "pts": "Points", "reb": "Rebounds", "ast": "Assists",
    "stl": "Steals", "blk": "Blocks",   "fg3m": "3-Pointers",
    "pra": "Pts+Reb+Ast", "pr": "Pts+Reb", "pa": "Pts+Ast",
}


def weighted_avg(values: list, decay: float = 0.92) -> float:
    if not values:
        return 0.0
    w = [decay ** (len(values) - 1 - i) for i in range(len(values))]
    tw = sum(w)
    return sum(v * wi for v, wi in zip(values, w)) / tw


def std_dev(values: list) -> float:
    if len(values) < 2:
        return 1.0
    avg = sum(values) / len(values)
    return max(math.sqrt(sum((v - avg) ** 2 for v in values) / (len(values) - 1)), 0.5)


def normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def prob_over(proj: float, sd: float, line: float) -> float:
    return 1 - normal_cdf((line - proj) / sd)


def hit_rate(values: list, line: float) -> float:
    if not values:
        return 0.5
    return sum(1 for v in values if v > line) / len(values)


def american_to_implied(odds: float) -> float:
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)


def compute_edge(model_p: float, implied_p: float) -> float:
    return ((model_p - implied_p) / implied_p) * 100 if implied_p > 0 else 0.0


def kelly_fraction(model_p: float, odds: float, fraction: float = 0.25) -> float:
    b = odds / 100 if odds > 0 else 100 / abs(odds)
    k = (b * model_p - (1 - model_p)) / b
    return max(k * fraction, 0.0)


def build_projection(stat_key: str, games: list, context: dict, n: int = 10) -> Optional[dict]:
    recent = games[-n:] if len(games) >= n else games
    extractor = STAT_EXTRACTORS.get(stat_key)
    if not extractor or not recent:
        return None
    values = [extractor(g) for g in recent]
    w_avg = weighted_avg(values)
    sd = std_dev(values)
    mult = context_multiplier(
        context.get("opponent_name", ""),
        context.get("is_home", True),
        context.get("is_b2b", False),
        stat_key,
    )
    avg_min = context.get("avg_minutes", 32.0)
    proj_min = context.get("projected_minutes", avg_min)
    min_mult = proj_min / avg_min if avg_min > 0 else 1.0
    return {
        "projection":   w_avg * mult * min_mult,
        "weighted_avg": w_avg,
        "sd":           sd,
        "values":       values,
        "adj_mult":     mult,
        "avg_min":      avg_min,
    }


def analyze_prop(prop: dict, proj: dict) -> dict:
    line = prop["line"]
    norm_over = prob_over(proj["projection"], proj["sd"], line)
    emp_over  = hit_rate(proj["values"], line)
    b_over  = norm_over * 0.6 + emp_over * 0.4
    b_under = 1 - b_over

    result = {
        **prop,
        "projection":        proj["projection"],
        "model_prob_over":   b_over,
        "model_prob_under":  b_under,
        "empirical_hr_over": emp_over,
        "best_edge": -999, "best_side": None,
        "best_odds": None, "best_kelly": 0.0,
    }

    edges = []
    for side, mp, odds_val, ekey in [
        ("Over",  b_over,  prop.get("over_odds"),  "edge_over"),
        ("Under", b_under, prop.get("under_odds"), "edge_under"),
    ]:
        if odds_val is not None:
            imp  = american_to_implied(odds_val)
            edge = compute_edge(mp, imp)
            k    = kelly_fraction(mp, odds_val)
            result[ekey] = edge
            result[f"implied_{side.lower()}"] = imp
            result[f"kelly_{side.lower()}"]   = k
            edges.append((side, edge, odds_val, k))

    if edges:
        best = max(edges, key=lambda x: x[1])
        result["best_side"]  = best[0]
        result["best_edge"]  = best[1]
        result["best_odds"]  = best[2]
        result["best_kelly"] = best[3]

    return result


# ── The Odds API ─────────────────────────────────────────────

ODDS_BASE = "https://api.the-odds-api.com/v4"

STAT_MARKET_MAP = {
    "pts":  ["player_points", "player_points_alternate"],
    "reb":  ["player_rebounds", "player_rebounds_alternate"],
    "ast":  ["player_assists", "player_assists_alternate"],
    "fg3m": ["player_threes"],
    "stl":  ["player_steals"],
    "blk":  ["player_blocks"],
    "pra":  ["player_points_rebounds_assists"],
    "pr":   ["player_points_rebounds"],
    "pa":   ["player_points_assists"],
}

PRIORITY_BOOKS = ["draftkings", "fanduel", "betmgm", "caesars", "pointsbet"]


def odds_get(endpoint: str, api_key: str, params: dict = None):
    p = {"apiKey": api_key}
    if params:
        p.update(params)
    r = requests.get(ODDS_BASE + endpoint, params=p, timeout=15)
    rem = r.headers.get("x-requests-remaining", "?")
    print(dim(f"  Odds API requests remaining: {rem}"))
    r.raise_for_status()
    return r.json()


def normalize(name: str) -> str:
    return name.lower().replace(".", "").replace("-", " ").strip()


def fetch_props(api_key: str, player_name: str, team_abbr: str) -> list:
    try:
        events = odds_get("/sports/basketball_nba/events", api_key)
    except Exception as e:
        print(yellow(f"  Could not fetch events: {e}"))
        return []

    event = None
    abbr_l = team_abbr.lower()
    for ev in events:
        if abbr_l in json.dumps(ev).lower():
            event = ev
            break

    if not event:
        print(yellow("  Could not match team to an upcoming odds event."))
        return []

    print(dim(f"  Matched: {event.get('home_team')} vs {event.get('away_team')}"))

    all_markets = list({m for mv in STAT_MARKET_MAP.values() for m in mv})
    player_norm = normalize(player_name)
    results = []

    for i in range(0, len(all_markets), 4):
        batch = all_markets[i:i+4]
        try:
            data = odds_get(
                f"/sports/basketball_nba/events/{event['id']}/odds",
                api_key,
                {"regions": "us", "markets": ",".join(batch), "oddsFormat": "american"},
            )
            books = sorted(data.get("bookmakers", []),
                           key=lambda b: PRIORITY_BOOKS.index(b["key"])
                           if b["key"] in PRIORITY_BOOKS else 99)
            for book in books:
                for market in book.get("markets", []):
                    mkey = market.get("key", "")
                    stat_key = next((k for k, v in STAT_MARKET_MAP.items() if mkey in v), None)
                    if not stat_key:
                        continue
                    for outcome in market.get("outcomes", []):
                        if player_norm not in normalize(outcome.get("description", "")):
                            continue
                        side  = outcome.get("name", "").lower()
                        point = outcome.get("point")
                        price = outcome.get("price")
                        if point is None or price is None:
                            continue
                        ex = next((r for r in results
                                   if r["stat_key"] == stat_key and r["line"] == point), None)
                        if ex is None:
                            ex = {"stat_key": stat_key, "line": point,
                                  "over_odds": None, "under_odds": None,
                                  "book": book.get("title", book["key"])}
                            results.append(ex)
                        if "over" in side:
                            ex["over_odds"] = price
                        elif "under" in side:
                            ex["under_odds"] = price
            time.sleep(0.4)
        except Exception as e:
            print(yellow(f"  Skipping batch: {e}"))

    return [r for r in results if r["over_odds"] is not None or r["under_odds"] is not None]


def synthetic_props(projections: dict) -> list:
    props = []
    for sk, proj in projections.items():
        if proj is None:
            continue
        line = round(proj["projection"] - 0.5, 1)
        props.append({"stat_key": sk, "line": line,
                      "over_odds": -110, "under_odds": -110, "book": "synthetic"})
    return props


# ── Display ──────────────────────────────────────────────────

def print_header(name: str):
    print()
    print(bold("=" * 62))
    print(bold(f"  NBA PROP PREDICTOR — {name.upper()}"))
    print(bold("=" * 62))


def print_context(ctx: dict):
    if not ctx:
        return
    print()
    print(bold("  NEXT GAME CONTEXT"))
    matchup = f"{ctx.get('away_team','?')} @ {ctx.get('home_team','?')}"
    opp = ctx.get("opponent_name", "?")
    opp_def = TEAM_DEF_RATINGS.get(opp, LEAGUE_AVG_DEF)
    def_rank = ("Elite" if opp_def < 110 else "Above avg" if opp_def < 112
                else "Average" if opp_def < 115 else "Weak")
    b2b = red("YES ⚠") if ctx.get("is_b2b") else "No"
    loc = "Home" if ctx.get("is_home") else "Away"
    print(f"  {'Date':<24} {ctx.get('game_date','TBD')}")
    print(f"  {'Matchup':<24} {matchup}")
    print(f"  {'Location':<24} {loc}")
    print(f"  {'Back-to-back':<24} {b2b}")
    print(f"  {'Opponent':<24} {opp}  (DefRtg {opp_def:.1f} — {def_rank})")
    print(f"  {'Avg minutes (L10)':<24} {ctx.get('avg_minutes',0):.1f} min")


def print_projections(projections: dict):
    print()
    print(bold("  STAT PROJECTIONS (context-adjusted)"))
    rows = []
    for sk, proj in projections.items():
        if proj is None:
            continue
        pct = (proj["adj_mult"] - 1) * 100
        rows.append([
            STAT_LABELS.get(sk, sk),
            f"{proj['projection']:.1f}",
            f"{proj['weighted_avg']:.1f}",
            f"±{proj['sd']:.1f}",
            f"{pct:+.1f}%",
        ])
    print(tabulate(rows, headers=["Stat","Projection","Wtd Avg","Std Dev","Context Adj"],
                   tablefmt="simple", stralign="left"))


def print_results(analyzed: list):
    print()
    print(bold("  PROP LINES — RANKED BY EDGE"))
    print(dim("  Positive edge = model prob exceeds book's implied prob\n"))
    if not analyzed:
        print(yellow("  No prop lines to display."))
        return

    ranked = sorted(analyzed, key=lambda x: x["best_edge"], reverse=True)
    rows = []
    for i, r in enumerate(ranked, 1):
        edge = r["best_edge"]
        col  = edge_color(edge)
        badge = ("🔥 STRONG" if edge >= 8 else "✅ VALUE" if edge >= 5
                 else "⚠  MARGINAL" if edge >= 1 else "❌ AVOID")
        odds = r["best_odds"]
        odds_str = (f"+{int(odds)}" if odds and odds > 0 else str(int(odds)) if odds else "N/A")
        mp = r["model_prob_over"] if r["best_side"] == "Over" else r["model_prob_under"]
        rows.append([
            f"#{i}",
            f"{STAT_LABELS.get(r['stat_key'],r['stat_key'])} {r['best_side']} {r['line']}",
            f"{r['projection']:.1f}",
            col(f"{edge:+.1f}%"),
            col(badge),
            f"{mp*100:.0f}%",
            odds_str,
            f"{r['best_kelly']*100:.1f}%",
            r.get("book",""),
        ])
    print(tabulate(rows,
        headers=["Rank","Prop","Proj","Edge","Signal","Model P","Odds","Kelly%","Book"],
        tablefmt="simple", stralign="left"))
    print()
    print(dim("  Kelly% = suggested bet size as % of bankroll (quarter-Kelly)."))
    print(dim("  Model = normal distribution (60%) + empirical hit rate (40%)."))


def print_deep_dive(analyzed: list, projections: dict):
    ranked = sorted(analyzed, key=lambda x: x["best_edge"], reverse=True)[:3]
    print()
    print(bold("  DETAILED BREAKDOWN — TOP 3 PICKS"))
    for r in ranked:
        proj = projections.get(r["stat_key"])
        col  = edge_color(r["best_edge"])
        label = STAT_LABELS.get(r["stat_key"], r["stat_key"])
        print()
        print(f"  {bold(label + ' ' + r['best_side'] + ' ' + str(r['line']))}")
        print(f"  {'Projection':<32} {r['projection']:.1f}")
        print(f"  {'Model P(over)':<32} {r['model_prob_over']*100:.1f}%")
        n_g = len(proj["values"]) if proj else "?"
        print(f"  {'Empirical HR over line':<32} {r['empirical_hr_over']*100:.0f}%  (last {n_g} games)")
        edge_str = f"{r['best_edge']:+.1f}%"
        print(f"  {'Edge':<32} {col(edge_str)}")
        if r.get("edge_over") is not None:
            print(f"  {'Over  model vs implied':<32} {r['model_prob_over']*100:.1f}% vs {r.get('implied_over',0)*100:.1f}%  ({r['edge_over']:+.1f}%)")
        if r.get("edge_under") is not None:
            print(f"  {'Under model vs implied':<32} {r['model_prob_under']*100:.1f}% vs {r.get('implied_under',0)*100:.1f}%  ({r['edge_under']:+.1f}%)")
        if proj:
            vals = proj["values"]
            recent_str = ", ".join(f"{v:.0f}" for v in vals[-5:])
            above = sum(1 for v in vals if v > r["line"])
            print(f"  {'Last 5 games':<32} {recent_str}")
            print(f"  {'Above line in sample':<32} {above}/{len(vals)} games")


# ── Entry point ──────────────────────────────────────────────

def run(player_name: str, odds_key: Optional[str], n_games: int, projected_minutes: Optional[float]):
    print_header(player_name)

    print(f"\n{cyan('Searching for player:')} {player_name}")
    player = find_player(player_name)
    if not player:
        print(red(f"  Player '{player_name}' not found."))
        sys.exit(1)

    player_id = player["id"]
    full_name  = player["full_name"]
    print(dim("  Fetching player info…"))
    info = get_player_info(player_id)
    team_name = info.get("team_name", "Unknown")
    team_abbr = info.get("team_abbr", "")
    print(f"  Found: {bold(full_name)} — {team_name} #{info.get('jersey','?')} | {info.get('position','?')}")

    season = "2024-25"
    print(f"\n{cyan('Fetching game log')} ({season})…")
    games = get_game_log(player_id, season=season, last_n=max(n_games + 5, 20))
    if not games:
        print(yellow("  No 2024-25 data, trying 2023-24…"))
        games = get_game_log(player_id, season="2023-24", last_n=max(n_games + 5, 20))
    if not games:
        print(red("  No game stats found."))
        sys.exit(1)
    print(f"  {len(games)} games loaded — using last {min(n_games, len(games))}")

    print(f"\n{cyan('Analyzing game context…')}")
    context = get_context(games, team_name)
    if projected_minutes:
        context["projected_minutes"] = projected_minutes
    print_context(context)

    print(f"\n{cyan('Building projections…')}")
    projections = {sk: build_projection(sk, games, context, n=n_games) for sk in STAT_EXTRACTORS}
    print_projections(projections)

    if odds_key:
        print(f"\n{cyan('Fetching sportsbook lines…')}")
        props = fetch_props(odds_key, full_name, team_abbr)
        if not props:
            print(yellow("  No props found — using synthetic lines."))
            props = synthetic_props(projections)
        else:
            print(f"  Found {len(props)} prop lines")
    else:
        print(f"\n{yellow('No --odds-key — using synthetic lines.')}")
        print(dim("  Get a free key at https://the-odds-api.com for real sportsbook lines.\n"))
        props = synthetic_props(projections)

    analyzed = [analyze_prop(p, projections[p["stat_key"]])
                for p in props if projections.get(p["stat_key"])]

    print_results(analyzed)
    if analyzed:
        print_deep_dive(analyzed, projections)

    print()
    print(dim("  " + "─" * 30))
    print(dim("  For informational/research use only. Gamble responsibly."))
    print()


def main():
    parser = argparse.ArgumentParser(description="NBA Prop Predictor", epilog=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("player", help="Player name e.g. 'LeBron James'")
    parser.add_argument("--odds-key", default=os.environ.get("ODDS_API_KEY"),
                        help="The Odds API key (free at the-odds-api.com)")
    parser.add_argument("--games", type=int, default=10,
                        help="Recent games to use (default: 10)")
    parser.add_argument("--minutes", type=float, default=None,
                        help="Override projected minutes e.g. 34.0")
    args = parser.parse_args()
    run(player_name=args.player, odds_key=args.odds_key,
        n_games=args.games, projected_minutes=args.minutes)


if __name__ == "__main__":
    main()
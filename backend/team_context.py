"""Cache current-season per-game averages for NBA and WNBA players/teams.

Used to estimate the scoring impact of an injured-out teammate so the model
can apply an opportunity boost to remaining players.
"""
import time
import requests
from difflib import get_close_matches

_nba_players: dict = {}   # lower_name -> {ppg, apg, rpg, team_abbr}
_nba_teams:   dict = {}   # team_abbr  -> ppg
_nba_ts:      float = 0.0

_wnba_players: dict = {}  # lower_name -> {ppg, apg, rpg, team_abbr}
_wnba_ts:      float = 0.0

TTL = 3600 * 6  # refresh every 6 hours


# ── NBA ──────────────────────────────────────────────────────────────────────

def _load_nba() -> None:
    global _nba_players, _nba_teams, _nba_ts
    if time.time() - _nba_ts < TTL and _nba_players:
        return
    try:
        from nba_api.stats.endpoints import leaguedashplayerstats, leaguedashteamstats
        from model import CURRENT_SEASON

        time.sleep(0.6)
        pdf = leaguedashplayerstats.LeagueDashPlayerStats(
            season=CURRENT_SEASON,
            per_mode_detailed="PerGame",
        ).get_data_frames()[0]
        _nba_players = {
            str(row["PLAYER_NAME"]).lower(): {
                "ppg": float(row["PTS"]),
                "apg": float(row["AST"]),
                "rpg": float(row["REB"]),
                "team_abbr": str(row["TEAM_ABBREVIATION"]),
            }
            for _, row in pdf.iterrows()
            if row["PLAYER_NAME"] and int(row.get("GP", 0)) >= 5
        }

        time.sleep(0.6)
        tdf = leaguedashteamstats.LeagueDashTeamStats(
            season=CURRENT_SEASON,
            per_mode_detailed="PerGame",
        ).get_data_frames()[0]
        _nba_teams = {
            str(row["TEAM_ABBREVIATION"]): float(row["PTS"])
            for _, row in tdf.iterrows()
        }
        _nba_ts = time.time()
    except Exception:
        pass


def get_nba_player_ppg(name: str) -> float | None:
    _load_nba()
    nl = name.lower().strip()
    if nl in _nba_players:
        return _nba_players[nl]["ppg"]
    matches = get_close_matches(nl, _nba_players.keys(), n=1, cutoff=0.80)
    return _nba_players[matches[0]]["ppg"] if matches else None


def get_nba_team_ppg(team_abbr: str) -> float:
    _load_nba()
    return _nba_teams.get(team_abbr, 112.0)  # league-average fallback


# ── WNBA ─────────────────────────────────────────────────────────────────────

def _load_wnba() -> None:
    """Load WNBA player averages from ESPN statistics leaders (single request)."""
    global _wnba_players, _wnba_ts
    if time.time() - _wnba_ts < TTL and _wnba_players:
        return
    try:
        r = requests.get(
            "https://site.web.api.espn.com/apis/site/v2/sports/basketball/wnba/statistics",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        r.raise_for_status()
        cats = r.json().get("stats", {}).get("categories", [])

        stat_lookup: dict[str, dict] = {}
        for cat in cats:
            cname = cat.get("name", "")
            if cname not in ("pointsPerGame", "assistsPerGame", "reboundsPerGame"):
                continue
            for leader in cat.get("leaders", []):
                athlete = leader.get("athlete", {})
                name    = athlete.get("displayName", "").strip()
                team    = leader.get("team", {}).get("abbreviation", "")
                val     = float(leader.get("value", 0) or 0)
                nl      = name.lower()
                if nl not in stat_lookup:
                    stat_lookup[nl] = {"team_abbr": team}
                stat_lookup[nl][cname] = val

        _wnba_players = {
            nl: {
                "ppg": vals.get("pointsPerGame", 0.0),
                "apg": vals.get("assistsPerGame", 0.0),
                "rpg": vals.get("reboundsPerGame", 0.0),
                "team_abbr": vals.get("team_abbr", ""),
            }
            for nl, vals in stat_lookup.items()
        }
        _wnba_ts = time.time()
    except Exception:
        pass


def get_wnba_player_ppg(name: str) -> float | None:
    _load_wnba()
    nl = name.lower().strip()
    if nl in _wnba_players:
        return _wnba_players[nl]["ppg"]
    matches = get_close_matches(nl, _wnba_players.keys(), n=1, cutoff=0.80)
    return _wnba_players[matches[0]]["ppg"] if matches else None


def get_wnba_team_ppg(team_abbr: str) -> float:
    # Leaders endpoint only covers top-50 per stat so team totals are incomplete;
    # use the 2026 WNBA league-average team score as a reliable fallback.
    return 82.0


# ── Unified helpers ───────────────────────────────────────────────────────────

def get_player_ppg(name: str, league: str) -> float | None:
    if league == "NBA":
        return get_nba_player_ppg(name)
    return get_wnba_player_ppg(name)


def get_team_ppg(team_abbr: str, league: str) -> float:
    if league == "NBA":
        return get_nba_team_ppg(team_abbr)
    return get_wnba_team_ppg(team_abbr)

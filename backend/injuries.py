import time
import difflib
import requests

CACHE_TTL = 1800  # 30 minutes

_cache: dict = {}
_wnba_team_id_to_abbr: dict = {}  # ESPN numeric team ID (str) -> abbreviation


def _get_wnba_team_id_map() -> dict:
    """Build a one-time map of ESPN WNBA team ID -> abbreviation."""
    global _wnba_team_id_to_abbr
    if _wnba_team_id_to_abbr:
        return _wnba_team_id_to_abbr
    try:
        r = requests.get(
            "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/teams",
            timeout=8, headers={"User-Agent": "Mozilla/5.0"},
        )
        teams = r.json()["sports"][0]["leagues"][0]["teams"]
        _wnba_team_id_to_abbr = {
            str(t["team"]["id"]): t["team"].get("abbreviation", "")
            for t in teams
        }
    except Exception:
        pass
    return _wnba_team_id_to_abbr


ESPN_URLS = {
    "NBA":  "https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba/injuries",
    "WNBA": "https://site.web.api.espn.com/apis/site/v2/sports/basketball/wnba/injuries",
}


def fetch_injuries(league: str) -> dict:
    """Returns {lowercase_name: {name, status, injury_type, comment, team_abbr}} for all injured players."""
    now = time.time()
    cached = _cache.get(league)
    if cached and now - cached["ts"] < CACHE_TTL:
        return cached["data"]

    try:
        r = requests.get(ESPN_URLS[league], timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        data = r.json()

        wnba_id_map = _get_wnba_team_id_map() if league == "WNBA" else {}

        result = {}
        by_team = {}  # team_abbr -> list of entry dicts (Out players only)
        for team in data.get("injuries", []):
            # NBA wraps team info under a "team" key; WNBA exposes it at the top level
            team_info = team.get("team", team)
            raw_abbr  = team_info.get("abbreviation", "")
            team_id   = str(team_info.get("id", ""))
            # For WNBA the top-level entry has no abbreviation — look it up via ID
            team_abbr = raw_abbr or wnba_id_map.get(team_id, team_id)
            for entry in team.get("injuries", []):
                athlete = entry.get("athlete", {})
                name = athlete.get("displayName", "").strip()
                if not name:
                    continue
                status = entry.get("status", "Unknown")
                details = entry.get("details", {})
                comment = entry.get("shortComment", "")
                rec = {
                    "name":        name,
                    "status":      status,
                    "injury_type": details.get("type", ""),
                    "comment":     comment,
                    "team_abbr":   team_abbr,
                }
                result[name.lower()] = rec
                if status.lower() == "out":
                    by_team.setdefault(team_abbr, []).append(rec)

        _cache[league] = {"data": result, "by_team": by_team, "ts": now}
        return result

    except Exception:
        return cached["data"] if cached else {}


def get_out_players_for_team(team_abbr: str, league: str) -> list:
    """Return list of Out players for the given team abbreviation."""
    fetch_injuries(league)
    cached = _cache.get(league, {})
    return cached.get("by_team", {}).get(team_abbr, [])


def get_player_injury(player_name: str, league: str) -> dict | None:
    """Returns injury info for a player, or None if not on the report."""
    injuries = fetch_injuries(league)
    nl = player_name.lower().strip()

    if nl in injuries:
        return injuries[nl]

    # Normalize: strip dots/apostrophes for fuzzy match
    def normalize(s):
        return s.replace(".", "").replace("'", "").replace("-", " ").lower()

    norm_nl = normalize(nl)
    norm_map = {normalize(k): v for k, v in injuries.items()}

    if norm_nl in norm_map:
        return norm_map[norm_nl]

    matches = difflib.get_close_matches(norm_nl, norm_map.keys(), n=1, cutoff=0.82)
    if matches:
        return norm_map[matches[0]]

    return None

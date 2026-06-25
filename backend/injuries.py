import time
import difflib
import requests

CACHE_TTL = 1800  # 30 minutes

_cache: dict = {}

ESPN_URLS = {
    "NBA":  "https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba/injuries",
    "WNBA": "https://site.web.api.espn.com/apis/site/v2/sports/basketball/wnba/injuries",
}


def fetch_injuries(league: str) -> dict:
    """Returns {lowercase_name: {name, status, injury_type, comment}} for all injured players."""
    now = time.time()
    cached = _cache.get(league)
    if cached and now - cached["ts"] < CACHE_TTL:
        return cached["data"]

    try:
        r = requests.get(ESPN_URLS[league], timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        data = r.json()

        result = {}
        for team in data.get("injuries", []):
            for entry in team.get("injuries", []):
                athlete = entry.get("athlete", {})
                name = athlete.get("displayName", "").strip()
                if not name:
                    continue
                status = entry.get("status", "Unknown")
                details = entry.get("details", {})
                comment = entry.get("shortComment", "")
                result[name.lower()] = {
                    "name":        name,
                    "status":      status,
                    "injury_type": details.get("type", ""),
                    "comment":     comment,
                }

        _cache[league] = {"data": result, "ts": now}
        return result

    except Exception:
        return cached["data"] if cached else {}


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

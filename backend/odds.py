import time
import requests
from difflib import get_close_matches

LEAGUE_IDS = {"NBA": 7, "WNBA": 3}
STAT_MAP   = {"Points": "PTS", "Assists": "AST", "Rebounds": "REB"}

_cache:      dict = {}
_cache_time: dict = {}
CACHE_TTL = 300  # seconds


def _normalize(name: str) -> str:
    return name.lower().replace(".", "").replace("'", "").replace("-", " ").strip()


def fetch_lines(league: str) -> dict:
    """Fetch and cache standard PrizePicks lines for the given league.

    Returns {display_name: {PTS: float, AST: float, REB: float}}.
    """
    now = time.time()
    key = league.upper()
    if key in _cache and now - _cache_time.get(key, 0) < CACHE_TTL:
        return _cache[key]

    league_id = LEAGUE_IDS.get(key, 7)
    try:
        r = requests.get(
            "https://api.prizepicks.com/projections",
            params={
                "league_id":   league_id,
                "per_page":    500,
                "single_stat": "true",
            },
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return {}

    projections = data.get("data", [])
    included    = data.get("included", [])
    players     = {x["id"]: x["attributes"] for x in included if x.get("type") == "new_player"}

    result: dict = {}
    for proj in projections:
        attrs = proj.get("attributes", {})
        if attrs.get("odds_type") != "standard":
            continue
        our_stat = STAT_MAP.get(attrs.get("stat_type", ""))
        if not our_stat:
            continue
        line = attrs.get("line_score")
        if line is None:
            continue
        pid  = proj["relationships"]["new_player"]["data"]["id"]
        name = players.get(pid, {}).get("display_name", "")
        if not name:
            continue
        if name not in result:
            result[name] = {}
        result[name][our_stat] = float(line)

    _cache[key]      = result
    _cache_time[key] = now
    return result


def get_player_lines(player_name: str, league: str) -> dict:
    """Return {PTS: float, AST: float, REB: float} for the player, or {}."""
    all_lines = fetch_lines(league)
    if not all_lines:
        return {}

    if player_name in all_lines:
        return all_lines[player_name]

    norm_target = _normalize(player_name)
    norm_map    = {_normalize(k): k for k in all_lines}

    if norm_target in norm_map:
        return all_lines[norm_map[norm_target]]

    matches = get_close_matches(norm_target, norm_map.keys(), n=1, cutoff=0.82)
    if matches:
        return all_lines[norm_map[matches[0]]]

    return {}

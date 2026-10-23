import os
import unicodedata
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import api_usage

load_dotenv()

API_KEY = os.environ["ODDS_API_KEY"]
BASE_URL = "https://api.the-odds-api.com/v4"

SPORTS = {"NBA": "basketball_nba", "WNBA": "basketball_wnba"}
MARKET_MAP = {
    "player_points":   "PTS",
    "player_assists":  "AST",
    "player_rebounds": "REB",
}

# NBA/WNBA schedules are published in US Eastern time; ESPN's game logs
# date games by that same local "gameday", so game dates must be derived
# in this zone rather than the server's own local time or UTC.
GAME_DATE_TZ = ZoneInfo("America/New_York")

_cache:       dict = {}
_cache_time:  dict = {}
_matchups: dict = {}
_game_dates:  dict = {}   # {league: {player_name: "YYYY-MM-DD"}}
CACHE_TTL = api_usage.setting('ODDS_CACHE_TTL_SECONDS', 21600)  # six-hour free-plan cache
_line_times: dict = {}


def _commence_time_to_game_date(commence_time: str) -> str | None:
    if not commence_time:
        return None
    try:
        dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
        return dt.astimezone(GAME_DATE_TZ).date().isoformat()
    except Exception:
        return None


def _normalize(name: str) -> str:
    name = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    return " ".join(name.casefold().replace(".", "").replace("'", "").replace("’", "").replace("-", " ").split())


def _paid_get(url, *, params, timeout):
    api_usage.reserve_odds_request(params.get("markets"))
    response = requests.get(url, params=params, timeout=timeout)
    used = response.headers.get('x-requests-used')
    if isinstance(used, str) and used.isdigit() and len(used) < 12:
        api_usage.store().observe_provider_usage(int(used))
    return response


def _download_lines(league: str) -> dict:
    """Fetch and cache player prop lines for the given league.

    Returns {player_name: {PTS: float, AST: float, REB: float}}.
    """
    now = time.time()
    key = league.upper()
    sport = SPORTS.get(key)
    if not sport:
        return {}

    # Get today's events
    try:
        events_resp = _paid_get(
            f"{BASE_URL}/sports/{sport}/events",
            params={"apiKey": API_KEY},
            timeout=10,
        )
        events_resp.raise_for_status()
        events = events_resp.json()
    except Exception:
        raise

    # Retain already-saved upcoming lines if a refresh only fits part of the
    # free-plan budget. Each player's own observation timestamp is preserved.
    result = {p: dict(lines) for p, lines in _cache.get(key, {}).items()}
    game_dates = dict(_game_dates.get(key, {}))
    matchups = dict(_matchups.get(key, {}))
    line_times = dict(_line_times.get(key, {}))
    limited = False

    today = datetime.now(GAME_DATE_TZ).date().isoformat()
    for event in sorted(events, key=lambda event: event.get('commence_time', '')):
        event_id = event.get("id")
        if not event_id:
            continue
        game_date = _commence_time_to_game_date(event.get("commence_time"))
        if game_date != today:
            continue
        try:
            odds_resp = _paid_get(
                f"{BASE_URL}/sports/{sport}/events/{event_id}/odds",
                params={
                    "apiKey":      API_KEY,
                    "regions":     "us",
                    "markets":     "player_points,player_assists,player_rebounds",
                    "bookmakers":  "draftkings",
                },
                timeout=10,
            )
            if odds_resp.status_code != 200:
                continue
            data = odds_resp.json()
        except (api_usage.UsageLimitExceeded, api_usage.UsageStoreUnavailable):
            limited = True
            break
        except Exception:
            continue

        refreshed_players = set()
        for bookmaker in data.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                stat = MARKET_MAP.get(market.get("key", ""))
                if not stat:
                    continue
                seen: set = set()
                for outcome in market.get("outcomes", []):
                    if outcome.get("name") != "Over":
                        continue
                    player = outcome.get("description", "")
                    line   = outcome.get("point")
                    if player and line is not None and player not in seen:
                        if player not in refreshed_players:
                            result[player] = {}
                            refreshed_players.add(player)
                        result[player][stat] = float(line)
                        line_times[player] = time.time()
                        matchups[player] = {"home_team": event.get("home_team"), "away_team": event.get("away_team"), "date": game_date}
                        seen.add(player)
                        if game_date:
                            game_dates[player] = game_date

    return {"lines": result, "dates": game_dates, "matchups": matchups, "line_times": line_times,
            "next_refresh": now + (3600 if limited else CACHE_TTL)}


def _activate(key, snapshot):
    today = datetime.now(GAME_DATE_TZ).date().isoformat()
    dates = snapshot.get('dates', {})
    valid = {p: lines for p, lines in snapshot.get('lines', {}).items() if dates.get(p, '') >= today}
    _cache[key] = valid
    _game_dates[key] = {p: dates[p] for p in valid}
    _matchups[key] = {p: value for p, value in snapshot.get('matchups', {}).items() if p in valid}
    _line_times[key] = {p: value for p, value in snapshot.get('line_times', {}).items() if p in valid}
    _cache_time[key] = snapshot.get('next_refresh', 0)
    return valid


def fetch_lines(league: str) -> dict:
    key = league.upper()
    if key not in SPORTS:
        return {}
    try:
        storage = api_usage.store()
        snapshot = storage.snapshot('odds-lines-' + key)
        if snapshot:
            _activate(key, snapshot)
            if time.time() < snapshot.get('next_refresh', 0):
                return _cache[key]
        try:
            reservation = storage.acquire(resource='odds-refresh-' + key, capacity=1)
        except api_usage.UsageLimitExceeded:
            return _cache.get(key, {})
        with reservation:
            # A different worker may have finished between the read and lease.
            snapshot = storage.snapshot('odds-lines-' + key)
            if snapshot:
                _activate(key, snapshot)
                if time.time() < snapshot.get('next_refresh', 0):
                    return _cache[key]
            try:
                snapshot = _download_lines(key)
            except Exception:
                # Share the retry delay too, so repeated searches cannot cause
                # a storm of failed upstream requests during an outage/quota block.
                snapshot = dict(snapshot or {})
                snapshot['next_refresh'] = time.time() + 300
            storage.save_snapshot('odds-lines-' + key, snapshot)
            return _activate(key, snapshot)
    except Exception:
        return _cache.get(key, {})


def get_line_metadata(player_name: str, league: str) -> dict:
    league = league.upper()
    key = _resolve_player_key(player_name, _cache.get(league, {}))
    observed = _line_times.get(league, {}).get(key)
    return {"lines_last_updated": datetime.fromtimestamp(observed, GAME_DATE_TZ).isoformat() if observed else None}


def _resolve_player_key(player_name: str, all_lines: dict) -> str | None:
    if player_name in all_lines:
        return player_name

    norm_target = _normalize(player_name)
    norm_map    = {_normalize(k): k for k in all_lines}

    if norm_target in norm_map:
        return norm_map[norm_target]

    return None


def get_player_lines(player_name: str, league: str) -> dict:
    """Return {PTS: float, AST: float, REB: float} for the player, or {}."""
    all_lines = fetch_lines(league)
    if not all_lines:
        return {}

    key = _resolve_player_key(player_name, all_lines)
    return all_lines[key] if key else {}


def get_player_game_date(player_name: str, league: str) -> str | None:
    """Return the scheduled game date ('YYYY-MM-DD', US Eastern) for the
    player's current prop, or None if unknown."""
    all_lines = fetch_lines(league)
    if not all_lines:
        return None

    key = _resolve_player_key(player_name, all_lines)
    if not key:
        return None

    return _game_dates.get(league.upper(), {}).get(key)


def get_player_matchup(player_name: str, league: str) -> dict:
    all_lines = fetch_lines(league)
    key = _resolve_player_key(player_name, all_lines)
    return _matchups.get(league, {}).get(key, {})

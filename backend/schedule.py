"""Date-specific NBA schedule, using the league's Eastern game day."""
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
import threading
import time

_cache = {}
_lock = threading.Lock()

EASTERN = ZoneInfo("America/New_York")


def _fetch_games_today():
    today = datetime.now(EASTERN).strftime("%Y%m%d")
    response = requests.get(
        "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
        params={"dates": today}, timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    # A malformed response must not be presented as an empty schedule.
    events = data["events"]
    result = []
    for event in events:
        comp = event["competitions"][0]
        status = comp["status"]["type"]
        state = status["state"]
        detail = status.get("shortDetail") or status.get("description", "Scheduled")
        if state == "pre":
            start = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
            detail = start.astimezone(EASTERN).strftime("%-I:%M %p %Z")

        def team(side):
            c = next(c for c in comp["competitors"] if c["homeAway"] == side)
            record = next((r["summary"] for r in c.get("records", []) if r.get("type") == "total"), "0-0")
            wins, losses = record.split("-")[:2]
            return {"tricode": {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"}.get(c["team"]["abbreviation"], c["team"]["abbreviation"]), "name": c["team"]["displayName"],
                    "score": int(c.get("score") or 0), "wins": int(wins), "losses": int(losses)}

        result.append({"gameId": event["id"], "status": detail,
                       "statusCode": {"pre": 1, "in": 2, "post": 3}[state],
                       "home": team("home"), "away": team("away")})
    return result


def nba_games_today():
    today = datetime.now(EASTERN).strftime("%Y%m%d")
    with _lock:
        if _cache.get("date") == today and time.monotonic() - _cache.get("ts", 0) < 30:
            return _cache["games"]
        games = _fetch_games_today()
        _cache.update(date=today, ts=time.monotonic(), games=games)
        return games

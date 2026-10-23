"""Refresh official current-season ratings; persist the last successful snapshot."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import threading
import time
import database

PATH = Path(__file__).parent / 'data_cache' / 'nba' / 'display_ratings.json'
_cache = None
_wnba_cache = None
_last_attempt = {}
_lock = threading.Lock()
REFRESH_SECONDS = 60


def _ratings(league):
    global _cache, _wnba_cache
    import model
    import wnba_model
    season = model.CURRENT_SEASON if league == 'NBA' else wnba_model._current_wnba_season()
    path = PATH if league == 'NBA' else PATH.with_name('wnba_display_ratings.json')
    with _lock:
        cache = _cache if league == 'NBA' else _wnba_cache
        if cache is None:
            try:
                saved = database.read_document('ratings-' + league) if database.enabled() else json.loads(path.read_text())
                saved = saved or {}
                if saved.get('season') == season:
                    cache = saved
            except (OSError, ValueError, KeyError):
                pass
        now = time.time()
        fresh = cache and cache.get('schema_version') == 3 and cache.get('season') == season and now - cache['updated_at'] < REFRESH_SECONDS
        if not fresh and now - _last_attempt.get(league, 0) >= REFRESH_SECONDS:
            _last_attempt[league] = now
            try:
                # Bypass the slower prediction-context cache: these badges track
                # official rankings independently of positional model enrichment.
                stats = model.fetch_team_defense_stats() if league == 'NBA' else wnba_model.fetch_wnba_team_stats()
                stats = {abbr: values for abbr, values in stats.items()
                         if all(isinstance(values.get(k), (int, float)) and math.isfinite(values[k])
                                and values[k] > 0 for k in ('off_rtg', 'def_rtg', 'off_rank', 'def_rank'))}
                if not stats:
                    raise ValueError('No current-season team ratings available')
                cache = {'schema_version': 3, 'season': season, 'fallback': False,
                         'team_count': len(stats), 'teams': stats, 'updated_at': time.time()}
                if database.enabled():
                    database.save_document('ratings-' + league, cache)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temp = path.with_suffix('.tmp')
                    temp.write_text(json.dumps(cache, allow_nan=False))
                    temp.replace(path)
            except Exception:
                # Keep the source timestamp intact when the feed fails.
                pass
        if league == 'NBA':
            _cache = cache
        else:
            _wnba_cache = cache
        if not cache or cache.get('season') != season:
            return None
        return {**cache, 'stale': cache.get('schema_version') != 3 or time.time() - cache['updated_at'] >= REFRESH_SECONDS}


def nba_ratings():
    return _ratings('NBA')


def rating_fields(league, team):
    display = _ratings(league)
    if not display:
        return None
    ratings = display['teams'].get(team, {})
    return {'team_off_rank': ratings.get('off_rank'), 'team_def_rank': ratings.get('def_rank'),
            'team_off_rating': ratings.get('off_rtg'), 'team_def_rating': ratings.get('def_rtg'),
            'ratings_season': display['season'], 'ratings_team_count': display['team_count'],
            'ratings_previous_season': False, 'ratings_stale': display['stale'],
            'ratings_last_updated': datetime.fromtimestamp(display['updated_at'], timezone.utc).isoformat()}

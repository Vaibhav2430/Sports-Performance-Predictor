"""Refresh optional NBA matchup context without blocking player predictions."""
import json
import logging
from pathlib import Path
import threading
import time
import pandas as pd

PATH = Path(__file__).parent / 'data_cache' / 'nba' / 'context.json'
_context = None
_lock = threading.Lock()
_refreshing = False
_retry_at = 0


def _refresh():
    global _context, _refreshing, _retry_at
    import model
    import defense_by_position as dbp
    try:
        stats = model.fetch_team_defense_stats()
        timeseries = model._team_def_timeseries()
        positions = dbp._build_nba_name_pos()
        defense = dbp.fetch_nba_def_by_pos(model.CURRENT_SEASON)
        context = {'season': model.CURRENT_SEASON, 'updated_at': time.time(), 'team_stats': stats,
                   'timeseries': timeseries, 'positions': positions, 'pos_def': defense}
        payload = {**context, 'timeseries': {k: json.loads(v.to_json(orient='records', date_format='iso')) for k,v in timeseries.items()}}
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.with_suffix('.tmp').write_text(json.dumps(payload, allow_nan=False))
        PATH.with_suffix('.tmp').replace(PATH)
        with _lock:
            _context = context
    except Exception:
        logging.getLogger(__name__).exception('NBA matchup data refresh failed; retaining available context')
    finally:
        with _lock:
            _retry_at = time.time() + 600
            _refreshing = False


def get_context():
    global _context, _refreshing
    import model
    with _lock:
        if _context is None:
            try:
                payload = json.loads(PATH.read_text())
                if payload['season'] != model.CURRENT_SEASON:
                    raise ValueError('Different season')
                for key, rows in payload['timeseries'].items():
                    frame = pd.DataFrame(rows)
                    frame['GAME_DATE'] = pd.to_datetime(frame.GAME_DATE)
                    payload['timeseries'][key] = frame
                _context = payload
            except (OSError, ValueError, KeyError):
                _context = {'team_stats': {}, 'timeseries': {}, 'positions': {}, 'pos_def': {}, 'updated_at': 0}
        if not _refreshing and time.time() > _retry_at and time.time() - _context['updated_at'] > 21600:
            _refreshing = True
            threading.Thread(target=_refresh, daemon=True).start()
        return _context

"""Shared regular-season box scores with bounded requests and disk fallback."""
from datetime import datetime, timezone
import threading
import time
from pathlib import Path
from io import StringIO
import database
import pandas as pd
from nba_api.stats.endpoints import playergamelogs

CACHE = Path(__file__).parent / 'data_cache' / 'nba'
_frames = {}
_lock = threading.Lock()
TTL = 1800


class HistoryUnavailable(RuntimeError):
    pass


class NoHistory(ValueError):
    pass


def season_games(season):
    with _lock:
        now = time.time()
        if season in _frames and now - _frames[season][0] < TTL:
            return _frames[season][1].copy()
        CACHE.mkdir(parents=True, exist_ok=True)
        path = CACHE / f'{season}.csv'
        saved = database.read_document('nba-games-' + season) if database.enabled() else None
        updated_at = saved['updated_at'] if saved else (path.stat().st_mtime if path.exists() else 0)
        def read_saved():
            return pd.read_csv(StringIO(saved['csv']) if saved else path, dtype={'GAME_ID': str})
        if updated_at and now - updated_at < TTL:
            frame = read_saved()
            frame.attrs['history_source'] = 'cached'
        else:
            try:
                frame = playergamelogs.PlayerGameLogs(season_nullable=season,
                    season_type_nullable='Regular Season', timeout=15).get_data_frames()[0]
                if not {'PLAYER_ID', 'GAME_ID', 'GAME_DATE', 'MIN', 'PTS', 'AST', 'REB', 'MATCHUP', 'WL'} <= set(frame):
                    raise ValueError('Incomplete NBA game log response')
                updated_at = time.time()
                if database.enabled():
                    database.save_document('nba-games-' + season, {'csv': frame.to_csv(index=False), 'updated_at': updated_at})
                else:
                    frame.to_csv(path.with_suffix('.tmp'), index=False)
                    path.with_suffix('.tmp').replace(path)
                frame.attrs['history_source'] = 'live'
            except Exception as exc:
                if not saved and not path.exists():
                    raise HistoryUnavailable('NBA box scores are temporarily unavailable. Please retry shortly.') from exc
                frame = read_saved()
                frame.attrs['history_source'] = 'stale_cache'
        if updated_at:
            frame.attrs['history_updated_at'] = datetime.fromtimestamp(updated_at, timezone.utc).isoformat()
        # Some feeds include preseason even with a season-type filter.
        frame = frame[frame.GAME_ID.astype(str).str.zfill(10).str.startswith('002')].copy()
        _frames[season] = (now, frame)
        return frame.copy()


def player_games(player_id, season):
    frame = season_games(season)
    df = frame[pd.to_numeric(frame.PLAYER_ID) == int(player_id)].copy()
    df['Game_ID'] = df['GAME_ID'].astype(str).str.zfill(10)
    df['GAME_DATE'] = pd.to_datetime(df.GAME_DATE, errors='coerce')
    df = df.dropna(subset=['GAME_DATE']).sort_values('GAME_DATE').drop_duplicates('Game_ID').reset_index(drop=True)
    for col in ('MIN', 'PTS', 'AST', 'REB'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['MIN', 'PTS', 'AST', 'REB'])
    df = df[(df.MIN > 0) & (df[['MIN', 'PTS', 'AST', 'REB']] >= 0).all(axis=1)].reset_index(drop=True)
    df['HOME'] = (~df.MATCHUP.str.contains('@')).astype(int)
    df['DAYS_REST'] = df.GAME_DATE.diff().dt.days.fillna(2).clip(0, 10)
    return df


_directory = None
_directory_until = 0


def player_directory(season):
    """Refresh the player directory so rookies absent from static data can resolve."""
    global _directory, _directory_until
    from nba_api.stats.endpoints import commonallplayers
    with _lock:
        if _directory is not None and time.time() < _directory_until:
            return _directory
        CACHE.mkdir(parents=True, exist_ok=True)
        path = CACHE / f'players-{season}.csv'
        saved = database.read_document('nba-players-' + season) if database.enabled() else None
        try:
            if saved and time.time() - saved['updated_at'] < 86400:
                frame = pd.read_csv(StringIO(saved['csv']))
            elif not database.enabled() and path.exists() and time.time() - path.stat().st_mtime < 86400:
                frame = pd.read_csv(path)
            else:
                frame = commonallplayers.CommonAllPlayers(is_only_current_season=1, season=season, timeout=10).get_data_frames()[0]
                if database.enabled():
                    database.save_document('nba-players-' + season, {'csv': frame.to_csv(index=False), 'updated_at': time.time()})
                else:
                    frame.to_csv(path, index=False)
        except Exception:
            frame = pd.read_csv(StringIO(saved['csv'])) if saved else (pd.read_csv(path) if path.exists() else pd.DataFrame())
        _directory = [{'id': int(r.PERSON_ID), 'full_name': r.DISPLAY_FIRST_LAST, 'is_active': True}
                      for r in frame.itertuples()]
        _directory_until = time.time() + (86400 if _directory else 300)
        return _directory

"""Exercise the production NBA predictor for every active player, offline.

Real cached league box scores drive the full model. Optional matchup providers
are empty, deliberately testing that missing enrichment cannot break forecasts.
No-history players require market lines and are reported separately.
"""
import json
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
import pandas as pd
from nba_api.stats.static import players
import model
from nba_data import NoHistory


def main():
    root = Path(__file__).parent
    frames = {}
    for season in (model.CURRENT_SEASON, model._prev_nba_season()):
        path = root / 'data_cache' / 'nba' / f'{season}.csv'
        df = pd.read_csv(path, dtype={'GAME_ID': str})
        df = df[df.GAME_ID.str.startswith('002')].copy()
        df['Game_ID'] = df.GAME_ID
        df['GAME_DATE'] = pd.to_datetime(df.GAME_DATE)
        for col in ('PTS','AST','REB','MIN'):
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna(subset=['PTS','AST','REB','MIN'])
        df = df[df.MIN > 0]
        df['HOME'] = (~df.MATCHUP.str.contains('@')).astype(int)
        frames[season] = df

    def history(pid, season=model.CURRENT_SEASON):
        df = frames[season][frames[season].PLAYER_ID == int(pid)].sort_values('GAME_DATE').copy().reset_index(drop=True)
        df['DAYS_REST'] = df.GAME_DATE.diff().dt.days.fillna(2).clip(0,10)
        return df

    report = {'scope': 'All active players in the installed NBA player directory; real cached current/prior regular-season histories; optional matchup data absent.',
              'trained': [], 'limited_history': [], 'needs_market_line': [], 'failures': []}
    active = [p for p in players.get_players() if p['is_active']]
    context = {'team_stats': {}, 'positions': {}, 'pos_def': {}, 'timeseries': {}}
    with ExitStack() as stack:
        stack.enter_context(patch.object(model, 'fetch_game_log', side_effect=history))
        stack.enter_context(patch.object(model, '_prediction_context', return_value=context))
        stack.enter_context(patch.object(model, '_next_game', return_value=None))
        for index, player in enumerate(active, 1):
            try:
                result = model.predict(player['full_name'], market_lines={})
                json.dumps(result, allow_nan=False)
                category = 'limited_history' if result['model_version'] == 'limited-history-average-v1' else 'trained'
                report[category].append(player['full_name'])
            except NoHistory:
                report['needs_market_line'].append(player['full_name'])
            except Exception as exc:
                report['failures'].append({'player': player['full_name'], 'error': str(exc)})
            if index % 25 == 0:
                print(f'Checked {index}/{len(active)}; failures={len(report["failures"])}', flush=True)
    report['counts'] = {k: len(report[k]) for k in ('trained','limited_history','needs_market_line','failures')}
    path = root / 'evaluation_results' / 'nba_player_audit.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report['counts'], indent=2))
    if report['failures']:
        raise SystemExit(1)

if __name__ == '__main__':
    main()

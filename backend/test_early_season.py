from contextlib import ExitStack
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
import model
import tracker


class EarlySeasonTests(unittest.TestCase):
    def test_one_current_game_returns_finite_forecasts_and_nullable_metadata(self):
        self.check_current_history(1)

    def test_zero_current_games_preserves_prior_date_types(self):
        self.check_current_history(0)

    def check_current_history(self, current_count):
        n = 24
        prior = pd.DataFrame({'GAME_DATE': pd.date_range('2026-01-01', periods=n),
            'MATCHUP': ['OKC vs. DEN'] * n, 'WL': 'W', 'MIN': 34.,
            'PTS': np.arange(n) % 12 + 20., 'AST': np.arange(n) % 5 + 4.,
            'REB': np.arange(n) % 7 + 3., 'HOME': 1, 'DAYS_REST': 2.})
        current = prior.iloc[[-1]].copy()
        current['GAME_DATE'] = pd.Timestamp('2026-10-21')
        if not current_count:
            current = pd.DataFrame(columns=prior.columns)
        stats = {'OKC': {'pace': 100., 'def_rtg': 110.}}
        with ExitStack() as stack:
            stack.enter_context(patch.object(model, 'fetch_game_log', side_effect=[current, prior]))
            stack.enter_context(patch.object(model, 'fetch_team_defense_stats', return_value=stats))
            stack.enter_context(patch.object(model, '_team_def_timeseries', return_value={}))
            stack.enter_context(patch.object(model, '_next_game', return_value=None))
            stack.enter_context(patch.object(model, '_prediction_context', return_value={'team_stats': stats, 'timeseries': {}, 'positions': {}, 'pos_def': {}}))
            stack.enter_context(patch.object(model.tuning, 'load_active', return_value=None))
            stack.enter_context(patch.object(model.dbp, 'get_nba_player_pos', return_value='PG'))
            stack.enter_context(patch.object(model.dbp, 'fetch_nba_def_by_pos', return_value={}))
            result = model.predict('Shai Gilgeous-Alexander')
        json.dumps(result, allow_nan=False)
        for stat in model.STATS:
            self.assertTrue(np.isfinite(result['predictions'][stat]['prediction']))
            self.assertGreater(result['predictions'][stat]['sigma'], 0)
        self.assertIsNone(result['game_log'][0]['OPP_OFF_RANK'])

    def test_invalid_saved_forecast_is_repaired_by_successful_retry(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(tracker, 'LOG_FILE', str(Path(directory) / 'log.json')):
            tracker.save_log([{'player': 'Test', 'date': '2026-10-22', 'resolved': False,
                               'predicted': {'PTS': float('nan')}}])
            tracker.log_prediction('Test', 1, 'NBA', {'PTS': 20.5}, {'PTS': {'prediction': 22.}}, game_date='2026-10-22')
            log = tracker.load_log()
            self.assertEqual(len(log), 1)
            self.assertEqual(log[0]['predicted']['PTS'], 22.)
            json.dumps(log, allow_nan=False)

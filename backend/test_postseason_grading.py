import unittest
from unittest.mock import Mock, patch

import pandas as pd
import main
import wnba_model
from test_daily_accuracy import record


class PostseasonGradingTests(unittest.TestCase):
    def feed(self):
        events = {'regular': {'gameDate': '2026-09-01T17:00:00Z'},
                  'finals': {'gameDate': '2026-10-24T17:00:00Z'},
                  'preseason': {'gameDate': '2026-05-01T17:00:00Z'}}
        return {'labels': ['MIN', 'PTS', 'AST', 'REB'], 'events': events,
                'seasonTypes': [
                    {'displayName': f'2026 {label}', 'categories': [{'events': [
                        {'eventId': key, 'stats': ['30', '20', '5', '10']}]}]}
                    for key, label in [('regular', 'Regular Season'), ('finals', 'Postseason'), ('preseason', 'Preseason')]]}

    def test_grading_can_include_finals_without_changing_prediction_history(self):
        response = Mock()
        response.json.return_value = self.feed()
        with patch.object(wnba_model.requests, 'get', return_value=response):
            history = wnba_model.fetch_game_log('1', season='2026')
            grading = wnba_model.fetch_game_log('1', season='2026', include_postseason=True)
        self.assertEqual(list(history.EVENT_ID), ['regular'])
        self.assertEqual(list(grading.EVENT_ID), ['regular', 'finals'])

    def test_resolver_requests_postseason_for_the_saved_game_year(self):
        entry = record('2020-10-24', league='WNBA', resolved=False)
        frame = pd.DataFrame([{'GAME_DATE': pd.Timestamp('2020-10-24'), 'MIN': 30.,
                               'PTS': 20., 'AST': 5., 'REB': 10., 'EVENT_ID': 'finals'}])
        with patch.object(main.tracker, 'load_log', return_value=[entry]), \
             patch.object(main.tracker, 'save_log') as save, \
             patch.object(main.wnba_model, 'find_player', return_value={'id': '1'}), \
             patch.object(main.wnba_model, 'fetch_game_log', return_value=frame) as fetch, \
             patch.object(main, '_detect_early_exit', return_value=False):
            metrics = main._resolve_all_locked()
        fetch.assert_called_once_with('1', season='2020', include_postseason=True)
        self.assertTrue(entry['resolved'])
        self.assertEqual(metrics['resolved'], 1)
        save.assert_called_once()

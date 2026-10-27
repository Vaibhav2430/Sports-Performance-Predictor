"""Only saved, posted DraftKings markets enter over/under grading."""
from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import api_usage
import main
import odds
import tracker


class DraftKingsGradingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for mocked in (patch.object(tracker, 'LOG_FILE', str(Path(directory.name) / 'predictions.json')),
                       patch.object(tracker.database, 'enabled', return_value=False)):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.predictions = {stat: {'prediction': value} for stat, value in [('PTS', 25), ('AST', 6), ('REB', 8)]}

    def test_player_without_draftkings_lines_is_not_recorded(self):
        tracker.log_prediction('No props', 1, 'NBA', {}, self.predictions, game_date='2026-10-27')
        self.assertEqual(tracker.load_log(), [])

    def test_only_posted_stats_are_saved_and_graded(self):
        tracker.log_prediction('Points only', 2, 'NBA', {'PTS': 24.5}, self.predictions, game_date='2026-10-27')
        record = tracker.load_log()[0]
        self.assertEqual(record['predicted'], {'PTS': 25})
        tracker.mark_resolved(record, {'PTS': 28, 'AST': 7, 'REB': 9})
        result = tracker.compute_stats([record], 'NBA')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['correct'], 1)
        self.assertEqual(result['by_stat']['AST']['total'], 0)
        self.assertEqual(result['by_stat']['REB']['total'], 0)

    def test_automatic_seeding_only_visits_players_with_posted_props(self):
        posted = {'Points only': {'PTS': 24.5}, 'Rebounds only': {'REB': 8.5}}
        with patch.object(odds, 'fetch_lines', return_value=posted), \
             patch.object(odds, 'get_player_game_date', return_value='2026-10-27'), \
             patch.object(main, 'nba_find_player', side_effect=lambda name: {'full_name': name}), \
             patch.object(api_usage, 'prediction', side_effect=lambda: nullcontext()), \
             patch.object(main, '_predict_and_record') as predict:
            main._seed_all_players('NBA')
        self.assertEqual([call.args[0] for call in predict.call_args_list], list(posted))

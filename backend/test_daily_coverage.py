from contextlib import nullcontext
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

import api_usage
import daily_coverage
import main
import odds
from test_daily_accuracy import record


class DailyCoverageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.storage = api_usage.SharedUsage(Path(directory.name) / 'usage.sqlite3')
        mocked = patch.object(api_usage, 'store', return_value=self.storage)
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_coverage_persists_posted_players_and_only_counts_saved_forecasts(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo('America/New_York')).date().isoformat()
        saved = [record(today, player='Nikola Jokic', resolved=False)]
        with patch.object(daily_coverage.tracker, 'load_log', return_value=saved):
            first = daily_coverage.record('NBA', {'Nikola Jokić': {'PTS': 25.5}, 'Other': {'REB': 5.5}}, {})
            second = daily_coverage.record('NBA', {}, {})
        self.assertEqual(first['captured'], 1)
        self.assertEqual(first['missing_players'], ['Other'])
        self.assertEqual(second['posted'], 2)
        self.assertEqual(second['captured'], 1)

    def test_automatic_collection_can_retry_every_fifteen_minutes(self):
        with patch.object(api_usage.time, 'time', return_value=1800):
            lease = api_usage.reserve_bulk('NBA')
            self.storage.release(lease.token)
            with self.assertRaises(api_usage.UsageLimitExceeded):
                api_usage.reserve_bulk('NBA')
        with patch.object(api_usage.time, 'time', return_value=2700):
            lease = api_usage.reserve_bulk('NBA')
            self.storage.release(lease.token)

    def test_short_prediction_rate_limit_is_retried_without_losing_later_players(self):
        posted = {'First': {'PTS': 20.5}, 'Second': {'PTS': 15.5}}
        admissions = [api_usage.UsageLimitExceeded('busy', retry_after=5), nullcontext(), nullcontext()]
        with patch.object(odds, 'fetch_lines', return_value=posted), \
             patch.object(odds, 'player_game_started', return_value=False), \
             patch.object(odds, 'get_player_game_date', return_value='2026-10-27'), \
             patch.object(main.tracker, 'load_log', return_value=[]), \
             patch.object(main, 'nba_find_player', side_effect=lambda name: {'full_name': name}), \
             patch.object(api_usage, 'prediction', side_effect=admissions), \
             patch.object(main, '_predict_and_record') as predict, \
             patch.object(main.time, 'sleep') as sleep, \
             patch.object(daily_coverage, 'record'):
            metrics = main._seed_all_players('NBA')
        sleep.assert_called_once_with(5)
        self.assertEqual([call.args[0] for call in predict.call_args_list], list(posted))
        self.assertEqual(metrics['generated'], 2)

    def test_scheduled_collection_does_not_generate_for_started_games(self):
        with patch.object(odds, 'fetch_lines', return_value={'Started': {'PTS': 20.5}}), \
             patch.object(odds, 'player_game_started', return_value=True), \
             patch.object(main.tracker, 'load_log', return_value=[]), \
             patch.object(main, '_predict_and_record') as predict, \
             patch.object(daily_coverage, 'record'):
            main._seed_all_players('NBA')
        predict.assert_not_called()

    def test_cloud_runner_refuses_local_only_storage(self):
        with patch.object(daily_coverage.database, 'enabled', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'postgres'):
                daily_coverage.run_once()

    def test_start_time_uses_a_timestamp_across_time_zones(self):
        with patch.dict(odds._cache, {'NBA': {'Player': {'PTS': 20.5}}}, clear=True), \
             patch.dict(odds._matchups, {'NBA': {'Player': {'commence_time': '2026-10-28T01:00:00Z'}}}, clear=True), \
             patch.object(odds.time, 'time', return_value=1793149200):
            self.assertTrue(odds.player_game_started('Player', 'NBA'))

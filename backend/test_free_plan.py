from datetime import datetime, timezone
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import api_usage
import odds


def stamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


class FreePlanTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'usage.sqlite3'
        self.storage = api_usage.SharedUsage(self.path)
        mocked = patch.object(api_usage, 'store', return_value=self.storage)
        mocked.start()
        self.addCleanup(mocked.stop)
        for memory in (odds._cache, odds._cache_time, odds._line_times, odds._game_dates, odds._matchups):
            cleared = patch.dict(memory, {}, clear=True)
            cleared.start()
            self.addCleanup(cleared.stop)

    def snapshot(self, next_refresh=0):
        today = datetime.now(odds.GAME_DATE_TZ).date().isoformat()
        return {'lines': {'Player': {'PTS': 25}}, 'dates': {'Player': today},
                'line_times': {'Player': 123}, 'matchups': {}, 'next_refresh': next_refresh}

    def test_month_windows_follow_calendar_including_leap_year_and_december(self):
        for start, end in [('2028-02-29', '2028-03-01'), ('2026-12-31', '2027-01-01'), ('2026-01-31', '2026-02-01')]:
            self.assertEqual(api_usage.month_window(stamp(start)), (start[:7], stamp(end)))

    def test_month_cap_survives_day_boundary_and_resets_next_month(self):
        with patch.dict(os.environ, {'ODDS_API_MONTHLY_CREDIT_BUDGET': '3'}):
            with patch.object(api_usage.time, 'time', return_value=stamp('2026-01-30')):
                api_usage.reserve_odds_request('a,b,c')
            with patch.object(api_usage.time, 'time', return_value=stamp('2026-01-31')):
                with self.assertRaises(api_usage.UsageLimitExceeded):
                    api_usage.reserve_odds_request('a')
            with patch.object(api_usage.time, 'time', return_value=stamp('2026-02-01')):
                api_usage.reserve_odds_request('a,b,c')

    def test_provider_usage_includes_prior_account_use_and_never_lowers_reservations(self):
        self.storage.observe_provider_usage(499)
        self.storage.observe_provider_usage(100)
        with self.assertRaises(api_usage.UsageLimitExceeded):
            api_usage.reserve_odds_request('a,b,c')
        # Free events remain available even if the account exceeds our cap.
        self.storage.observe_provider_usage(501)
        api_usage.reserve_odds_request()

    def test_response_header_stops_next_paid_request_before_network(self):
        response = Mock(headers={'x-requests-used': '500'})
        with patch.object(odds.requests, 'get', return_value=response) as get:
            odds._paid_get('unused', params={}, timeout=1)
            with self.assertRaises(api_usage.UsageLimitExceeded):
                odds._paid_get('unused', params={'markets': 'a'}, timeout=1)
            self.assertEqual(get.call_count, 1)

    def test_shared_cache_survives_worker_restart_and_preserves_observation_time(self):
        self.storage.save_snapshot('odds-lines-NBA', self.snapshot(float('1e12')))
        restarted = api_usage.SharedUsage(self.path)
        with patch.object(api_usage, 'store', return_value=restarted), patch.object(odds, '_download_lines') as download:
            self.assertEqual(odds.fetch_lines('NBA'), {'Player': {'PTS': 25}})
            self.assertIsNotNone(odds.get_line_metadata('Player', 'nba')['lines_last_updated'])
            self.assertEqual(odds._line_times['NBA']['Player'], 123)
            download.assert_not_called()

    def test_yesterdays_lines_are_not_served_from_fresh_cache(self):
        snapshot = self.snapshot(float('1e12'))
        snapshot['dates']['Player'] = '2000-01-01'
        self.storage.save_snapshot('odds-lines-NBA', snapshot)
        self.assertEqual(odds.fetch_lines('NBA'), {})

    def test_refresh_failure_is_shared_and_keeps_timestamp(self):
        self.storage.save_snapshot('odds-lines-NBA', self.snapshot())
        with patch.object(odds, '_download_lines', side_effect=api_usage.UsageLimitExceeded('quota')) as download:
            self.assertEqual(odds.fetch_lines('NBA'), {'Player': {'PTS': 25}})
            odds.fetch_lines('NBA')
            self.assertEqual(download.call_count, 1)
            self.assertEqual(odds._line_times['NBA']['Player'], 123)

    def test_other_worker_refresh_prevents_duplicate_upstream_calls(self):
        self.storage.save_snapshot('odds-lines-NBA', self.snapshot())
        with self.storage.acquire(resource='odds-refresh-NBA'):
            with patch.object(odds, '_download_lines') as download:
                self.assertEqual(odds.fetch_lines('NBA'), {'Player': {'PTS': 25}})
                download.assert_not_called()

    def test_partial_refresh_does_not_make_missing_market_look_new(self):
        snapshot = self.snapshot()
        snapshot['lines']['Player']['AST'] = 8
        odds._activate('NBA', snapshot)
        today = datetime.now(odds.GAME_DATE_TZ).date().isoformat()
        event = {'id': 'game', 'commence_time': today + 'T16:00:00-04:00'}
        free = Mock()
        free.json.return_value = [event]
        paid = Mock(status_code=200)
        paid.json.return_value = {'bookmakers': [{'markets': [{'key': 'player_points', 'outcomes': [
            {'name': 'Over', 'description': 'Player', 'point': 26}]}]}]}
        with patch.object(odds, '_paid_get', side_effect=[free, paid]):
            refreshed = odds._download_lines('NBA')
        self.assertEqual(refreshed['lines']['Player'], {'PTS': 26.0})


if __name__ == '__main__':
    unittest.main()

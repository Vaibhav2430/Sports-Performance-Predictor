from datetime import datetime, timezone
from pathlib import Path
import os
import time
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
        for memory in (odds._cache, odds._cache_time, odds._line_times, odds._game_dates, odds._matchups, odds._line_status):
            cleared = patch.dict(memory, {}, clear=True)
            cleared.start()
            self.addCleanup(cleared.stop)

    def snapshot(self, next_refresh=0):
        today = datetime.now(odds.GAME_DATE_TZ).date().isoformat()
        return {'lines': {'Player': {'PTS': 25}}, 'dates': {'Player': today},
                'line_times': {'Player': 123}, 'matchups': {}, 'next_refresh': next_refresh, 'cache_policy': odds.CACHE_POLICY}

    def test_full_slate_is_not_cut_off_by_old_daily_or_minute_caps(self):
        today = datetime.now(odds.GAME_DATE_TZ).date().isoformat()
        events = [{'id': str(i), 'commence_time': today + 'T19:00:00-04:00'} for i in range(16)]
        event_response = Mock(status_code=200, headers={})
        event_response.json.return_value = events
        responses = [event_response]
        for i in range(16):
            response = Mock(status_code=200, headers={})
            response.json.return_value = {'bookmakers': [{'key': 'draftkings', 'markets': [
                {'key': 'player_points', 'outcomes': [
                    {'name': 'Over', 'description': f'Player {i}', 'point': 20.5}]}]}]}
            responses.append(response)
        # Even leftover configuration and exhausted legacy counters cannot truncate the slate.
        self.storage.acquire([api_usage.Budget('odds-http-day', 40, 86400, 40),
                              api_usage.Budget('odds-http-minute', 10, 60, 10),
                              api_usage.Budget('odds-credit-reservations-day', 15, 86400, 15)])
        old_limits = {'ODDS_API_REQUESTS_PER_DAY': '1', 'ODDS_API_REQUESTS_PER_MINUTE': '1',
                      'ODDS_API_DAILY_CREDIT_BUDGET': '1', 'ODDS_API_MONTHLY_CREDIT_BUDGET': '500'}
        with patch.dict(os.environ, old_limits), patch.object(odds.requests, 'get', side_effect=responses) as get:
            snapshot = odds._download_lines('NBA')
        self.assertEqual(len(snapshot['lines']), 16)
        self.assertEqual(get.call_count, 17)
        self.assertFalse(snapshot['monitor_counts']['deferred'])
        for call in get.call_args_list[1:]:
            self.assertEqual(call.kwargs['params']['bookmakers'], 'draftkings')
        self.assertAlmostEqual(snapshot['next_refresh'] - time.time(), odds.CACHE_TTL, delta=5)

    def test_old_six_hour_snapshot_refreshes_without_deleting_saved_lines(self):
        old = self.snapshot(1e12)
        old.pop('cache_policy')
        self.storage.save_snapshot('odds-lines-NBA', old)
        new = self.snapshot(1e12)
        new['lines']['Player']['PTS'] = 26
        with patch.object(odds, '_download_lines', return_value=new) as download:
            self.assertEqual(odds.fetch_lines('NBA')['Player']['PTS'], 26)
            odds.fetch_lines('NBA')
        download.assert_called_once()

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


class LineAvailabilityTests(unittest.TestCase):
    setUp = FreePlanTests.setUp
    snapshot = FreePlanTests.snapshot

    def test_deferred_legacy_snapshot_explains_missing_lines(self):
        self.storage.save_snapshot('odds-lines-NBA', {
            'lines': {}, 'next_refresh': 1e12, 'cache_policy': odds.CACHE_POLICY, 'monitor_counts': {'deferred': True}})
        self.assertEqual(odds.get_player_lines('Player', 'NBA'), {})
        metadata = odds.get_line_metadata('Player', 'NBA')
        self.assertEqual(metadata['lines_status'], 'quota_limited')
        self.assertIn('usage limit', metadata['lines_message'])

    def test_provider_error_is_safe_and_preserves_saved_lines(self):
        self.storage.save_snapshot('odds-lines-NBA', self.snapshot())
        with patch.object(odds, '_download_lines', side_effect=RuntimeError('secret-url')):
            odds.fetch_lines('NBA')
        metadata = odds.get_line_metadata('Missing', 'NBA')
        self.assertEqual(metadata['lines_status'], 'upstream_unavailable')
        self.assertNotIn('secret-url', str(metadata))
        self.assertEqual(odds.get_line_metadata('Player', 'NBA')['lines_status'], 'available')

    def test_no_player_props_is_explicit(self):
        self.storage.save_snapshot('odds-lines-NBA', {'lines': {}, 'next_refresh': 1e12, 'cache_policy': odds.CACHE_POLICY})
        odds.fetch_lines('NBA')
        self.assertEqual(odds.get_line_metadata('Player', 'NBA')['lines_status'], 'not_posted')


if __name__ == '__main__':
    unittest.main()

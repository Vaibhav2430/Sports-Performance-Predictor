import unittest
from unittest.mock import patch, Mock
import main
import schedule


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        schedule._cache.clear()

    def test_one_league_failure_does_not_block_other(self):
        with patch.object(main, '_seed_all', side_effect=[RuntimeError('offline'), None]) as seed:
            main._seed_if_needed()
        self.assertEqual([c.args[0] for c in seed.call_args_list], ['NBA', 'WNBA'])

    def test_worker_repeats_and_stops(self):
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.side_effect = [False, True]
        with patch.object(main, '_seed_if_needed') as seed, patch.object(main, '_resolve_all') as resolve:
            main._prediction_worker(stop)
        self.assertEqual(seed.call_count, 2)
        self.assertEqual(resolve.call_count, 2)
        stop.wait.assert_called_with(3600)

    def test_schedule_failure_is_not_empty_day(self):
        with patch.object(schedule.requests, 'get') as get:
            get.return_value.raise_for_status.side_effect = RuntimeError('offline')
            with self.assertRaises(RuntimeError):
                schedule.nba_games_today()

    def test_utc_next_day_game_displays_evening_eastern(self):
        competitors = [{'homeAway': side, 'team': {'abbreviation': name, 'displayName': name},
                        'records': [{'type': 'total', 'summary': '1-0'}]} for side, name in [('home', 'OKC'), ('away', 'DEN')]]
        event = {'id': '123', 'date': '2026-10-23T01:30Z', 'competitions': [
            {'status': {'type': {'state': 'pre'}}, 'competitors': competitors}]}
        with patch.object(schedule.requests, 'get') as get:
            get.return_value.json.return_value = {'events': [event]}
            games = schedule.nba_games_today()
        self.assertEqual(games[0]['status'], '9:30 PM EDT')
        self.assertEqual(games[0]['home']['tricode'], 'OKC')

if __name__ == '__main__':
    unittest.main()

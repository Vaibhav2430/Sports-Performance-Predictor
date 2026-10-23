from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import model
import team_ratings


class TeamRatingTests(unittest.TestCase):
    def setUp(self):
        team_ratings._last_attempt.clear()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(TemporaryDirectory())
        self.stack.enter_context(patch.object(team_ratings, 'PATH', Path(directory)/'ratings.json'))
        self.stack.enter_context(patch.object(team_ratings, '_cache', None))
        self.stack.enter_context(patch.object(team_ratings, '_wnba_cache', None))

    def data(self, count):
        return {str(i): {'off_rtg': 100. + i, 'def_rtg': 110. + i, 'off_rank': i+1, 'def_rank': count-i} for i in range(count)}

    def test_snapshot_restored_on_next_launch_without_refetching_fresh_data(self):
        with patch.object(model,'fetch_team_defense_stats',return_value=self.data(28)) as fetch:
            first=team_ratings.nba_ratings()
            self.assertEqual(first['team_count'],28)
            self.assertTrue(team_ratings.PATH.exists())
            team_ratings._cache=None
            restored=team_ratings.nba_ratings()
            self.assertEqual(restored,first)
            fetch.assert_called_once()

    def test_rankings_refresh_after_one_minute_independent_of_prediction_context(self):
        updated=self.data(28)
        updated['0']['off_rank']=25
        with patch.object(team_ratings.time,'time',return_value=2000), patch.object(model,'fetch_team_defense_stats',return_value=self.data(28)):
            first=team_ratings.nba_ratings()
        with patch.object(team_ratings.time,'time',return_value=2061), patch.object(model,'fetch_team_defense_stats',return_value=updated) as fetch, patch('nba_context.get_context',side_effect=AssertionError('Must bypass six-hour model cache')):
            refreshed=team_ratings.nba_ratings()
        self.assertEqual(first['teams']['0']['off_rank'],1)
        self.assertEqual(refreshed['teams']['0']['off_rank'],25)
        self.assertGreater(refreshed['updated_at'],first['updated_at'])
        fetch.assert_called_once()

    def test_feed_failure_keeps_real_source_time_and_marks_saved_data_stale(self):
        with patch.object(team_ratings.time,'time',return_value=2000), patch.object(model,'fetch_team_defense_stats',return_value=self.data(28)):
            first=team_ratings.nba_ratings()
        with patch.object(team_ratings.time,'time',return_value=2061), patch.object(model,'fetch_team_defense_stats',side_effect=TimeoutError):
            saved=team_ratings.nba_ratings()
        self.assertTrue(saved['stale'])
        self.assertEqual(saved['updated_at'],first['updated_at'])

    def test_unavailable_ratings_are_omitted(self):
        values=self.data(3)
        values['not-ready']={'off_rtg':None,'def_rtg':None}
        with patch.object(model,'fetch_team_defense_stats',return_value=values):
            result=team_ratings.nba_ratings()
        self.assertNotIn('not-ready',result['teams'])
        self.assertEqual(result['team_count'],3)

    def test_wnba_uses_its_own_current_feed_and_saved_snapshot(self):
        with patch('wnba_model.fetch_wnba_team_stats',return_value=self.data(15)) as fetch:
            fields=team_ratings.rating_fields('WNBA','0')
        self.assertEqual(fields['ratings_team_count'],15)
        self.assertEqual(fields['team_off_rating'],100.)
        self.assertFalse(fields['ratings_stale'])
        fetch.assert_called_once()

from contextlib import ExitStack
import unittest
from unittest.mock import patch
import main
import tracker


class LeagueParityTests(unittest.TestCase):
    def check_pipeline(self, league, injury=None, minutes=30, minimum=0):
        result = {'player': 'Test Player', 'predictions': {'PTS': {'prediction': 20}},
                  'game_log': [{'MIN': minutes}], 'prediction_stages': {'raw': {'PTS': 20}},
                  'model_version': 'test'}
        predictor = 'predict' if league == 'NBA' else 'wnba_model.predict'
        finder = 'nba_find_player' if league == 'NBA' else 'wnba_model.find_player'
        with ExitStack() as stack:
            stack.enter_context(patch('main.' + predictor, return_value=result))
            stack.enter_context(patch('main.' + finder, return_value={'id': 123, 'full_name': 'Test Player'}))
            stack.enter_context(patch.object(main.odds, 'get_player_lines', return_value={'PTS': 19.5}))
            stack.enter_context(patch.object(main.odds, 'get_player_game_date', return_value='2026-10-22'))
            stack.enter_context(patch.object(main.injuries, 'get_player_injury', return_value=injury))
            adjust = stack.enter_context(patch.object(main, '_adjust_and_record'))
            stack.enter_context(patch.object(main, '_attach_p_over'))
            log = stack.enter_context(patch.object(main.tracker, 'log_prediction'))
            response = main._predict_and_record('Test Player', league, minimum)
            adjust.assert_called_once_with(result, league, injury, target_id=123)
            self.assertEqual(response['injury'], injury)
            if injury and injury['status'] == 'Out' or minutes < minimum:
                log.assert_not_called()
            else:
                self.assertEqual(log.call_args.args[2], league)
                self.assertEqual(log.call_args.kwargs['prediction_stages'], result['prediction_stages'])

    def test_same_adjustments_and_recording_for_both_leagues(self):
        for league in ('NBA', 'WNBA'):
            with self.subTest(league=league):
                self.check_pipeline(league)
                self.check_pipeline(league, injury={'status': 'Out'})
                self.check_pipeline(league, minutes=10, minimum=15)

    def test_accuracy_pending_counts_only_selected_league(self):
        log = [{'league': 'NBA', 'resolved': False}, {'league': 'WNBA', 'resolved': False},
               {'league': 'NBA', 'resolved': True, 'excluded': True}]
        self.assertEqual(tracker.compute_stats(log, 'NBA')['pending_count'], 1)
        self.assertEqual(tracker.compute_stats(log, 'WNBA')['pending_count'], 1)

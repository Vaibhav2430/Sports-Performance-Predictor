from unittest.mock import patch
import json
import unittest
import pandas as pd
import model
import market_estimate
import odds


class MarketEstimateTests(unittest.TestCase):
    def setUp(self):
        self.player = {'id': 1, 'full_name': 'Test Rookie'}
        self.context = {'team_stats': {
            'DEN': {'pace': 120., 'def_rtg': 130.}, 'OKC': {'pace': 110., 'def_rtg': 100.}},
            'positions': {}, 'pos_def': {}, 'timeseries': {}}

    def estimate(self, lines):
        with patch.object(market_estimate, 'player_team', return_value='OKC'), patch.object(odds, 'get_player_matchup', return_value={'home_team': 'Oklahoma City Thunder', 'away_team': 'Denver Nuggets'}):
            return market_estimate.predict_from_market(self.player, lines, self.context)

    def test_lines_and_verified_matchup_produce_bounded_estimate(self):
        result = self.estimate({'PTS': 20.5, 'AST': 4.5, 'REB': 6.5})
        for stat, base in [('PTS', 20.5), ('AST', 4.5), ('REB', 6.5)]:
            value = result['predictions'][stat]
            self.assertLessEqual(abs(value['market_adjustment'] - 1), .050001)
            self.assertIsNone(value['sigma'])
        self.assertEqual(result['next_opponent']['abbr'], 'DEN')
        self.assertEqual(result['games_used'], 0)
        self.assertFalse(result['apply_adjustments'])
        json.dumps(result, allow_nan=False)

    def test_partial_lines_leave_missing_stats_unavailable(self):
        result = self.estimate({'PTS': 11.5})
        self.assertIsNone(result['predictions']['AST']['prediction'])
        self.assertIsNotNone(result['predictions']['PTS']['prediction'])

    def test_absent_or_invalid_lines_cannot_invent_forecasts(self):
        for lines in ({}, {'PTS': float('nan')}, {'PTS': -3}):
            with self.assertRaises(model.NoHistory):
                self.estimate(lines)

    def test_unverified_matchup_does_not_adjust_line(self):
        with patch.object(market_estimate, 'player_team', return_value=''), patch.object(odds, 'get_player_matchup', return_value={}):
            result = market_estimate.predict_from_market(self.player, {'PTS': 10.5}, self.context)
        self.assertEqual(result['predictions']['PTS']['prediction'], 10.5)

    def test_model_with_no_history_routes_to_market(self):
        with patch.object(model, 'find_player', return_value=self.player), patch.object(model, 'fetch_game_log', return_value=pd.DataFrame()), patch.object(model, '_prediction_context', return_value=self.context), patch.object(market_estimate, 'player_team', return_value=''), patch.object(odds, 'get_player_matchup', return_value={}):
            result = model.predict('Test Rookie', market_lines={'PTS': 8.5})
        self.assertEqual(result['predictions']['PTS']['prediction'], 8.5)

    def test_similar_player_does_not_borrow_someone_elses_lines(self):
        self.assertIsNone(odds._resolve_player_key('Nikola Jokic', {'Nikola Topic': {'PTS': 9.5}}))
        self.assertEqual(odds._resolve_player_key('Nikola Jokić', {'Nikola Jokic': {}}), 'Nikola Jokic')

    def test_market_explanation_handles_missing_stat(self):
        import chatbot
        result = self.estimate({'PTS': 11.5})
        explanation = chatbot.build_reasoning(result)
        self.assertIn('no posted line', ' '.join(explanation['blocks']))

    def test_limited_history_returns_estimate_instead_of_error(self):
        history = pd.DataFrame({'GAME_DATE': pd.to_datetime(['2026-10-20']),
            'MATCHUP': ['OKC vs. DEN'], 'WL': ['W'], 'MIN': [20.],
            'PTS': [12.], 'AST': [4.], 'REB': [5.], 'HOME': [1], 'DAYS_REST': [2.]})
        with patch.object(model, 'fetch_game_log', side_effect=[history, pd.DataFrame()]), patch.object(model, '_prediction_context', return_value=self.context), patch.object(model, '_next_game', return_value=None):
            result = model.predict('Shai Gilgeous-Alexander', market_lines={})
        self.assertEqual(result['model_version'], 'limited-history-average-v1')
        json.dumps(result, allow_nan=False)

    def test_no_history_partial_market_estimate_survives_full_api_workflow(self):
        from contextlib import ExitStack
        import main
        with ExitStack() as stack:
            stack.enter_context(patch.object(model, 'find_player', return_value=self.player))
            stack.enter_context(patch.object(main, 'nba_find_player', return_value=self.player))
            stack.enter_context(patch.object(model, 'fetch_game_log', return_value=pd.DataFrame()))
            stack.enter_context(patch.object(model, '_prediction_context', return_value=self.context))
            stack.enter_context(patch.object(market_estimate, 'player_team', return_value=''))
            stack.enter_context(patch.object(odds, 'get_player_lines', return_value={'PTS': 12.5}))
            stack.enter_context(patch.object(odds, 'get_player_matchup', return_value={}))
            stack.enter_context(patch.object(odds, 'get_player_game_date', return_value='2026-10-22'))
            stack.enter_context(patch.object(main.injuries, 'get_player_injury', return_value=None))
            save = stack.enter_context(patch.object(main.tracker, 'log_prediction'))
            result = main._predict_and_record('Test Rookie', 'NBA')
            save.assert_called_once()
        self.assertEqual(result['predictions']['PTS']['prediction'],12.5)
        self.assertIsNone(result['predictions']['AST']['prediction'])
        self.assertNotIn('p_over',result['predictions']['PTS'])
        self.assertEqual(result['prediction_stages']['raw'],{'PTS':12.5})
        json.dumps(result,allow_nan=False)

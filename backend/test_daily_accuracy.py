import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

import pandas as pd
import main
import season_accuracy
from test_api_usage import asgi_request


def baseline():
    return {'season': '2026-27', 'date_end': '2026-10-21', 'player_games': 276,
            'by_stat': {stat: {'n': 276, 'within_margin': correct, 'margin': margin}
                        for stat, correct, margin in [('PTS', 151, 5), ('AST', 199, 2), ('REB', 210, 3)]}}


def record(day='2026-10-22', player='Test', league='NBA', resolved=True):
    return {'player': player, 'player_id': player, 'date': day, 'league': league,
            'resolved': resolved, 'predicted': {'PTS': 20, 'AST': 5, 'REB': 10},
            'lines': {'PTS': 20.5, 'AST': 5.5, 'REB': 10.5},
            'actual': {'PTS': 25, 'AST': 7, 'REB': 14} if resolved else {}}


class CombinedAccuracyTests(unittest.TestCase):
    def compute(self, entries, saved=None):
        return season_accuracy.combined(entries, baseline() if saved is None else saved, '2026-27')

    def test_baseline_counts_are_kept_without_new_results(self):
        result = self.compute([])
        self.assertEqual(result['by_stat']['PTS']['pct'], 54.7)
        self.assertEqual(result['by_stat']['AST']['pct'], 72.1)
        self.assertEqual(result['by_stat']['REB']['pct'], 76.1)
        self.assertEqual(result['by_stat']['PTS']['total'], 276)

    def test_new_results_add_raw_counts_with_inclusive_margins(self):
        result = self.compute([record()])
        for stat, expected in [('PTS', 152), ('AST', 200), ('REB', 210)]:
            self.assertEqual(result['by_stat'][stat]['correct'], expected)
            self.assertEqual(result['by_stat'][stat]['total'], 277)
            self.assertEqual(result['by_stat'][stat]['pct'], round(expected / 277 * 100, 1))
        self.assertEqual(result['date_end'], '2026-10-22')

    def test_overlapping_dates_other_leagues_and_seasons_are_not_counted(self):
        entries = [record('2026-10-21'), record('2026-10-20', resolved=False),
                   record(league='WNBA'), record('2025-10-22'), record('2027-10-22')]
        result = self.compute(entries)
        self.assertEqual(result['live_player_games'], 0)
        self.assertEqual(result['pending_count'], 0)
        self.assertEqual(result['by_stat']['PTS']['total'], 276)

    def test_pending_and_excluded_results_never_increase_denominators(self):
        excluded = {**record(player='Excluded'), 'excluded': True}
        result = self.compute([record(resolved=False), excluded])
        self.assertEqual(result['pending_count'], 1)
        self.assertEqual(result['by_stat']['PTS']['total'], 276)

    def test_duplicate_forecasts_do_not_increase_accuracy_samples(self):
        original = record()
        later = deepcopy(original)
        later['actual']['PTS'] = 100
        result = self.compute([original, later])
        self.assertEqual(result['by_stat']['PTS']['total'], 277)
        self.assertEqual(result['by_stat']['PTS']['correct'], 152)

    def test_nonfinite_actuals_are_ignored_per_stat(self):
        entry = record()
        entry['actual']['PTS'] = float('nan')
        result = self.compute([entry])
        self.assertEqual(result['by_stat']['PTS']['total'], 276)
        self.assertEqual(result['by_stat']['AST']['total'], 277)

    def test_old_baseline_does_not_leak_into_new_season(self):
        old = {**baseline(), 'season': '2025-26'}
        result = self.compute([record()], old)
        self.assertEqual(result['baseline_player_games'], 0)
        self.assertEqual(result['by_stat']['PTS']['total'], 1)

    def test_incompatible_margins_cannot_be_silently_combined(self):
        saved = baseline()
        saved['by_stat']['PTS']['margin'] = 10
        with self.assertRaises(ValueError):
            self.compute([], saved)


class DailyPipelineTests(unittest.TestCase):
    def frame(self, day='2026-10-22'):
        return pd.DataFrame([{'GAME_DATE': pd.Timestamp(day), 'MIN': 30,
                              'PTS': 25., 'AST': 7., 'REB': 14., 'Game_ID': '0022600001'}])

    def run_grade(self, entries, frame):
        with patch.object(main.tracker, 'load_log', return_value=entries), \
             patch.object(main.tracker, 'save_log') as save, \
             patch.object(main, 'nba_find_player', return_value={'id': 1}), \
             patch.object(main.wnba_model, 'find_player', return_value={'id': 1}), \
             patch.object(main, 'nba_fetch_game_log', return_value=frame), \
             patch.object(main.wnba_model, 'fetch_game_log', return_value=frame), \
             patch.object(main, '_detect_early_exit', return_value=False):
            main._resolve_all_locked()
        return save

    def test_both_leagues_grade_actuals_without_changing_original_forecast(self):
        for league in ('NBA', 'WNBA'):
            with self.subTest(league=league):
                entry = record('2020-10-22', league=league, resolved=False)
                original = deepcopy(entry['predicted'])
                save = self.run_grade([entry], self.frame('2020-10-22'))
                self.assertTrue(entry['resolved'])
                self.assertEqual(entry['actual']['PTS'], 25)
                self.assertEqual(entry['predicted'], original)
                save.assert_called_once()

    def test_adjacent_day_is_never_used_as_the_actual_game(self):
        entry = record('2020-10-22', resolved=False)
        save = self.run_grade([entry], self.frame('2020-10-21'))
        self.assertFalse(entry['resolved'])
        save.assert_not_called()

    def test_delayed_box_score_is_retried_on_next_pass(self):
        entry = record('2020-10-22', resolved=False)
        self.run_grade([entry], self.frame('2020-10-21'))
        self.assertFalse(entry['resolved'])
        self.run_grade([entry], self.frame('2020-10-22'))
        self.assertTrue(entry['resolved'])

    def test_incomplete_box_score_is_not_permanently_resolved(self):
        entry = record('2020-10-22', resolved=False)
        frame = self.frame('2020-10-22')
        frame['AST'] = float('nan')
        save = self.run_grade([entry], frame)
        self.assertFalse(entry['resolved'])
        save.assert_not_called()

    def test_get_accuracy_combines_saved_results_without_running_jobs(self):
        with patch.object(main.tracker, 'load_log', return_value=[record()]), \
             patch.object(main.season_accuracy, 'load_baseline', return_value=baseline()), \
             patch.object(main, '_resolve_all') as resolve:
            status, _, result = asyncio.run(asgi_request('/accuracy', query='league=NBA&metric=within_margin'))
        self.assertEqual(status, 200)
        self.assertEqual(result['by_stat']['PTS']['total'], 277)
        resolve.assert_not_called()


if __name__ == '__main__':
    unittest.main()

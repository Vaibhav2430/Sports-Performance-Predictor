from datetime import date
import unittest
import numpy as np
import pandas as pd
from backtest_season import evaluate


class SeasonBacktestTests(unittest.TestCase):
    def prior(self):
        n = 25
        return pd.DataFrame({'GAME_ID': [f'002250{i:04d}' for i in range(n)],
            'GAME_DATE': pd.date_range('2026-02-01', periods=n, freq='2D'),
            'PLAYER_ID': 1, 'PLAYER_NAME': 'Test Player', 'MATCHUP': 'DEN vs. OKC',
            'MIN': 30., 'PTS': np.arange(n) % 10 + 15, 'AST': np.arange(n) % 5 + 3,
            'REB': np.arange(n) % 7 + 5})

    def current(self):
        return pd.DataFrame({'GAME_ID': ['0022600001', '0022600002', '0012600003', '0022600004'],
            'GAME_DATE': ['2026-10-20', '2026-10-21', '2026-10-19', '2026-10-22'],
            'PLAYER_ID': 1, 'PLAYER_NAME': 'Test Player', 'MATCHUP': 'DEN @ OKC',
            'MIN': 30., 'PTS': 20., 'AST': 5., 'REB': 10.})

    def test_completed_regular_season_only_and_no_target_leakage(self):
        current = self.current()
        report, rows = evaluate(self.prior(), current, '2026-27', date(2026, 10, 22))
        self.assertEqual(report['completed_games'], 2)
        self.assertEqual(len(rows), 6)
        self.assertTrue((rows.training_through < rows.date).all())
        current.loc[0, ['PTS', 'AST', 'REB']] = 1000
        _, changed = evaluate(self.prior(), current, '2026-27', date(2026, 10, 22))
        np.testing.assert_array_equal(rows[rows.date == '2026-10-20'].prediction,
                                      changed[changed.date == '2026-10-20'].prediction)
        for stat, part in rows.groupby('stat'):
            self.assertAlmostEqual(report['by_stat'][stat]['mae'], part.absolute_error.mean(), places=2)

    def test_insufficient_history_is_disclosed(self):
        current = self.current()
        rookie = current.iloc[[0]].copy()
        rookie['PLAYER_ID'] = 2
        rookie['PLAYER_NAME'] = 'Rookie'
        report, rows = evaluate(self.prior(), pd.concat([current, rookie]), '2026-27', date(2026, 10, 22))
        self.assertEqual(report['skipped_player_games'], 1)
        self.assertNotIn('Rookie', rows.player.tolist())

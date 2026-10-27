import unittest
from export_predictions import build_export


class PredictionExportTests(unittest.TestCase):
    def test_only_requested_nba_season_and_grading_groups(self):
        base = {'league': 'NBA', 'date': '2026-10-22', 'player': 'Player', 'resolved': True}
        rows = [base, {**base, 'excluded': True}, {**base, 'resolved': False},
                {**base, 'league': 'WNBA'}, {**base, 'date': '2025-10-22'},
                {**base, 'date': '2027-07-01'}]
        result = build_export(rows, '2026-27', 'test')
        self.assertEqual(result['counts'], {'player_games': 3, 'graded': 1, 'excluded': 1, 'pending': 1})

    def test_preserves_analysis_fields_without_exporting_unknown_fields(self):
        row = {'league': 'NBA', 'date': '2026-10-23', 'resolved': True,
               'lines': {'PTS': 25.5}, 'predicted': {'PTS': 26}, 'actual': {'PTS': 27},
               'early_exit': True, 'private_config': 'never export'}
        exported = build_export([row], '2026-27', 'test')['graded_records'][0]
        self.assertEqual(exported['actual'], row['actual'])
        self.assertTrue(exported['early_exit'])
        self.assertNotIn('private_config', exported)

    def test_invalid_seasons(self):
        for season in ('2026', '2026-28', '../2026-27'):
            with self.assertRaises(ValueError):
                build_export([], season, 'test')

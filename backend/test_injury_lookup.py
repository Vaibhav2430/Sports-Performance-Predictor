import unittest
from unittest.mock import patch
import injuries

class InjuryLookupTests(unittest.TestCase):
    def test_jokic_does_not_match_topic(self):
        report = {'nikola topic': {'name': 'Nikola Topic', 'status': 'Out'}}
        with patch.object(injuries, 'fetch_injuries', return_value=report):
            for name in ('Nikola Jokic', 'Nikola Jokić'):
                self.assertIsNone(injuries.get_player_injury(name, 'NBA'))
            self.assertEqual(injuries.get_player_injury('Nikola Topić', 'NBA'), report['nikola topic'])

    def test_real_report_matches_accent_and_punctuation_variants(self):
        for stored, query in [('Nikola Jokić', 'Nikola Jokic'), ('A’ja Wilson', "A'ja Wilson")]:
            report = {stored.lower(): {'name': stored, 'status': 'Questionable'}}
            with patch.object(injuries, 'fetch_injuries', return_value=report):
                self.assertEqual(injuries.get_player_injury(query, 'NBA'), report[stored.lower()])

    def test_similar_name_is_not_identity(self):
        with patch.object(injuries, 'fetch_injuries', return_value={'jalen williams': {'status': 'Out'}}):
            self.assertIsNone(injuries.get_player_injury('Jaylin Williams', 'NBA'))

import unittest
from unittest.mock import patch
from model import find_player

class PlayerLookupTests(unittest.TestCase):
    def test_jokic_with_and_without_accents(self):
        for name in ('Nikola Jokic', 'Nikola Jokić', 'jokic', '  NIKOLA JOKIC  '):
            with self.subTest(name=name):
                self.assertEqual(find_player(name)['id'], 203999)

    def test_other_accented_player(self):
        self.assertEqual(find_player('Luka Doncic')['id'], 1629029)

    def test_empty_or_unknown_is_not_a_player(self):
        self.assertIsNone(find_player('  '))
        with patch('nba_data.player_directory', return_value=[]):
            self.assertIsNone(find_player('Not A Real Player'))

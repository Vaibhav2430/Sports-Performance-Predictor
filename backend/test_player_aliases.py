import unittest
from unittest.mock import patch

import model
import odds


class SportsbookNameTests(unittest.TestCase):
    def test_draftkings_suffix_resolves_to_unique_nba_player(self):
        paul = {'id': 1630194, 'full_name': 'Paul Reed'}
        with patch.object(model.nba_players, 'get_players', return_value=[paul]):
            self.assertEqual(model.find_player('Paul Reed Jr'), paul)
        self.assertEqual(odds._resolve_player_key('Paul Reed', {'Paul Reed Jr': {'PTS': 8.5}}), 'Paul Reed Jr')

    def test_ambiguous_normalized_props_are_not_borrowed(self):
        self.assertIsNone(odds._resolve_player_key('Player', {'Player Jr': {}, 'Player Sr': {}}))
        self.assertEqual(odds._resolve_player_key('Player Jr', {'Player Jr': {}, 'Player Sr': {}}), 'Player Jr')

    def test_similar_first_names_still_do_not_match(self):
        self.assertIsNone(odds._resolve_player_key('Nikola Jokic', {'Nikola Topic': {'PTS': 10.5}}))

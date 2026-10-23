import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pandas as pd
import nba_data
import model


class NBADataTests(unittest.TestCase):
    def setUp(self):
        nba_data._frames.clear()

    def frame(self):
        return pd.DataFrame({'PLAYER_ID': [1,2], 'GAME_ID':['0022600001','0012600002'],
            'GAME_DATE':['2026-10-20','2026-10-10'], 'MIN':[20.,15.],
            'PTS':[10.,12.], 'AST':[2.,3.], 'REB':[4.,5.], 'MATCHUP':['OKC vs. DEN']*2, 'WL':['W']*2})

    def test_shared_download_excludes_preseason(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(nba_data,'CACHE',Path(directory)), patch.object(nba_data.playergamelogs,'PlayerGameLogs') as api:
            api.return_value.get_data_frames.return_value=[self.frame()]
            first=nba_data.player_games(1,'2026-27')
            second=nba_data.player_games(2,'2026-27')
            api.assert_called_once()
            self.assertEqual(len(first),1)
            self.assertTrue(second.empty)
            self.assertEqual(first.iloc[0].Game_ID,'0022600001')

    def test_failed_feed_uses_labeled_saved_history(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(nba_data,'CACHE',Path(directory)), patch.object(nba_data.playergamelogs,'PlayerGameLogs',side_effect=TimeoutError):
            path=Path(directory)/'2026-27.csv'
            self.frame().to_csv(path,index=False)
            os.utime(path,(0,0))
            frame=nba_data.player_games(1,'2026-27')
            self.assertEqual(frame.attrs['history_source'],'stale_cache')
            self.assertEqual(len(frame),1)

    def test_failed_feed_without_snapshot_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(nba_data,'CACHE',Path(directory)), patch.object(nba_data.playergamelogs,'PlayerGameLogs',side_effect=TimeoutError):
            with self.assertRaises(nba_data.HistoryUnavailable):
                nba_data.player_games(1,'2026-27')

    def test_rookie_not_in_static_directory_can_resolve(self):
        with patch('nba_data.player_directory',return_value=[{'id':999999,'full_name':'New Rookie Name','is_active':True}]):
            self.assertEqual(model.find_player('New Rookie Name')['id'],999999)

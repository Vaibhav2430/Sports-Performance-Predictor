"""Live tests use a disposable private schema, never production tables.

Opt in with COURTCAST_INTEGRATION_TESTS=1 and a configured DATABASE_URL.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid
import tempfile
from cryptography.fernet import Fernet
import backups

import api_usage
import database


def forecast(player='Test', prediction=20):
    return {'player': player, 'player_id': player, 'league': 'NBA', 'date': '2026-10-22',
            'lines': {'PTS': 19.5}, 'predicted': {'PTS': prediction}, 'actual': {}, 'resolved': False}


class StorageUnitTests(unittest.TestCase):
    def test_grading_preserves_forecast_identity(self):
        original = forecast()
        graded = {**original, 'actual': {'PTS': 22}, 'resolved': True, 'early_exit': True}
        self.assertEqual(database.entry_key(original), database.entry_key(graded))
        self.assertNotEqual(database.entry_key(original), database.entry_key(forecast(prediction=25)))

    def test_invalid_backend_configuration_never_falls_back_to_local(self):
        with patch.dict(os.environ, {'STORAGE_BACKEND': 'misspelled'}):
            with self.assertRaises(database.DatabaseUnavailable):
                database.enabled()

    def test_nonfinite_legacy_values_become_json_null(self):
        self.assertEqual(database.clean({'PTS': float('nan'), 'AST': float('inf')}), {'PTS': None, 'AST': None})


@unittest.skipUnless(os.environ.get('COURTCAST_INTEGRATION_TESTS') == '1', 'Live database tests are opt-in')
class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = 'courtcast_test_' + uuid.uuid4().hex
        cls.real_connect = staticmethod(database.connect)
        sql = Path(__file__).with_name('migrations').joinpath('001_storage.sql').read_text()
        with cls.real_connect() as connection:
            connection.execute(sql.replace('courtcast', cls.schema))
        cls.connection_patch = patch.object(database, 'connect', cls.scoped_connect)
        cls.connection_patch.start()

    @classmethod
    @contextmanager
    def scoped_connect(cls):
        from psycopg import sql
        with cls.real_connect() as connection:
            connection.execute(sql.SQL('SET LOCAL search_path TO {},pg_catalog').format(sql.Identifier(cls.schema)))
            yield connection

    @classmethod
    def tearDownClass(cls):
        from psycopg import sql
        cls.connection_patch.stop()
        with cls.real_connect() as connection:
            connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(cls.schema)))

    def setUp(self):
        with database.connect() as connection:
            connection.execute('TRUNCATE predictions,documents,budgets,leases,snapshots')

    def test_first_forecast_is_preserved_under_concurrent_writes(self):
        database.insert_prediction(forecast())
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(database.insert_prediction, [forecast(prediction=i) for i in range(25, 33)]))
        self.assertEqual(database.read_predictions(), [forecast()])

    def test_grading_preserves_original_and_concurrently_added_players(self):
        database.insert_prediction(forecast())
        stale = database.read_predictions()
        database.insert_prediction(forecast('Other'))
        stale[0].update(actual={'PTS': 22}, resolved=True)
        database.save_predictions(stale)
        database.save_predictions([forecast()])
        rows = database.read_predictions()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['predicted']['PTS'], 20)
        self.assertEqual(rows[0]['actual']['PTS'], 22)
        self.assertTrue(rows[0]['resolved'])
        database.insert_prediction(forecast(prediction=30))
        self.assertEqual(len(database.read_predictions()), 2)

    def test_import_is_idempotent_and_does_not_undo_grading(self):
        database.save_predictions([forecast()], importing=True)
        graded = {**forecast(), 'actual': {'PTS': 21}, 'resolved': True}
        database.save_predictions([graded])
        database.save_predictions([forecast()], importing=True)
        self.assertEqual(database.read_predictions(), [graded])

    def test_concurrent_clients_share_atomic_budget(self):
        def attempt(_):
            try:
                api_usage.PostgresUsage().acquire([api_usage.Budget('test-budget', 2, 3600)])
                return 1
            except api_usage.UsageLimitExceeded:
                return 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(attempt, range(8))), 2)

    def test_lease_rejection_rolls_back_budget_and_releases_on_exit(self):
        storage = api_usage.PostgresUsage()
        with storage.acquire(resource='test-job'):
            with self.assertRaises(api_usage.UsageLimitExceeded):
                storage.acquire([api_usage.Budget('unused', 1, 3600)], resource='test-job')
        with storage.acquire([api_usage.Budget('unused', 1, 3600)], resource='test-job'):
            pass

    def test_provider_quota_and_saved_lines_persist_across_instances(self):
        first = api_usage.PostgresUsage()
        first.observe_provider_usage(499)
        first.observe_provider_usage(2)
        first.save_snapshot('lines', {'Player': {'PTS': 20.5}})
        second = api_usage.PostgresUsage()
        self.assertEqual(second.snapshot('lines'), {'Player': {'PTS': 20.5}})
        with patch.object(api_usage, 'store', return_value=second):
            with self.assertRaises(api_usage.UsageLimitExceeded):
                api_usage.reserve_odds_request('player_points,player_assists,player_rebounds')

    def test_documents_merge_atomically_and_browser_roles_cannot_read_tables(self):
        database.save_document('ratings', {'updated_at': 123})
        database.save_document('ratings', {'updated_at': 1}, importing=True)
        self.assertEqual(database.read_document('ratings')['updated_at'], 123)
        def increment(_):
            database.update_document('counter', lambda value: {'n': value.get('n', 0) + 1})
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(increment, range(8)))
        self.assertEqual(database.read_document('counter'), {'n': 8})
        with database.connect() as connection:
            for role in ('anon', 'authenticated', 'service_role'):
                allowed = connection.execute('SELECT has_schema_privilege(%s,%s,\'USAGE\')', (role, self.schema)).fetchone()[0]
                self.assertFalse(allowed)

    def test_encrypted_backup_restores_deleted_data_and_preserves_newer_rows(self):
        original=forecast()
        database.insert_prediction(original)
        database.save_document('accuracy',{'n':276})
        storage=api_usage.PostgresUsage()
        storage.acquire([api_usage.Budget('quota',10,3600,3)])
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'BACKUP_DIRECTORY':directory,'BACKUP_ENCRYPTION_KEY':Fernet.generate_key().decode()}):
            path,counts=backups.create_backup()
            self.assertEqual(counts['predictions'],1)
            with database.connect() as connection:
                connection.execute('DELETE FROM predictions')
            database.insert_prediction(forecast('Newer'))
            database.save_document('accuracy',{'n':300})
            storage.acquire([api_usage.Budget('quota',10,3600,4)])
            backups.restore(path,apply=True)
            backups.restore(path,apply=True)
            rows=database.read_predictions()
            self.assertEqual(len(rows),2)
            self.assertIn(original,rows)
            self.assertEqual(database.read_document('accuracy'),{'n':300})
            with database.connect() as connection:
                self.assertEqual(connection.execute('SELECT used FROM budgets').fetchone()[0],7)


if __name__ == '__main__':
    unittest.main()

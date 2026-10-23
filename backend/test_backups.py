from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
import backups
import database


def sample():
    entry = {'player': 'Test', 'league': 'NBA', 'date': '2026-10-22',
             'predicted': {'PTS': 20.}, 'actual': {}, 'resolved': False}
    data = {'predictions': [{'entry_key': database.entry_key(entry), 'payload': entry}],
            'documents': [{'key': 'accuracy', 'payload': {'n': 276}}],
            'budgets': [{'key': 'credits', 'used': 15, 'expires': 9999999999}],
            'snapshots': [{'key': 'lines', 'payload': '{}'}]}
    return {'format': 'courtcast-backup-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
            'counts': {k: len(v) for k,v in data.items()},
            'sha256': hashlib.sha256(backups.canonical(data)).hexdigest(), 'data': data}


class BackupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.env = patch.dict(os.environ, {'BACKUP_DIRECTORY':directory.name, 'BACKUP_ENCRYPTION_KEY':Fernet.generate_key().decode(), 'BACKUP_ENABLED':'true'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def create(self):
        with patch.object(backups, 'capture', return_value=sample()):
            return backups.create_backup()[0]

    def test_archive_is_encrypted_verified_and_private(self):
        path = self.create()
        self.assertNotIn(b'predicted', path.read_bytes())
        self.assertEqual(backups.decode(path.read_bytes())['counts']['predictions'],1)
        self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_wrong_key_and_tampered_files_are_rejected(self):
        path = self.create()
        with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY':Fernet.generate_key().decode()}):
            with self.assertRaises(backups.BackupError): backups.decode(path.read_bytes())
        token=bytearray(path.read_bytes()); token[len(token)//2]^=1
        with self.assertRaises(backups.BackupError): backups.decode(bytes(token))

    def test_corrupt_manifest_is_rejected_before_restore(self):
        archive=sample(); archive['counts']['predictions']=100
        token=backups.cipher().encrypt(gzip.compress(backups.canonical(archive)))
        path=self.directory/'bad.ccbackup'; path.write_bytes(token)
        with patch.object(database,'connect') as connection:
            with self.assertRaises(backups.BackupError): backups.restore(path,apply=True)
            connection.assert_not_called()

    def test_restore_preview_never_connects_to_database(self):
        path=self.create()
        with patch.object(database,'connect') as connection:
            self.assertEqual(backups.restore(path)['predictions'],1)
            connection.assert_not_called()

    def test_failed_export_keeps_previous_daily_backup(self):
        path=self.create(); original=path.read_bytes()
        with patch.object(backups,'capture',side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError): backups.create_backup()
        self.assertEqual(path.read_bytes(),original)

    def test_retention_runs_only_after_verified_replacement(self):
        old=self.directory/'courtcast-2000-01-01.ccbackup'; old.write_bytes(b'old')
        os.utime(old,(0,0))
        unrelated=self.directory/'other.txt'; unrelated.write_text('keep')
        path=self.create()
        self.assertFalse(old.exists()); self.assertTrue(path.exists()); self.assertTrue(unrelated.exists())

    def test_daily_schedule_skips_valid_copy_and_repairs_corruption(self):
        self.create()
        with patch.object(database,'enabled',return_value=True),patch.object(backups,'create_backup') as create:
            backups.backup_if_due(); create.assert_not_called()
            next(self.directory.glob('*.ccbackup')).write_bytes(b'broken')
            backups.backup_if_due(); create.assert_called_once()

    def test_missing_key_does_not_export_plaintext(self):
        with patch.dict(os.environ,{'BACKUP_ENCRYPTION_KEY':''}),patch.object(backups,'capture') as capture:
            with self.assertRaises(backups.BackupError): backups.create_backup()
            capture.assert_not_called()
        self.assertEqual(list(self.directory.iterdir()),[])

"""Encrypted, consistent exports of CourtCast application data (not account secrets)."""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import threading
import time
import zlib

from cryptography.fernet import Fernet, InvalidToken
import database

TABLES = {
    'predictions': ('entry_key', 'payload'),
    'documents': ('key', 'payload'),
    'budgets': ('key', 'used', 'expires'),
    'snapshots': ('key', 'payload'),
}
DEFAULT_DIRECTORY = Path(__file__).parent / 'data_cache/backups'
logger = logging.getLogger(__name__)


class BackupError(RuntimeError):
    pass


def cipher():
    try:
        return Fernet(os.environ.get('BACKUP_ENCRYPTION_KEY', '').encode())
    except (ValueError, TypeError):
        raise BackupError('A valid private BACKUP_ENCRYPTION_KEY is required.') from None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def capture():
    data = {}
    with database.connect() as connection:
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        for table, columns in TABLES.items():
            order = 'sequence' if table == 'predictions' else 'key'
            rows = connection.execute(f'SELECT {",".join(columns)} FROM {table} ORDER BY {order}').fetchall()
            data[table] = [dict(zip(columns, row)) for row in rows]
    return {'format': 'courtcast-backup-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
            'counts': {key: len(rows) for key, rows in data.items()},
            'sha256': hashlib.sha256(canonical(data)).hexdigest(), 'data': data}


def decode(token):
    try:
        archive = json.loads(gzip.decompress(cipher().decrypt(token)))
        if archive.get('format') != 'courtcast-backup-v1':
            raise ValueError('Unknown format')
        data = archive['data']
        if set(data) != set(TABLES) or archive['sha256'] != hashlib.sha256(canonical(data)).hexdigest():
            raise ValueError('Integrity failure')
        for table, columns in TABLES.items():
            if archive['counts'][table] != len(data[table]):
                raise ValueError('Count mismatch')
            if any(set(row) != set(columns) for row in data[table]):
                raise ValueError('Invalid row shape')
        for row in data['predictions']:
            if row['entry_key'] != database.entry_key(row['payload']):
                raise ValueError('Invalid forecast identity')
        return archive
    except BackupError:
        raise
    except (InvalidToken, ValueError, KeyError, TypeError, OSError, EOFError, zlib.error):
        raise BackupError('Backup verification failed: wrong key, damaged file, or unsupported data.') from None


def directory():
    return Path(os.environ.get('BACKUP_DIRECTORY') or DEFAULT_DIRECTORY)


def create_backup():
    encryption = cipher()  # Validate before any database export.
    archive = capture()
    token = encryption.encrypt(gzip.compress(canonical(archive)))
    verified = decode(token)
    if verified != archive:
        raise BackupError('Backup round-trip verification failed.')
    destination = directory()
    destination.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    path = destination / f'courtcast-{day}.ccbackup'
    temp = destination / ('.backup-' + secrets.token_hex(12))
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(token)
            handle.flush()
            os.fsync(handle.fileno())
        # Verify the bytes actually written before replacing the previous daily copy.
        decode(temp.read_bytes())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    prune(destination, keep=path)
    return path, archive['counts']


def prune(destination, keep):
    days = max(1, int(os.environ.get('BACKUP_RETENTION_DAYS', '30')))
    cutoff = time.time() - days * 86400
    for path in destination.glob('courtcast-*.ccbackup'):
        if path != keep and re.fullmatch(r'courtcast-\d{4}-\d{2}-\d{2}\.ccbackup', path.name) and path.stat().st_mtime < cutoff:
            path.unlink()


def backup_if_due():
    if os.environ.get('BACKUP_ENABLED', '').lower() != 'true' or not database.enabled():
        return None
    today = directory() / ('courtcast-' + datetime.now(timezone.utc).strftime('%Y-%m-%d') + '.ccbackup')
    if today.exists():
        # A missing/deleted/corrupt snapshot is repaired on the next check.
        try:
            decode(today.read_bytes())
            return None
        except BackupError:
            logger.warning('The current backup could not be verified; replacing it.')
    return create_backup()


def worker(stop):
    while not stop.is_set():
        try:
            result = backup_if_due()
            if result:
                logger.info('Daily encrypted backup verified: %s prediction records', result[1]['predictions'])
        except Exception:
            # No driver errors, environment values, or payloads in logs.
            logger.error('Daily backup failed; will retry. Check private backup settings and database availability.')
        if stop.wait(3600):
            break


def restore(path, apply=False):
    archive = decode(Path(path).read_bytes())
    if not apply:
        return archive['counts']
    data = archive['data']
    # Non-destructive recovery: newer records/grades/documents are retained and
    # live quota counters can only increase. Job leases are intentionally omitted.
    with database.connect() as connection:
        connection.execute('SELECT pg_advisory_xact_lock(78432002)')
        with connection.cursor() as cursor:
            cursor.executemany('INSERT INTO predictions(entry_key,payload) VALUES (%s,%s::jsonb) ON CONFLICT DO NOTHING',
                               [(row['entry_key'], database.json_text(row['payload'])) for row in data['predictions']])
            cursor.executemany('UPDATE predictions SET payload=payload || %s::jsonb WHERE entry_key=%s AND NOT COALESCE((payload->>\'resolved\')::boolean,false)',
                               [(database.json_text({key: row['payload'][key] for key in ('actual', 'resolved', 'excluded', 'early_exit') if key in row['payload']}), row['entry_key'])
                                for row in data['predictions'] if row['payload'].get('resolved')])
            cursor.executemany('INSERT INTO documents(key,payload) VALUES (%s,%s::jsonb) ON CONFLICT DO NOTHING',
                               [(row['key'], database.json_text(row['payload'])) for row in data['documents']])
            cursor.executemany('INSERT INTO snapshots(key,payload) VALUES (%s,%s) ON CONFLICT DO NOTHING',
                               [(row['key'], row['payload']) for row in data['snapshots']])
            cursor.executemany('INSERT INTO budgets(key,used,expires) VALUES (%s,%s,%s) ON CONFLICT(key) DO UPDATE SET used=GREATEST(budgets.used,excluded.used),expires=GREATEST(budgets.expires,excluded.expires)',
                               [(row['key'], row['used'], row['expires']) for row in data['budgets']])
    return archive['counts']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore', type=Path, help='Verify an archive; add --apply to recover missing records')
    parser.add_argument('--apply', action='store_true', help='Apply a verified restore to the configured database')
    args = parser.parse_args()
    try:
        if args.apply and not args.restore:
            parser.error('--apply requires --restore')
        if args.restore:
            counts = restore(args.restore, args.apply)
            print(('Restore merged.' if args.apply else 'Backup verified; no database changes.'), counts)
        else:
            path, counts = create_backup()
            print('Encrypted backup created and verified:', path, counts)
    except (BackupError, database.DatabaseUnavailable, OSError):
        parser.exit(1, 'Backup operation failed. Check the private settings, key, file, and database availability.\n')


if __name__ == '__main__':
    main()

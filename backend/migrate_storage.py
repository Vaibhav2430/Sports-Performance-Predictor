"""Idempotent local-to-Supabase import. Run with the local backend stopped."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import time

import database

ROOT = Path(__file__).parent


def migrate():
    # Fail before touching files if credentials are invalid.
    with database.connect() as connection:
        connection.execute('SELECT 1')
    backup = ROOT / 'data_cache' / 'migration_backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup.mkdir(parents=True)
    sources = {'injury-history': ROOT / 'injury_history.json',
               'nba-accuracy-baseline': ROOT / 'data_cache/nba/accuracy_baseline.json',
               'nba-season-backtest': ROOT / 'evaluation_results/nba_season/report.json',
               'ratings-NBA': ROOT / 'data_cache/nba/display_ratings.json',
               'ratings-WNBA': ROOT / 'data_cache/nba/wnba_display_ratings.json',
               'nba-context': ROOT / 'data_cache/nba/context.json'}
    log_path = ROOT / 'predictions_log.json'
    entries = json.loads(log_path.read_text()) if log_path.exists() else []
    for path in [log_path, *sources.values()]:
        if path.exists():
            target = backup / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    with database.connect() as connection:
        connection.execute((ROOT / 'migrations/001_storage.sql').read_text())
    database.save_predictions(entries, importing=True)
    for key, path in sources.items():
        if path.exists():
            database.save_document(key, json.loads(path.read_text()), importing=True)
    for path in (ROOT / 'data_cache/nba').glob('*.csv'):
        prefix = 'nba-players-' if path.stem.startswith('players-') else 'nba-games-'
        season = path.stem.removeprefix('players-')
        database.save_document(prefix + season, {'csv': path.read_text(), 'updated_at': path.stat().st_mtime}, importing=True)
    # Copy all live counters, not just paid credits. MAX prevents rerunning the
    # importer from reducing usage accumulated after a successful cutover.
    import os
    local_path = Path(os.environ.get('API_USAGE_DB') or ROOT / 'data_cache/api_usage.sqlite3')
    if local_path.exists():
        source = sqlite3.connect(local_path)
        target = sqlite3.connect(backup / 'api_usage.sqlite3')
        try:
            source.backup(target)
            counters = source.execute('SELECT key,used,expires FROM budgets WHERE expires>?', (time.time(),)).fetchall()
            snapshots = source.execute('SELECT key,payload FROM snapshots').fetchall()
        finally:
            source.close()
            target.close()
        with database.connect() as connection:
            for key, used, expires in counters:
                connection.execute('INSERT INTO budgets VALUES (%s,%s,%s) ON CONFLICT(key) DO UPDATE SET used=GREATEST(budgets.used,excluded.used),expires=GREATEST(budgets.expires,excluded.expires)', (key, used, expires))
            for key, payload in snapshots:
                connection.execute('INSERT INTO snapshots VALUES (%s,%s) ON CONFLICT DO NOTHING', (key, payload))
    saved = database.read_predictions()
    expected = {database.entry_key(entry) for entry in entries}
    actual = {database.entry_key(entry) for entry in saved}
    if not expected <= actual:
        raise RuntimeError('Import verification failed. Local storage remains active.')
    import tracker
    if not database.enabled() and tracker.compute_stats(database.clean(entries)) != tracker.compute_stats(saved):
        raise RuntimeError('Accuracy verification failed. Local storage remains active.')
    print(f'Imported and verified {len(expected)} prediction records. Local backup: {backup}')
    print('Database is ready. Set STORAGE_BACKEND=postgres and restart the backend to activate it.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Create private tables and copy local records without deleting sources')
    args = parser.parse_args()
    try:
        if args.apply:
            migrate()
        else:
            with database.connect() as connection:
                connection.execute('SELECT 1')
            print('Encrypted database connection successful. No data changed.')
    except database.DatabaseUnavailable:
        parser.exit(1, 'Database connection failed. Check private connection settings; local data was not deleted.\n')

"""Backend-only PostgreSQL persistence. Never log connection credentials."""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name('.env'))


class DatabaseUnavailable(RuntimeError):
    pass


def enabled():
    backend = os.environ.get('STORAGE_BACKEND', 'local')
    if backend not in ('local', 'postgres'):
        raise DatabaseUnavailable('Invalid storage backend configuration.')
    return backend == 'postgres'


@contextmanager
def connect():
    import psycopg
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise DatabaseUnavailable('Database connection is not configured.')
    try:
        with psycopg.connect(url, sslmode='require', connect_timeout=10,
                             options='-c statement_timeout=15000 -c lock_timeout=5000 -c search_path=courtcast,pg_catalog') as connection:
            yield connection
    except psycopg.Error:
        # Some driver errors contain the connection string. Suppress the chain
        # as well as the message so logs/HTTP responses cannot expose secrets.
        raise DatabaseUnavailable('Database is temporarily unavailable; saved data was not changed.') from None


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def json_text(value):
    return json.dumps(clean(value), allow_nan=False, sort_keys=True)


def entry_key(entry):
    # Resolution must not change the identity of an original forecast.
    original = {k: v for k, v in entry.items() if k not in ('actual', 'resolved', 'excluded', 'early_exit')}
    return hashlib.sha256(json_text(original).encode()).hexdigest()


def read_predictions():
    with connect() as connection:
        return [row[0] for row in connection.execute('SELECT payload FROM predictions ORDER BY sequence')]


def insert_prediction(entry):
    with connect() as connection:
        # All hosts serialize the check-and-insert, including missing rows.
        connection.execute('SELECT pg_advisory_xact_lock(78432002)')
        existing = connection.execute("SELECT entry_key,payload FROM predictions WHERE payload->>'league'=%s AND payload->>'player'=%s AND payload->>'date'=%s",
                                      (entry['league'], entry['player'], entry['date'])).fetchall()
        for _, saved in existing:
            values = saved.get('predicted', {})
            if values and all(isinstance(v, (int, float)) and math.isfinite(v) for v in values.values()):
                return  # Keep the first forecast, even after it is graded.
        for key, _ in existing:
            connection.execute('DELETE FROM predictions WHERE entry_key=%s', (key,))
        connection.execute('INSERT INTO predictions(entry_key,payload) VALUES (%s,%s::jsonb) ON CONFLICT DO NOTHING',
                           (entry_key(entry), json_text(entry)))


def save_predictions(entries, importing=False):
    with connect() as connection:
        with connection.cursor() as cursor:
            if importing:
                cursor.executemany('INSERT INTO predictions(entry_key,payload) VALUES (%s,%s::jsonb) ON CONFLICT DO NOTHING',
                                   [(entry_key(entry), json_text(entry)) for entry in entries])
            else:
                # Merge grading fields only; stale writers cannot overwrite an
                # original forecast or remove a concurrently inserted record.
                grades = [(json_text({k: entry[k] for k in ('actual', 'resolved', 'excluded', 'early_exit') if k in entry}), entry_key(entry))
                          for entry in entries if entry.get('resolved')]
                cursor.executemany('UPDATE predictions SET payload=payload || %s::jsonb WHERE entry_key=%s AND NOT COALESCE((payload->>\'resolved\')::boolean,false)', grades)


def read_document(key):
    with connect() as connection:
        row = connection.execute('SELECT payload FROM documents WHERE key=%s', (key,)).fetchone()
        return row[0] if row else None


def save_document(key, value, importing=False):
    clause = 'DO NOTHING' if importing else 'DO UPDATE SET payload=excluded.payload'
    with connect() as connection:
        connection.execute('INSERT INTO documents(key,payload) VALUES (%s,%s::jsonb) ON CONFLICT(key) ' + clause,
                           (key, json_text(value)))


def update_document(key, change):
    with connect() as connection:
        connection.execute('SELECT pg_advisory_xact_lock(78432003)')
        row = connection.execute('SELECT payload FROM documents WHERE key=%s FOR UPDATE', (key,)).fetchone()
        value = change(row[0] if row else {})
        connection.execute('INSERT INTO documents(key,payload) VALUES (%s,%s::jsonb) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload',
                           (key, json_text(value)))

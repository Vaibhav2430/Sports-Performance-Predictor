"""Atomic usage budgets and expiring job leases shared by local worker processes.

All workers must use the same API_USAGE_DB on a persistent local volume. This
SQLite store is for one backend host, not separate hosts or network filesystems.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import database


class UsageLimitExceeded(RuntimeError):
    def __init__(self, detail, retry_after=60, budget_name=None):
        self.detail = detail
        self.retry_after = max(1, int(retry_after))
        self.budget_name = budget_name
        super().__init__(detail)


class UsageStoreUnavailable(RuntimeError):
    pass


def setting(name, default):
    value = int(os.environ.get(name, default))
    if value < 0:
        raise ValueError(f'{name} must be non-negative')
    return value


@dataclass(frozen=True)
class Budget:
    name: str
    maximum: int
    seconds: int
    cost: int = 1
    calendar_month: bool = False


def month_window(now):
    stamp = datetime.fromtimestamp(now, timezone.utc)
    year, month = (stamp.year + 1, 1) if stamp.month == 12 else (stamp.year, stamp.month + 1)
    return stamp.strftime('%Y-%m'), datetime(year, month, 1, tzinfo=timezone.utc).timestamp()


class SharedUsage:
    def __init__(self, path):
        self.path = str(path)
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with self.connect() as connection:
                connection.execute('CREATE TABLE IF NOT EXISTS budgets (key TEXT PRIMARY KEY, used INTEGER NOT NULL, expires REAL NOT NULL)')
                connection.execute('CREATE TABLE IF NOT EXISTS leases (token TEXT PRIMARY KEY, resource TEXT NOT NULL, expires REAL NOT NULL)')
                connection.execute('CREATE INDEX IF NOT EXISTS lease_resource ON leases(resource)')
                connection.execute('CREATE TABLE IF NOT EXISTS snapshots (key TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        except (OSError, sqlite3.Error) as exc:
            raise UsageStoreUnavailable('Usage protection is unavailable; expensive operations are temporarily disabled.') from exc

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=3, isolation_level=None)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def acquire(self, budgets=(), resource=None, capacity=1, ttl=600):
        now = time.time()
        token = secrets.token_hex(24) if resource else None
        try:
            with self.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                connection.execute('DELETE FROM budgets WHERE expires <= ?', (now,))
                connection.execute('DELETE FROM leases WHERE expires <= ?', (now,))
                counters = []
                for budget in budgets:
                    if budget.calendar_month:
                        bucket, expires = month_window(now)
                        key = f'{budget.name}:month:{bucket}'
                    else:
                        bucket = int(now // budget.seconds)
                        key = f'{budget.name}:{budget.seconds}:{bucket}'
                        expires = (bucket + 1) * budget.seconds
                    row = connection.execute('SELECT used FROM budgets WHERE key=?', (key,)).fetchone()
                    used = row[0] if row else 0
                    if budget.cost and used + budget.cost > budget.maximum:
                        raise UsageLimitExceeded('Shared usage limit reached. Please try again later.', int(expires - now) + 1, budget.name)
                    counters.append((key, budget.cost, expires))
                if resource:
                    active = connection.execute('SELECT COUNT(*) FROM leases WHERE resource=?', (resource,)).fetchone()[0]
                    if active >= capacity:
                        raise UsageLimitExceeded('The prediction service is busy. Please try again shortly.', 5)
                for key, cost, expires in counters:
                    connection.execute('INSERT INTO budgets VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET used=used+excluded.used', (key, cost, expires))
                if token:
                    connection.execute('INSERT INTO leases VALUES (?, ?, ?)', (token, resource, now + ttl))
                connection.commit()
            return Lease(self, token, ttl)
        except UsageLimitExceeded:
            raise
        except sqlite3.Error as exc:
            raise UsageStoreUnavailable('Usage protection is unavailable; expensive operations are temporarily disabled.') from exc

    def observe_provider_usage(self, used):
        """Never lower reservations; include usage that predates this limiter."""
        bucket, expires = month_window(time.time())
        key = f'odds-credit-reservations-month:month:{bucket}'
        try:
            with self.connect() as connection:
                connection.execute('INSERT INTO budgets VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET used=MAX(used,excluded.used)', (key, used, expires))
        except sqlite3.Error as exc:
            raise UsageStoreUnavailable('Could not save provider quota usage') from exc

    def snapshot(self, key):
        try:
            with self.connect() as connection:
                row = connection.execute('SELECT payload FROM snapshots WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else None
        except (sqlite3.Error, ValueError) as exc:
            raise UsageStoreUnavailable('Shared line cache is unavailable') from exc

    def save_snapshot(self, key, payload):
        try:
            with self.connect() as connection:
                connection.execute('INSERT INTO snapshots VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload', (key, json.dumps(payload, allow_nan=False)))
        except (sqlite3.Error, ValueError) as exc:
            raise UsageStoreUnavailable('Could not save shared line cache') from exc

    def update_snapshot(self, key, change):
        """Atomic read/modify/write, shared by local and PostgreSQL workers."""
        try:
            with self.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                row = connection.execute('SELECT payload FROM snapshots WHERE key=?', (key,)).fetchone()
                updated = change(json.loads(row[0]) if row else {})
                connection.execute('INSERT INTO snapshots VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload',
                                   (key, json.dumps(updated, allow_nan=False)))
                connection.commit()
                return updated
        except (sqlite3.Error, ValueError) as exc:
            raise UsageStoreUnavailable('Could not save monitoring state') from exc

    def renew(self, token, ttl):
        try:
            now = time.time()
            with self.connect() as connection:
                changed = connection.execute('UPDATE leases SET expires=? WHERE token=? AND expires>?', (now + ttl, token, now)).rowcount
            return changed == 1
        except sqlite3.Error as exc:
            raise UsageStoreUnavailable('Cannot renew shared job lease') from exc

    def release(self, token):
        if not token:
            return
        try:
            with self.connect() as connection:
                connection.execute('DELETE FROM leases WHERE token=?', (token,))
        except sqlite3.Error:
            # A crashed/unreachable worker cannot permanently consume a slot.
            pass


class Lease:
    def __init__(self, store, token, ttl):
        self.store, self.token, self.ttl = store, token, ttl
        self.stop = threading.Event()
        self.thread = None

    def __enter__(self):
        if self.token:
            if not self.store.renew(self.token, self.ttl):
                raise UsageLimitExceeded('Job reservation expired. Please retry.', 5)
            self.thread = threading.Thread(target=self._heartbeat, daemon=True)
            self.thread.start()
        return self

    def _heartbeat(self):
        while not self.stop.wait(self.ttl / 3):
            try:
                if not self.store.renew(self.token, self.ttl):
                    break
            except UsageStoreUnavailable:
                break

    def __exit__(self, *_):
        self.stop.set()
        self.store.release(self.token)


@lru_cache(maxsize=1)
def store():
    if database.enabled():
        return PostgresUsage()
    path = os.environ.get('API_USAGE_DB') or Path(__file__).parent / 'data_cache' / 'api_usage.sqlite3'
    return SharedUsage(path)


class PostgresUsage(SharedUsage):
    """Same admission policy, with cross-host PostgreSQL transactions."""
    def __init__(self):
        # Schema creation is an explicit deployment migration, never a request.
        pass

    @contextmanager
    def connect(self):
        try:
            with database.connect() as connection:
                yield _PostgresConnection(connection)
        except database.DatabaseUnavailable:
            raise UsageStoreUnavailable('Shared database usage protection is unavailable.') from None


class _PostgresConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=()):
        if sql == 'BEGIN IMMEDIATE':
            return self.connection.execute('SELECT pg_advisory_xact_lock(78432001)')
        sql = sql.replace('?', '%s')
        sql = sql.replace('used=used+excluded.used', 'used=budgets.used+excluded.used')
        sql = sql.replace('used=MAX(used,excluded.used)', 'used=GREATEST(budgets.used,excluded.used)')
        return self.connection.execute(sql, params)

    def commit(self):
        self.connection.commit()


def prediction(client=None):
    budgets = [Budget('predictions-global-minute', setting('PREDICTIONS_GLOBAL_PER_MINUTE', 60), 60),
               Budget('predictions-global-hour', setting('PREDICTIONS_GLOBAL_PER_HOUR', 600), 3600)]
    if client:
        client_hash = hashlib.sha256(client.encode()).hexdigest()
        budgets.append(Budget('predictions-client-' + client_hash, setting('PREDICTIONS_PER_IP_PER_MINUTE', 10), 60))
    return store().acquire(budgets, 'predictions', setting('MAX_CONCURRENT_PREDICTIONS', 2))


def reserve_bulk(league, admin=False):
    budgets = [Budget('admin-bulk-hour', setting('ADMIN_BULK_RUNS_PER_HOUR', 2), 3600)] if admin else [Budget('automatic-bulk-' + league, 1, 900)]
    return store().acquire(budgets, 'bulk-seeding', 1)


def reserve_resolution():
    return store().acquire([Budget('resolution', 1, 300)], 'resolution', 1)


def reserve_odds_request(markets=None):
    # Event-list requests are free; paid event odds cost at most one credit
    # per requested market for our single-region DraftKings filter.
    # Do not impose per-day or per-minute slate limits: fetch every posted game.
    cost = len(set(markets.split(','))) if markets else 0
    try:
        return store().acquire([
            Budget('odds-credit-reservations-month', setting('ODDS_API_MONTHLY_CREDIT_BUDGET', 500), 1, cost, calendar_month=True),
        ])
    except UsageLimitExceeded as exc:
        import monitoring
        monitoring.odds_limit(exc.budget_name, exc.retry_after)
        raise


def enrichment(client):
    client_hash = hashlib.sha256(client.encode()).hexdigest()
    return store().acquire([
        Budget('enrichment-global-minute', setting('ENRICHMENT_GLOBAL_PER_MINUTE', 120), 60),
        Budget('enrichment-client-' + client_hash, setting('ENRICHMENT_PER_IP_PER_MINUTE', 30), 60),
    ], 'enrichment', setting('MAX_CONCURRENT_ENRICHMENT_REQUESTS', 4))

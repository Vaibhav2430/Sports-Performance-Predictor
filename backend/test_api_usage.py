import asyncio
from concurrent.futures import ProcessPoolExecutor
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import api_usage
import main
import odds


def process_attempt(path):
    try:
        api_usage.SharedUsage(path).acquire([api_usage.Budget('shared-test', 2, 3600)])
        return 1
    except api_usage.UsageLimitExceeded:
        return 0


async def asgi_request(path, method='GET', headers=(), query='', client='127.0.0.1'):
    messages = []
    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}
    async def send(message):
        messages.append(message)
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
             'method': method, 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
             'query_string': query.encode(), 'root_path': '', 'headers': list(headers),
             'server': ('localhost', 8000), 'client': (client, 12345)}
    await main.app(scope, receive, send)
    start = next(m for m in messages if m['type'] == 'http.response.start')
    body = b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')
    return start['status'], dict(start['headers']), json.loads(body)


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / 'usage.sqlite3')
        self.store = api_usage.SharedUsage(self.path)

    def test_budget_is_shared_between_independent_workers(self):
        context = multiprocessing.get_context('spawn')
        with ProcessPoolExecutor(max_workers=3, mp_context=context) as pool:
            accepted = list(pool.map(process_attempt, [self.path] * 8))
        self.assertEqual(sum(accepted), 2)

    def test_busy_slot_does_not_spend_rejected_request_budget(self):
        first = self.store.acquire([], 'predictions', 1)
        with self.assertRaises(api_usage.UsageLimitExceeded):
            self.store.acquire([api_usage.Budget('not-spent', 1, 3600)], 'predictions', 1)
        self.store.release(first.token)
        with self.store.acquire([api_usage.Budget('not-spent', 1, 3600)], 'predictions', 1):
            pass

    def test_exception_releases_concurrency_slot(self):
        with self.assertRaises(ValueError):
            with self.store.acquire([], 'predictions', 1):
                raise ValueError('Prediction failed')
        with self.store.acquire([], 'predictions', 1):
            pass

    def test_crashed_worker_lease_expires(self):
        with patch.object(api_usage.time, 'time', return_value=1000):
            self.store.acquire([], 'bulk', 1, ttl=60)
        with patch.object(api_usage.time, 'time', return_value=1061):
            self.store.acquire([], 'bulk', 1, ttl=60)

    def test_heartbeat_renewal_keeps_running_job_exclusive(self):
        with patch.object(api_usage.time, 'time', return_value=1000):
            lease = self.store.acquire([], 'bulk', 1, ttl=60)
        with patch.object(api_usage.time, 'time', return_value=1040):
            self.assertTrue(self.store.renew(lease.token, 60))
        with patch.object(api_usage.time, 'time', return_value=1061):
            with self.assertRaises(api_usage.UsageLimitExceeded):
                self.store.acquire([], 'bulk', 1, ttl=60)
        with patch.object(api_usage.time, 'time', return_value=1101):
            self.store.acquire([], 'bulk', 1, ttl=60)

    def test_scheduled_seeding_deduplicates_worker_startups(self):
        with patch.object(api_usage, 'store', return_value=self.store):
            lease = api_usage.reserve_bulk('NBA')
            self.store.release(lease.token)
            with self.assertRaises(api_usage.UsageLimitExceeded):
                api_usage.reserve_bulk('NBA')
            # The other league is independent once the shared bulk slot is free.
            other = api_usage.reserve_bulk('WNBA')
            self.store.release(other.token)

    def test_partial_multi_budget_rejection_does_not_spend_other_budget(self):
        self.store.acquire([api_usage.Budget('exhausted', 1, 3600)])
        with self.assertRaises(api_usage.UsageLimitExceeded):
            self.store.acquire([api_usage.Budget('unused', 1, 3600), api_usage.Budget('exhausted', 1, 3600)])
        self.store.acquire([api_usage.Budget('unused', 1, 3600)])

    def test_usage_state_survives_restart(self):
        self.store.acquire([api_usage.Budget('persistent', 1, 3600)])
        restarted = api_usage.SharedUsage(self.path)
        with self.assertRaises(api_usage.UsageLimitExceeded):
            restarted.acquire([api_usage.Budget('persistent', 1, 3600)])

    def test_paid_request_is_blocked_before_network_io(self):
        with patch.object(api_usage, 'store', return_value=self.store), patch.dict(os.environ, {'ODDS_API_DAILY_CREDIT_BUDGET': '3'}), patch.object(odds.requests, 'get') as get:
            odds._paid_get('https://example.invalid/odds', params={'markets': 'player_points,player_assists,player_rebounds'}, timeout=10)
            with self.assertRaises(api_usage.UsageLimitExceeded):
                odds._paid_get('https://example.invalid/odds', params={'markets': 'player_points'}, timeout=10)
            get.assert_called_once()

    def test_store_failure_fails_closed(self):
        with patch.object(self.store, 'connect', side_effect=sqlite3.OperationalError('unavailable')):
            with self.assertRaises(api_usage.UsageStoreUnavailable):
                self.store.acquire([api_usage.Budget('test', 1, 60)])


class ProtectedAPITests(unittest.TestCase):
    TOKEN = 'non-secret-test-token-' + 'x' * 40

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        shared = api_usage.SharedUsage(Path(self.directory.name) / 'api.sqlite3')
        self.store_patch = patch.object(api_usage, 'store', return_value=shared)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
        self.auth_patch = patch.dict(os.environ, {'ADMIN_API_TOKEN': self.TOKEN})
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)
        self.auth = [(b'authorization', ('Bearer ' + self.TOKEN).encode())]

    def request(self, *args, **kwargs):
        return asyncio.run(asgi_request(*args, **kwargs))

    def test_unauthorized_bulk_requests_never_schedule_work(self):
        with patch.object(main, '_seed_all') as seed:
            self.assertEqual(self.request('/accuracy/seed', 'POST')[0], 401)
            self.assertEqual(self.request('/accuracy/seed', 'POST', [(b'authorization', b'Bearer wrong')])[0], 401)
            self.assertEqual(self.request('/accuracy/seed', 'POST', query='token=' + self.TOKEN)[0], 401)
            seed.assert_not_called()

    def test_unconfigured_admin_is_disabled(self):
        with patch.dict(os.environ, {'ADMIN_API_TOKEN': ''}), patch.object(main, '_seed_all') as seed:
            self.assertEqual(self.request('/accuracy/seed', 'POST')[0], 503)
            seed.assert_not_called()

    def test_valid_admin_can_seed_but_shared_hourly_limit_is_enforced(self):
        def completed_seed(league, reservation):
            with reservation:
                pass
        with patch.object(main, '_seed_all', side_effect=completed_seed) as seed, patch.dict(os.environ, {'ADMIN_BULK_RUNS_PER_HOUR': '1'}):
            first = self.request('/accuracy/seed', 'POST', self.auth, 'league=NBA')
            self.assertEqual(first[0], 200)
            self.assertEqual(first[2]['status'], 'started')
            second = self.request('/accuracy/seed', 'POST', self.auth, 'league=WNBA')
            self.assertEqual(second[0], 429)
            self.assertIn(b'retry-after', second[1])
            self.assertEqual(seed.call_count, 1)

    def test_invalid_league_cannot_trigger_bulk_work(self):
        with patch.object(main, '_seed_all') as seed:
            self.assertEqual(self.request('/accuracy/seed', 'POST', self.auth, 'league=not-a-league')[0], 422)
            seed.assert_not_called()

    def test_prediction_limits_are_shared_across_leagues(self):
        with patch.dict(os.environ, {'PREDICTIONS_GLOBAL_PER_HOUR': '1'}), patch.object(main, '_predict_and_record', return_value={'ok': True}) as predict:
            self.assertEqual(self.request('/predict', query='player=Test')[0], 200)
            limited = self.request('/wnba/predict', query='player=Test')
            self.assertEqual(limited[0], 429)
            self.assertEqual(predict.call_count, 1)

    def test_prediction_storage_failure_is_503_and_does_not_run_model(self):
        with patch.object(api_usage, 'prediction', side_effect=api_usage.UsageStoreUnavailable), patch.object(main, '_predict_and_record') as predict:
            self.assertEqual(self.request('/predict', query='player=Test')[0], 503)
            predict.assert_not_called()

    def test_accuracy_reads_cannot_trigger_expensive_grading(self):
        with patch.object(main, '_resolve_all') as resolve, patch.object(main.tracker, 'load_log', return_value=[]):
            self.assertEqual(self.request('/accuracy')[0], 200)
            resolve.assert_not_called()

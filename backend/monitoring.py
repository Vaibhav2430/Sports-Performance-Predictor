"""Persistent job and quota signals. No exception text, credentials, or paid probes."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import os
import secrets
import threading
import time

import api_usage
import database

logger = logging.getLogger(__name__)
JOBS = ('seed-NBA', 'seed-WNBA', 'grading', 'scheduler', 'odds-NBA', 'odds-WNBA')
COUNTERS = ('attempted', 'generated', 'failed', 'resolved', 'excluded', 'pending', 'eligible', 'posted', 'captured', 'missing')
_cache = None
_cache_until = 0
_lock = threading.Lock()


def _update(key, change):
    global _cache_until
    try:
        value = api_usage.store().update_snapshot('monitor-' + key, change)
        _cache_until = 0
        return value
    except Exception:
        logger.error('Monitoring state could not be saved. Check database availability.')
        return None


def _reason(exc):
    if isinstance(exc, (database.DatabaseUnavailable, api_usage.UsageStoreUnavailable)):
        return 'storage_unavailable'
    return 'job_failed'


@contextmanager
def job(name):
    if name not in JOBS:
        raise ValueError('Unknown monitored job')
    run = secrets.token_hex(12)
    now = time.time()
    _update('job-' + name, lambda old: {**old, 'run_id': run, 'status': 'running', 'started_at': now, 'reason': None})
    metrics = {}
    try:
        yield metrics
    except Exception as exc:
        _finish(name, run, metrics, error=_reason(exc))
        raise
    else:
        _finish(name, run, metrics)


def _finish(name, run, metrics, error=None):
    now = time.time()
    counts = {key: int(metrics[key]) for key in COUNTERS if isinstance(metrics.get(key), (int, float))}
    failed = counts.get('failed', 0)
    if error:
        status = 'error'
    elif failed:
        status = 'error' if failed >= counts.get('attempted', 0) > 0 else 'partial'
    elif metrics.get('deferred'):
        status = 'deferred'
    else:
        status = 'success'
    def change(old):
        if old.get('run_id') != run:
            return old  # An older worker cannot erase a newer run's result.
        result = {**old, 'status': status, 'finished_at': now, 'counts': counts,
                  'reason': error or ('some_items_failed' if failed else None)}
        if status == 'success':
            result.update(last_success_at=now, consecutive_failures=0)
        elif status in ('error', 'partial'):
            result.update(last_failure_at=now, consecutive_failures=old.get('consecutive_failures', 0) + 1)
            logger.error('Monitoring: %s completed with %s (%s failed items).', name, status, failed)
        oldest = metrics.get('oldest_pending_date')
        if isinstance(oldest, str) and len(oldest) == 10:
            result['oldest_pending_date'] = oldest
        return result
    _update('job-' + name, change)


def deferred(name):
    # Busy/rate-limited admission is not a successful run and must not reset a
    # previous failure or advance the last successful completion timestamp.
    _update('job-' + name, lambda old: {**old, 'last_deferred_at': time.time()})


def odds_limit(budget, retry_after):
    if budget != 'odds-credit-reservations-month':
        return
    _update('odds-limit', lambda old: {'budget': budget, 'reset_at': time.time() + retry_after})


def observe_odds(response):
    def number(header):
        raw = response.headers.get(header)
        return int(raw) if isinstance(raw, str) and raw.isdigit() and len(raw) < 12 else None
    used, remaining = number('x-requests-used'), number('x-requests-remaining')
    status = response.status_code
    if not isinstance(status, int):
        return
    code = None
    if status >= 400:
        try:
            body = response.json()
            if isinstance(body, dict) and body.get('error_code') in ('OUT_OF_USAGE_CREDITS', 'INVALID_KEY', 'MISSING_KEY', 'DEACTIVATED_KEY'):
                code = body['error_code']
        except Exception:
            pass
    _, reset = api_usage.month_window(time.time())
    _update('odds-provider', lambda old: {'observed_at': time.time(), 'used': used, 'remaining': remaining,
                                         'http_status': status, 'error_code': code, 'reset_at': reset})


def _signals():
    with api_usage.store().connect() as connection:
        rows = connection.execute('SELECT key,payload FROM snapshots WHERE key LIKE ?', ('monitor-%',)).fetchall()
        budgets = connection.execute('SELECT key,used,expires FROM budgets WHERE expires>? AND key LIKE ?', (time.time(), 'odds-credit-%')).fetchall()
    return {key.removeprefix('monitor-'): json.loads(payload) for key, payload in rows}, budgets


def evaluate(signals, budgets=(), now=None):
    now = time.time() if now is None else now
    alerts = []
    def alert(code, severity, message):
        if not any(item['code'] == code for item in alerts):
            alerts.append({'code': code, 'severity': severity, 'message': message})
    required = os.environ.get('MONITOR_REQUIRE_SCHEDULER', 'true' if os.environ.get('APP_ENV') == 'production' else 'false').lower() == 'true'
    stale_seconds = int(os.environ.get('MONITOR_STALE_JOB_SECONDS', '10800'))
    if not any(signals.get('job-' + name, {}).get('started_at') for name in JOBS):
        alert('monitor_not_initialized', 'warning', 'No monitored jobs have reported yet. Start the updated backend to initialize monitoring.')
    for name in JOBS:
        state = signals.get('job-' + name, {})
        unrecovered = state.get('status') == 'deferred' and state.get('last_failure_at', 0) > state.get('last_success_at', 0)
        if state.get('status') == 'error' or unrecovered:
            alert('job_failed_' + name, 'critical', name + ' failed; inspect the backend logs and retry.')
        elif state.get('status') == 'partial':
            alert('job_partial_' + name, 'warning', name + ' had failed items; remaining items will retry.')
        if state.get('status') == 'running' and now - state.get('started_at', now) > stale_seconds:
            alert('job_stuck_' + name, 'critical', name + ' has not completed within the expected time.')
        if required and name in ('seed-NBA', 'seed-WNBA', 'grading', 'scheduler'):
            last = max(state.get('started_at', 0), state.get('finished_at', 0))
            if not last or now - last > stale_seconds:
                alert('job_stale_' + name, 'critical', name + ' has stopped reporting. Check that the backend is running.')
    grading = signals.get('job-grading', {})
    oldest = grading.get('oldest_pending_date')
    if grading.get('counts', {}).get('pending') and oldest:
        try:
            age = now - datetime.fromisoformat(oldest).replace(tzinfo=timezone.utc).timestamp()
            if age > int(os.environ.get('MONITOR_PENDING_GRACE_DAYS', '3')) * 86400:
                alert('grading_backlog', 'warning', 'Some results have been pending for several days. Check missing box scores, DNPs, or postponements.')
        except ValueError:
            pass
    provider = signals.get('odds-provider', {})
    if provider.get('reset_at', 0) > now:
        if provider.get('remaining') == 0 or provider.get('error_code') == 'OUT_OF_USAGE_CREDITS':
            alert('odds_credits_exhausted', 'critical', 'Odds API monthly credits are exhausted. Cached lines may still be available.')
        elif provider.get('error_code') in ('INVALID_KEY', 'MISSING_KEY', 'DEACTIVATED_KEY'):
            alert('odds_auth_failed', 'critical', 'Odds API credentials were rejected. Check the private API settings.')
        elif isinstance(provider.get('remaining'), int) and provider['remaining'] <= int(os.environ.get('MONITOR_LOW_CREDITS', '50')):
            alert('odds_credits_low', 'warning', 'Odds API monthly credits are running low.')
    quota = signals.get('odds-limit', {})
    monthly_used = None
    for key, used, expires in budgets:
        if expires <= now:
            continue
        if key.startswith('odds-credit-reservations-month:'):
            monthly_used = used
            if used >= api_usage.setting('ODDS_API_MONTHLY_CREDIT_BUDGET', 500):
                alert('odds_monthly_budget', 'critical', 'The shared monthly odds-credit budget is exhausted.')
    if quota.get('reset_at', 0) > now:
        if quota.get('budget') == 'odds-credit-reservations-month':
            alert('odds_monthly_budget', 'critical', 'The next request would exceed the shared monthly odds-credit budget.')
    severity = 'critical' if any(item['severity'] == 'critical' for item in alerts) else ('warning' if alerts else 'healthy')
    return {'status': severity, 'checked_at': datetime.fromtimestamp(now, timezone.utc).isoformat(),
            'alerts': alerts, 'jobs': {name: signals.get('job-' + name, {}) for name in JOBS},
            'quota': {'monthly_reserved': monthly_used, 'provider': provider}, 'scheduler_required': required}


def status(cached=True):
    global _cache, _cache_until
    with _lock:
        if cached and _cache is not None and time.monotonic() < _cache_until:
            return _cache
        try:
            signals, budgets = _signals()
            result = evaluate(signals, budgets)
        except Exception:
            result = {'status': 'critical', 'alerts': [{'code': 'monitor_storage_unavailable', 'severity': 'critical',
                      'message': 'Monitoring cannot read the database. Check database availability.'}]}
        _cache, _cache_until = result, time.monotonic() + 15
        return result


def main():
    result = status(cached=False)
    print('CourtCast monitoring:', result['status'])
    for item in result['alerts']:
        print(item['severity'].upper() + ': ' + item['message'])
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            print(('::error::' if item['severity'] == 'critical' else '::warning::') + item['message'])
    if result['status'] == 'critical':
        raise SystemExit(1)


if __name__ == '__main__':
    main()

import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import api_usage
import main
import monitoring
import odds
from test_api_usage import asgi_request


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.storage = api_usage.SharedUsage(Path(directory.name) / 'usage.sqlite3')
        mocked = patch.object(api_usage, 'store', return_value=self.storage)
        mocked.start(); self.addCleanup(mocked.stop)
        env = patch.dict(os.environ, {'MONITOR_REQUIRE_SCHEDULER':'false','MONITOR_STALE_JOB_SECONDS':'10800'})
        env.start(); self.addCleanup(env.stop)

    def current(self):
        return monitoring.status(cached=False)

    def codes(self):
        return {item['code'] for item in self.current()['alerts']}

    def test_failure_is_persisted_without_exception_text_and_success_recovers(self):
        with self.assertRaises(RuntimeError):
            with monitoring.job('seed-NBA'):
                raise RuntimeError('apiKey=never-save-this-secret')
        state=self.storage.snapshot('monitor-job-seed-NBA')
        self.assertEqual(state['status'],'error')
        self.assertNotIn('never-save-this-secret',json.dumps(state))
        self.assertIn('job_failed_seed-NBA',self.codes())
        with monitoring.job('seed-NBA') as metrics:
            metrics.update(attempted=2,generated=2,failed=0)
        self.assertNotIn('job_failed_seed-NBA',self.codes())
        self.assertEqual(self.storage.snapshot('monitor-job-seed-NBA')['consecutive_failures'],0)

    def test_partial_failure_is_not_marked_successful(self):
        with monitoring.job('grading') as metrics: metrics.update(attempted=5,failed=1,resolved=4)
        self.assertIn('job_partial_grading',self.codes())
        self.assertEqual(self.storage.snapshot('monitor-job-grading')['status'],'partial')

    def test_all_failed_items_trigger_critical_alert(self):
        with monitoring.job('seed-WNBA') as metrics: metrics.update(attempted=2,failed=2)
        self.assertEqual(self.current()['status'],'critical')

    def test_deferred_admission_does_not_erase_failed_job(self):
        with self.assertRaises(RuntimeError):
            with monitoring.job('grading'): raise RuntimeError()
        monitoring.deferred('grading')
        self.assertIn('job_failed_grading',self.codes())
        with monitoring.job('grading') as metrics: metrics['deferred']=True
        self.assertIn('job_failed_grading',self.codes())

    def test_job_from_older_worker_cannot_overwrite_newer_result(self):
        self.storage.save_snapshot('monitor-job-grading',{'run_id':'new','status':'success'})
        monitoring._finish('grading','old',{},error='job_failed')
        self.assertEqual(self.storage.snapshot('monitor-job-grading')['status'],'success')

    def test_optional_scheduler_does_not_alarm_when_local_computer_is_off(self):
        report=monitoring.evaluate({'job-grading':{'status':'success','finished_at':1}},now=100000)
        self.assertFalse(any('stale' in item['code'] for item in report['alerts']))

    def test_always_on_scheduler_detects_stopped_and_stuck_jobs(self):
        with patch.dict(os.environ,{'MONITOR_REQUIRE_SCHEDULER':'true'}):
            report=monitoring.evaluate({'job-grading':{'status':'running','started_at':1}},now=20000)
        codes={item['code'] for item in report['alerts']}
        self.assertIn('job_stuck_grading',codes)
        self.assertIn('job_stale_scheduler',codes)

    def test_provider_headers_detect_exhaustion_without_spending_credits(self):
        response=Mock(status_code=200,headers={'x-requests-used':'500','x-requests-remaining':'0'})
        with patch.object(odds.requests,'get') as get:
            monitoring.observe_odds(response)
            self.assertIn('odds_credits_exhausted',self.codes())
            get.assert_not_called()

    def test_provider_error_code_detects_exhaustion_without_headers(self):
        response=Mock(status_code=401,headers={})
        response.json.return_value={'error_code':'OUT_OF_USAGE_CREDITS','message':'secret-like text never saved'}
        monitoring.observe_odds(response)
        self.assertIn('odds_credits_exhausted',self.codes())
        self.assertNotIn('secret-like',json.dumps(self.current()))

    def test_low_credits_warns_and_recovery_clears_incident(self):
        monitoring.observe_odds(Mock(status_code=200,headers={'x-requests-remaining':'10'}))
        self.assertIn('odds_credits_low',self.codes())
        monitoring.observe_odds(Mock(status_code=200,headers={'x-requests-remaining':'499'}))
        self.assertNotIn('odds_credits_low',self.codes())

    def test_expired_monthly_incidents_clear_after_reset(self):
        report=monitoring.evaluate({'odds-provider':{'remaining':0,'reset_at':100},'odds-limit':{'budget':'odds-credit-reservations-month','reset_at':100}},now=101)
        self.assertFalse(any(item['code'].startswith('odds_') for item in report['alerts']))

    def test_daily_cap_is_warning_while_monthly_cap_is_critical(self):
        now=1000
        report=monitoring.evaluate({},[('odds-credit-reservations-day:86400:0',15,2000)],now)
        self.assertEqual(next(item for item in report['alerts'] if item['code']=='odds_daily_budget')['severity'],'warning')
        report=monitoring.evaluate({},[('odds-credit-reservations-month:month:2026-10',500,2000)],now)
        self.assertEqual(report['status'],'critical')

    def test_failed_quota_reservation_records_reason_before_any_paid_call(self):
        with patch.dict(os.environ,{'ODDS_API_MONTHLY_CREDIT_BUDGET':'0'}),patch.object(odds.requests,'get') as get:
            with self.assertRaises(api_usage.UsageLimitExceeded):
                odds._paid_get('unused',params={'markets':'player_points'},timeout=1)
            get.assert_not_called()
        self.assertIn('odds_monthly_budget',self.codes())

    def test_public_health_redacts_details_and_does_not_restart_on_quota(self):
        state={'status':'critical','quota':{'monthly_reserved':500},'alerts':[{'code':'odds_monthly_budget'}]}
        with patch.object(monitoring,'status',return_value=state):
            status,_,body=asyncio.run(asgi_request('/health'))
        self.assertEqual(status,200)
        self.assertEqual(body,{'status':'critical'})

    def test_database_outage_is_visible_without_traceback(self):
        with patch.object(monitoring,'_signals',side_effect=RuntimeError('private-url')):
            state=self.current()
        self.assertEqual(state['status'],'critical')
        self.assertNotIn('private-url',json.dumps(state))
        with patch.object(monitoring,'status',return_value=state):
            self.assertEqual(asyncio.run(asgi_request('/health'))[0],503)

    def test_monitor_details_require_admin_authentication(self):
        with patch.dict(os.environ,{'ADMIN_API_TOKEN':'x'*40}):
            self.assertEqual(asyncio.run(asgi_request('/admin/monitoring'))[0],401)

    def test_pending_grading_backlog_is_a_warning_not_a_fabricated_result(self):
        state={'job-grading':{'status':'success','counts':{'pending':2},'oldest_pending_date':'2020-01-01'}}
        report=monitoring.evaluate(state)
        self.assertIn('grading_backlog',{item['code'] for item in report['alerts']})

    def test_hidden_grading_exception_reaches_monitoring(self):
        from test_daily_accuracy import record
        entry=record('2020-10-22',resolved=False)
        with patch.object(main.api_usage,'reserve_resolution',return_value=nullcontext()), \
             patch.object(main.tracker,'load_log',return_value=[entry]), \
             patch.object(main,'nba_find_player',side_effect=RuntimeError('offline')):
            main._resolve_all()
        self.assertIn('job_failed_grading',self.codes())

    def test_hidden_player_prediction_failure_reaches_monitoring(self):
        with patch.object(main.api_usage,'reserve_bulk',return_value=nullcontext()), \
             patch.object(main.odds,'fetch_lines',return_value={'Test':{'PTS':20}}), \
             patch.object(main.tracker,'load_log',return_value=[]), \
             patch.object(main,'nba_find_player',side_effect=RuntimeError('offline')):
            main._seed_all('NBA')
        self.assertIn('job_failed_seed-NBA',self.codes())


if __name__=='__main__': unittest.main()

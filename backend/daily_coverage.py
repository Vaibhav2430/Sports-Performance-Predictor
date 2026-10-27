"""Scheduled collection without a running web server, using shared saved forecasts."""
import json
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import api_usage
import database
import tracker


def record(league, posted_lines, metrics):
    """Keep today's observed prop players and actual saved coverage across runs."""
    day = datetime.now(ZoneInfo('America/New_York')).date().isoformat()
    saved = {e['player'] for e in tracker.load_log()
             if e.get('league') == league and e.get('date') == day and tracker.has_valid_predictions(e)}
    def update(old):
        posted = set(old.get('posted_players', [])) | set(posted_lines)
        # Compare canonical player names using the same resolver as the odds cache.
        import odds
        normalized_saved = {odds._normalize(name) for name in saved}
        captured = sorted(name for name in posted if odds._normalize(name) in normalized_saved)
        missing = sorted(posted - set(captured))
        return {'league': league, 'date': day, 'checked_at': datetime.now(ZoneInfo('UTC')).isoformat(),
                'posted_players': sorted(posted), 'captured_players': captured,
                'missing_players': missing, 'posted': len(posted), 'captured': len(captured),
                'missing': len(missing), 'last_run': dict(metrics)}
    report = api_usage.store().update_snapshot(f'daily-coverage-{league}-{day}', update)
    metrics.update(posted=report['posted'], captured=report['captured'], missing=report['missing'])
    return report


def run_once():
    # An ephemeral cloud runner must never silently save forecasts to local disk.
    if not database.enabled():
        raise RuntimeError('Cloud collection requires STORAGE_BACKEND=postgres.')
    if not os.environ.get('DATABASE_URL') or not os.environ.get('ODDS_API_KEY'):
        raise RuntimeError('Cloud collection credentials are not configured.')
    import main
    with main.monitoring.job('scheduler'):
        runs = main._seed_if_needed()
        main._resolve_all()
    log = tracker.load_log()
    day = datetime.now(ZoneInfo('America/New_York')).date().isoformat()
    reports = {league: api_usage.store().snapshot(f'daily-coverage-{league}-{day}') for league in ('NBA', 'WNBA')}
    # Public job output contains aggregate counts only, never connection strings.
    summary = {'date': day, 'runs': runs,
               'status': main.monitoring.status(cached=False)['status'],
               'coverage': {league: {key: (report or {}).get(key) for key in ('posted', 'captured', 'missing')}
                            for league, report in reports.items()},
               'accuracy': {league: tracker.compute_stats(log, league) for league in ('NBA', 'WNBA')}}
    database.save_document('daily-collection-latest', summary)
    return summary


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        result = run_once()
    except Exception:
        # Network/library exceptions may contain credentials in URLs.
        print('Daily collection failed. Check database connectivity, private credentials, and saved monitoring state.')
        raise SystemExit(1) from None
    print(json.dumps(result, allow_nan=False))
    if result['status'] == 'critical':
        print('::error::Daily collection has a blocking job or quota incident. Check the saved monitoring state.')
        raise SystemExit(1)
    if any((report.get('missing') or 0) > 0 for report in result['coverage'].values()):
        print('::warning::Some posted players still need forecasts; the next scheduled run will retry.')


if __name__ == '__main__':
    main()

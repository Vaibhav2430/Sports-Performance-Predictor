"""Read-only JSON export of saved NBA forecasts; never calls the odds API."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import database

# Export application data only, never environment, database URLs, or quota snapshots.
FIELDS = ('player', 'player_id', 'league', 'date', 'lines', 'predicted', 'p_over',
          'actual', 'resolved', 'excluded', 'early_exit', 'injury_status',
          'created_at', 'model_version', 'prediction_stages')


def build_export(records, season, source):
    if not re.fullmatch(r'\d{4}-\d{2}', season):
        raise ValueError('Use a season such as 2026-27')
    year = int(season[:4])
    if int(season[-2:]) != (year + 1) % 100:
        raise ValueError('Season years must be consecutive')
    # Saved records have game dates but no regular-season/playoff marker.
    start, end = f'{year}-10-01', f'{year + 1}-07-01'
    selected = [{k: e[k] for k in FIELDS if k in e} for e in records
                if e.get('league') == 'NBA' and start <= e.get('date', '') < end]
    selected.sort(key=lambda e: (e['date'], e.get('player', '')))
    graded = [e for e in selected if e.get('resolved') and not e.get('excluded')]
    excluded = [e for e in selected if e.get('resolved') and e.get('excluded')]
    pending = [e for e in selected if not e.get('resolved')]
    return {
        'format': 'courtcast-nba-predictions-v1',
        'season': season, 'source': source,
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'date_filter': {'inclusive': start, 'exclusive': end},
        'note': 'Saved live forecasts and lines, not the reconstructed season backtest. '
                'Date filter may include preseason; records do not identify game type. '
                'Each player-game can contain up to three stat picks. Preserve exclusions, '
                'early-exit voids, prediction ties, and actual pushes when calculating hit rates.',
        'counts': {'player_games': len(selected), 'graded': len(graded),
                   'excluded': len(excluded), 'pending': len(pending)},
        'graded_records': graded, 'excluded_records': excluded, 'pending_records': pending,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--season', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        source = 'postgres' if database.enabled() else 'local'
        records = (database.read_predictions() if source == 'postgres' else
                   json.loads(Path(__file__).with_name('predictions_log.json').read_text()))
        payload = build_export(records, args.season, source)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2, allow_nan=False) + '\n')
    except database.DatabaseUnavailable:
        parser.exit(1, 'Saved database records are unavailable. No local fallback was exported.\n')
    except (ValueError, OSError):
        parser.exit(1, 'Export failed: check the season, source data, and output path.\n')
    print(json.dumps({'output': str(args.output.resolve()), 'counts': payload['counts']}))


if __name__ == '__main__':
    main()

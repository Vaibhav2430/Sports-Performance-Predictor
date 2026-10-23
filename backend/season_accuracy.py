"""Frozen historical NBA baseline plus newly graded saved forecasts."""
from datetime import datetime
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import database

ROOT = Path(__file__).parent
BASELINE_PATH = ROOT / 'data_cache/nba/accuracy_baseline.json'
MARGINS = {'PTS': 5, 'AST': 2, 'REB': 3}


def load_baseline():
    if database.enabled():
        return database.read_document('nba-accuracy-baseline') or database.read_document('nba-season-backtest')
    path = BASELINE_PATH if BASELINE_PATH.exists() else ROOT / 'evaluation_results/nba_season/report.json'
    return json.loads(path.read_text()) if path.exists() else None


def combined(log, baseline, season):
    start = f'{season[:4]}-10-01'
    end = f'{int(season[:4]) + 1}-10-01'
    baseline = baseline if baseline and baseline.get('season') == season else None
    cutoff = baseline['date_end'] if baseline else start
    baseline_stats = baseline.get('by_stat', {}) if baseline else {}
    stats = {}
    for stat, margin in MARGINS.items():
        saved = baseline_stats.get(stat, {})
        if saved and saved.get('margin') != margin:
            raise ValueError('Historical accuracy margin does not match live scoring')
        stats[stat] = {'correct': saved.get('within_margin', 0), 'total': saved.get('n', 0),
                       'margin': margin, 'baseline_correct': saved.get('within_margin', 0),
                       'baseline_total': saved.get('n', 0), 'live_correct': 0, 'live_total': 0}

    # The baseline already covers all dates through cutoff. Only append later
    # dates; overlapping player-games must never contribute twice.
    entries = {}
    for entry in log:
        day = entry.get('date', '')
        if entry.get('league') != 'NBA' or not (start <= day < end and day > cutoff):
            continue
        key = (str(entry.get('player_id') or entry.get('player')), day)
        if key not in entries:
            entries[key] = entry
    pending = 0
    graded = 0
    through = cutoff if baseline else None
    for entry in entries.values():
        if not entry.get('resolved'):
            pending += 1
            continue
        if entry.get('excluded'):
            continue
        counted = False
        for stat, margin in MARGINS.items():
            predicted = entry.get('predicted', {}).get(stat)
            actual = entry.get('actual', {}).get(stat)
            if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (predicted, actual)):
                continue
            hit = int(abs(predicted - actual) <= margin)
            stats[stat]['correct'] += hit
            stats[stat]['total'] += 1
            stats[stat]['live_correct'] += hit
            stats[stat]['live_total'] += 1
            counted = True
        if counted:
            graded += 1
            through = max(through or entry['date'], entry['date'])
    for value in stats.values():
        value['pct'] = round(value['correct'] / value['total'] * 100, 1) if value['total'] else None
    total = sum(value['total'] for value in stats.values())
    correct = sum(value['correct'] for value in stats.values())
    return {'league': 'NBA', 'season': season, 'metric': 'within_margin',
            'total': total, 'correct': correct, 'pct': round(correct / total * 100, 1) if total else None,
            'by_stat': stats, 'pending_count': pending, 'resolved_count': graded,
            'baseline_player_games': baseline.get('player_games', 0) if baseline else 0,
            'live_player_games': graded, 'baseline_date_end': cutoff if baseline else None,
            'date_end': through, 'updated_at': datetime.now(ZoneInfo('UTC')).isoformat(),
            'note': 'Historical reconstructed forecasts plus saved live forecasts after the baseline cutoff. '
                    'Points within ±5, assists ±2, rebounds ±3. Not over/under betting win rates.'}

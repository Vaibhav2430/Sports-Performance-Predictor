"""Reconstruct season forecasts using strictly earlier regular-season box scores.

Uses the existing current_history_only evaluation configuration. This is a
history-only reconstruction, not a replay of production injuries/team ratings.
"""
import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import numpy as np
import pandas as pd
import tuning

ROOT = Path(__file__).parent
MARGINS = {'PTS': 5, 'AST': 2, 'REB': 3}


def evaluate(prior, current, season, cutoff):
    current = current[current.GAME_ID.str.startswith('002')].copy()
    current = current[pd.to_datetime(current.GAME_DATE).dt.date < cutoff]
    prior = prior[prior.GAME_ID.str.startswith('002')].copy()
    all_games = pd.concat([prior, current], ignore_index=True)
    all_games['GAME_DATE'] = pd.to_datetime(all_games.GAME_DATE)
    all_games['HOME'] = (~all_games.MATCHUP.str.contains('@')).astype(int)
    records, skipped = [], []
    for pid, games in all_games.groupby('PLAYER_ID'):
        targets = current[current.PLAYER_ID == pid]
        if targets.empty:
            continue
        games = tuning.clean_games(games)
        examples = tuning.examples(games)
        for target in targets.itertuples():
            date = pd.Timestamp(target.GAME_DATE)
            hist = games[games.GAME_DATE < date]
            train = examples[examples.GAME_DATE < date] if not examples.empty else examples
            if len(hist) < 15 or len(train) < 5:
                skipped.append({'player': target.PLAYER_NAME, 'game_id': target.GAME_ID,
                                'reason': 'Fewer than 15 prior regular-season games'})
                continue
            home = float('@' not in target.MATCHUP)
            row = tuning.feature_row(hist, home, (date - hist.GAME_DATE.iloc[-1]).days)
            x = np.array([[row[c] for c in tuning.FEATURES]])
            # Same early-season sanity band and minutes ceiling as production.
            curr_hist = hist[hist.GAME_DATE >= pd.Timestamp(season[:4] + '-10-01')]
            reference = curr_hist if len(curr_hist) else hist
            proj_min = .65 * hist.MIN.tail(3).mean() + .35 * hist.MIN.tail(10).mean()
            for stat in tuning.STATS:
                model = tuning.fit(train, stat, tuning.CONFIGS[0], 'NBA')
                pred = max(0., float(model.predict(x)[0]))
                avg, std = float(reference[stat].mean()), float(reference[stat].std())
                if not np.isfinite(std):
                    std = float(hist[stat].std())
                low, high = max(0., avg - 2 * std), avg + 2 * std
                if reference.MIN.mean() > 0:
                    high = min(high, avg / reference.MIN.mean() * proj_min * 1.15)
                pred = round(float(np.clip(pred, low, max(low, high))), 1)
                actual = float(getattr(target, stat))
                records.append({'player_id': int(pid), 'player': target.PLAYER_NAME,
                    'game_id': target.GAME_ID, 'date': date.strftime('%Y-%m-%d'),
                    'matchup': target.MATCHUP, 'stat': stat, 'prediction': pred,
                    'actual': actual, 'absolute_error': abs(pred - actual),
                    'baseline': round(tuning.baseline(hist, stat), 1),
                    'training_through': hist.GAME_DATE.max().strftime('%Y-%m-%d')})
        print(f'Evaluated {targets.PLAYER_NAME.iloc[0]}', flush=True)
    frame = pd.DataFrame(records)
    if frame.empty:
        raise ValueError('No eligible completed games')
    stats = {}
    for stat, margin in MARGINS.items():
        part = frame[frame.stat == stat]
        hit = int((part.absolute_error <= margin).sum())
        stats[stat] = {'n': len(part), 'mae': round(float(part.absolute_error.mean()), 2),
            'rmse': round(float(np.sqrt(np.mean(part.absolute_error ** 2))), 2),
            'margin': margin, 'within_margin': hit, 'within_margin_pct': round(hit / len(part) * 100, 1),
            'baseline_mae': round(float(abs(part.baseline - part.actual).mean()), 2)}
    report = {'season': season, 'through': str(cutoff), 'generated_at': datetime.now(ZoneInfo('UTC')).isoformat(),
        'method': 'history-only reconstruction',
        'note': 'Reconstructed after games using only earlier regular-season box scores. Uses the existing history-only XGBoost evaluation model and production sanity caps. Excludes historical injury adjustments and opponent ratings; not an exact replay of the live model. No historical betting lines: percentages measure stated error margins, not over/under wins.',
        'completed_games': int(current.GAME_ID.nunique()), 'player_games': int(len(frame) // 3),
        'eligible_box_scores': len(current), 'skipped_player_games': len(skipped), 'skipped': skipped,
        'date_start': frame.date.min(), 'date_end': frame.date.max(), 'by_stat': stats}
    return report, frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--season', default='2026-27')
    parser.add_argument('--refresh', action='store_true', help='Download fresh current and previous season logs')
    parser.add_argument('--cutoff', default=datetime.now(ZoneInfo('America/New_York')).date().isoformat())
    args = parser.parse_args()
    cache = ROOT / 'evaluation_cache' / 'nba_season'
    year = int(args.season[:4]) - 1
    prior_season = f'{year}-{str(year+1)[-2:]}'
    cache.mkdir(parents=True, exist_ok=True)
    for season in (prior_season, args.season):
        path = cache / f'{season}.csv'
        if args.refresh or not path.exists():
            from nba_api.stats.endpoints import playergamelogs
            data = playergamelogs.PlayerGameLogs(season_nullable=season,
                season_type_nullable='Regular Season', timeout=60).get_data_frames()[0]
            data.to_csv(path, index=False)
    read = lambda season: pd.read_csv(cache / f'{season}.csv', dtype={'GAME_ID': str})
    report, frame = evaluate(read(prior_season), read(args.season), args.season, datetime.fromisoformat(args.cutoff).date())
    out = ROOT / 'evaluation_results' / 'nba_season'
    out.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out / 'predictions.csv', index=False)
    (out / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({k:v for k,v in report.items() if k != 'skipped'}, indent=2))

if __name__ == '__main__':
    main()

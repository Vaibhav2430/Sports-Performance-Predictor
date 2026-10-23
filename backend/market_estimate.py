"""Transparent, uncalibrated DraftKings fallback for players without history."""
from functools import lru_cache
import math
from nba_api.stats.endpoints import commonplayerinfo
from nba_api.stats.static import teams
from nba_data import NoHistory


@lru_cache(maxsize=1024)
def player_team(player_id):
    try:
        row = commonplayerinfo.CommonPlayerInfo(player_id=player_id, timeout=8).get_data_frames()[0].iloc[0]
        return str(row.get('TEAM_ABBREVIATION', ''))
    except Exception:
        return ''


def predict_from_market(player, lines, context, warnings=None):
    import odds
    if lines is None:
        lines = odds.get_player_lines(player['full_name'], 'NBA')
    lines = {s: float(v) for s,v in (lines or {}).items()
             if s in ('PTS', 'AST', 'REB') and isinstance(v, (int, float)) and math.isfinite(v) and v >= 0}
    if not lines:
        raise NoHistory(f"No usable NBA history or posted DraftKings points, assists, or rebounds lines are available for {player['full_name']} yet.")
    team = player_team(player['id'])
    matchup = odds.get_player_matchup(player['full_name'], 'NBA')
    by_name = {t['full_name']: t['abbreviation'] for t in teams.get_teams()}
    home, away = by_name.get(matchup.get('home_team')), by_name.get(matchup.get('away_team'))
    opponent = (away if team == home else home) if team and team in (home, away) else None
    stats = context.get('team_stats', {})
    def mean(key):
        values = [float(s[key]) for s in stats.values() if s.get(key) is not None and math.isfinite(float(s[key])) and float(s[key]) > 0]
        return sum(values) / len(values) if values else None
    factor, details = 1.0, []
    avg_pace, avg_def = mean('pace'), mean('def_rtg')
    own, opp = stats.get(team, {}), stats.get(opponent, {})
    if opponent and avg_pace and all(isinstance(s.get('pace'), (int,float)) and math.isfinite(s['pace']) and s['pace'] > 0 for s in (own, opp)):
        factor *= (own['pace'] + opp['pace']) / (2 * avg_pace)
        details.append('team and opponent pace')
    defense = opp.get('def_rtg')
    defense_factor = defense / avg_def if opponent and avg_def and isinstance(defense, (int,float)) and math.isfinite(defense) and defense > 0 else 1.0
    if defense_factor != 1.0:
        details.append('opponent defensive rating')
    predictions = {}
    for stat in ('PTS', 'AST', 'REB'):
        adjustment = max(.95, min(1.05, factor * (defense_factor if stat in ('PTS', 'AST') else 1.0)))
        predictions[stat] = {'prediction': round(lines[stat] * adjustment, 1) if stat in lines else None,
                             'last5_avg': None, 'season_avg': None, 'sigma': None,
                             'basis': 'DraftKings line and matchup heuristic' if details else 'DraftKings line',
                             'market_adjustment': round(adjustment, 4) if stat in lines else None}
    notes = list(warnings or [])
    notes.append('DraftKings-based estimate: no usable NBA history. This is a market reference, not a trained player forecast or a calibrated betting edge.')
    notes.append('Matchup adjustments use available pace and defense data, capped at ±5%. DraftKings lines may already reflect this matchup; this heuristic has not been backtested.' if details else 'No verified matchup metrics are available yet; posted DraftKings lines are used unchanged.')
    if len(lines) < 3:
        notes.append('Stats without a posted DraftKings line are unavailable.')
    return {'player': player['full_name'], 'team': team, 'team_name': next((t['full_name'] for t in teams.get_teams() if t['abbreviation'] == team), team),
            'team_off_rank': own.get('off_rank'), 'team_def_rank': own.get('def_rank'),
            'season': __import__('model').CURRENT_SEASON, 'games_used': 0, 'proj_min': None,
            'next_opponent': {'abbr': opponent, 'home': team == home, 'def_rank': opp.get('def_rank'), 'off_rank': opp.get('off_rank')} if opponent else None,
            'model_version': 'draftkings-matchup-reference-v1', 'apply_adjustments': False,
            'warnings': notes, 'predictions': predictions, 'game_log': [], 'lines': lines}

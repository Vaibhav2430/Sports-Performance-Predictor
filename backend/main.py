import math
import time
import logging
import threading
import database
import season_accuracy
import backups
from web_config import allowed_origins
import pandas as pd
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Request, Body, Depends
from fastapi.responses import JSONResponse
from typing import Literal
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from nba_api.stats.static import players as nba_players
from nba_api.live.nba.endpoints import scoreboard as live_scoreboard
from model import (
    normalize_player_name, predict, find_player as nba_find_player, fetch_game_log as nba_fetch_game_log,
    played_second_half as nba_played_second_half,
    fetch_team_defense_stats as nba_fetch_team_defense_stats,
    fetch_team_roster_averages as nba_fetch_team_roster_averages,
    _prev_nba_season as nba_prev_season,
)
import wnba_model
import odds
import tracker
import injuries
import team_context
import chatbot
from nba_data import NoHistory, HistoryUnavailable
from admin_auth import require_admin
import api_usage

logger = logging.getLogger(__name__)

def _seed_if_needed():
    """Retry each league independently; _seed_all skips saved player/game pairs."""
    for league in ("NBA", "WNBA"):
        try:
            _seed_all(league)
        except Exception:
            logger.exception("Automatic predictions failed for %s; will retry", league)


def _prediction_worker(stop):
    while not stop.is_set():
        try:
            _resolve_all()
        except Exception:
            logger.exception("Automatic box-score grading failed; will retry")
        _seed_if_needed()
        if stop.wait(3600):
            break


MIN_HISTORY_GAMES = 3          # need at least this many missed/normal games to trust history
HIST_MULT_BOUNDS  = (0.6, 1.6)  # clamp empirical multiplier to +/-40% either way


def _combined_log(player_id, league: str) -> pd.DataFrame:
    """Current + previous season game log for one player, so a player who's
    only recently returned still has enough missed-game history to compare."""
    if league == "NBA":
        cur  = nba_fetch_game_log(player_id)
        prev = nba_fetch_game_log(player_id, season=nba_prev_season())
    else:
        cur  = wnba_model.fetch_game_log(player_id, season=wnba_model._current_wnba_season())
        prev = wnba_model.fetch_game_log(player_id, season=wnba_model._prev_wnba_season())

    if cur.empty:
        return prev
    if prev.empty:
        return cur
    return pd.concat([prev, cur], ignore_index=True)


def _historical_stat_mults(target_id, op_id, league: str) -> dict | None:
    """Compare the target player's stat line in games the (currently Out)
    teammate has actually missed before vs. games that teammate played, over
    the current + previous season. Returns {"PTS": mult, "AST": mult, "REB": mult}
    if the teammate has enough missed-game history to trust; None otherwise,
    so the caller falls back to the fixed ppg-redistribution formula."""
    try:
        op_df  = _combined_log(op_id, league)
        tgt_df = _combined_log(target_id, league)
        if op_df.empty or tgt_df.empty:
            return None

        op_dates    = set(op_df["GAME_DATE"].dt.date)
        tgt_dates   = tgt_df["GAME_DATE"].dt.date
        missed_mask = ~tgt_dates.isin(op_dates)

        missed = tgt_df[missed_mask]
        normal = tgt_df[~missed_mask]
        if len(missed) < MIN_HISTORY_GAMES or len(normal) < MIN_HISTORY_GAMES:
            return None

        mults = {}
        for stat in ("PTS", "AST", "REB"):
            normal_avg = normal[stat].mean()
            missed_avg = missed[stat].mean()
            if normal_avg <= 0:
                continue
            mult = missed_avg / normal_avg
            mults[stat] = min(max(mult, HIST_MULT_BOUNDS[0]), HIST_MULT_BOUNDS[1])
        return mults or None
    except Exception:
        return None


def _apply_teammate_boosts(result: dict, league: str, target_id=None) -> None:
    """Mutate result['predictions'] in-place to account for Out teammates.

    Only key rotation players (top 6 on their team in minutes/game) move the
    prediction at all - a deep bench player being Out shouldn't shift anyone's
    number.

    For each qualifying Out teammate, prefer an empirically-derived boost:
    look back at every game that teammate has actually missed before (this
    season + last) and see how this specific player performed in those exact
    games vs. games the teammate played - i.e. make the prediction look like
    what actually happened last time. If the teammate doesn't have enough
    missed-game history yet (never hurt before), fall back to the fixed
    ppg-redistribution formula: boost pct = out_ppg * 0.5 / (team_ppg - out_ppg),
    capped at 25%, with AST getting 60% of the PTS boost and REB 20%.
    """
    team_abbr = result.get("team") or result.get("team_abbr")
    if not team_abbr:
        return

    out_players = injuries.get_out_players_for_team(team_abbr, league)
    if not out_players:
        return

    out_players = [
        op for op in out_players
        if team_context.is_top_mpg_player(op["name"], team_abbr, league)
    ]
    if not out_players:
        return

    team_ppg = team_context.get_team_ppg(team_abbr, league)
    boosts_applied = []

    stat_mults = {"PTS": 1.0, "AST": 1.0, "REB": 1.0}
    total_formula_pts_boost = 0.0

    for op in out_players:
        op_player = nba_find_player(op["name"]) if league == "NBA" else wnba_model.find_player(op["name"])
        hist = _historical_stat_mults(target_id, op_player["id"], league) if (op_player and target_id) else None

        if hist:
            for stat, mult in hist.items():
                stat_mults[stat] *= mult
            boosts_applied.append({
                "player": op["name"],
                "source": "historical",
                **{f"{stat.lower()}_mult": round(mult, 3) for stat, mult in hist.items()},
            })
            continue

        ppg = team_context.get_player_ppg(op["name"], league)
        if ppg is None or ppg < 8:  # skip low-impact players
            continue
        denominator = max(team_ppg - ppg, 40)  # avoid division edge cases
        boost = (ppg * 0.5) / denominator
        total_formula_pts_boost += boost
        boosts_applied.append({
            "player": op["name"], "source": "formula",
            "ppg": round(ppg, 1), "boost_pct": round(boost * 100, 1),
        })

    if not boosts_applied:
        return

    total_formula_pts_boost = min(total_formula_pts_boost, 0.25)  # hard cap at 25%
    stat_mults["PTS"] *= 1 + total_formula_pts_boost
    stat_mults["AST"] *= 1 + total_formula_pts_boost * 0.6
    stat_mults["REB"] *= 1 + total_formula_pts_boost * 0.2

    preds = result.get("predictions", {})
    for stat, multiplier in stat_mults.items():
        if stat not in preds or multiplier == 1.0:
            continue
        p = preds[stat]
        orig = float(p["prediction"])
        boosted = round(orig * multiplier, 1)
        p["prediction"]     = boosted
        p["season_avg"]     = p.get("season_avg", orig)   # keep original for display
        p["last5_avg"]      = p.get("last5_avg", orig)
        p["teammate_boost"] = round((multiplier - 1) * 100, 1)

    result["teammate_boosts"] = boosts_applied


RETURN_WINDOW_DAYS = 5  # how long a returning teammate's dampening effect tapers over


def _apply_return_dampening(result: dict, league: str) -> None:
    """Mutate result['predictions'] in-place to taper down teammates' stats as a
    recently-returned (previously Out) high-usage player reclaims touches.

    Mirrors _apply_teammate_boosts in reverse: while a star is out, usage shifts
    to teammates (see the boost above); once they're back that usage reverts, but
    not instantly, since box scores don't catch up until new games are played.
    The effect decays linearly to 0 over RETURN_WINDOW_DAYS.
    """
    team_abbr = result.get("team") or result.get("team_abbr")
    if not team_abbr:
        return

    returned = injuries.get_recently_returned_players(team_abbr, league, RETURN_WINDOW_DAYS)
    queried_player = (result.get("player") or "").lower()
    returned = [r for r in returned if r["name"].lower() != queried_player]
    if not returned:
        return

    team_ppg = team_context.get_team_ppg(team_abbr, league)
    dampers_applied = []

    total_pts_damp = 0.0
    for rp in returned:
        ppg = team_context.get_player_ppg(rp["name"], league)
        if ppg is None or ppg < 8:  # skip low-impact players
            continue
        denominator = max(team_ppg - ppg, 40)
        decay = max(0.0, 1 - rp["days_since"] / RETURN_WINDOW_DAYS)
        damp = (ppg * 0.5) / denominator * decay
        total_pts_damp += damp
        dampers_applied.append({
            "player":            rp["name"],
            "ppg":               round(ppg, 1),
            "days_since_return": rp["days_since"],
            "dampen_pct":        round(damp * 100, 1),
        })

    if not dampers_applied:
        return

    total_pts_damp = min(total_pts_damp, 0.25)  # same hard cap as the boost
    total_ast_damp = total_pts_damp * 0.6
    total_reb_damp = total_pts_damp * 0.2

    preds = result.get("predictions", {})
    for stat, multiplier in [("PTS", total_pts_damp), ("AST", total_ast_damp), ("REB", total_reb_damp)]:
        if stat not in preds or multiplier == 0:
            continue
        p = preds[stat]
        orig = float(p["prediction"])
        dampened = round(orig * (1 - multiplier), 1)
        p["prediction"]    = dampened
        p["season_avg"]    = p.get("season_avg", orig)
        p["last5_avg"]     = p.get("last5_avg", orig)
        p["return_dampen"] = round(multiplier * 100, 1)

    result["return_dampening"] = dampers_applied


# Statuses where the player will most likely suit up but at reduced or
# uncertain load - shade the projection down. "Out" is handled separately
# (no prediction is logged at all).
_QUESTIONABLE_MULT = {
    "doubtful":           0.60,
    "questionable":       0.85,
    "game time decision": 0.85,
    "game-time decision": 0.85,
    "day-to-day":         0.93,
}


def _apply_questionable_gate(result: dict, injury: dict | None) -> None:
    """Mutate result['predictions'] in-place, scaling every stat down when the
    player himself is not fully cleared to play."""
    if not injury:
        return
    status = (injury.get("status") or "").strip().lower()
    mult   = _QUESTIONABLE_MULT.get(status)
    if not mult:
        return

    for p in result.get("predictions", {}).values():
        orig = float(p["prediction"])
        p["prediction"]        = round(orig * mult, 1)
        p["season_avg"]        = p.get("season_avg", orig)   # keep originals for display
        p["last5_avg"]         = p.get("last5_avg", orig)
        p["questionable_gate"] = round((mult - 1) * 100, 1)

    result["questionable_gate"] = {"status": injury.get("status"), "mult": mult}


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _adjust_and_record(result: dict, league: str, injury: dict | None, target_id=None) -> None:
    """Preserve each forecast stage so adjustment effects can be paired later."""
    def snapshot():
        return {stat: float(p["prediction"]) for stat, p in result["predictions"].items() if p.get("prediction") is not None}

    stages = {"raw": snapshot()}
    if result.get("apply_adjustments", True):
        _apply_teammate_boosts(result, league, target_id=target_id)
        stages["teammate"] = snapshot()
        _apply_return_dampening(result, league)
        stages["return"] = snapshot()
        _apply_questionable_gate(result, injury)
        stages["questionable"] = snapshot()
    stages["final"] = snapshot()
    result["prediction_stages"] = stages


def _attach_p_over(result: dict) -> None:
    """For every stat with a posted line, add the model's probability the
    result lands OVER the line: P(X > line) for X ~ Normal(prediction, sigma).
    Call this last - after every adjustment that can move `prediction`."""
    lines = result.get("lines") or {}
    for stat, p in result.get("predictions", {}).items():
        line  = lines.get(stat)
        sigma = p.get("sigma")
        if line is None or not sigma or p.get("prediction") is None:
            continue
        threshold = math.floor(float(line)) + 0.5 if result.get("model_version", "").startswith("history-v1-") else float(line)
        z = (threshold - float(p["prediction"])) / max(float(sigma), 1e-6)
        p_over = round(1.0 - _normal_cdf(z), 3)
        p["p_over"]  = p_over
        if result.get("model_version", "").startswith("history-v1-"):
            # Integer lines have a nonzero push probability.
            under_z = (math.ceil(float(line)) - 0.5 - float(p["prediction"])) / max(float(sigma), 1e-6)
            p["p_under"] = round(_normal_cdf(under_z), 3)
        else:
            p["p_under"] = round(1.0 - p_over, 3)


@asynccontextmanager
async def lifespan(_app):
    if database.enabled():
        with database.connect() as connection:
            connection.execute('SELECT entry_key FROM predictions LIMIT 1')
            connection.execute('SELECT key FROM budgets LIMIT 1')
    stop = threading.Event()
    worker = threading.Thread(target=_prediction_worker, args=(stop,), daemon=True)
    worker.start()
    backup_worker = threading.Thread(target=backups.worker, args=(stop,), daemon=True)
    backup_worker.start()
    try:
        yield
    finally:
        stop.set()


app = FastAPI(title="NBA Stat Predictor API", lifespan=lifespan)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(database.DatabaseUnavailable)
async def database_unavailable(_request, _exc):
    return JSONResponse(status_code=503, content={"detail": "Saved data is temporarily unavailable. Please retry shortly."})

@app.exception_handler(api_usage.UsageLimitExceeded)
async def shared_usage_exceeded(_request, exc):
    return JSONResponse(status_code=429, content={"detail": exc.detail}, headers={"Retry-After": str(exc.retry_after)})


@app.exception_handler(api_usage.UsageStoreUnavailable)
async def shared_usage_unavailable(_request, _exc):
    return JSONResponse(status_code=503, content={"detail": "Usage protection is unavailable. Please retry shortly."}, headers={"Retry-After": "30"})


def guard_enrichment(request: Request):
    with api_usage.enrichment(get_remote_address(request)):
        yield


app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=False,
    allow_methods=['GET', 'POST'],
    allow_headers=['Content-Type', 'Authorization'],
    expose_headers=['Retry-After'],
)


def _predict_and_record(player: str, league: str, minimum_minutes: float = 0):
    """One prediction/injury/recording workflow for both leagues and seeding."""
    predictor = predict if league == "NBA" else wnba_model.predict
    finder = nba_find_player if league == "NBA" else wnba_model.find_player
    lines = odds.get_player_lines(player, league)
    result = predictor(player, market_lines=lines) if league == "NBA" else predictor(player)
    result["lines"] = lines
    result.update(odds.get_line_metadata(player, league))
    team_abbr = result.get("team") if league == "NBA" else result.get("team_abbr")
    if team_abbr:
        try:
            from team_ratings import rating_fields
            display = rating_fields(league, team_abbr)
            if display:
                result.update(display)
        except Exception:
            logger.exception("Display ratings unavailable for %s", player)
    injury = injuries.get_player_injury(result["player"], league)
    result["injury"] = injury
    p = finder(result["player"])
    if p:
        result["player_id"] = p["id"]
        result["headshot_url"] = (f"https://cdn.nba.com/headshots/nba/latest/1040x760/{p['id']}.png"
                                  if league == "NBA" else f"https://a.espncdn.com/i/headshots/wnba/players/full/{p['id']}.png")
    from copy import deepcopy
    base_predictions = deepcopy(result["predictions"])
    try:
        _adjust_and_record(result, league, injury, target_id=p["id"] if p else None)
    except Exception:
        logger.exception("Optional injury adjustment failed for %s", player)
        result["predictions"] = base_predictions
        result["prediction_stages"] = {"raw": {k: v["prediction"] for k,v in base_predictions.items() if v.get("prediction") is not None}}
        result.setdefault("warnings", []).append("Injury adjustments are unavailable; base forecast shown.")
    _attach_p_over(result)
    for stat, values in result["predictions"].items():
        if values.get("prediction") is not None and not math.isfinite(float(values["prediction"])):
            raise RuntimeError(f"Invalid numerical prediction for {stat}")
    inj_status = injury["status"] if injury else None
    is_out = inj_status and inj_status.lower() == "out"
    recent = result.get("game_log", [])[:5]
    enough_minutes = not recent or sum(g["MIN"] for g in recent) / len(recent) >= minimum_minutes
    if p and not is_out and enough_minutes:
        game_date = odds.get_player_game_date(player, league)
        tracker.log_prediction(result["player"], p["id"], league, lines, result["predictions"],
                               injury_status=inj_status, game_date=game_date,
                               prediction_stages=result["prediction_stages"], model_version=result.get("model_version"))
    return _json_safe(result)


def _json_safe(value):
    """Missing metadata becomes JSON null; forecast values are validated above."""
    import numbers
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        return value if math.isfinite(float(value)) else None
    return value


@app.get("/predict")
@limiter.limit("20/minute")
def predict_endpoint(request: Request, player: str = Query(..., description="Player full name")):
    try:
        with api_usage.prediction(get_remote_address(request)):
            return _predict_and_record(player, "NBA")
    except (api_usage.UsageLimitExceeded, api_usage.UsageStoreUnavailable, database.DatabaseUnavailable):
        raise
    except NoHistory as e:
        raise HTTPException(status_code=422, detail=str(e))
    except HistoryUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        logger.exception("NBA prediction failed for player=%r", player)
        raise HTTPException(status_code=500, detail="Prediction failed")


@app.get("/search")
@limiter.limit("60/minute")
def search_endpoint(request: Request, q: str = Query(default="")):
    all_players = sorted(p["full_name"] for p in nba_players.get_players() if p["is_active"])
    if not q:
        return all_players[:50]
    q_lower = normalize_player_name(q)
    matches = [name for name in all_players if q_lower in normalize_player_name(name)]
    return matches[:15]


@app.get("/wnba/search", dependencies=[Depends(guard_enrichment)])
@limiter.limit("60/minute")
def wnba_search(request: Request, q: str = Query(default="")):
    return wnba_model.search_players(q)


@app.get("/wnba/predict")
@limiter.limit("20/minute")
def wnba_predict(request: Request, player: str = Query(...)):
    try:
        with api_usage.prediction(get_remote_address(request)):
            return _predict_and_record(player, "WNBA")
    except (api_usage.UsageLimitExceeded, api_usage.UsageStoreUnavailable, database.DatabaseUnavailable):
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception:
        logger.exception("WNBA prediction failed for player=%r", player)
        raise HTTPException(status_code=500, detail="Prediction failed")


@app.get("/wnba/games/today", dependencies=[Depends(guard_enrichment)])
@limiter.limit("30/minute")
def wnba_games_today(request: Request):
    return wnba_model.games_today()


def _detect_early_exit(entry: dict, df: pd.DataFrame, game_idx: int, min_played: float | None,
                        game: pd.Series, player_id) -> bool:
    """True if the player left this game early with an injury: minutes well
    below their usual workload (cheap pre-filter, avoids a play-by-play call
    on every normal game), confirmed via play-by-play that they have no
    events in the 3rd/4th quarter, and now on the injury report."""
    if min_played is None:
        return False
    prior = df.iloc[:game_idx]
    recent_avg_min = prior["MIN"].tail(10).mean() if len(prior) >= 3 else None
    if not (recent_avg_min and min_played < recent_avg_min * 0.6):
        return False

    if entry["league"] == "NBA":
        played_2h = nba_played_second_half(game["Game_ID"], player_id)
    else:
        played_2h = wnba_model.played_second_half(game["EVENT_ID"], player_id)
    if played_2h is not False:
        # True (they were out there after halftime) or None (couldn't
        # verify) — either way don't assume an early exit.
        return False

    inj = injuries.get_player_injury(entry["player"], entry["league"])
    return bool(inj and inj.get("status", "").lower() in ("out", "day-to-day", "doubtful"))


def _resolve_all():
    """Fetch actual game results and mark predictions resolved. Runs in background.

    Holds tracker.lock for the whole pass (not just the load/save) so a
    log_prediction() call from a concurrent request can't append a new entry
    that this function's final save_log() would otherwise silently drop.
    """
    from datetime import date
    try:
        with api_usage.reserve_resolution():
            with tracker.lock:
                _resolve_all_locked()
    except api_usage.UsageLimitExceeded:
        return
    except api_usage.UsageStoreUnavailable:
        logger.warning("Box-score grading deferred while usage protection is unavailable")


def _resolve_all_locked():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    log = tracker.load_log()
    today = datetime.now(ZoneInfo('America/New_York')).date().isoformat()
    changed = False

    for entry in log:
        if entry.get("resolved"):
            continue
        if entry["date"] >= today:
            continue

        try:
            if entry["league"] == "NBA":
                p = nba_find_player(entry["player"])
                if not p:
                    continue
                df = nba_fetch_game_log(p["id"])
            else:
                p = wnba_model.find_player(entry["player"])
                if not p:
                    continue
                df = wnba_model.fetch_game_log(p["id"])

            if df.empty:
                continue

            pred_date = pd.Timestamp(entry["date"])
            same_day = df[df["GAME_DATE"].dt.date == pred_date.date()]
            if same_day.empty:
                # A stale feed, postponed game, or DNP is not a box score.
                # Retry rather than grade a different date or permanently
                # exclude a player while the result feed is still catching up.
                continue

            game_idx = same_day.index[0]
            game = same_day.iloc[0]

            # Exclude if the player barely played (DNP-illness/injury), even
            # if no injury status was on record at prediction time.
            min_played = None
            try:
                raw_min = game["MIN"]
                min_played = float(str(raw_min).split(":")[0]) if ":" in str(raw_min) else float(raw_min)
                if min_played < 5:
                    tracker.mark_excluded(entry)
                    changed = True
                    continue
            except Exception:
                min_played = None

            # When this fires, compute_stats voids an "over" that hadn't hit
            # yet instead of grading it a miss — an "under" that already
            # cleared the line still counts, same as sportsbooks grade it.
            if _detect_early_exit(entry, df, game_idx, min_played, game, p["id"]):
                entry["early_exit"] = True

            actual = {
                stat: float(game[stat])
                for stat in entry.get("lines", {})
                if stat in game.index
            }
            expected = set(entry.get('predicted', {})) & {'PTS', 'AST', 'REB'}
            if expected and expected <= actual.keys() and all(math.isfinite(actual[stat]) for stat in expected):
                tracker.mark_resolved(entry, actual)
                changed = True
        except Exception:
            continue

    if changed:
        tracker.save_log(log)


@app.get("/accuracy/backtest")
def season_backtest():
    import json
    from pathlib import Path
    if database.enabled():
        report = database.read_document('nba-season-backtest')
        if report is None:
            raise HTTPException(status_code=404, detail="Season backtest has not been imported")
        return report
    path = Path(__file__).parent / "evaluation_results" / "nba_season" / "report.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Season backtest has not been run")
    return json.loads(path.read_text())


@app.get("/accuracy")
@limiter.limit("30/minute")
def accuracy_endpoint(request: Request, background_tasks: BackgroundTasks, league: str = Query(default=None),
                      metric: Literal['over_under', 'within_margin'] = 'over_under'):
    log = tracker.load_log()
    if metric == 'within_margin':
        if league != 'NBA':
            raise HTTPException(status_code=422, detail='Combined season accuracy is available for NBA')
        from model import CURRENT_SEASON
        return season_accuracy.combined(log, season_accuracy.load_baseline(), CURRENT_SEASON)
    return tracker.compute_stats(log, league=league)


@app.get("/chat/leaderboard")
@limiter.limit("30/minute")
def chat_leaderboard(
    request: Request,
    background_tasks: BackgroundTasks,
    league: str = Query(default=None),
    limit: int = Query(default=10, ge=1, le=25),
    min_picks: int = Query(default=5, ge=1, le=50),
):
    """Best / worst player hit rates over resolved predictions (chatbot Q2/Q3)."""
    return chatbot.leaderboard(tracker.load_log(), league=league, limit=limit, min_picks=min_picks)


@app.post("/chat/reasoning")
@limiter.limit("30/minute")
def chat_reasoning(request: Request, payload: dict = Body(...), league: str = Query(default="NBA")):
    """Explain an already-fetched /predict payload in plain language (chatbot Q1).
    The frontend posts back the prediction result it already has, so this does
    no model re-run."""
    if not payload or not payload.get("predictions"):
        raise HTTPException(status_code=400, detail="Search a player first, then ask for the reasoning.")
    return chatbot.build_reasoning(payload, league=league)


def _seed_all(league: str, reservation=None):
    try:
        reservation = reservation or api_usage.reserve_bulk(league)
        with reservation:
            _seed_all_players(league)
    except api_usage.UsageLimitExceeded:
        logger.info("Bulk %s predictions deferred by shared usage protection", league)
    except api_usage.UsageStoreUnavailable:
        logger.warning("Bulk predictions disabled while usage protection is unavailable")


def _seed_all_players(league: str):
    from datetime import date
    all_lines = odds.fetch_lines(league)
    if not all_lines:
        return
    log = tracker.load_log()
    logged_pairs = {(e["player"], e["date"]) for e in log if e.get("league") == league and tracker.has_valid_predictions(e)}

    finder = nba_find_player if league == "NBA" else wnba_model.find_player
    for pp_name in all_lines:
        try:
            player = finder(pp_name)
            player_name = player["full_name"] if player else pp_name
            game_date = odds.get_player_game_date(pp_name, league) or str(date.today())
            if (player_name, game_date) in logged_pairs:
                continue
            with api_usage.prediction():
                _predict_and_record(pp_name, league, minimum_minutes=15)
        except (api_usage.UsageLimitExceeded, api_usage.UsageStoreUnavailable):
            logger.info("Bulk %s predictions paused at shared budget; remaining players will retry on the next scheduled run", league)
            break
        except Exception:
            logger.exception("Prediction failed for %s player %s; will retry", league, pp_name)


@app.post("/accuracy/seed")
@limiter.limit("2/minute")
def seed_accuracy(request: Request, background_tasks: BackgroundTasks,
                  league: Literal["NBA", "WNBA"] = Query(default="WNBA"), _admin=Depends(require_admin)):
    reservation = api_usage.reserve_bulk(league, admin=True)
    try:
        background_tasks.add_task(_seed_all, league, reservation)
    except Exception:
        reservation.store.release(reservation.token)
        raise
    return {"status": "started", "league": league}


@app.get("/team_ratings", dependencies=[Depends(guard_enrichment)])
def current_team_ratings(team: str, league: str = "NBA"):
    if league not in ("NBA", "WNBA"):
        raise HTTPException(status_code=422, detail="Choose NBA or WNBA")
    from team_ratings import rating_fields
    result = rating_fields(league, team)
    if result is None:
        raise HTTPException(status_code=503, detail="Current team ratings are temporarily unavailable")
    return result


@app.get("/team_preview", dependencies=[Depends(guard_enrichment)])
@limiter.limit("30/minute")
def team_preview(
    request: Request,
    home: str = Query(...),
    away: str = Query(...),
    league: str = Query(default="NBA"),
):
    is_wnba = league == "WNBA"
    try:
        team_stats = wnba_model.fetch_wnba_team_stats() if is_wnba else nba_fetch_team_defense_stats()
        get_roster = wnba_model.fetch_team_roster_averages if is_wnba else nba_fetch_team_roster_averages

        def build(abbr: str) -> dict:
            info = team_stats.get(abbr, {})
            return {
                "tricode":  abbr,
                "team_name": info.get("team_name", abbr),
                "off_rtg":  info.get("off_rtg"),
                "def_rtg":  info.get("def_rtg"),
                "off_rank": info.get("off_rank"),
                "def_rank": info.get("def_rank"),
                "players":  get_roster(abbr),
            }

        return {"home": build(home), "away": build(away)}
    except Exception:
        logger.exception("Team preview failed for home=%r away=%r league=%r", home, away, league)
        raise HTTPException(status_code=500, detail="Team preview failed")


@app.get("/games/today", dependencies=[Depends(guard_enrichment)])
@limiter.limit("30/minute")
def games_today(request: Request):
    from schedule import nba_games_today
    try:
        return nba_games_today()
    except Exception:
        logger.exception("Dated NBA schedule failed; trying NBA live feed")
    try:
        board = live_scoreboard.ScoreBoard()
        data  = board.get_dict()
        from datetime import datetime
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
        if data.get("scoreboard", {}).get("gameDate") != today:
            raise ValueError("NBA live scoreboard is for a different day")
        games = data.get("scoreboard", {}).get("games", [])
        result = []
        for g in games:
            home = g["homeTeam"]
            away = g["awayTeam"]
            result.append({
                "gameId":     g["gameId"],
                "status":     g["gameStatusText"].strip(),
                "statusCode": g["gameStatus"],   # 1=scheduled 2=live 3=final
                "home": {
                    "tricode": home["teamTricode"],
                    "name":    home["teamCity"] + " " + home["teamName"],
                    "score":   home.get("score", 0) or 0,
                    "wins":    home.get("wins", 0),
                    "losses":  home.get("losses", 0),
                },
                "away": {
                    "tricode": away["teamTricode"],
                    "name":    away["teamCity"] + " " + away["teamName"],
                    "score":   away.get("score", 0) or 0,
                    "wins":    away.get("wins", 0),
                    "losses":  away.get("losses", 0),
                },
            })
        return result
    except Exception:
        logger.exception("NBA schedule unavailable")
        raise HTTPException(status_code=503, detail="Schedule temporarily unavailable")

import threading
import pandas as pd
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from nba_api.stats.static import players as nba_players
from nba_api.live.nba.endpoints import scoreboard as live_scoreboard
from model import predict, find_player as nba_find_player, fetch_game_log as nba_fetch_game_log
import wnba_model
import odds
import tracker
import injuries

@asynccontextmanager
async def lifespan(_app):
    threading.Thread(target=_seed_all, args=("WNBA",), daemon=True).start()
    threading.Thread(target=_seed_all, args=("NBA",),  daemon=True).start()
    yield


app = FastAPI(title="NBA Stat Predictor API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/predict")
def predict_endpoint(player: str = Query(..., description="Player full name")):
    try:
        result = predict(player)
        lines = odds.get_player_lines(player, "NBA")
        result["lines"] = lines
        injury = injuries.get_player_injury(player, "NBA")
        result["injury"] = injury
        p = nba_find_player(player)
        inj_status = injury["status"] if injury else None
        is_out = inj_status and inj_status.lower() == "out"
        if p and not is_out:
            tracker.log_prediction(player, p["id"], "NBA", lines, result["predictions"], injury_status=inj_status)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prediction failed: {e}")


@app.get("/search")
def search_endpoint(q: str = Query(default="")):
    all_players = sorted(p["full_name"] for p in nba_players.get_players() if p["is_active"])
    if not q:
        return all_players[:50]
    q_lower = q.lower()
    matches = [name for name in all_players if q_lower in name.lower()]
    return matches[:15]


@app.get("/wnba/search")
def wnba_search(q: str = Query(default="")):
    return wnba_model.search_players(q)


@app.get("/wnba/predict")
def wnba_predict(player: str = Query(...)):
    try:
        result = wnba_model.predict(player)
        lines = odds.get_player_lines(player, "WNBA")
        result["lines"] = lines
        injury = injuries.get_player_injury(player, "WNBA")
        result["injury"] = injury
        p = wnba_model.find_player(player)
        inj_status = injury["status"] if injury else None
        is_out = inj_status and inj_status.lower() == "out"
        if p and not is_out:
            tracker.log_prediction(player, p["id"], "WNBA", lines, result["predictions"], injury_status=inj_status)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"WNBA prediction failed: {e}")


@app.get("/wnba/games/today")
def wnba_games_today():
    return wnba_model.games_today()


def _resolve_all():
    """Fetch actual game results and mark predictions resolved. Runs in background."""
    from datetime import date
    log = tracker.load_log()
    today = str(date.today())
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
            future = df[df["GAME_DATE"] >= pred_date]
            if future.empty:
                continue

            game = future.iloc[0]

            # If player was on the injury report and the first game after the
            # predicted date is on a DIFFERENT date, they DNP'd — exclude.
            inj_status = entry.get("injury_status")
            if inj_status:
                game_date = game["GAME_DATE"]
                if hasattr(game_date, "date") and game_date.date() > pred_date.date():
                    tracker.mark_excluded(entry)
                    changed = True
                    continue
                # Also exclude if they barely played (left early due to injury)
                try:
                    raw_min = game["MIN"]
                    min_played = float(str(raw_min).split(":")[0]) if ":" in str(raw_min) else float(raw_min)
                    if min_played < 5:
                        tracker.mark_excluded(entry)
                        changed = True
                        continue
                except Exception:
                    pass

            actual = {
                stat: float(game[stat])
                for stat in entry.get("lines", {})
                if stat in game.index
            }
            if actual:
                tracker.mark_resolved(entry, actual)
                changed = True
        except Exception:
            continue

    if changed:
        tracker.save_log(log)


@app.get("/accuracy")
def accuracy_endpoint(background_tasks: BackgroundTasks, league: str = Query(default=None)):
    background_tasks.add_task(_resolve_all)
    return tracker.compute_stats(tracker.load_log(), league=league)


def _seed_all(league: str):
    from datetime import date
    all_lines = odds.fetch_lines(league)
    if not all_lines:
        return
    today = str(date.today())
    log = tracker.load_log()
    already_logged = {e["player"] for e in log if e["date"] == today}

    for pp_name, lines in all_lines.items():
        if pp_name in already_logged:
            continue
        try:
            if league == "NBA":
                result = predict(pp_name)
            else:
                result = wnba_model.predict(pp_name)

            # Skip players averaging under 15 min
            game_log = result.get("game_log", [])
            if game_log:
                recent_min = sum(g["MIN"] for g in game_log[:5]) / min(5, len(game_log))
                if recent_min < 15:
                    continue

            player_name = result["player"]
            p = nba_find_player(player_name) if league == "NBA" else wnba_model.find_player(player_name)
            if p:
                tracker.log_prediction(player_name, p["id"], league, lines, result["predictions"])
        except Exception:
            continue


@app.post("/accuracy/seed")
def seed_accuracy(background_tasks: BackgroundTasks, league: str = Query(default="WNBA")):
    background_tasks.add_task(_seed_all, league)
    return {"status": "started", "league": league}


@app.get("/games/today")
def games_today():
    try:
        board = live_scoreboard.ScoreBoard()
        data  = board.get_dict()
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
        return []

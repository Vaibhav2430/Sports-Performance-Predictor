import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from nba_api.stats.static import players as nba_players
from nba_api.live.nba.endpoints import scoreboard as live_scoreboard
from model import predict, find_player as nba_find_player, fetch_game_log as nba_fetch_game_log
import wnba_model
import odds
import tracker

app = FastAPI(title="NBA Stat Predictor API")

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
        p = nba_find_player(player)
        if p:
            tracker.log_prediction(player, p["id"], "NBA", lines, result["predictions"])
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
        p = wnba_model.find_player(player)
        if p:
            tracker.log_prediction(player, p["id"], "WNBA", lines, result["predictions"])
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"WNBA prediction failed: {e}")


@app.get("/wnba/games/today")
def wnba_games_today():
    return wnba_model.games_today()


@app.get("/accuracy")
def accuracy_endpoint():
    from datetime import date
    log = tracker.load_log()
    today = str(date.today())
    changed = False

    for entry in log:
        if entry.get("resolved"):
            continue
        if entry["date"] >= today:
            continue  # Game may not have been played yet

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
                continue  # No game found yet on or after prediction date

            game = future.iloc[0]
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

    return tracker.compute_stats(log)


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

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from nba_api.stats.static import players as nba_players
from nba_api.live.nba.endpoints import scoreboard as live_scoreboard
from model import predict, find_player

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
        return predict(player)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prediction failed: {e}")


@app.get("/search")
def search_endpoint(q: str = Query(..., min_length=2)):
    q_lower = q.lower()
    matches = [
        p["full_name"]
        for p in nba_players.get_players()
        if q_lower in p["full_name"].lower()
    ]
    return sorted(matches)[:10]


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

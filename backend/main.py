from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from nba_api.stats.static import players as nba_players
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

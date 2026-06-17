# NBA Stat Predictor

Predicts a player's **Points**, **Assists**, and **Rebounds** for their next game using XGBoost models trained on their real NBA game logs.

## Stack

- **Backend** – FastAPI + XGBoost + nba_api
- **Frontend** – React + Vite + Recharts

## How it works

1. Fetches the player's last 3 seasons of game logs via `nba_api`
2. Engineers features: rolling L5/L10 averages, home/away, days rest
3. Trains three XGBoost regressors (one per stat) on that player's history
4. Predicts the next game and returns a floor/ceiling range based on recent variance

## Run locally

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # or use the root .venv
pip install -r requirements.txt
uvicorn main:app --reload
# → http://localhost:8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

Open [http://localhost:5173](http://localhost:5173), type a player name, and hit **Predict**.

> First prediction takes ~10s (3 API calls + model training). Results are not cached between sessions.

## API

`GET /predict?player=LeBron James`

`GET /search?q=lebron`

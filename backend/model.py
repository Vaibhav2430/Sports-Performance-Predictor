import time
import numpy as np
import pandas as pd
from nba_api.stats.endpoints import playergamelog
from nba_api.stats.static import players as nba_players
from xgboost import XGBRegressor

STATS = ["PTS", "AST", "REB"]
CURRENT_SEASON = "2024-25"
FEATURE_COLS = [
    "pts_l5", "pts_l10",
    "ast_l5", "ast_l10",
    "reb_l5", "reb_l10",
    "min_l5", "home", "days_rest",
]


def find_player(name: str) -> dict | None:
    all_p = nba_players.get_players()
    nl = name.lower().strip()
    exact = [p for p in all_p if p["full_name"].lower() == nl]
    if exact:
        return exact[0]
    partial = [p for p in all_p if nl in p["full_name"].lower()]
    return partial[0] if partial else None


def fetch_game_log(player_id: int) -> pd.DataFrame:
    time.sleep(0.65)
    log = playergamelog.PlayerGameLog(player_id=player_id, season=CURRENT_SEASON)
    df = log.get_data_frames()[0]
    if df.empty:
        return pd.DataFrame()

    df = df[["GAME_DATE", "MATCHUP", "WL", "MIN", "PTS", "AST", "REB"]].copy()
    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"])
    df = df.sort_values("GAME_DATE").reset_index(drop=True)
    for col in ["PTS", "AST", "REB", "MIN"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["HOME"] = df["MATCHUP"].apply(lambda x: 0 if "@" in x else 1)
    df["DAYS_REST"] = df["GAME_DATE"].diff().dt.days.fillna(2).clip(0, 10)
    return df


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for i in range(10, len(df)):
        hist = df.iloc[:i]
        cur = df.iloc[i]
        row = {
            "pts_l5":  hist["PTS"].tail(5).mean(),
            "pts_l10": hist["PTS"].tail(10).mean(),
            "ast_l5":  hist["AST"].tail(5).mean(),
            "ast_l10": hist["AST"].tail(10).mean(),
            "reb_l5":  hist["REB"].tail(5).mean(),
            "reb_l10": hist["REB"].tail(10).mean(),
            "min_l5":  hist["MIN"].tail(5).mean(),
            "home":      cur["HOME"],
            "days_rest": cur["DAYS_REST"],
            "PTS": cur["PTS"],
            "AST": cur["AST"],
            "REB": cur["REB"],
        }
        rows.append(row)
    return pd.DataFrame(rows)


def _next_features(df: pd.DataFrame) -> np.ndarray:
    feats = {
        "pts_l5":   df["PTS"].tail(5).mean(),
        "pts_l10":  df["PTS"].tail(10).mean(),
        "ast_l5":   df["AST"].tail(5).mean(),
        "ast_l10":  df["AST"].tail(10).mean(),
        "reb_l5":   df["REB"].tail(5).mean(),
        "reb_l10":  df["REB"].tail(10).mean(),
        "min_l5":   df["MIN"].tail(5).mean(),
        "home":     0.5,
        "days_rest": 2.0,
    }
    return np.array([[feats[c] for c in FEATURE_COLS]])


def predict(player_name: str) -> dict:
    player = find_player(player_name)
    if not player:
        raise ValueError(f"Player '{player_name}' not found.")

    df = fetch_game_log(player["id"])
    if len(df) < 15:
        raise ValueError(f"Not enough game data for {player['full_name']}.")

    feature_df = _build_features(df)
    X = feature_df[FEATURE_COLS].values
    X_next = _next_features(df)

    # Exponential weights: oldest game ~1x, most recent game ~7x
    n = len(X)
    sample_weights = np.exp(np.linspace(0, 2, n))

    predictions: dict = {}
    for stat in STATS:
        y = feature_df[stat].values
        model = XGBRegressor(
            n_estimators=150,
            max_depth=3,
            learning_rate=0.08,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            verbosity=0,
        )
        model.fit(X, y, sample_weight=sample_weights)
        pred = float(model.predict(X_next)[0])
        pred = max(0.0, pred)

        recent = df[stat].tail(10)
        std = float(recent.std())
        predictions[stat] = {
            "prediction": round(pred, 1),
            "floor":      round(max(0.0, pred - std), 1),
            "ceiling":    round(pred + std, 1),
            "last5_avg":  round(float(df[stat].tail(5).mean()), 1),
            "season_avg": round(float(df[stat].mean()), 1),
        }

    game_log = (
        df.tail(20)[["GAME_DATE", "MATCHUP", "WL", "MIN", "PTS", "AST", "REB"]]
        .copy()
        .iloc[::-1]
        .reset_index(drop=True)
    )
    game_log["GAME_DATE"] = game_log["GAME_DATE"].dt.strftime("%Y-%m-%d")
    game_log["MIN"] = game_log["MIN"].round(0).astype(int)

    return {
        "player":      player["full_name"],
        "season":      CURRENT_SEASON,
        "games_used":  len(df),
        "predictions": predictions,
        "game_log":    game_log.to_dict(orient="records"),
    }

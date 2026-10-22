"""Shared, causal features and prediction code for offline tuning and serving.

No current team ratings, injury reports, or future box scores enter these
features. Configuration selection and uncertainty fitting belong to the
validation period, never the final test period.
"""
from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

STATS = ("PTS", "AST", "REB")
SCHEMA_VERSION = 1
FEATURES = [f"{s.lower()}_l{n}" for s in (*STATS, "MIN") for n in (5, 10)]
FEATURES += ["home", "days_rest"]
FEATURES += [f"{s.lower()}_{venue}_avg" for s in STATS for venue in ("home", "away")]
ACTIVE_DIR = Path(__file__).parent / "model_configs"


@dataclass(frozen=True)
class Config:
    name: str
    depth: int = 2
    trees: int = 80
    rate: float = 0.04
    regularization: float = 5.0
    recent_weight: float = 1.0
    window: int | None = None
    adaptive: bool = False


CONFIGS = (
    Config("current_history_only", depth=3, trees=150, rate=0.08,
           regularization=1.0, recent_weight=2.1, adaptive=True),
    Config("shallow", depth=1, regularization=10.0),
    Config("regularized", depth=2),
    Config("recent20", depth=1, trees=60, window=20, recent_weight=2.1),
    Config("recent40", depth=2, trees=100, rate=0.03, window=40),
    Config("deeper", depth=3, trees=100, rate=0.03, regularization=10.0),
)


def clean_games(frame: pd.DataFrame) -> pd.DataFrame:
    """Canonical player game history. Bad numeric observations fail loudly."""
    if frame.empty:
        return frame.copy()
    df = frame.copy()
    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"]).dt.tz_localize(None).dt.normalize()
    df = df.dropna(subset=["GAME_DATE"])
    for c in (*STATS, "MIN", "HOME"):
        df[c] = pd.to_numeric(df[c], errors="raise")
        if not np.isfinite(df[c]).all():
            raise ValueError(f"Non-finite {c} in historical game data")
    # There is at most one regular-season game per player per calendar day.
    # Reject conflicting duplicates rather than silently choosing a box score.
    for _, group in df.groupby("GAME_DATE"):
        if len(group) > 1 and len(group[[*STATS, "MIN", "HOME"]].drop_duplicates()) > 1:
            raise ValueError("Conflicting box scores for one player/date")
    return df.drop_duplicates("GAME_DATE").sort_values("GAME_DATE").reset_index(drop=True)


def feature_row(history: pd.DataFrame, home: float, days_rest: float) -> dict:
    row = {f"{s.lower()}_l{n}": float(history[s].tail(n).mean())
           for s in (*STATS, "MIN") for n in (5, 10)}
    row.update(home=float(home), days_rest=float(np.clip(days_rest, 0, 10)))
    for stat in STATS:
        for venue, code in (("home", 1), ("away", 0)):
            split = history.loc[history["HOME"] == code, stat]
            row[f"{stat.lower()}_{venue}_avg"] = float(split.mean() if len(split) else history[stat].mean())
    return row


def examples(games: pd.DataFrame) -> pd.DataFrame:
    df = clean_games(games)
    rows = []
    for i in range(10, len(df)):
        hist, cur = df.iloc[:i], df.iloc[i]
        row = feature_row(hist, cur["HOME"], (cur["GAME_DATE"] - hist.iloc[-1]["GAME_DATE"]).days)
        row["GAME_DATE"] = cur["GAME_DATE"]
        for stat in STATS:
            row[stat] = float(cur[stat])
            row[f"{stat}_baseline"] = baseline(hist, stat)
            row[f"{stat}_scale"] = scale(hist, stat)
        rows.append(row)
    return pd.DataFrame(rows)


def baseline(history: pd.DataFrame, stat: str) -> float:
    return float(0.6 * history[stat].tail(5).mean() + 0.4 * history[stat].tail(10).mean())


def scale(history: pd.DataFrame, stat: str) -> float:
    return max(0.5, float(history[stat].tail(10).std(ddof=0)))


def fit(train: pd.DataFrame, stat: str, config: Config, league: str = "WNBA") -> XGBRegressor:
    train = train.tail(config.window) if config.window else train
    n = len(train)
    small = n < 15
    weights = np.ones(n)
    weight = 2.25 if config.adaptive and league == "NBA" else config.recent_weight
    weights[-10:] = weight
    model = XGBRegressor(
        n_estimators=max(30, min(150, n * 8)) if config.adaptive else config.trees,
        max_depth=(2 if small else 3) if config.adaptive else config.depth,
        learning_rate=config.rate,
        reg_lambda=(5.0 if small else 1.0) if config.adaptive else config.regularization,
        subsample=0.8, colsample_bytree=0.8, random_state=42,
        n_jobs=1, verbosity=0,
    )
    model.fit(train[FEATURES].to_numpy(), train[stat].to_numpy(), sample_weight=weights)
    return model


def load_active(league: str) -> dict | None:
    path = ACTIVE_DIR / f"{league.lower()}.json"
    if not path.exists():
        return None
    config = json.loads(path.read_text())
    if config.get("schema_version") != SCHEMA_VERSION or config.get("league") != league:
        raise ValueError(f"Incompatible tuning configuration: {path}")
    return config


def predict_tuned(games: pd.DataFrame, next_game: dict | None, artifact: dict) -> dict:
    """Use exactly the features, blend and uncertainty scale evaluated offline."""
    df = clean_games(games)
    train = examples(df)
    if len(train) < 5:
        raise ValueError("Tuned predictions require at least 15 historical games")
    row = feature_row(df, next_game["home"] if next_game else 0.5,
                      next_game["days_rest"] if next_game else 2.0)
    x = np.array([[row[c] for c in FEATURES]])
    out = {}
    for stat in STATS:
        setting = artifact["stats"][stat]
        alpha = setting["model_weight"]
        pred = baseline(df, stat)
        if alpha:
            model = fit(train, stat, Config(**setting["config"]), artifact["league"])
            pred = alpha * max(0.0, float(model.predict(x)[0])) + (1 - alpha) * pred
        out[stat] = {
            "prediction": round(float(pred), 1),
            "last5_avg": round(float(df[stat].tail(5).mean()), 1),
            "season_avg": round(float(df[stat].mean()), 1),
            "sigma": round(scale(df, stat) * setting["sigma_multiplier"], 2),
        }
    return out


def config_dict(config: Config) -> dict:
    return asdict(config)

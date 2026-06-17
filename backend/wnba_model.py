import time
import requests
import numpy as np
import pandas as pd
from xgboost import XGBRegressor

STATS        = ["PTS", "AST", "REB"]
FEATURE_COLS = [
    "pts_l5", "pts_l10",
    "ast_l5", "ast_l10",
    "reb_l5", "reb_l10",
    "min_l5", "home", "days_rest",
]

ESPN_BASE    = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba"
ESPN_WEB     = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/wnba"

_player_cache: dict | None = None   # name (lower) -> {id, full_name, team}


def _build_player_cache() -> dict:
    global _player_cache
    if _player_cache is not None:
        return _player_cache
    teams_r = requests.get(f"{ESPN_BASE}/teams", timeout=10)
    teams   = teams_r.json()["sports"][0]["leagues"][0]["teams"]
    cache: dict = {}
    for t in teams:
        tid   = t["team"]["id"]
        tname = t["team"]["displayName"]
        r = requests.get(f"{ESPN_BASE}/teams/{tid}/roster", timeout=10)
        if r.status_code == 200:
            for p in r.json().get("athletes", []):
                key = p["displayName"].lower()
                cache[key] = {
                    "id":        p["id"],
                    "full_name": p["displayName"],
                    "team":      tname,
                }
        time.sleep(0.2)
    _player_cache = cache
    return cache


def search_players(query: str) -> list[str]:
    cache  = _build_player_cache()
    q      = query.lower().strip()
    return sorted(
        p["full_name"] for p in cache.values() if q in p["full_name"].lower()
    )[:10]


def find_player(name: str) -> dict | None:
    cache = _build_player_cache()
    nl    = name.lower().strip()
    if nl in cache:
        return cache[nl]
    matches = [p for p in cache.values() if nl in p["full_name"].lower()]
    return matches[0] if matches else None


def fetch_game_log(player_id: str) -> pd.DataFrame:
    r = requests.get(f"{ESPN_WEB}/athletes/{player_id}/gamelog", timeout=15)
    r.raise_for_status()
    data = r.json()

    labels    = data["labels"]           # ['MIN','PTS','REB','AST',...]
    events_meta = data["events"]         # {eventId -> {date, homeAway, opponent, ...}}

    try:
        pts_idx = labels.index("PTS")
        ast_idx = labels.index("AST")
        reb_idx = labels.index("REB")
        min_idx = labels.index("MIN")
    except ValueError as e:
        raise ValueError(f"Missing stat column: {e}")

    rows = []
    for season_type in data.get("seasonTypes", []):
        if "Regular" not in season_type.get("displayName", ""):
            continue
        for category in season_type.get("categories", []):
            for event in category.get("events", []):
                eid   = event["eventId"]
                stats = event.get("stats", [])
                if not stats:
                    continue
                meta  = events_meta.get(eid, {})

                # Parse date
                raw_date = meta.get("gameDate", meta.get("date", ""))
                try:
                    game_date = pd.to_datetime(raw_date[:10]) if raw_date else pd.NaT
                except Exception:
                    game_date = pd.NaT

                # Home/away
                home_away = meta.get("homeAway", "")
                home      = 1 if home_away == "home" else 0

                # Opponent
                opp = ""
                for comp in meta.get("competitors", []):
                    if comp.get("homeAway") != home_away:
                        opp = comp.get("abbreviation", "")
                        break

                def _num(v):
                    try:
                        return float(str(v).split("-")[0])
                    except Exception:
                        return 0.0

                rows.append({
                    "GAME_DATE": game_date,
                    "MATCHUP":   opp,
                    "HOME":      home,
                    "MIN":  _num(stats[min_idx] if min_idx < len(stats) else 0),
                    "PTS":  _num(stats[pts_idx] if pts_idx < len(stats) else 0),
                    "AST":  _num(stats[ast_idx] if ast_idx < len(stats) else 0),
                    "REB":  _num(stats[reb_idx] if reb_idx < len(stats) else 0),
                })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.dropna(subset=["GAME_DATE"]).sort_values("GAME_DATE").reset_index(drop=True)
    df["DAYS_REST"] = df["GAME_DATE"].diff().dt.days.fillna(2).clip(0, 10)
    return df


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for i in range(10, len(df)):
        hist = df.iloc[:i]
        cur  = df.iloc[i]
        rows.append({
            "pts_l5":   hist["PTS"].tail(5).mean(),
            "pts_l10":  hist["PTS"].tail(10).mean(),
            "ast_l5":   hist["AST"].tail(5).mean(),
            "ast_l10":  hist["AST"].tail(10).mean(),
            "reb_l5":   hist["REB"].tail(5).mean(),
            "reb_l10":  hist["REB"].tail(10).mean(),
            "min_l5":   hist["MIN"].tail(5).mean(),
            "home":     cur["HOME"],
            "days_rest": cur["DAYS_REST"],
            "PTS": cur["PTS"],
            "AST": cur["AST"],
            "REB": cur["REB"],
        })
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
        raise ValueError(f"WNBA player '{player_name}' not found.")

    df = fetch_game_log(player["id"])
    if len(df) < 12:
        raise ValueError(f"Not enough game data for {player['full_name']}.")

    feature_df = _build_features(df)
    X          = feature_df[FEATURE_COLS].values
    X_next     = _next_features(df)
    n          = len(X)
    weights    = np.exp(np.linspace(0, 2, n))

    predictions: dict = {}
    for stat in STATS:
        y = feature_df[stat].values
        model = XGBRegressor(
            n_estimators=150, max_depth=3, learning_rate=0.08,
            subsample=0.8, colsample_bytree=0.8, random_state=42, verbosity=0,
        )
        model.fit(X, y, sample_weight=weights)
        pred = max(0.0, float(model.predict(X_next)[0]))
        std  = float(df[stat].tail(10).std())
        predictions[stat] = {
            "prediction": round(pred, 1),
            "floor":      round(max(0.0, pred - std), 1),
            "ceiling":    round(pred + std, 1),
            "last5_avg":  round(float(df[stat].tail(5).mean()), 1),
            "season_avg": round(float(df[stat].mean()), 1),
        }

    game_log = (
        df.tail(20)[["GAME_DATE", "MATCHUP", "HOME", "MIN", "PTS", "AST", "REB"]]
        .copy().iloc[::-1].reset_index(drop=True)
    )
    game_log["GAME_DATE"] = game_log["GAME_DATE"].dt.strftime("%Y-%m-%d")
    game_log["WL"]        = ""
    game_log["MIN"]       = game_log["MIN"].round(0).astype(int)

    return {
        "player":      player["full_name"],
        "team":        player["team"],
        "league":      "WNBA",
        "season":      "2026",
        "games_used":  len(df),
        "predictions": predictions,
        "game_log":    game_log[["GAME_DATE", "MATCHUP", "WL", "MIN", "PTS", "AST", "REB"]].to_dict(orient="records"),
    }


def games_today() -> list:
    try:
        r     = requests.get(f"{ESPN_BASE}/scoreboard", timeout=10)
        data  = r.json()
        result = []
        for event in data.get("events", []):
            comp       = event["competitions"][0]
            status     = comp["status"]["type"]
            state      = status["state"]          # pre / in / post
            status_txt = status["description"]    # "Scheduled", "In Progress", "Final"
            status_code = 1 if state == "pre" else (2 if state == "in" else 3)

            # Q + clock for live
            if state == "in":
                period  = comp["status"].get("period", "")
                clock   = comp["status"].get("displayClock", "")
                status_txt = f"Q{period} {clock}"

            competitors = comp["competitors"]
            home = next((c for c in competitors if c["homeAway"] == "home"), competitors[0])
            away = next((c for c in competitors if c["homeAway"] == "away"), competitors[1])

            def rec(c):
                rec_str = ""
                for r_item in c.get("records", []):
                    if r_item.get("type") == "total":
                        rec_str = r_item.get("summary", "")
                        break
                wins, losses = 0, 0
                if "-" in rec_str:
                    parts = rec_str.split("-")
                    try:
                        wins   = int(parts[0])
                        losses = int(parts[1])
                    except Exception:
                        pass
                return {
                    "tricode": c["team"].get("abbreviation", ""),
                    "name":    c["team"].get("displayName", ""),
                    "score":   int(c.get("score", 0) or 0),
                    "wins":    wins,
                    "losses":  losses,
                }

            result.append({
                "gameId":     event["id"],
                "status":     status_txt,
                "statusCode": status_code,
                "home": rec(home),
                "away": rec(away),
            })
        return result
    except Exception:
        return []

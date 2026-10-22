"""Reproducible chronological tuning. Run --help for download/evaluate options."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import tuning

ROOT = Path(__file__).parent


def metrics(actual, predicted) -> dict:
    error = np.asarray(predicted) - np.asarray(actual)
    return {"n": len(error), "mae": float(np.mean(np.abs(error))),
            "rmse": float(np.sqrt(np.mean(error ** 2))), "bias": float(np.mean(error))}


def collect(log_path: Path, league: str, cache: Path, limit: int | None, refresh: bool, offline: bool = False) -> dict:
    entries = json.loads(log_path.read_text())
    entries = [e for e in entries if e.get("league") == league and e.get("resolved") and not e.get("excluded")]
    players = sorted({str(e["player_id"]): e["player"] for e in entries}.items())
    if limit:
        players = players[:limit]
    if not entries:
        raise ValueError(f"No resolved {league} predictions; supply a log identifying players and dates")
    years = sorted({int(e["date"][:4]) for e in entries})
    seasons = sorted({str(y) for year in years for y in (year - 1, year)}) if league == "WNBA" else sorted({
        f"{y}-{str(y + 1)[-2:]}" for e in entries
        for start in [int(e["date"][:4]) - (int(e["date"][5:7]) < 10)] for y in (start - 1, start)})
    cache.mkdir(parents=True, exist_ok=True)
    import model
    import wnba_model
    fetch = wnba_model.fetch_game_log if league == "WNBA" else model.fetch_game_log

    def one(pid, name):
        frames, failures = [], []
        for season in seasons:
            path = cache / f"{league}_{pid}_{season}.csv"
            try:
                if path.exists() and (offline or not refresh):
                    df = pd.read_csv(path)
                elif offline:
                    failures.append(f"{name}/{season}: missing cached history")
                    continue
                else:
                    df = fetch(pid if league == "WNBA" else int(pid), season=season)
                    if df.empty:
                        failures.append(f"{name}/{season}: no games")
                        continue
                    df.to_csv(path, index=False)
                if not df.empty:
                    frames.append(df)
            except Exception as exc:
                failures.append(f"{name}/{season}: {type(exc).__name__}: {exc}")
        return pid, name, frames, failures

    data, failures = {}, []
    # NBA endpoints are more rate sensitive; avoid parallel calls there.
    with ThreadPoolExecutor(max_workers=4 if league == "WNBA" else 1) as pool:
        jobs = [pool.submit(one, pid, name) for pid, name in players]
        for index, job in enumerate(as_completed(jobs), 1):
            pid, name, frames, errors = job.result()
            failures.extend(errors)
            if frames:
                try:
                    data[pid] = {"name": name, "games": tuning.clean_games(pd.concat(frames, ignore_index=True))}
                except ValueError as exc:
                    failures.append(f"{name}: {exc}")
            print(f"History {index}/{len(players)}: {name}", flush=True)
    return {"players": data, "failures": failures, "requested_players": len(players), "seasons": seasons}


def split_dates(dates) -> list:
    dates = sorted(set(pd.to_datetime(dates)))
    if len(dates) < 20:
        raise ValueError("Need at least 20 distinct game dates for chronological validation/test")
    # First 40% supplies training; next three 15% blocks select parameters;
    # final 15% is an untouched test. Games on a date always stay together.
    boundaries = [dates[int(len(dates) * p)] for p in (0.40, 0.55, 0.70, 0.85)]
    return boundaries + [dates[-1] + pd.Timedelta(days=1)]


def walk_forward(players: dict, boundaries: list, league: str) -> pd.DataFrame:
    records = []
    for index, (pid, player) in enumerate(sorted(players.items()), 1):
        data = tuning.examples(player["games"])
        if data.empty:
            continue
        for fold, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            train = data[data.GAME_DATE < start]
            test = data[(data.GAME_DATE >= start) & (data.GAME_DATE < end)]
            if len(train) < 5 or test.empty:
                continue
            for stat in tuning.STATS:
                predictions = {}
                for config in tuning.CONFIGS:
                    estimator = tuning.fit(train, stat, config, league)
                    predictions[config.name] = np.maximum(0, estimator.predict(test[tuning.FEATURES].to_numpy()))
                for j, (_, row) in enumerate(test.iterrows()):
                    record = {"player_id": pid, "player": player["name"], "date": row.GAME_DATE.strftime("%Y-%m-%d"),
                              "fold": fold, "split": "test" if fold == 3 else "validation", "stat": stat,
                              "actual": row[stat], "baseline": row[f"{stat}_baseline"], "scale": row[f"{stat}_scale"]}
                    record.update({name: float(values[j]) for name, values in predictions.items()})
                    records.append(record)
        print(f"Evaluated {index}/{len(players)}: {player['name']}", flush=True)
    if not records:
        raise ValueError("Insufficient prior games for any evaluation fold")
    return pd.DataFrame(records)


def choose(validation: pd.DataFrame) -> tuple[dict, dict]:
    selected, rankings = {}, {}
    for stat in tuning.STATS:
        rows = validation[validation.stat == stat]
        if len(rows) < 30:
            raise ValueError(f"Need at least 30 validation observations for {stat}")
        candidates = [{"name": "baseline", "model_weight": 0.0, "config": None,
                       **metrics(rows.actual, rows.baseline)}]
        for config in tuning.CONFIGS:
            for alpha in (0.25, 0.5, 1.0):
                pred = alpha * rows[config.name] + (1 - alpha) * rows.baseline
                candidates.append({"name": f"{config.name}:{alpha}", "model_weight": alpha,
                                   "config": tuning.config_dict(config), **metrics(rows.actual, pred)})
        # Baseline wins ties. Nothing from the test period is used here.
        candidates.sort(key=lambda c: (c["mae"], c["model_weight"]))
        winner = dict(candidates[0])
        alpha = winner["model_weight"]
        pred = rows.baseline if not alpha else alpha * rows[winner["config"]["name"]] + (1 - alpha) * rows.baseline
        winner["sigma_multiplier"] = max(0.1, float(np.sqrt(np.mean(((rows.actual - pred) / rows.scale) ** 2))))
        selected[stat], rankings[stat] = winner, candidates
    return selected, rankings


def apply_selection(rows: pd.DataFrame, selected: dict) -> pd.DataFrame:
    rows = rows.copy()
    rows["selected"] = rows.baseline
    rows["sigma"] = np.nan
    for stat, setting in selected.items():
        mask = rows.stat == stat
        alpha = setting["model_weight"]
        if alpha:
            rows.loc[mask, "selected"] = alpha * rows.loc[mask, setting["config"]["name"]] + (1 - alpha) * rows.loc[mask, "baseline"]
        rows.loc[mask, "sigma"] = rows.loc[mask, "scale"] * setting["sigma_multiplier"]
    return rows


def score_test(rows: pd.DataFrame) -> dict:
    out = {}
    for stat in tuning.STATS:
        part = rows[rows.stat == stat]
        if part.empty:
            raise ValueError(f"No untouched test observations for {stat}")
        scores = {method: metrics(part.actual, part[method])
                  for method in ("baseline", "current_history_only", "selected")}
        abs_error = abs(part.actual - part.selected)
        scores["selected"]["coverage_80"] = float((abs_error <= 1.2815515655 * part.sigma).mean())
        scores["selected"]["coverage_95"] = float((abs_error <= 1.9599639845 * part.sigma).mean())
        # Date-clustered bootstrap preserves within-game/player correlation better
        # than treating every player observation as an independent sample.
        differences = (abs(part.actual - part.selected) - abs(part.actual - part.baseline))
        groups = pd.DataFrame({"date": part.date, "diff": differences}).groupby("date").agg(["sum", "count"])["diff"]
        rng = np.random.default_rng(42)
        samples = rng.integers(0, len(groups), size=(1000, len(groups)))
        delta = groups["sum"].to_numpy()[samples].sum(axis=1) / groups["count"].to_numpy()[samples].sum(axis=1)
        scores["selected"]["mae_delta_vs_baseline_95_ci"] = np.quantile(delta, [0.025, 0.975]).tolist()
        out[stat] = scores
    return out


def audit_logged(log_path: Path, test: pd.DataFrame, league: str) -> dict:
    """Compare stored final/stage forecasts only on identical matched observations."""
    entries = json.loads(log_path.read_text())
    matched, seen = [], set()
    mismatch_details = []
    mismatches = 0
    lookup = {(str(r.player_id), r.date, r.stat): r for r in test.itertuples()}
    for entry in entries:
        if entry.get("league") != league or not entry.get("resolved") or entry.get("excluded"):
            continue
        for stat, actual in entry.get("actual", {}).items():
            key = (str(entry["player_id"]), entry["date"], stat)
            row = lookup.get(key)
            final = entry.get("predicted", {}).get(stat)
            if row is None or key in seen or final is None or actual is None:
                continue
            if not math.isclose(float(actual), row.actual):
                mismatches += 1
                mismatch_details.append({"player_id": key[0], "date": key[1], "stat": stat,
                                         "logged_actual": float(actual), "historical_actual": row.actual})
                continue
            seen.add(key)
            item = {"stat": stat, "actual": row.actual, "logged_final": float(final),
                    "selected": row.selected, "baseline": row.baseline}
            for stage, values in entry.get("prediction_stages", {}).items():
                if values.get(stat) is not None:
                    item[f"stage_{stage}"] = float(values[stat])
            line = entry.get("lines", {}).get(stat)
            if line is not None:
                item["line"] = float(line)
                # Continuity correction for integer-valued game outcomes.
                item["selected_p_over"] = 0.5 * (1 - math.erf((math.floor(float(line)) + 0.5 - row.selected) / (row.sigma * math.sqrt(2))))
                item["logged_p_over"] = entry.get("p_over", {}).get(stat)
            matched.append(item)
    out = {"matched": len(matched), "actual_mismatches": mismatches, "mismatch_details": mismatch_details, "by_stat": {},
           "note": "Stored forecasts may span model versions and lack proof of pregame creation. Stage comparisons require new stage logging. No early-exit losses are removed."}
    if not matched:
        return out
    frame = pd.DataFrame(matched)
    for stat, part in frame.groupby("stat"):
        result = {method: metrics(part.actual, part[method]) for method in ("logged_final", "selected", "baseline")}
        for stage in [c for c in frame if c.startswith("stage_")]:
            paired = part.dropna(subset=[stage])
            if len(paired):
                result[stage] = {**metrics(paired.actual, paired[stage]),
                                 "final_on_same_rows": metrics(paired.actual, paired.logged_final)}
        if "line" in part:
            common = part[part.line.notna() & (part.actual != part.line)]
            for method in ("selected", "logged_final", "baseline"):
                common = common[common[method] != common.line]
            for method in ("selected", "logged_final", "baseline"):
                eligible = common
                if len(eligible):
                    result[method]["directional_n"] = len(eligible)
                    result[method]["directional_accuracy"] = float(((eligible[method] > eligible.line) == (eligible.actual > eligible.line)).mean())
            # Compare probabilities on the same rows where both were saved.
            paired = part.dropna(subset=["line", "selected_p_over", "logged_p_over"])
            for method, column in (("selected", "selected_p_over"), ("logged_final", "logged_p_over")):
                if len(paired):
                    result[method]["brier_n"] = len(paired)
                    result[method]["brier"] = float(np.mean((paired[column] - (paired.actual > paired.line)) ** 2))
        out["by_stat"][stat] = result
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", choices=("NBA", "WNBA"), default="WNBA")
    parser.add_argument("--log", type=Path, default=ROOT / "predictions_log.json")
    parser.add_argument("--cache", type=Path, default=ROOT / "evaluation_cache" / "eastern-v1")
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation_results")
    parser.add_argument("--limit", type=int, help="Quick sample of players; cannot activate")
    parser.add_argument("--refresh", action="store_true", help="Re-download histories")
    parser.add_argument("--offline", action="store_true", help="Use only existing cached CSVs; never access network")
    parser.add_argument("--activate", action="store_true", help="Install validation-selected settings after evaluating; requires full data and no worse test MAE than baseline")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.offline and args.refresh:
        parser.error("--offline and --refresh cannot be combined")
    # Freeze the input log: the running app may append/resolve entries mid-run.
    log_bytes = args.log.read_bytes()
    args.output.mkdir(parents=True, exist_ok=True)
    args.log = args.output / "input_log.json"
    args.log.write_bytes(log_bytes)
    bundle = collect(args.log, args.league, args.cache, args.limit, args.refresh, args.offline)
    if not bundle["players"]:
        raise ValueError("No historical data available; see network/cache access")
    all_dates = [d for p in bundle["players"].values() for d in p["games"].GAME_DATE]
    boundaries = split_dates(all_dates)
    rows = walk_forward(bundle["players"], boundaries, args.league)
    selected, rankings = choose(rows[rows.split == "validation"])
    rows = apply_selection(rows, selected)
    test = rows[rows.split == "test"]
    scores = score_test(test)
    fingerprint = hashlib.sha256("".join(
        pid + p["games"][["GAME_DATE", *tuning.STATS, "MIN", "HOME"]].to_csv(index=False)
        for pid, p in sorted(bundle["players"].items())).encode()).hexdigest()
    version = hashlib.sha256((fingerprint + json.dumps(selected, sort_keys=True)).encode()).hexdigest()[:12]
    artifact = {"schema_version": tuning.SCHEMA_VERSION, "league": args.league,
                "version": f"history-v1-{version}", "stats": selected,
                "apply_adjustments": False, "created_at": datetime.now(timezone.utc).isoformat(),
                "training_seasons": bundle["seasons"]}
    import xgboost
    report = {"artifact": artifact, "data_sha256": fingerprint,
              "log_sha256": hashlib.sha256(log_bytes).hexdigest(),
              "versions": {"xgboost": xgboost.__version__, "numpy": np.__version__, "pandas": pd.__version__},
              "players": len(bundle["players"]), "requested_players": bundle["requested_players"],
              "failures": bundle["failures"], "boundaries": [str(d.date()) for d in boundaries],
              "validation_rankings": rankings, "test": scores,
              "logged_comparison": audit_logged(args.log, test, args.league),
              "limitations": ["Historical player features only; current defense/injury context intentionally excluded.",
                              "Models refit at fold boundaries; rolling features update after each observed game. Live models refit per request.",
                              "Player cohort comes from the existing prediction log, not the entire league.",
                              "Validation residuals fit uncertainty; test coverage assesses it, but does not guarantee calibrated probabilities.",
                              "Reusing the test period to redesign candidates invalidates its status as an untouched test."]}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    (args.output / "candidate.json").write_text(json.dumps(artifact, indent=2, allow_nan=False) + "\n")
    rows.to_csv(args.output / "predictions.csv", index=False)
    print("\nUntouched test MAE (lower is better)")
    for stat, score in scores.items():
        print(f"{stat}: baseline={score['baseline']['mae']:.3f}, current/history-only={score['current_history_only']['mae']:.3f}, selected={score['selected']['mae']:.3f}, n={score['selected']['n']}; {selected[stat]['name']}")
    print(f"Report: {args.output / 'report.json'}")
    if args.activate:
        if args.limit or bundle["failures"] or any(scores[s]["selected"]["mae"] > scores[s]["baseline"]["mae"] for s in tuning.STATS):
            raise SystemExit("Results saved, but activation blocked: sample/incomplete data or test regression against baseline. Existing live model unchanged.")
        tuning.ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
        path = tuning.ACTIVE_DIR / f"{args.league.lower()}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(artifact, indent=2, allow_nan=False) + "\n")
        temporary.replace(path)
        print(f"Activated: {path}")


if __name__ == "__main__":
    main()

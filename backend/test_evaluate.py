"""Offline regression checks: python -m unittest discover -s backend -p 'test_*.py'."""
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import evaluate
import tuning
import wnba_model


def games(n=60):
    rng = np.random.default_rng(17)
    return pd.DataFrame({"GAME_DATE": pd.date_range("2025-05-01", periods=n, freq="2D"),
                         "PTS": rng.integers(8, 25, n), "AST": rng.integers(0, 8, n),
                         "REB": rng.integers(1, 12, n), "MIN": rng.integers(20, 36, n),
                         "HOME": np.arange(n) % 2})


class EvaluationTests(unittest.TestCase):
    def test_features_cannot_see_target_or_future(self):
        original = games()
        mutated = original.copy()
        mutated.loc[20:, ["PTS", "AST", "REB", "MIN"]] = 999
        before, after = tuning.examples(original), tuning.examples(mutated)
        pd.testing.assert_frame_equal(before.loc[:10, tuning.FEATURES], after.loc[:10, tuning.FEATURES])
        self.assertNotEqual(before.iloc[10].PTS, after.iloc[10].PTS)

    def test_train_and_serve_features_match(self):
        df = games()
        row = tuning.feature_row(df.iloc[:20], df.iloc[20].HOME, 2)
        expected = tuning.examples(df).iloc[10]
        np.testing.assert_allclose([row[c] for c in tuning.FEATURES], expected[tuning.FEATURES].astype(float))

    def test_date_splits_do_not_overlap(self):
        boundaries = evaluate.split_dates(list(games().GAME_DATE) * 2)
        self.assertEqual(len(boundaries), 5)
        self.assertTrue(all(a < b for a, b in zip(boundaries, boundaries[1:])))

    def test_conflicting_duplicate_rejected(self):
        df = games()
        duplicate = df.iloc[[0]].copy()
        duplicate["PTS"] = 900
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            tuning.clean_games(pd.concat([df, duplicate]))
        self.assertEqual(len(tuning.clean_games(pd.concat([df, df]))), len(df))

    def test_walk_forward_uses_only_prior_targets(self):
        df = games(50)
        bounds = evaluate.split_dates(df.GAME_DATE)
        config = tuning.Config("test", trees=3)
        actual_fit = tuning.fit
        fit_dates = []

        def checked_fit(train, *args):
            fit_dates.append(train.GAME_DATE.max())
            return actual_fit(train, *args)

        with patch.object(tuning, "CONFIGS", (config,)), patch.object(tuning, "fit", side_effect=checked_fit):
            rows = evaluate.walk_forward({"1": {"name": "Test", "games": df}}, bounds, "WNBA")
        for fold in range(4):
            self.assertTrue(all(d < bounds[fold] for d in fit_dates[fold * 3:fold * 3 + 3]))
        self.assertEqual(set(rows.split), {"validation", "test"})

    def test_baseline_can_win_selection(self):
        records = []
        for stat in tuning.STATS:
            for _ in range(35):
                records.append({"stat": stat, "actual": 10, "baseline": 10, "scale": 2,
                                **{c.name: 20 for c in tuning.CONFIGS}})
        selected, _ = evaluate.choose(pd.DataFrame(records))
        self.assertTrue(all(s["model_weight"] == 0 for s in selected.values()))

    def test_serving_uses_selected_blend_and_validation_sigma(self):
        df = games()
        config = tuning.Config("test", trees=3)
        artifact = {"league": "WNBA", "stats": {
            s: {"model_weight": 0.5, "config": tuning.config_dict(config), "sigma_multiplier": 1.7}
            for s in tuning.STATS}}
        result = tuning.predict_tuned(df, {"home": 1, "days_rest": 2}, artifact)
        train = tuning.examples(df)
        features = tuning.feature_row(df, 1, 2)
        x = np.array([[features[c] for c in tuning.FEATURES]])
        for stat in tuning.STATS:
            estimator = tuning.fit(train, stat, config)
            expected = 0.5 * max(0, float(estimator.predict(x)[0])) + 0.5 * tuning.baseline(df, stat)
            self.assertEqual(result[stat]["prediction"], round(expected, 1))
            self.assertEqual(result[stat]["sigma"], round(tuning.scale(df, stat) * 1.7, 2))

    def test_logged_audit_excludes_push_and_mismatch(self):
        test = pd.DataFrame([{"player_id": "1", "date": "2026-06-01", "stat": "PTS",
                              "actual": 10, "selected": 11, "baseline": 9, "sigma": 2}])
        entry = {"player_id": "1", "date": "2026-06-01", "league": "WNBA", "resolved": True,
                 "actual": {"PTS": 10}, "predicted": {"PTS": 11}, "lines": {"PTS": 10}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "log.json"
            path.write_text(json.dumps([entry, entry]))
            report = evaluate.audit_logged(path, test, "WNBA")
            self.assertEqual(report["matched"], 1)
            self.assertNotIn("directional_n", report["by_stat"]["PTS"]["selected"])
            entry["actual"]["PTS"] = 40
            path.write_text(json.dumps([entry]))
            report = evaluate.audit_logged(path, test, "WNBA")
            self.assertEqual(report["actual_mismatches"], 1)
            self.assertEqual(report["matched"], 0)

    def test_espn_dates_and_offseason(self):
        self.assertEqual(wnba_model._game_date("2026-05-22T00:00:00.000+00:00"), pd.Timestamp("2026-05-21"))
        self.assertEqual(wnba_model._game_date("2026-05-22T23:30:00.000+00:00"), pd.Timestamp("2026-05-22"))
        self.assertEqual(wnba_model._game_date("2026-05-22"), pd.Timestamp("2026-05-22"))
        with patch.object(wnba_model, "date") as fake:
            fake.today.return_value = date(2026, 10, 8)
            self.assertEqual(wnba_model._current_wnba_season(), "2026")
            fake.today.return_value = date(2027, 1, 8)
            self.assertEqual(wnba_model._current_wnba_season(), "2026")

    def test_offline_mode_never_fetches_and_reports_missing_season(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "log.json"
            log.write_text(json.dumps([{"league": "WNBA", "player_id": "1", "player": "Test",
                                        "date": "2026-06-01", "resolved": True}]))
            games().to_csv(root / "WNBA_1_2026.csv", index=False)
            with patch.object(wnba_model, "fetch_game_log") as fetch:
                bundle = evaluate.collect(log, "WNBA", root, None, False, offline=True)
                fetch.assert_not_called()
            self.assertEqual(len(bundle["players"]), 1)
            self.assertEqual(len(bundle["failures"]), 1)


class LoggingTests(unittest.TestCase):
    def test_tuned_integer_line_preserves_push_probability(self):
        import main
        result = {"model_version": "history-v1-test", "lines": {"PTS": 10},
                  "predictions": {"PTS": {"prediction": 10, "sigma": 2}}}
        main._attach_p_over(result)
        p = result["predictions"]["PTS"]
        self.assertEqual(p["p_over"], p["p_under"])
        self.assertLess(p["p_over"] + p["p_under"], 1)

    def test_adjustment_snapshots_are_independent(self):
        import main
        result = {"predictions": {"PTS": {"prediction": 10}}}

        def teammate(r, *args, **kwargs):
            r["predictions"]["PTS"]["prediction"] = 12

        with patch.object(main, "_apply_teammate_boosts", side_effect=teammate), \
             patch.object(main, "_apply_return_dampening"), patch.object(main, "_apply_questionable_gate"):
            main._adjust_and_record(result, "WNBA", None)
        self.assertEqual(result["prediction_stages"]["raw"]["PTS"], 10)
        self.assertEqual(result["prediction_stages"]["final"]["PTS"], 12)
        result["apply_adjustments"] = False
        with patch.object(main, "_apply_teammate_boosts") as adjustment:
            main._adjust_and_record(result, "WNBA", None)
            adjustment.assert_not_called()

    def test_tracker_preserves_stage_metadata(self):
        import tracker
        with tempfile.TemporaryDirectory() as directory, patch.object(tracker, "LOG_FILE", str(Path(directory) / "log.json")):
            tracker.log_prediction("Test", "1", "WNBA", {"PTS": 10.5}, {"PTS": {"prediction": 12}},
                                   prediction_stages={"raw": {"PTS": 10}}, model_version="test-v1")
            entry = tracker.load_log()[0]
            self.assertEqual(entry["model_version"], "test-v1")
            self.assertEqual(entry["prediction_stages"]["raw"]["PTS"], 10)
            self.assertIn("created_at", entry)


if __name__ == "__main__":
    unittest.main()

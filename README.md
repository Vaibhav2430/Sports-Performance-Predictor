# NBA & WNBA Stat Predictor

AI-powered predictions for **Points**, **Assists**, and **Rebounds** for any NBA or WNBA player's next game, using XGBoost models trained on real game logs.

## Stack

- **Backend** – FastAPI + XGBoost + nba_api + ESPN API
- **Frontend** – React + Vite + Recharts

## Features

- NBA and WNBA support with a league toggle
- Player search with live autocomplete dropdown (active players only)
- Per-game predictions with floor/ceiling range
- Today's live scoreboard in the sidebar with team logos, scores, records, and game times
- Player banner showing team logo, full team name, and offensive/defensive ranking badges (color-coded by tier)
- Game log table with opponent team logos and their offensive/defensive rankings
- Previous season data fallback when a player has limited current-season games
- Prediction cap based on current season average + standard deviation to prevent overestimates

## How it works

1. Fetches the player's current season game log (falls back to previous season if fewer than 15 games)
2. Engineers 17 features per game:
   - Rolling L5/L10 averages for PTS, AST, REB, MIN
   - Home/away splits (player's actual home vs away averages)
   - Days rest
   - Opponent defensive rating and pace (NBA) / points allowed per game (WNBA)
3. Trains three XGBoost regressors (one per stat) with recent games weighted 2.25× more than older ones
4. Caps the prediction at `season_avg + 1 standard deviation` using current season data only

## Ranking colors

Rank badges use dynamic thirds based on league size (30 NBA teams, 15 WNBA teams):
- **Green** – top third (elite)
- **Orange** – middle third
- **Red** – bottom third

## Evaluate and tune

From the project root, run the full WNBA comparison:

```bash
.venv/bin/python backend/evaluate.py --league WNBA
```

This downloads and caches current/prior historical seasons identified by the
prediction log, then compares recent averages, six XGBoost configurations, and
25%/50%/100% model blends. It selects a configuration separately for points,
assists, and rebounds using average absolute error (MAE).

The first 40% of distinct game dates supplies initial training history. Three
subsequent 15% blocks select settings; the final 15% is reserved for testing.
Models refit at each block boundary using only earlier targets. Rolling player
features update after each observed game. Current defensive ratings and injury
reports are excluded because historical snapshots are not available.
`current_history_only` uses the old tree settings on these same safe features;
it is not an exact replay of the production model. The saved-forecast audit
provides a separate comparison against the actual logged final predictions.

Outputs are in `backend/evaluation_results/`:

- `report.json`: validation rankings, test MAE/RMSE, interval coverage, matched
  saved-forecast comparisons, missing histories, and conflicting recorded results.
- `predictions.csv`: individual out-of-sample predictions and split assignments.
- `candidate.json`: selected settings and uncertainty fitted on validation errors.
- `input_log.json`: frozen input log for reproducibility. The live log is not edited.

Repeat from cached data without network access:

```bash
.venv/bin/python backend/evaluate.py --league WNBA --offline
```

Use `--limit 10` for a quick sample, `--refresh` to fetch updated histories, and
`--output PATH` to preserve separate runs. `--league NBA` uses NBA histories when
the input log contains resolved NBA predictions. Keep candidate settings fixed
after looking at the test results; tuning against that same test would invalidate
the comparison. Future changes need a new untouched period.

The workflow saves a candidate by default and does not replace the live model.
`--activate` installs it only for a full run with no missing/error histories and
no test MAE regression against the baseline in any stat. MAE is not over/under
accuracy: review both before activation. Active settings live in
`backend/model_configs/wnba.json` (or `nba.json`); move that file aside to restore
the legacy model. The tuned path uses the same feature/blend implementation as
evaluation and skips unevaluated injury multipliers. Live models refit per
request, whereas evaluation models refit per block.

New predictions record the model version, creation timestamp, and outputs before
and after each injury adjustment. Existing records cannot retrospectively reveal
these stages. The audit uses matching player/date/stat observations, checks the
actual outcome, deduplicates them, excludes actual pushes from directional
accuracy, and counts early-exit losses normally. It reports probability error on
rows where both probabilities exist. Old logs without timestamps are not proof of
pregame performance.

Run the offline regression checks:

```bash
.venv/bin/python -m unittest discover -s backend -p 'test_*.py'
```

## Run locally

```bash
./start.sh
```

This kills any existing processes on ports 8000 and 5173, starts the backend and frontend together, waits for the backend to be ready, then opens the app in your browser.

Or run manually:

```bash
# Backend
cd backend
uvicorn main:app --port 8000

# Frontend (separate terminal)
cd frontend
npm run dev
```

Open [http://localhost:5173](http://localhost:5173), click the search box, select a player, and hit **Predict**.

> NBA predictions take ~10s. WNBA predictions take ~15s (ESPN API + model training). Results are not cached between sessions.

## API

```
GET /predict?player=LeBron James
GET /search?q=lebron

GET /wnba/predict?player=Caitlin Clark
GET /wnba/search?q=clark

GET /games/today
GET /wnba/games/today
```

Automatic predictions run when the backend starts and every hour afterward.
Each league is retried independently, and saved player/game predictions are
skipped. Keep the backend running and the computer awake for ongoing checks;
starting it again checks the currently available lines, not historical ones.
NBA schedules use an explicit Eastern date from ESPN, with the NBA live feed
as a fallback. Feed failures appear as an error instead of an empty schedule.

## NBA season-to-date backtest

Run `.venv/bin/python backend/backtest_season.py --season 2026-27 --refresh`
to download regular-season box scores and reconstruct predictions for completed
Eastern game days before today. Omit `--refresh` to reuse downloaded CSVs.
Last season supplies early-season training history. Every target uses strictly
earlier player games; players with fewer than 15 prior games are reported as
skipped. Preseason games are excluded. The existing `current_history_only`
XGBoost evaluation configuration and production sanity caps are fixed in advance.
Historical injuries and opponent ratings are excluded, so this is not an exact
replay of the live model. It does not alter the live prediction log.

The backtest report records average error and the fraction within ±5 points,
±2 assists, and ±3 rebounds. These are not betting win rates. The top NBA accuracy
cards retain these season backtest results; WNBA cards show saved over/under results. Outputs: `backend/evaluation_results/nba_season/report.json`
and `predictions.csv`, including actuals, predictions, and training cutoff dates.

Both leagues share prediction adjustments, injury checks, forecast logging,
automatic seeding, and hourly box-score grading. The header uses the same card layout for each league. NBA shows season backtest
percentages and counts; WNBA shows over/under percentages and counts. Hover for
the metric definition. Backtest results remain separate from live forecast logs.

NBA predictions now share league-wide regular-season box-score snapshots across
players. Live requests have a time limit and can use a labeled saved snapshot
when the feed fails. Optional matchup enrichment refreshes in the background.
Players with 1–14 usable games receive a labeled recent-average estimate. Players
without usable history receive a DraftKings-based reference when lines exist;
verified team/opponent pace and defense can apply a heuristic capped at ±5%.
This heuristic is uncalibrated, and lines may already reflect the matchup.
No probability is reported for these market references. Unposted stats remain
unavailable. No-history players without lines receive an explicit explanation.

Run `.venv/bin/python backend/audit_nba_players.py` to exercise the NBA predictor
across the installed active-player directory using saved real box scores and
missing optional matchup data. Results go to
`backend/evaluation_results/nba_player_audit.json`. This checks forecast generation
and JSON validity; it does not measure accuracy or audit live feed availability.

Player-header offensive/defensive badges refresh from the official current-season
team-ratings feed at most once per minute for each league, independently of the
prediction-context cache. An open player page polls each minute and on returning
to the app. The last successful ratings snapshot persists across restarts. A
failed refresh preserves the original source timestamp, and the UI labels the
saved ratings rather than treating them as fresh. Hover shows the update time.

## Protecting expensive API operations

`POST /accuracy/seed` requires an `Authorization: Bearer <token>` header matching
`ADMIN_API_TOKEN`. Admin operations are disabled (503) when this backend-only
secret is unset or shorter than 32 characters. Invalid/missing credentials return
401; tokens in query strings are not accepted. Generate a cryptographically
random token and put it in the hosting provider's backend secrets or local `.env`.
Never put it in a `VITE_` variable, browser code, a public URL, or source control.
Send admin requests from a trusted server/terminal over HTTPS in production.

Shared usage protection uses PostgreSQL transactions when `STORAGE_BACKEND=postgres`,
including cross-host quotas and expiring job leases. With `STORAGE_BACKEND=local`,
it uses SQLite transactions rather than per-process counters.
Set `API_USAGE_DB` to an absolute path on a persistent **local** volume, shared
by every worker of the **same backend host**. It is not a distributed limiter:
do not deploy independent replicas, give each worker its own usage database, or
put this SQLite file on a network filesystem. A multi-host deployment requires
a distributed backend such as the Supabase PostgreSQL mode below. Do not delete/reset the database to bypass
limits. Database errors fail closed (503) for expensive operations.

Defaults (all configurable in `backend/.env.example`):

| Operation | Shared limit |
| --- | --- |
| Player forecasts, both leagues combined | 10/minute per IP, 60/minute globally, 600/hour globally |
| Concurrent player forecasts, including scheduled ones | 2 |
| Bulk runs requested by admins | 2/hour, one bulk job at a time |
| Automatic bulk seeding | Once/hour per league, shared across workers |
| Matchup, schedule, roster-search, rating requests | 30/minute per IP, 120/minute globally, 4 concurrent |
| Odds API HTTP calls, including scheduled ones | 10/minute, 40/day |
| Conservatively reserved odds credits, both leagues combined | 15/day, 500/calendar month |
| DraftKings line cache | 6 hours, shared across workers and restarts |

Limits use fixed UTC time windows; they can admit a burst across a window
boundary. They persist across backend restarts. Job leases renew while work runs
and expire after a crashed worker. A throttled API returns 429 with `Retry-After`.
Configure trusted proxy forwarding on the host so rate limiting sees real client
IPs; never blindly trust client-supplied forwarding headers.

The paid API budget is checked **before** network calls. Event lists cost no
credits; each event-odds request reserves up to three (one per requested market,
one region). The free plan's 500-credit limit resets on the first of each month
at UTC midnight. Responses' `x-requests-used` headers raise the monthly ledger
to include earlier account usage; they never reduce local reservations. These
reservations are conservative estimates, not the provider's billing ledger.
Monitor remaining provider credits, especially if other apps share the key.

Only today's events are fetched for props, in game-time order. The free budget
cannot cover continuously refreshed lines for every game: up to five complete
three-market event requests fit the daily cap, including repeated refreshes.
Saved upcoming lines remain available when refreshes are blocked, and the UI
shows when each player's lines were obtained. Yesterday's lines are removed.
Schedules and team-rating refreshes are independent of this six-hour cache.
Failed refreshes share a retry delay so searches do not hammer the provider.
If a bulk run exhausts a budget, remaining
players are deferred to the next scheduled run. Read-only accuracy/leaderboard
requests no longer trigger grading jobs; the existing scheduled worker handles
shared, throttled box-score grading instead.

## Supabase storage

The Python backend connects to Supabase PostgreSQL over TLS using a private
`DATABASE_URL`. Use the Session pooler URI (port 5432); percent-encode the password.
Keep this setting in backend secrets, never in frontend variables or Git.
`STORAGE_BACKEND=local` is the default. A connection string alone does not enable
database storage.

For a first migration, stop all local backend workers, install the backend
requirements, then run from `backend`:

```sh
../.venv/bin/python migrate_storage.py --apply
```

The importer creates the private `courtcast` schema, backs up the local prediction
log, saved reports/history, and SQLite usage database under
`backend/data_cache/migration_backups`, then copies records without deleting local
sources. It imports original forecasts and actuals, the NBA accuracy backtest,
injury history, rating/context snapshots, cached NBA box scores/player directory,
unexpired quota counters and saved betting lines. Repeated imports do not overwrite
existing database forecasts, grading, or documents, or lower consumed quotas.
All source forecast identities and the live accuracy totals are verified before
the importer reports success. Leases are not migrated: stop old workers first.

After verification, set `STORAGE_BACKEND=postgres` in backend settings and restart.
All deployed backend instances must use the same database. Requests fail closed
when the database is unavailable rather than silently switching to local files.
The first valid saved forecast for a player/game is retained; grading adds results
without changing that original prediction or dropping other players' records.

Database tables have RLS enabled and no grants for browser/API roles. They are in
the `courtcast` schema, which must **not** be added to Supabase's exposed schemas.
In Supabase's Table Editor, select `courtcast` to inspect them. The existing frontend
continues calling the Python API; it does not need Supabase keys. Account/login
features are not required for this setup. Model code and rebuildable in-memory
caches remain in the backend application.

Keep backups of the hosted database after cutover. The local migration snapshot
is a point-in-time backup and will not contain predictions saved later. Returning
to local mode without exporting newer records would hide those newer predictions.

Run optional live persistence tests with
`COURTCAST_INTEGRATION_TESTS=1 ../.venv/bin/python -m unittest test_postgres_storage -v`
from `backend`. Tests create and remove a disposable private schema, leaving the
production `courtcast` records untouched.

Run the regular offline suite with `STORAGE_BACKEND=local .venv/bin/python -m
unittest discover -s backend -p 'test_*.py'` from the repository root. The explicit
local setting prevents tests from using a real database configured in `.env`.

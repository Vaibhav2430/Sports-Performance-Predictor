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

Automatic predictions run when the backend starts and every 15 minutes afterward.
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
±2 assists, and ±3 rebounds. These are not betting win rates. Both NBA and WNBA header accuracy cards show
saved over/under results instead of this reconstructed baseline. Outputs: `backend/evaluation_results/nba_season/report.json`
and `predictions.csv`, including actuals, predictions, and training cutoff dates.

Both leagues share prediction adjustments, injury checks, forecast logging,
automatic seeding, and recurring box-score grading. Grading runs before new seeding
on startup and each scheduled pass, using the Eastern game day. A prediction is
graded only against its exact game date; missing or incomplete box scores stay
pending and retry instead of being matched to an adjacent day. Only completed
past game days are graded, so the previous day's available results are included
on the next scheduled pass while the backend runs.

Both headers request `/accuracy?league=NBA&metric=over_under` (or `league=WNBA`).
They display the same saved-pick win-rate calculation: the model projection
selects over or under the posted DraftKings line, and the actual box score
determines whether that pick won. Only saved markets with valid projections,
lines, and results count; exclusions, tied model projections, and injury voids
follow the shared over/under grader. Counts are summed before calculating each
stat's percentage. Headers refresh each minute and on returning to the page.

The historical reconstruction stays available separately at `/accuracy/backtest`.
The optional `/accuracy?league=NBA&metric=within_margin` endpoint still combines
`nba-accuracy-baseline` with newer saved forecasts using ±5/±2/±3 error margins,
without double-counting baseline dates. It is not used by the header cards.

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
| Automatic bulk seeding | Once/15 minutes per league, shared across workers |
| Matchup, schedule, roster-search, rating requests | 30/minute per IP, 120/minute globally, 4 concurrent |
| Conservatively reserved odds credits, both leagues combined | 500/calendar month; no app daily cap |
| DraftKings line cache | 30 minutes, shared across workers and restarts |

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

Only today's events are fetched for props, in game-time order. Every returned
event is requested, subject to the remaining monthly allowance and provider
availability; no app daily-credit or HTTP-count cap truncates the slate.
Saved upcoming lines remain available when refreshes are blocked, and the UI
shows when each player's lines were obtained. Yesterday's lines are removed.
Schedules and team-rating refreshes are independent of this 30-minute cache.
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

## Ongoing encrypted backups

`backend/backups.py` exports a transactionally consistent snapshot of CourtCast's
predictions (including original forecasts and actuals), accuracy/baseline documents,
other saved documents, API quota counters, and cached betting lines. It excludes
environment secrets, database accounts, and temporary job leases. This is an
application-data recovery archive, not a full Supabase account/schema/Storage dump.
The versioned JSON export has row counts and a SHA-256 integrity manifest, is
compressed and authenticated-encrypted with Fernet, and is decrypted and verified
before atomic publication. Only encrypted `.ccbackup` files are written.

Set `BACKUP_ENABLED=true`, `BACKUP_ENCRYPTION_KEY` to a generated Fernet key, and
optionally `BACKUP_DIRECTORY` to a persistent private directory. The default is
`backend/data_cache/backups`. The backend checks hourly and makes one verified
copy per UTC day, including immediately after a missed day when restarted.
`BACKUP_RETENTION_DAYS=30` removes old copies only after a new valid copy is saved.
Missing keys or failed exports never produce plaintext or replace the previous
backup. Local automation requires the backend and computer to be running.

Keep the encryption key in a password manager separately from the backups.
The locally configured recovery-key copy is `backend/data_cache/backups/restore-key.txt`
(private file permissions, excluded from Git). Losing the key makes the encrypted
archives unrecoverable. The `.env` file, backup files, and recovery key must never
be committed. On ephemeral cloud hosts use durable backup storage or the offsite
workflow below; local container files do not survive all redeployments.

Create a backup or verify a restore from the `backend` directory:

```sh
../.venv/bin/python backups.py
../.venv/bin/python backups.py --restore data_cache/backups/courtcast-YYYY-MM-DD.ccbackup
```

Verification is read-only. To recover missing records, configure `DATABASE_URL`
for the destination database (create `courtcast` with `migrations/001_storage.sql`
first if empty) and explicitly add `--apply` to the restore command. Recovery
merges missing records, fills missing grading, keeps newer records/documents, and
never lowers existing quota counters. It does not erase the target to recreate a
past point in time. Stop writers during recovery and test against a separate
database/schema before recovering production. Backups of auth users, schema
changes, secrets, and external assets require their own backup strategy.

`.github/workflows/database-backup.yml` prepares a daily offsite run at **09:17 UTC**
and manual runs from GitHub Actions. It uploads only encrypted archives with a
30-day artifact retention. It needs two GitHub Actions repository secrets:
`COURTCAST_BACKUP_DATABASE_URL` (the dedicated read-only export login) and
`COURTCAST_BACKUP_ENCRYPTION_KEY` (the same private recovery key). Never publish
these values as repository files or ordinary Actions variables. Provision the
read-only login using `backend/setup_backups.py`; its private connection is stored
as `BACKUP_DATABASE_URL` in backend `.env` and verified to lack write permission.
The cloud workflow is **not active** until the secrets are configured and the
workflow is pushed to the repository's default branch. Scheduled jobs can be
delayed/disabled by GitHub; review the run history and failure notifications.
Artifacts expire after retention and are not permanent archival storage.

## Website-to-backend connection

All frontend requests use `frontend/src/api.js`. For separate frontend/backend
hosts set **`VITE_API_BASE_URL=https://YOUR-BACKEND-HOST`** in the frontend hosting
provider's build settings, then rebuild/redeploy the frontend. This address is
public configuration; no database URLs, admin tokens, or other secrets may use
the `VITE_` prefix. HTTPS backend URLs are validated during production builds;
localhost, insecure HTTP, credentials in URLs, and query/fragment values are
rejected. Leave blank for the local Vite proxy or an explicitly configured
same-origin production reverse proxy. `/api` is also supported if a production
gateway forwards that path to the Python backend; Vite's proxy only works in
development and does not create such a production gateway automatically.

On the backend host set **`APP_ENV=production`** and
**`CORS_ALLOWED_ORIGINS=https://YOUR-WEBSITE-HOST`**. Add multiple exact HTTPS origins
with commas (for example the primary domain and its `www` domain). Production
startup rejects missing origins, HTTP origins, paths, embedded credentials, and
wildcards. Development defaults allow localhost/127.0.0.1 on the development
ports. Browser cookies are not sent; GET/POST, Content-Type, and Authorization
preflights are supported, and Retry-After is readable for throttled requests.
Preview domains must be explicitly added instead of allowing all Vercel domains.
CORS controls browser access; it does not replace the server's authentication
and shared usage limits. Configure trusted proxy forwarding for the chosen
backend host before launch so per-IP rate limiting identifies real visitors.

Both hosted addresses remain to be assigned when hosting is selected. The code
is configured for this separation, but creating a hosting account or a public
backend endpoint is a separate deployment step.

## Job and API-credit monitoring

The scheduler persists started/completed times, last successful run, failure
counts, and outcome counters in private `courtcast.snapshots` records prefixed
`monitor-`. Both league prediction jobs, box-score grading, the scheduler cycle,
and odds refreshes are monitored. Individual player failures no longer disappear
inside an otherwise "successful" bulk run: partial failures produce warnings,
and total failures produce critical incidents. Busy admission and normal rate
limits do not advance successful-job timestamps or erase a previous failure.
A successful retry clears the failed-job state. Monitoring stores fixed reason
codes, never exception messages, API URLs, passwords, player names, or keys.
The monitor writes atomically across workers and does not prevent predictions
from running if telemetry storage itself fails; the storage failure is logged.

The Odds API's normal response headers (`x-requests-used` and
`x-requests-remaining`) and `OUT_OF_USAGE_CREDITS` error code track provider quota.
Existing shared local budgets are also inspected, including when the next
three-market request cannot fit the remaining allowance. **No paid probes are
sent by monitoring.** Monthly exhaustion, rejected credentials, or failed jobs are critical. Low provider
credits warn at `MONITOR_LOW_CREDITS=50`. Monthly incidents expire with their UTC quota window. Provider numbers are the last observed response, not
an independently polled live balance.

`GET /health` gives only the aggregate status. Database unavailability returns
503; business incidents such as depleted credits retain HTTP 200 to avoid
hosting restart loops that cannot fix the incident. Inspect its JSON status or
use the dedicated watchdog for those alerts. Detailed job counters and incidents
are at `GET /admin/monitoring`, protected by the same backend-only Bearer admin
token as bulk seeding. Admin routes remain disabled if no token is configured.
For local inspection, run `../.venv/bin/python monitoring.py` from `backend`.
The CLI prints sanitized alerts and exits nonzero for critical incidents.

`.github/workflows/system-monitor.yml` runs independently every three hours and
supports manual runs. It uses the **existing** read-only
`COURTCAST_BACKUP_DATABASE_URL` repository secret, reads saved signals and quota
counters, and fails on critical incidents so GitHub Actions' failure
notifications can alert the repository owner. Warnings appear in run logs and
annotations without failing the job. This reuses the already configured backup
connection; no admin token, encryption key, or paid API key is needed. Keep
GitHub Actions failure notifications enabled. Publishing the workflow to the
default branch activates its schedule; schedules may be delayed by GitHub.

While the app only runs on a local computer, `MONITOR_REQUIRE_SCHEDULER=false`
avoids claiming an intentionally stopped computer is a failed always-on host.
After hosting, set this to **true** in both backend settings and the GitHub
repository's Actions **variable** `MONITOR_REQUIRE_SCHEDULER`. Missing/stale job
reports then become critical after `MONITOR_STALE_JOB_SECONDS=10800` (3 hours).
Production defaults to requiring scheduler reports unless explicitly overridden.
A run that stays in progress past that window is also flagged. Missing box
scores stay pending; `MONITOR_PENDING_GRACE_DAYS=3` raises a warning for old
pending results without assuming whether the cause is a DNP, postponement, or
feed problem. No result is fabricated or excluded to silence that warning.

Run optional live persistence tests with
`COURTCAST_INTEGRATION_TESTS=1 ../.venv/bin/python -m unittest test_postgres_storage -v`
from `backend`. Tests create and remove a disposable private schema, leaving the
production `courtcast` records untouched.

Run the regular offline suite with `STORAGE_BACKEND=local .venv/bin/python -m
unittest discover -s backend -p 'test_*.py'` from the repository root. The explicit
local setting prevents tests from using a real database configured in `.env`.

### Diagnosing missing DraftKings lines and exporting live forecasts

`/predict` attaches `lines` and `lines_last_updated` from `odds.py` to the model
response; `App.jsx` passes `lines` into `PropCards.jsx`. Missing stat lines now
show **DraftKings Line — Unavailable**. The response also includes a safe
`lines_status` and `lines_message`, distinguishing a shared usage limit from a
provider failure or no returned props. Saved lines can still be displayed while
a refresh is blocked. These messages do not include provider URLs or API keys.

The original 30-minute line refresh is restored. App daily-credit, daily HTTP,
and per-minute odds-request caps are removed, including enforcement of their
old saved counters. Old six-hour or daily-blocked snapshots refresh once on the
next line lookup after restarting the updated backend. The 500-credit monthly
cap remains shared across both leagues; provider availability and monthly
credits still determine which posted lines can be returned. Cached lines are
preserved if a refresh fails or reaches that monthly cap.

Automatic seeding starts from players returned by DraftKings. Predictions without
lines are not saved for over/under grading, and each saved forecast is graded
only for stats that had a posted line. All applicable injury and minutes filters
continue to apply.

The backend worker grades past games and seeds forecasts on startup, then waits
15 minutes after each pass. It runs only while the backend process is alive.
The GitHub monitoring workflow checks saved job state every three hours; it does
not run prediction generation or grading.

Export live NBA records from the **configured storage backend** with:

```bash
.venv/bin/python backend/export_predictions.py --season 2026-27 \
  --output backend/evaluation_results/exports/nba_2026-27_live_predictions.json
```

The JSON separates graded, excluded, and pending player-games and preserves
per-stat lines, forecasts, actuals, injury flags, timestamps, and model versions.
Postgres mode reads `courtcast.predictions`; local mode reads
`backend/predictions_log.json`. A database failure stops the export instead of
silently substituting the older local file. No odds requests or grading writes
are performed. The October-through-June date filter does not identify game type;
source records have no regular-season/playoff marker. For hit-rate analysis,
apply per-stat validity checks, prediction ties, actual pushes, and early-exit
voids before choosing a minimum sample size.

`backend/evaluation_results/nba_season/predictions.csv` is a reconstructed,
history-only backtest. It has no historical sportsbook lines and cannot be used
as a live DraftKings over/under hit-rate record.

WNBA grading includes regular-season and postseason box scores for the saved
game year, so completed Finals predictions can resolve. Model training continues
to use regular-season history. Grading preserves the original posted lines and
forecasts and matches only the saved Eastern game date.


## Daily cloud prediction coverage

`.github/workflows/daily-predictions.yml` runs `backend/daily_coverage.py` every
30 minutes using GitHub-hosted runners. It also supports a manual run. The Mac
and web app can be off. GitHub schedules can be delayed; this is recurring
collection, not a continuously running web host. Scheduled workflows must be on
the repository's default branch before GitHub activates them.

Configure these **encrypted Actions secrets**, never repository files:

- `COURTCAST_DATABASE_URL`: the backend's write-capable PostgreSQL connection.
  The existing backup connection is read-only and cannot save forecasts.
- `ODDS_API_KEY`: the backend's Odds API key.

The collector requires PostgreSQL; it refuses to silently use an ephemeral
runner's local JSON file. It reuses shared line snapshots, quota counters,
renewing job leases, and first-forecast deduplication. Separate Mac/cloud workers
cannot overwrite an original forecast. Short prediction admission delays are
retried once in the same run, and incomplete players retry on later runs.
Known game start times prevent forecasts being saved after tipoff.

Per-date snapshots named `daily-coverage-NBA-YYYY-MM-DD` and
`daily-coverage-WNBA-YYYY-MM-DD` retain the players observed with posted lines,
which have saved forecasts, and which are missing. A posted player filtered out
by the existing injury/minutes checks remains visible as missing rather than
being falsely counted as captured. `daily-collection-latest` stores aggregate
coverage and over/under accuracy. Job output contains aggregate counts, not keys
or database URLs. Failures and quota blocks remain visible through monitoring;
cloud jobs fail on critical monitoring incidents and annotate missing coverage.

The 500-credit monthly cap is still shared across both leagues. Scheduling does
not add provider credits: frequent full-slate refreshes can exhaust that allowance.
Match any higher cap to the provider plan before changing it. This workflow does
not buy or change a subscription and does not reconstruct missing historical
forecasts as if they had been saved before games.

# AGENTS.md
<!-- Updated: 2026-08-27 00:19:44 +0800 -->

## Session Memory
@.bob/memory/session.md

This file provides guidance to agents when working with code in this repository.

## Stack

- **Backend**: Python 3 (FastAPI + Click CLI + SQLite)
- **Frontend**: Nginx static Web UI (`web/` — port 3001), proxies API calls to `elk-analyser:8080`
- **Container**: Podman Compose — two services: `elk-analyser` (port 8080) + `elk-analyser-web` (port 3001)
  - Build context for `elk-analyser` is `./bob-analyser/`, NOT repo root
- **AI Bridge**: Bob CLI (`bob run`) runs **directly inside** the `elk-analyser` container (not SSH to external sandbox)
- **No scheduler** — analysis is triggered by: Kibana Alert Webhook / Web UI / manual CLI → `POST /analyse`

## Commands

```bash
# Build & start all containers
podman-compose up --build -d

# On-demand analysis (inside container)
podman exec elk-analyser python cli.py run --from "2025-01-15 08:00" --to "2025-01-15 12:00"

# Trigger via API — manual (from host)
curl -X POST http://localhost:8080/analyse \
  -H "Content-Type: application/json" \
  -d '{"from_time":"2025-01-15 08:00","to_time":"2025-01-15 12:00"}'

# Trigger via API — async (returns job_id immediately)
curl -X POST http://localhost:8080/analyse/async \
  -H "Content-Type: application/json" \
  -d '{"from_time":"2025-01-15 08:00","to_time":"2025-01-15 12:00"}'

# Trigger via API — simulate Kibana Alert (lookback_minutes)
curl -X POST http://localhost:8080/analyse \
  -H "Content-Type: application/json" \
  -d '{"to_time":"2025-01-15 12:00","lookback_minutes":5,"trigger":"kibana_alert"}'

# Inject demo data (WAS/MQ/DB2 mock logs)
curl -X POST http://localhost:8080/demo/inject

# View analysis history / download report
podman exec elk-analyser python cli.py history --limit 10
curl http://localhost:8080/jobs
curl -O http://localhost:8080/jobs/1/report

# API health check
curl http://localhost:8080/health
```

No unit tests or linter configs exist in this repo. Validation is manual via container exec.

## Critical Architecture

**Pipeline**: `extractor.py` → `preprocessor.py` → `bob_bridge.py` → `report_builder.py`

All four stages are orchestrated by [`analysis_pipeline.run_analysis()`](bob-analyser/analysis_pipeline.py).

**Containers** (podman-compose.yml):
- `elk-analyser` — FastAPI + Bob CLI bridge; fixed IP `10.89.1.10` on `elk-analyser-network`
- `elk-analyser-web` — Nginx static UI (port 3001), proxies `/api/` → `elk-analyser:8080`

**Volumes**:
- `bob_workspace` → `/workspace` — event-sequence JSON handoff (shared with bob)
- `elk_reports` → `/reports` — PPTX output
- `elk_db` → `/db` — SQLite persistence

**Checkpoint**: stored in SQLite `checkpoint` table (single row, `id=1`). Only written on `completed` status — failures preserve the previous checkpoint so the next run retries the same window.

**`POST /analyse` query window resolution** (four-stage priority):
1. `from_time` provided → use directly (Web UI / CLI)
2. `lookback_minutes` provided → `from_time = to_time - lookback_minutes` (Kibana Alert)
3. Neither provided, Checkpoint exists → resume from last `query_to`
4. None of the above → `now - config.checkpoint.default_lookback_minutes` (default 60 min)

**Bob Bridge** writes event sequence JSON to `/workspace/logs/job-{id}.json` then calls:
```
bob run --accept-license --mode log-analyst --format json --max-turns 5 @/workspace/logs/job-{id}.json
```
Parses `last_message` from the JSON output. Strips markdown code fences before JSON parse.

**Paths inside container** (hardcoded):
- Config: `/app/config/config.yaml`
- DB: `/db/history.db`
- Reports: `/reports/`
- Workspace: `/workspace/logs/`

## Code Style

- Module-level `logger = logging.getLogger(__name__)` — never `print()` in analyser modules
- Config always loaded via `_load_config(config_path)` helper at top of each module
- `config_path` defaulting to `/app/config/config.yaml` is the contract — always propagate it
- Datetime objects must carry timezone info (`tzinfo=timezone.utc`); naive datetimes break ES queries
- `json.dumps(..., ensure_ascii=False)` for any JSON with CJK content
- Error returns use `{"error": "...", "raw": ...}` dict pattern — never raise exceptions from `analyse()` or `build_report()`
- `_update_job()` uses `WHERE rowid = (SELECT rowid ... ORDER BY rowid DESC LIMIT 1)` — not by job_id UUID

## Elasticsearch Mode

`config.yaml` `elasticsearch.mode` controls mock/local/remote:
- `mock`: reads from `bob-analyser/mock/sample_logs.json`
- `local`/`remote`: uses `elasticsearch-py` with scroll pagination (500/batch, max 5000 hits)
- Field names are NOT hardcoded — always resolved through `config["field_mapping"]`

## Bob Custom Mode

`bob-analyser/bob-custom-modes/custom_modes.yaml` is copied to `/root/.bob/settings/custom_modes.yaml` at image build — this is bob 2.x's `globalScopeRoot` for custom modes. To update the `log-analyst` mode, edit that file and **rebuild the container**.

## Environment

`BOB_API_KEY` must be set in `bob-analyser/.env` (copy from `.env.example`). The container passes it to `bob run` via `env=` in `subprocess.run`. (`BOBSHELL_API_KEY` is kept for backward compatibility — both keys are accepted.)

## Trigger Priority (`POST /analyse`)

| Priority | Condition | `from_time` source |
|---|---|---|
| 1 | `from_time` provided | direct (Web UI / CLI) |
| 2 | `lookback_minutes` provided | `to_time - lookback_minutes` (Kibana Alert) |
| 3 | Neither, Checkpoint exists | resume from last `query_to` |
| 4 | None of above | `now - config.checkpoint.default_lookback_minutes` (default 60 min) |

## Key Files

| File | Role |
|------|------|
| `bob-analyser/analysis_pipeline.py` | Pipeline orchestrator — import hub for all four stages |
| `bob-analyser/api.py` | FastAPI app — all REST endpoints |
| `bob-analyser/cli.py` | Click CLI — `run` / `history` / `show` subcommands |
| `bob-analyser/analyser/extractor.py` | ES query + checkpoint read/write |
| `bob-analyser/analyser/preprocessor.py` | Build event-sequence dict from raw logs |
| `bob-analyser/analyser/bob_bridge.py` | Write JSON to `/workspace/logs/`, call `bob run`, parse result |
| `bob-analyser/analyser/report_builder.py` | Generate PPTX via python-pptx + matplotlib |
| `bob-analyser/bob-custom-modes/custom_modes.yaml` | Deployed as bob 2.x custom modes at build time |
| `bob-analyser/config/config.yaml` | All runtime config — ES, field mapping, paths, checkpoint |
| `bob-analyser/db/schema.sql` | SQLite schema for `analysis_jobs` + `checkpoint` tables |
| `web/index.html` | Single-page Morandi dashboard (history, trigger, download) |
| `docs/adr/0006-elasticsearch-alert-trigger.md` | ADR for Kibana Alert trigger design |

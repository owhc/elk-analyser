# AGENTS.md
<!-- Updated: 2026-09-15 22:45:24 +0800 -->

## Session Memory
@.bob/memory/session.md

## Stack & Architecture
- **Core**: Python 3 (FastAPI + Click CLI + SQLite); proxy `web:3001` -> `elk-analyser:8080`
- **Banking Demo**: WAS Liberty -> Artemis(`bankingQueue`) -> MDB -> PostgreSQL 16 (JDBC via `jdbcExecutor.submit()`) -> Logstash -> ES
- **Pipeline**: [`extractor`](bob-analyser/analyser/extractor.py) -> [`preprocessor`](bob-analyser/analyser/preprocessor.py) -> [`bob_bridge`](bob-analyser/analyser/bob_bridge.py) -> [`report_builder`](bob-analyser/analyser/report_builder.py) orchestrated by [`run_analysis()`](bob-analyser/analysis_pipeline.py)
- **Instana**: Trigger (`*_instana` / `--with-instana`) -> [`instana_collector`](bob-analyser/instana_collector.py) -> injects `instana_context` into `event_sequence` & PPTX Slide 3.5; pure ELK fallback on failure
- **Window Priority**: (1) `from_time` -> (2) `lookback_minutes` -> (3) Checkpoint (`id=1`, updated on `completed` only) -> (4) `now - default_lookback_minutes`
- **Bob Bridge**: `bob run --accept-license --mode log-analyst --format json --max-turns 5 @/workspace/logs/job-{id}.json` (strip fences, parse `last_message`)
- **Paths**: Config `/app/config/config.yaml` (`ELK_CONFIG` override) · DB `/db/history.db` · Reports `/reports/` · Workspace `/workspace/logs/`

## Hard Rules
- **No Exceptions**: `analyse()` & `build_report()` must return `{"error": "..."}` dict, never raise
- **Timezone**: All `datetime` must be timezone-aware (`replace(tzinfo=timezone.utc)`)
- **Config Seam**: Load solely via `AppConfig.load(config_path)` (explicit > `ELK_CONFIG` > `/app/config/config.yaml`)
- **Logging & CJK**: Use `logging.getLogger(__name__)` (no `print()`); all CJK JSON requires `json.dumps(..., ensure_ascii=False)`
- **DB Mutations**: `JobRepository.complete_job` / `fail_job` use `WHERE id=?`

## Quick Reference
```bash
# Start Stack / pg_monitor
bash scripts/setup-instana.sh && podman-compose -f podman-compose.yml -f podman-compose.override.yml up -d
podman exec -it banking-db psql -U db2inst1 -d bankdb -c "GRANT pg_monitor TO db2inst1;"
# Trigger Analysis (CLI / Webhook / Demo Inject)
podman exec elk-analyser python cli.py run --from "2026-01-15 08:00" --to "2026-01-15 12:00" --with-instana
curl -sX POST http://localhost:8080/analyse -H 'Content-Type: application/json' -d '{"from_time":"2026-01-15 08:00","to_time":"2026-01-15 12:00","trigger":"webui_instana"}'
curl -sX POST http://localhost:8080/demo/inject -H 'Content-Type: application/json' -d '{"from_time":"2026-01-15 08:00","to_time":"2026-01-15 09:00","trigger_analyse":true}'
# Verify & E2E Incident Test
curl http://localhost:8080/jobs && curl -O http://localhost:8080/jobs/1/report && bash scripts/generate-observability-incident.sh 1
```
- **Endpoints**: `POST /analyse` (409 busy, 208 deduplicated 5m), `POST /analyse/async`, `GET|DELETE /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/status`, `GET /jobs/{id}/report`, `POST /demo/inject`
- **Network**: `elk-analyser-web` (:3001), `elk-analyser` (10.89.1.10 & 10.89.2.50 :8080), `instana-agent` (10.89.2.5 :42699), `banking-app` (10.89.2.20 :9080/5672/8161), `banking-db` (10.89.2.10 :5432), `elasticsearch` (10.89.2.30 :9200), `logstash` (:31), `kibana` (:5601)
- **Key Modules**: [`analysis_pipeline.py`](bob-analyser/analysis_pipeline.py), [`api.py`](bob-analyser/api.py), [`cli.py`](bob-analyser/cli.py), [`config_loader.py`](bob-analyser/config_loader.py), [`instana_collector.py`](bob-analyser/instana_collector.py), [`job_repository.py`](bob-analyser/db/job_repository.py), [`report_builder.py`](bob-analyser/analyser/report_builder.py), [`config.yaml`](bob-analyser/config/config.yaml), [`custom_modes.yaml`](bob-analyser/bob-custom-modes/custom_modes.yaml)

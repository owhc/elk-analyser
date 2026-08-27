# Project Coding Rules
<!-- Updated: 2026-08-26 17:46:35 +0800 -->

- **Never raise exceptions** from `analyse()` or `build_report()` — always return `{"error": "..."}` dict
- **Always propagate `config_path`** through the full pipeline; each module has a `_load_config(config_path)` helper — don't open config.yaml directly
- **All datetime objects must be timezone-aware** (`replace(tzinfo=timezone.utc)`) before passing to `extract()` or writing to DB
- **Bob CLI command** is `bob run --accept-license --mode log-analyst --format json --max-turns 5 @<file>` — exact flags matter（bob 2.x 已移除 `--auth-method` 和 `-y`）
- **Bob API key env var** is `BOB_API_KEY`（bob 2.x）；`BOBSHELL_API_KEY` 保留向後相容，兩者皆傳入 subprocess `env=`
- **`_update_job()`** targets `WHERE rowid = (SELECT rowid ... ORDER BY rowid DESC LIMIT 1)`, not by `job_id` UUID
- **`json.dumps(..., ensure_ascii=False)`** required for all JSON containing CJK text
- **Event sequence JSON** must be written to `/workspace/logs/job-{id}.json` before calling `bob run`; temp file is deleted after successful parse
- **Checkpoint is only written on `completed`** — a failed run must NOT update the checkpoint
- **Bob output parsing**: strip markdown fences (```` ```json ... ``` ````) before `json.loads(last_message)`
- Container paths (`/app/config/`, `/db/`, `/reports/`, `/workspace/`) are **hardcoded** — they map to named Podman volumes, do not make them configurable without updating compose

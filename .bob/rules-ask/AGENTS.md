# Project Documentation Context
<!-- Updated: 2026-08-26 17:29:12 +0800 -->

- **`bob-analyser/` is the Docker build context** — repo root is just the Podman Compose wrapper; Python source lives under `bob-analyser/`
- **Bob CLI runs inside `elk-analyser` container** — no SSH, no separate bob-sandbox. Earlier docs/ADRs mentioning SSH are superseded by current Dockerfile
- **`config/crontab`** controls actual schedule; `config.yaml` `cron_expr` field is documentation only
- **`bob-custom-modes/custom_modes.yaml`** (not `log-analyst.yaml`) is what gets deployed — it contains both `log-analyst` and `report-builder` modes
- **Domain vocabulary** is in `CONTEXT.md` — use those exact terms: 分析任務, 查詢視窗, Checkpoint, 事件序列, Bob Bridge, 診斷報告
- **`web/` is a static Nginx frontend** — it only proxies to the FastAPI backend; all logic is in `bob-analyser/`
- **ADR 0004** (shared volume) describes the old SSH approach; current implementation is direct `subprocess.run` per ADR pattern in `bob_bridge.py`

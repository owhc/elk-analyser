# Session Memory — elk-analyser
<!-- Updated: 2026-09-15 22:30:10 +0800 -->

## Project
ELK Analyser：從 Elasticsearch 日誌提取 → Bob log-analyst 根因分析 → PPTX 報告，加掛 Instana APM 整合。

## Session goal
實作 Instana APM 整合（docs/instana-integration-plan.md，全部 5 個 sub-tasks）。

## Key decisions
- `instana_collector.py` 為純 function 模組，不依賴 AppConfig（改用 `_load_config()` 直讀 yaml），與既有模式一致
- `_resolve_instana_context()` 在 `api.py` 中以閉包呼叫 `instana_collector.collect()`，直接使用 ELK_CONFIG 或預設路徑
- `instana_context` 透過 `preprocessor → event_sequence → bob_bridge → analysis` 透傳至 `report_builder`
- `bob_bridge.analyse()` 在 `_parse_bob_output` 後補回 `instana_context`（AI 不輸出它）
- Slide 3.5 僅 `available=true` 時插入，`available=false` 報告結構不變（降級守則）
- CLI `--with-instana` flag 使用 `trigger=cli_instana`

## Files changed this session
| File | Change |
|------|--------|
| `bob-analyser/instana_collector.py` | **新建** — Instana REST API 封裝 |
| `bob-analyser/config/config.yaml` | 新增 `instana:` 設定區塊 |
| `bob-analyser/config_loader.py` | 新增 `instana_config` / `instana_enabled` 屬性 |
| `bob-analyser/api.py` | `AnalyseRequest` + `_resolve_instana_context()` + trigger 整合 |
| `bob-analyser/analysis_pipeline.py` | `run_analysis()` 新增 `instana_context` 參數 |
| `bob-analyser/analyser/preprocessor.py` | `_build_instana_context()` + `preprocess()` 注入 |
| `bob-analyser/analyser/bob_bridge.py` | `_ANALYSIS_PROMPT` 更新 + `instana_context` 透傳 |
| `bob-analyser/bob-custom-modes/custom_modes.yaml` | `log-analyst` customInstructions 補充 instana_context 說明 |
| `bob-analyser/analyser/report_builder.py` | `_add_instana_slide()` + Slide 3.5 插入 |
| `bob-analyser/cli.py` | `--with-instana` flag |
| `bob-analyser/.env.example` | 新增 `INSTANA_API_TOKEN` |
| `AGENTS.md` | Instana 整合路徑說明 + 新指令 + Key Files |

## Rules & constraints discovered
- `bob_bridge` 的 AI 輸出不含 `instana_context`，需在 `analyse()` 末段手動透傳
- `_add_instana_slide` 的 local helper fn 需在函式內定義（不能跨 `_build_report` 共用）
- `instana_collector.collect()` 任一子項目失敗只 warning，仍回傳部分資料（available=true）

## Completed since last session
- `config.yaml instana.enabled` 已改為 `true`（正式啟用）
- Web UI「ELK + Instana 合併分析」按鈕已實作（`web/index.html` `triggerAnalyseWithInstana()`）
- API 新增端點：`DELETE /jobs/{id}`、`DELETE /jobs`、`GET /jobs/{id}/status`、`POST /demo/inject`
- `AppConfig` 取代所有模組 `_load_config()` helper；`JobRepository` 集中所有 SQLite 操作
- `scripts/generate-observability-incident.sh` 端對端驗證腳本已建立

## Open threads / next steps
- Sub-task 5 端對端驗證需容器啟動後手動執行（`bash scripts/generate-observability-incident.sh`）
- AGENTS.md 已同步更新反映目前實際狀態

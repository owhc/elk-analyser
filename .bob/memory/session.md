# Session Memory — elk-analyser
<!-- Updated: 2026-08-27 16:21:19 +0800 -->

## Project
自動化日誌分析與報告生成系統：Elasticsearch 提取 → Bob CLI 根因分析 → PPTX 診斷報告。

## Session goal
執行架構審查（improve-codebase-architecture），識別並修正架構摩擦點。

## Key decisions
- 建立 `JobRepository`（深模組）集中所有 SQLite 操作，取代 20+ 處散落的 sqlite3 呼叫
- 建立 `AppConfig`（深模組）取代 7 個模組各自的 `_load_config()` 與 config_path 參數傳遞鏈
- `run_analysis()` 改用 `repo.create_job()` 的 `lastrowid` 而非 `SELECT rowid DESC LIMIT 1`，修復競態條件
- `bob_bridge.py` subprocess env 改為白名單（HOME/PATH/USER/TMPDIR/LANG/TERM）
- `preprocessor.py` 以 enumerate index 取代 `id()` 記憶體位址追蹤，修復 CPython 可移植性問題
- `Dockerfile` 新增 `config_loader.py` 到 COPY 指令

## Files changed this session
| File | Change |
|------|--------|
| `bob-analyser/db/job_repository.py` | 新建：JobRepository 深模組 |
| `bob-analyser/config_loader.py` | 新建：AppConfig 深模組 |
| `bob-analyser/analyser/preprocessor.py` | 移除 _load_config、修復 id() bug、接受 AppConfig |
| `bob-analyser/analyser/bob_bridge.py` | 移除 _load_config、env 白名單、BOB_API_KEY 優先 |
| `bob-analyser/analyser/extractor.py` | Checkpoint 委派 JobRepository、接受 AppConfig |
| `bob-analyser/analyser/report_builder.py` | 移除 _load_config、接受 AppConfig |
| `bob-analyser/analysis_pipeline.py` | 改用 JobRepository+AppConfig、移除散落 SQL、修復競態 |
| `bob-analyser/api.py` | 改用 JobRepository+AppConfig、移除 20+ 散落 SQL |
| `bob-analyser/cli.py` | 改用 JobRepository+AppConfig、移除散落 SQL |
| `bob-analyser/Dockerfile` | 新增 config_loader.py 到 COPY |

## Rules & constraints discovered
- `PYTHONPATH=/app` 在容器內，所有模組以 `/app` 為根（`from db.job_repository import ...`）
- `analysis_jobs.id` 是 INTEGER PRIMARY KEY（即 rowid），直接用 `lastrowid` 即可
- 向後相容：extractor.py 的 `read_checkpoint/write_checkpoint` 保留為薄包裝，委派給 JobRepository

## Open threads / next steps
- `get_job` 回傳的欄位少了 `error`（改名為 `error_message`），前端 web/index.html 可能需確認
- 未加 DB 索引（analysis_jobs.status/started_at），流量大時可補
- 未寫單元測試（無測試框架），可考慮用 InMemoryJobRepository 補測

<!-- Updated: 2026-08-26 23:48:51 +0800 -->
# 使用 SQLite 儲存分析歷史與 Checkpoint

選擇 SQLite 作為分析歷史的持久化儲存，而非獨立資料庫 container 或純 JSON 檔案。SQLite 無需額外服務、Python 原生支援、單一檔案便於備份，足以應付分析歷史的查詢需求。若未來需要多實例並發或複雜查詢，可遷移至 PostgreSQL（schema 相容）。

資料庫檔案路徑固定為 `/db/history.db`（容器內），掛載至命名 volume `elk_db`，確保 container 重建後資料不遺失。

## Schema

```sql
-- analysis_jobs：每次分析任務的完整紀錄
-- checkpoint：單行（id=1），記錄排程模式的最後分析結束時間
```

## Consequences

- Checkpoint 存入同一個 SQLite，確保下次觸發時可從正確時間點接續，不會重複分析或產生空隙
- `_update_job()` 以 `WHERE rowid = (SELECT rowid ... ORDER BY rowid DESC LIMIT 1)` 更新最新任務，非依 UUID
- 系統為無排程架構（by ADR-0005），Checkpoint 由 `analysis_pipeline.run_analysis()` 在 `completed` 後呼叫 `write_checkpoint()` 寫入；`run_scheduled()` 已移除
- `POST /analyse` 的查詢視窗解析優先序：`from_time` → `lookback_minutes` → Checkpoint → fallback（詳見 ADR-0003）

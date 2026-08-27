-- Created: 2026-08-26 12:34:17 +0800
-- ELK Analyser SQLite Schema
-- 包含分析任務歷史紀錄與 Checkpoint 兩張資料表

-- ── 分析任務歷史 ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS analysis_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    -- 觸發模式：scheduled | on_demand
    trigger_mode    TEXT    NOT NULL,
    -- 查詢視窗
    query_from      TEXT    NOT NULL,   -- ISO8601 格式
    query_to        TEXT    NOT NULL,   -- ISO8601 格式
    -- 狀態：running | completed | failed
    status          TEXT    NOT NULL DEFAULT 'running',
    -- 執行時間
    started_at      TEXT    NOT NULL,
    completed_at    TEXT,
    -- 分析摘要（JSON 字串）
    summary_json    TEXT,
    -- 產出報告路徑
    report_path     TEXT,
    -- 錯誤訊息（失敗時記錄）
    error_message   TEXT,
    -- Bob Shell 原始 JSON 回應
    bob_raw_json    TEXT
);

-- ── Checkpoint（排程接續點）─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS checkpoint (
    id              INTEGER PRIMARY KEY CHECK (id = 1),  -- 只有一筆
    -- 最後成功完成的分析任務結束時間
    last_analysed_to TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- 初始化 Checkpoint（若不存在）
-- 實際值由第一次成功的分析任務寫入（JobRepository.set_checkpoint）

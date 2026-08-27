<!-- Created: 2025-01-15 00:00:00 +0800 -->
# ELK Analyser

自動化日誌分析與報告生成系統。從 Elasticsearch 提取日誌，透過 Bob Shell AI 進行根因分析，產出 PPTX 簡報報告。支援排程式被動分析與使用者觸發的主動查詢兩種模式。

## Language

### 核心流程概念

**分析任務 (Analysis Job)**：
一次完整的分析執行單元，包含查詢參數、執行狀態、分析結果與產出報告的完整紀錄。
_Avoid_：任務、工作、run、execution

**查詢視窗 (Query Window)**：
一次分析任務所覆蓋的時間範圍，由開始時間與結束時間構成。
_Avoid_：時間範圍、time range、interval

**Checkpoint**：
系統成功完成一次排程分析後，所記錄的最後分析結束時間戳記。下次排程執行時從此時間點接續，確保日誌覆蓋無空隙。
_Avoid_：上次執行時間、last run

**事件序列 (Event Sequence)**：
預處理器將原始日誌行依時序整理後，組合成的有序事件清單。這是傳遞給 Bob Shell 進行因果鏈推理的基本單位。
_Avoid_：日誌清單、log list、原始日誌

### 觸發模式

**排程模式 (Scheduled Mode)**：
系統由內部排程器自動定期觸發的分析模式，依 Checkpoint 接續查詢視窗。
_Avoid_：自動模式、cron mode、passive mode

**主動查詢模式 (On-Demand Mode)**：
使用者透過 CLI 或 REST API 指定確切查詢視窗，立即觸發的分析模式。
_Avoid_：手動模式、interactive mode、manual mode

### 整合邊界

**Bob Bridge**：
負責與 bob-sandbox container 通訊的橋接模組。透過 SSH 將事件序列以共享 Volume 檔案加 `@filename` 引用方式傳入，調用 `log-analyst` custom mode，並解析 JSON 回應。
_Avoid_：bob client、AI 模組、shell caller

**Log Analyst Mode**：
部署在 bob-sandbox 中的 Bob Shell custom mode，專責接收事件序列並輸出結構化的根因分析 JSON。
_Avoid_：AI 分析器、bob agent、分析 persona

**分析歷史 (Analysis History)**：
SQLite 資料庫中所有分析任務的完整紀錄，包含查詢參數、執行時間、摘要結果與報告檔案路徑。
_Avoid_：查詢紀錄、log、history table

### 產出物

**診斷報告 (Diagnostic Report)**：
一次分析任務的最終產出，為 PPTX 格式的五頁簡報，涵蓋封面、執行摘要、錯誤分析、根因分析與改善建議。
_Avoid_：報告、report、PPTX 檔案

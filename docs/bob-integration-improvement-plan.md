<!-- Created: 2026-08-27 -->
<!-- status: IMPLEMENTED — AppConfig 深模組化、JobRepository 集中化、API 端點擴充均已完成 -->
# ELK Analyser × Bob 深度整合改善計畫

## 背景與問題陳述

目前 ELK Analyser 使用 Bob CLI 的方式等同於「帶 system prompt 的 LLM 問答」，任何
提供 headless CLI 的競品（Claude CLI、OpenAI Codex CLI）都可以直接替換。客戶沒有
非選 Bob 不可的理由。

本計畫透過三條路線讓 Bob 成為不可替換的核心，並建立以下護城河：

1. **工具使用層**：讓 Bob 直接呼叫 `office_edit` 產出 PPTX（競品無此工具）
2. **資料整合層**：透過 MCP 讓 Bob 跨 ELK + Instana 雙資料源分析
3. **觸發層**：Instana Smart Alert 替換 Kibana，進入 IBM 觀測性生態

---

## 整體架構目標

```
現況（可被替換）
  Kibana Alert → FastAPI → bob run log-analyst → python-pptx → PPTX

目標（不可替換）
  Instana Alert → FastAPI → bob run log-analyst
                               ├── MCP: elk-server（ES logs）
                               └── MCP: instana-server（traces + events）
                            ↓
                         bob run report-builder
                               └── office_edit → PPTX（Bob 獨有工具）
```

---

## 路線一：接通 report-builder mode（office_edit 生成 PPTX）

### 背景

- `report-builder` mode 已定義於 `custom_modes.yaml`（groups: edit，model: claude-sonnet-4-5）
- 目前 PPTX 由 `report_builder.py`（python-pptx）生成，Bob 完全沒有參與報告產出
- `office_edit` 是 Bob 獨有工具，Claude CLI / Codex CLI 均無此能力

### 目標

將 `report_builder.py` 的 PPTX 生成工作移交給 `bob run --mode report-builder`，
讓 Bob 透過 `office_edit` 直接建立報告。python-pptx 作為備援保留。

### Sub-Task 1.1：設計 report-builder prompt 格式

**Intent**：定義 `bob run --mode report-builder` 的輸入 prompt 結構，讓 Bob 知道
要建立什麼內容、存到哪裡。

**Expected Outcomes**：
- 有一個 JSON schema 定義 report-builder 的 prompt payload
- schema 包含：output_path、query_window、summary、event_chains（沿用 log-analyst 輸出）
- `custom_modes.yaml` 的 `report-builder` customInstructions 更新為對應此 schema

**Todo List**：
1. 確認 `office_edit` 工具在 `groups: [edit]` 下是否可用（查閱 Bob 文件）
2. 設計 prompt JSON schema（output_path + analysis 結果直接傳入）
3. 更新 `bob-custom-modes/custom_modes.yaml` report-builder 的 customInstructions，
   明確說明 5 頁結構、色彩規範、各頁從 prompt JSON 哪個欄位取值
4. 重新 build container 驗證 mode 載入正確

**Relevant Context**：
- `bob-analyser/bob-custom-modes/custom_modes.yaml` 行 75-108（report-builder 定義）
- `bob-analyser/analyser/report_builder.py`（現有色彩、投影片結構，作為設計參考）
- Bob 文件：groups: edit 允許 write_file、apply_diff、office_edit

**Status**：[ ] pending

---

### Sub-Task 1.2：新增 bob_bridge.build_report_with_bob()

**Intent**：在 `bob_bridge.py` 中新增一個函式，負責呼叫 `bob run --mode report-builder`，
將 analysis 結果以 JSON 寫入暫存檔，傳給 Bob 產出 PPTX。

**Expected Outcomes**：
- 新函式 `build_report_with_bob(analysis, job_id, query_from, query_to, config) -> str`
- 將 report prompt JSON 寫入 `/workspace/logs/report-job-{id}.json`
- 呼叫 `bob run --mode report-builder --yolo @/workspace/logs/report-job-{id}.json`
- 解析 Bob 回應，確認 output_path 存在，回傳路徑字串
- 失敗時回傳 `{"error": "..."}` dict，不拋出例外

**Todo List**：
1. 在 `bob_bridge.py` 新增 `_build_report_prompt(analysis, job_id, query_from, query_to, output_path) -> dict`，產生 report-builder 的 prompt JSON
2. 新增 `build_report_with_bob()` 主函式：寫暫存 JSON → `_run_bob(mode="report-builder")` → 解析回傳路徑
3. `_run_bob()` 確認 `--yolo` flag 需要加入（report-builder 需要 write 權限）
4. 單元測試：mock `subprocess.run` 驗證 cmd 參數正確

**Relevant Context**：
- `bob-analyser/analyser/bob_bridge.py` 行 65-108（`_run_bob()` 現有實作）
- `bob-analyser/analyser/bob_bridge.py` 行 151-216（`analyse()` 作為結構參考）
- Bob Shell 文件：`--yolo` flag 允許 non-interactive 模式下 write 檔案
- `config.yaml` `bob_bridge.timeout: 120`（report-builder 可能需要更長，考慮 180s）

**Status**：[ ] pending

---

### Sub-Task 1.3：修改 analysis_pipeline.py 串接 report-builder

**Intent**：在 pipeline 第四階段，優先呼叫 `build_report_with_bob()`；若失敗則
fallback 到現有的 python-pptx `build_report()`，確保向後相容。

**Expected Outcomes**：
- `run_analysis()` 中 report 生成有兩條路徑：Bob 優先、python-pptx fallback
- 兩條路徑都回傳相同的 `report_path` 字串給後續 DB 儲存
- 使用哪條路徑記錄在 job 的 `bob_raw_json` 或 log 中

**Todo List**：
1. 在 `analysis_pipeline.py` 匯入 `bob_bridge.build_report_with_bob`
2. 修改第四階段邏輯：先呼叫 `build_report_with_bob()`；若回傳 `{"error": ...}` 則 fallback
3. 在 fallback 路徑加 `logger.warning("report-builder 失敗，fallback 至 python-pptx")`
4. 確認 `complete_job()` 呼叫不需要修改（report_path 介面不變）

**Relevant Context**：
- `bob-analyser/analysis_pipeline.py` 行 67（`build_report()` 呼叫點）
- `bob-analyser/analyser/report_builder.py`（fallback 保留不動）

**Status**：[ ] pending

---

### Sub-Task 1.4：config.yaml 新增 report-builder 設定

**Intent**：讓 report-builder 的 Bob mode slug、timeout 可配置，不 hardcode。

**Expected Outcomes**：
- `config.yaml` 新增 `bob_bridge.report_builder_mode` 和 `bob_bridge.report_builder_timeout`
- `config_loader.py` 對應新增 AppConfig 屬性
- `bob_bridge.build_report_with_bob()` 從 config 讀取，不 hardcode

**Todo List**：
1. 在 `config.yaml` `bob_bridge` 區塊新增：
   - `report_builder_mode: "report-builder"`
   - `report_builder_timeout: 180`
2. 在 `config_loader.py` 新增對應屬性
3. 在 `bob_bridge.build_report_with_bob()` 使用 config 值

**Relevant Context**：
- `bob-analyser/config/config.yaml` 行 43-48（bob_bridge 現有設定）
- `bob-analyser/config_loader.py`（AppConfig 類別）

**Status**：[ ] pending

---

## 路線二：MCP 雙資料源整合（Instana + ELK）

### 背景與資料架構釐清

客戶同時使用 ELK 收 Application Log 和 Instana 做 APM 監控。
**重要：Instana ↔ ELK 的整合是 UI 連結，不是資料轉送。**

Instana 的「ELK 整合」（Settings → Integrations → Logging → ELK）只做一件事：
在 Instana 的 Host/Pod 頁面上顯示一個「ELK」按鈕，點擊後跳轉到 Kibana Dashboard。
**沒有任何資料從 Instana 推送到 Elasticsearch。**

因此兩個資料源的資料是嚴格分離的：

| 資料類型 | 存放位置 | 現在是否已用 |
|---------|---------|-----------|
| Application Logs（錯誤訊息、堆疊追蹤） | ELK Elasticsearch | ✅ 已用（extractor.py） |
| Distributed Traces（呼叫鏈、span 耗時） | Instana Backend | ❌ 未用 |
| Service Dependency Map（服務拓撲） | Instana Backend | ❌ 未用 |
| Infrastructure Events / Change Events | Instana Backend | ❌ 未用 |
| 1-second Metrics（CPU、GC、memory） | Instana Backend | ❌ 未用 |

### 兩資料源的天然橋樑：trace_id

ELK 的 application log 裡會帶有 `trace.id` 欄位（對應 Instana 的 `X-INSTANA-T`
或 W3C 的 `traceparent`），這是連結兩個資料源的關鍵：

```
ELK log 中的一筆記錄
{
  "@timestamp":   "2025-01-15T08:05:00Z",
  "log.level":    "ERROR",
  "message":      "DB connection timeout after 30s",
  "service.name": "payment-service",
  "trace.id":     "7fa8b643c98711ef"   ← 同時存在 Instana trace 裡
}
         ↓ 用這個 trace_id
Instana API: GET /api/application-monitoring/traces/7fa8b643c98711ef
         ↓ 得到
{
  "spans": [
    { "service": "api-gateway",     "duration_ms": 5 },
    { "service": "payment-service", "duration_ms": 30012 },  ← 在這裡超時
    { "service": "db2-pool",        "duration_ms": 30000 }   ← 根因在這
  ]
}
```

**這表示 Bob 可以從 ELK log 取得 trace_id，再主動 query Instana 補充完整呼叫鏈，
做出純 log 分析無法達到的精確根因定位。** 這個跨資料源分析流程只有透過 MCP
整合才能讓 Bob 自主執行，是真正的技術差異化。

### ⚡ 重要發現：官方 MCP Server 已存在，無需自建

| MCP Server | 官方 Repository | 維護方 |
|-----------|----------------|--------|
| **Elasticsearch MCP** | https://github.com/elastic/mcp-server-elasticsearch | Elastic 官方 |
| **Instana MCP** | https://github.com/instana/mcp-instana | IBM Instana 官方 |

**這意味著 Sub-Task 2.1 和 2.2 的工程量大幅降低**：
不需要自己包裝 API，只需要在 container 中安裝並設定這兩個官方 MCP server。
自建 `mcp-servers/` 目錄的方案廢棄，改為直接整合官方套件。

---

### Sub-Task 2.1：整合 elastic/mcp-server-elasticsearch

**Intent**：使用 Elastic 官方 MCP server 取代自建 elk-server，讓 Bob 可以
主動 query Elasticsearch，並取得 log 中的 `trace.id` 供後續 Instana 查詢使用。

**Expected Outcomes**：
- Dockerfile 新增安裝 `mcp-server-elasticsearch`（npm 套件）
- Bob MCP 設定（`/root/.bob/settings/mcp.json`）註冊 elasticsearch server
- 設定連線至 `config.yaml` 的 ES host/port/credentials
- Bob 能透過 MCP 呼叫 ES query tools，回傳結果保留 `trace.id` 欄位

**Todo List**：
1. 確認 `mcp-server-elasticsearch` 的安裝方式與支援的 tools（查閱官方 README）
2. 在 `Dockerfile` 新增：`RUN npm install -g @elastic/mcp-server-elasticsearch`
3. 在 `bob-custom-modes/` 新增 `mcp.json`，於 image build 時複製到
   `/root/.bob/settings/mcp.json`，設定 ES 連線參數（從環境變數注入）
4. 確認官方 tools 是否涵蓋 `query_logs by trace_id` 的使用情境；
   若不足，考慮在 `customInstructions` 補充查詢策略

**Relevant Context**：
- https://github.com/elastic/mcp-server-elasticsearch（官方文件與 tools 清單）
- `bob-analyser/Dockerfile`（已基於 node:22-slim，npm 已可用）
- `bob-analyser/config/config.yaml`（ES host/port/credentials）
- Bob 文件：MCP server 設定於 `~/.bob/settings/mcp.json`

**Status**：[ ] pending

---

### Sub-Task 2.2：整合 instana/mcp-instana

**Intent**：使用 IBM Instana 官方 MCP server，讓 Bob 能用 ELK log 中取得的
`trace_id` 反查 Instana，取得完整分散式呼叫鏈與服務拓撲，補充純 log 分析的盲點。

**整合依據**：
- Instana ↔ ELK 無資料轉送，Instana trace 資料只存在 Instana Backend
- ELK log 的 `trace.id` 對應 Instana 的 `X-INSTANA-T`（16 字元 hex）或
  W3C `traceparent` header 中的 trace-id 段
- 兩者透過 `trace_id` 做 cross-reference，不需要 Instana 推送任何資料到 ELK
- 官方 MCP server 直接封裝 Instana REST API，無需手動實作

**Expected Outcomes**：
- Dockerfile 新增安裝 `mcp-instana`（官方套件）
- `/root/.bob/settings/mcp.json` 新增 instana server 設定
- 設定 `INSTANA_BASE_URL` / `INSTANA_API_TOKEN` 環境變數（`.env` 注入）
- Bob 能呼叫 MCP tools 取得：trace 呼叫鏈、events、service map、metrics

**Cross-reference 流程**（Bob 的分析策略）：
```
Step 1: 呼叫 elasticsearch MCP → query ERROR logs（含 trace.id）
Step 2: 對每個 trace.id 呼叫 instana MCP → get_trace(trace_id) → 完整呼叫鏈
Step 3: 呼叫 instana MCP → get_events(time_range) → Instana 偵測到的 issues
Step 4: 合併 log + trace span + Instana event → Bob 輸出根因分析 JSON
```

**Todo List**：
1. 確認 `mcp-instana` 的安裝方式與支援的 tools（查閱官方 README）
2. 在 `Dockerfile` 新增安裝指令
3. 在 `mcp.json` 新增 instana server 設定，連線參數從環境變數讀取
4. 在 `.env.example` 新增 `INSTANA_BASE_URL` / `INSTANA_API_TOKEN` 欄位
5. 確認官方 MCP tools 是否支援 `get_trace(traceId)` cross-reference 使用情境

**Relevant Context**：
- https://github.com/instana/mcp-instana（官方文件與 tools 清單）
- `bob-analyser/Dockerfile`（安裝位置）
- `bob-analyser/.env.example`（新增環境變數範本）
- `bob-analyser/config/config.yaml`（新增 `instana` 設定區塊）
- Instana trace header：`X-INSTANA-T`（16 char hex）= ELK log 的 `trace.id`

**Status**：[ ] pending

---

### Sub-Task 2.3：更新 log-analyst mode 使用 MCP 工具

**Intent**：升級 `log-analyst` mode，讓 Bob 在分析時主動呼叫 MCP tools 補充上下文，
而非只分析 Python 預處理好的靜態 JSON。

**Expected Outcomes**：
- `custom_modes.yaml` log-analyst `groups` 新增 MCP tools 權限
- `customInstructions` 新增：「優先呼叫 elk-server 和 instana-server 補充資料」
- `bob_bridge.analyse()` 的 `@filename` 改為輕量觸發 prompt，讓 Bob 自行決定查詢策略

**Status**：[ ] pending

---

## 路線三：Instana Smart Alert 觸發層整合

### Sub-Task 3.1：新增 Instana Alert Webhook 支援

**Intent**：Instana Smart Alert 的 webhook payload 格式與 Kibana Alert 不同，
需要在 `api.py` 新增解析邏輯。

**Expected Outcomes**：
- `POST /analyse` 支援 `trigger=instana_alert` 參數
- 解析 Instana webhook payload（含 `issue.start`、`issue.end`、`issue.severity`）
- 自動從 Instana event 取出 `time_range` 作為查詢窗口

**Relevant Context**：
- `bob-analyser/api.py`（現有 Kibana Alert 解析邏輯）
- `docs/adr/0006-elasticsearch-alert-trigger.md`（Kibana Alert ADR，作為對照）
- Instana Webhook payload 結構（見 Instana 文件）

**Status**：[ ] pending

---

## 執行優先順序

| 優先 | 路線 | Sub-Task | 難度 | 護城河強度 |
|------|------|----------|------|-----------|
| 1 | 路線一 | 1.1 更新 report-builder customInstructions | 低 | ★★★ |
| 2 | 路線一 | 1.2 新增 build_report_with_bob() | 中 | ★★★ |
| 3 | 路線一 | 1.3 修改 pipeline fallback 串接 | 低 | ★★★ |
| 4 | 路線一 | 1.4 config 新增設定 | 低 | ★ |
| 5 | 路線三 | 3.1 Instana webhook 觸發 | 中 | ★★ |
| 6 | 路線二 | 2.1 elk-mcp-server | 高 | ★★★★ |
| 7 | 路線二 | 2.2 instana-mcp-server | 高 | ★★★★★ |
| 8 | 路線二 | 2.3 log-analyst 使用 MCP | 中 | ★★★★★ |

---

## 完成後的競爭壁壘

```
換掉 Bob 需要：
1. 找到支援 office_edit（PPTX 寫入）的替代工具         ← 無競品
2. 重新設計 MCP server（ELK + Instana 雙資料源）       ← 高工程成本
3. 重新建立 custom modes yaml + container 部署流程     ← 中等成本
4. 離開 IBM Enterprise IAM / Bobcoin 用量管控體系      ← 採購/合規阻力
```

---

## 路線四：AGENTS.md 客戶知識庫累積（越用越準飛輪）

### 背景與價值

每次分析完成後，Bob 手上有寶貴的環境知識：
- 哪些服務在什麼時間點容易出問題
- 哪些根因是這個客戶環境的已知慣性問題
- 哪些 CRITICAL 事件曾反覆發生、沿用相同的短期修復

目前這些知識只存在 SQLite 的 `bob_raw_json` 欄位，**下一次分析時 Bob 完全不知道上一次發生了什麼**。

將過往 CRITICAL/HIGH 根因摘要寫入 `AGENTS.md`，Bob 每次啟動時自動載入，
形成「越用越準」的飛輪——分析越多次，Bob 對這個環境的理解越深。

競品要達到相同效果，需要自行設計 RAG 或 prompt injection 機制。
Bob 的 `AGENTS.md` 原生支援是這個 feature 的基礎設施，無需額外開發。

### 設計規格

**觸發時機**：每次 `analysis_pipeline.run_analysis()` 完成（status=completed）後自動執行

**過濾規則**：
- 只保留 `severity = CRITICAL` 或 `HIGH` 的 event_chain
- 最多保留最近 50 筆符合條件的 event_chain（跨多次分析）
- 同一個 `chain_id` 若重複出現，累計發生次數

**AGENTS.md 知識庫區塊格式**（追加在現有 AGENTS.md 末尾，由程式管理）：

```markdown
## Environment Knowledge Base
<!-- AUTO-GENERATED: Do not edit manually. Updated by elk-analyser after each analysis. -->
<!-- Last updated: 2025-01-15T09:05:45+00:00 | Total patterns: 12 -->

### Recurring Error Patterns
這個客戶環境中 Bob AI 分析歸納的高頻根因模式，供下次分析參考。

| 發生時間 | 嚴重等級 | 受影響服務 | 根因摘要 | 短期處置 | 發生次數 |
|----------|---------|-----------|---------|---------|---------|
| 2025-01-15 09:00 | CRITICAL | WAS, DB2 | DB2 連線池耗盡，...| 重啟連線池 | 3 |
| 2025-01-14 14:30 | HIGH | MQ | MQ channel 積壓超過 5000 則... | 擴充 consumer | 1 |
```

---

### Sub-Task 4.1：實作 knowledge_updater.py

**Intent**：新增獨立模組，負責讀取最近 N 筆分析的 CRITICAL/HIGH event_chain，
將其整理成 markdown 表格，寫入 `AGENTS.md` 的指定區塊。

**Expected Outcomes**：
- 新增 `bob-analyser/analyser/knowledge_updater.py`
- 函式 `update_knowledge_base(config, repo) -> None`：
  1. 呼叫 `repo.get_recent_critical_chains(limit=50)` 取得歷史資料
  2. 整理去重（同 chain_id 合併計次）
  3. 讀取 `AGENTS.md`，找到 `## Environment Knowledge Base` 區塊
  4. 若存在則整塊替換；若不存在則 append
  5. 寫回 `AGENTS.md`

**Todo List**：
1. 在 `bob-analyser/db/job_repository.py` 新增 `get_recent_critical_chains(limit=50) -> list[dict]`：
   - 查詢最近 50 筆 completed job 的 `bob_raw_json`
   - 解析每筆的 `event_chains`，過濾 severity 為 CRITICAL 或 HIGH
   - 回傳 list，每筆含：`occurred_at`、`severity`、`affected_modules`、
     `title`、`root_cause`、`short_term_fix`、`chain_id`
2. 新增 `knowledge_updater.py`，實作 `update_knowledge_base()`：
   - 對相同 `chain_id` 做去重合併，計算 `occurrence_count`
   - 依 `occurred_at` 降冪排序，取前 50 筆
   - 產生 markdown 表格（含 `<!-- AUTO-GENERATED -->` 標記）
   - 讀取 AGENTS.md → 替換或 append 區塊 → 寫回
3. AGENTS.md 路徑從 config 讀取（`knowledge.agents_md_path`），預設為
   容器內 `/app/AGENTS.md`（build 時 COPY 進去）或 workspace 根目錄

**Relevant Context**：
- `bob-analyser/db/job_repository.py`（新增查詢方法）
- `bob-analyser/db/schema.sql`（`bob_raw_json` 欄位存完整 analysis dict）
- `AGENTS.md`（被更新的目標檔案）
- `bob_raw_json` 截斷上限 10000 字元（需注意超長內容可能被截斷）

**Status**：[ ] pending

---

### Sub-Task 4.2：在 analysis_pipeline 中串接 knowledge_updater

**Intent**：在 `run_analysis()` 成功完成後，自動呼叫 `update_knowledge_base()`，
讓每次分析都為 AGENTS.md 貢獻知識。

**Expected Outcomes**：
- `analysis_pipeline.run_analysis()` 在 `complete_job()` 之後呼叫 `update_knowledge_base()`
- 若 `update_knowledge_base()` 失敗，只記 warning log，不影響主流程回傳
- AGENTS.md 更新成功時 log 一筆 info

**Todo List**：
1. 在 `analysis_pipeline.py` import `knowledge_updater.update_knowledge_base`
2. 在 `complete_job()` 呼叫之後（pipeline 最後一步）加入：
   ```python
   try:
       update_knowledge_base(cfg, repo)
       logger.info("AGENTS.md 知識庫已更新")
   except Exception as e:
       logger.warning("AGENTS.md 更新失敗（不影響主流程）：%s", e)
   ```
3. 確認 AGENTS.md 在容器內的可寫路徑（Dockerfile COPY 路徑與 volume 設定）

**Relevant Context**：
- `bob-analyser/analysis_pipeline.py` 行 67-80（complete_job 與 set_checkpoint 呼叫點）
- `bob-analyser/Dockerfile`（AGENTS.md 是否被 COPY 進容器，或從 volume 掛載）

**Status**：[ ] pending

---

### Sub-Task 4.3：Dockerfile 確保 AGENTS.md 可讀寫

**Intent**：AGENTS.md 在容器內必須是可讀寫的，且路徑對 Bob CLI 和 Python 程式
都可見。確認掛載或 COPY 策略。

**Expected Outcomes**：
- AGENTS.md 在容器內路徑為 `/app/AGENTS.md`（或透過 volume 對應到 workspace）
- Bob CLI 執行時能讀到最新的 AGENTS.md（Bob 自動載入 workspace 根目錄的 AGENTS.md）
- Python 程式（knowledge_updater）能寫入同一個檔案

**Todo List**：
1. 確認 `bob run` 在容器內的 working directory（預設 `/app` 或 `/workspace`）
2. 若 AGENTS.md 在 `/app`，確認 Dockerfile `WORKDIR /app` 時 Bob 能自動載入
3. 考慮是否需要在 `podman-compose.yml` 把 AGENTS.md 對應到 host 以便持久化：
   - 若 AGENTS.md 只存在容器內，重新 build 後知識庫會消失
   - 建議透過 `elk_db` volume 或新增 `elk_knowledge` volume 持久化

**Relevant Context**：
- `bob-analyser/Dockerfile`（WORKDIR、COPY 指令）
- `podman-compose.yml`（volumes 設定）
- Bob 文件：Bob 自動載入 workspace 根目錄的 `AGENTS.md`

**Status**：[ ] pending

---

### Sub-Task 4.4：config.yaml 新增 knowledge 設定區塊

**Intent**：讓知識庫的行為可配置（路徑、保留筆數、過濾等級），不 hardcode。

**Expected Outcomes**：
- `config.yaml` 新增 `knowledge` 區塊
- `config_loader.py` 對應新增 AppConfig 屬性
- `knowledge_updater.py` 從 config 讀取所有參數

**Todo List**：
1. 在 `config.yaml` 新增：
   ```yaml
   knowledge:
     agents_md_path: "/app/AGENTS.md"
     max_patterns: 50
     min_severity: "HIGH"   # HIGH 或 CRITICAL 以上才納入
   ```
2. 在 `config_loader.py` 新增對應屬性
3. `knowledge_updater.py` 使用 config 值，不 hardcode

**Relevant Context**：
- `bob-analyser/config/config.yaml`
- `bob-analyser/config_loader.py`

**Status**：[ ] pending


---

## 附錄：watsonx.governance 整合設計（參考文件，不實作）

> **定位**：適用於金融、醫療等受監管行業的高階版本加購項目。
> 競品（Claude Code、Codex）無對應的企業級 AI 稽核工具，是真實差異化。
> 技術上可行，但需客戶另行採購 watsonx.governance 授權。

---

### 整合架構

watsonx.governance **不監控 Bob CLI 工具本身**，而是監控
Bob 背後呼叫的 LLM（`claude-sonnet-4-5`）的輸入/輸出行為。

```
elk-analyser
  └── bob_bridge.analyse()
        ├── 送出：事件序列 JSON（prompt）
        │         ↓
        │   claude-sonnet-4-5（Bob 內部路由）
        │         ↓
        └── 收到：根因分析 JSON（last_message）
                  ↓
        [新增] governance_logger.log_transaction()
                  ↓
        watsonx.governance Payload Logging API
```

---

### 整合步驟概述

#### Step 1：建立 Detached Prompt Template

在 watsonx.governance UI（或 API）中建立外部模型的 governance 資產：

| 欄位 | 值 |
|------|---|
| Name | `elk-analyser-log-analyst` |
| Foundation Model Name | `claude-sonnet-4-5` |
| Foundation Model ID | Bob 內部路由的模型識別碼 |
| Task type | `Content generation` |
| Prompt variables | `event_sequence`（輸入的事件序列） |

這步驟讓 governance 知道「有一個外部 AI 資產需要被治理」。

#### Step 2：設定 Payload Logging

在 watsonx.governance 啟用 payload logging，governance 會建立一個
**payload logging table**，每筆記錄包含：

| 欄位 | 內容 |
|------|------|
| `event_sequence`（prompt variable） | 送給 Bob 的事件序列 JSON |
| `generated_text` | Bob 回傳的根因分析 JSON（`last_message`） |
| `input_token_count` | 輸入 token 數（選填） |
| `generated_token_count` | 輸出 token 數（選填） |
| timestamp | 分析完成時間 |

#### Step 3：analysis_pipeline 新增推送步驟

在 `analysis_pipeline.run_analysis()` 的 `complete_job()` 之後，
新增約 50 行 Python 呼叫 watsonx.governance Python SDK：

```python
# 虛擬碼，僅示意
from ibm_aigov_facts_client import AIGovFactsClient

client = AIGovFactsClient(
    cloud_api_key=os.environ["WATSONX_GOV_API_KEY"],
    container_id=config.governance.deployment_id,
)
client.export_payload(
    payload=[{
        "event_sequence": json.dumps(event_sequence),
        "generated_text":  json.dumps(analysis),
    }]
)
```

#### Step 4：設定評估指標與告警門檻

在 watsonx.governance 設定自動評估項目：

| 評估維度 | 告警條件 | 對此 solution 的意義 |
|---------|---------|---------------------|
| **PII 偵測** | 輸入日誌含個人識別資料 | 客戶日誌不小心含員工帳號、IP 等敏感資料時自動 flag |
| **GenAI Quality** | 輸出品質分數低於門檻 | 追蹤每次 Bob 分析品質，偵測品質退化趨勢 |
| **Drift v2** | 輸出模式偏離基線 | Bob 若開始回傳不符格式的 JSON，自動告警 |
| **Guardrails** | 輸出含 Abuse / Profanity | 防止惡意構造的 log 內容操控 Bob 輸出 |
| **Factsheet** | 每次 AI 決策完整元數據 | 受監管行業的稽核要求（可匯出供外部稽核） |

---

### 限制說明

| 項目 | 說明 |
|------|------|
| **原生 agentic monitoring 不適用** | watsonx.governance 的 agentic dashboard 目前只支援 **watsonx Orchestrate** agents，Bob CLI（subprocess 呼叫）不在支援範圍 |
| **需額外採購** | watsonx.governance 是獨立授權產品，需客戶另行購買 |
| **Payload logging 為主動推送** | 非自動攔截，需在 pipeline 中手動加推送步驟 |
| **適用受眾** | 金融、醫療、政府等受監管行業；一般 IT 客戶需求較低 |

---

### 與競品的差異化論點

> **Claude Code 和 Codex 沒有對應的企業級 AI 決策稽核工具。**
> 客戶若在受監管行業，每次 AI 生成的根因分析必須可稽核、可追溯、
> 品質可量化——watsonx.governance 是目前市場上唯一能對
> 「第三方 LLM 呼叫」提供完整 Payload Logging + Drift + PII 偵測的
> 整合式治理平台，且與 IBM 產品組合同一個採購框架。


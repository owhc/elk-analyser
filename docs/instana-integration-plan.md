# Instana 整合分析計劃
<!-- Updated: 2026-09-15 19:55:23 +0800 -->
<!-- status: IMPLEMENTED — 2026-09-15 全部 5 個 sub-tasks 完成；instana_collector.py 已建立並整合至 pipeline -->

## 目標

將 Instana 的 APM 資料（Events、Endpoint Metrics、Trace Groups）與 Elasticsearch 日誌合併，
送入 Bob `log-analyst` mode 進行統一根因分析，輸出更完整的 PPTX 診斷報告。

## 範疇

- **Application**: `banking-app-liberty`（Instana Application ID: `XdhrXfC4QYqm2ixHSlY5dQ`）
- **Services**: `banking-app-liberty`（HTTP）、`bankdb`（PostgreSQL）
- **Endpoints**: `POST /api/login`、`GET /api/accounts/{accountId}`
- **所有正式觸發路徑均支援兩條分析路徑**：
  - **純 ELK**：只用 Elasticsearch 日誌分析（現有行為，不需 Instana）
  - **ELK + Instana**：合併 Instana APM 歷史資料做更完整根因分析

## 架構決策

### 正式觸發路徑（使用者操作）

使用者**不需要手動執行任何 Instana 收集步驟**。  
`trigger` 欄位決定是否由 `api.py` 內部自動向 Instana 查詢歷史資料。

#### 觸發路徑對照表

| 觸發來源 | 純 ELK | ELK + Instana |
|---|---|---|
| **Kibana Webhook 1** | `trigger: kibana_alert`（現有不變） | — |
| **Kibana Webhook 2** | — | `trigger: kibana_alert_instana`<br/>api.py 內部用相同時間窗口查 Instana 歷史 |
| **Web UI 立即分析** | 現有不變 | — |
| **Web UI 含 Instana** | — | `trigger: webui_instana`<br/>api.py 內部自動 collect |
| **CLI** | `--from ... --to ...`（現有不變） | `--from ... --to ... --with-instana` |

#### 資料流（ELK + Instana 路徑）

```
Kibana Webhook 2 / Web UI 含 Instana / CLI --with-instana
  │
  ▼
POST /analyse  { trigger: "kibana_alert_instana" | "webui_instana", ... }
  │
  ▼ api.py：用 query_from / query_to 向 Instana 查歷史 API
  ├─ instana_collector.collect_events()         → Issues/Incidents（同時間窗口）
  ├─ instana_collector.collect_endpoint_metrics() → calls / errors / latency P95
  └─ instana_collector.collect_trace_summary()  → error traces 彙總
  │
  ▼ instana_ctx dict（不落磁碟，直接傳入 pipeline）
  │
  ├─ extractor.py   → ES 日誌（banking-logs-*）
  │
  ▼
preprocessor.py
  ├─ 正規化日誌 → event_chains
  ├─ 注入 instana_context（available=true）
  └─ 輸出合併 event_sequence
  │
  ▼
bob_bridge.py → bob run --mode log-analyst
  │  （_ANALYSIS_PROMPT 含 Instana 交叉比對指示）
  ▼
report_builder.py → PPTX（含 Instana APM 概覽頁 Slide 3.5）
```

#### api.py instana_context 解析優先序

```python
# api.py 內部（偽碼）
if req.trigger in ("kibana_alert_instana", "webui_instana"):
    instana_ctx = instana_collector.collect(query_from, query_to)  # 正式路徑
elif req.instana_context:           # dev/test：inline dict
    instana_ctx = req.instana_context
elif req.instana_context_path:      # dev/test：磁碟檔案
    instana_ctx = load_json(req.instana_context_path)
else:
    instana_ctx = None              # 純 ELK 降級
```

#### 降級原則

`instana_ctx` 為 None 或 collect 失敗時：
- `instana_context.available = false`
- `event_sequence` 正常產生（純 ELK event_chains）
- `log-analyst` 僅用 ELK 日誌分析
- PPTX 不插入 APM 頁面，報告結構不變

---

### 開發 / 測試用隱藏欄位（不對外宣傳）

`AnalyseRequest` 保留以下兩個隱藏欄位，**僅供開發偵錯與整合測試使用**，
不在 Web UI 上出現，也不列入正式使用文件。

| 欄位 | 說明 | 典型用途 |
|---|---|---|
| `instana_context_path: Optional[str]` | 指定磁碟上已存在的 instana JSON 路徑 | 重播特定時間點的 Instana 資料做 debug |
| `instana_context: Optional[dict]` | 直接在 body 帶入 instana_context dict | 單元測試注入假資料、驗證 preprocessor 注入邏輯 |

---

## Instana 資料規格

### 必要資料（三類，強相關）

| # | 類別 | instana_collector 函式 | 注入欄位 |
|---|---|---|---|
| ① | Events / Issues（同時間窗口） | `collect_events()` | `instana_context.events` |
| ② | Endpoint metrics（calls、errors、latency P95） | `collect_endpoint_metrics()` | `instana_context.endpoint_metrics` |
| ③ | Trace groups（error trace 彙總） | `collect_trace_summary()` | `instana_context.trace_summary` |

### 可選資料（補充脈絡，config 控制）

| # | 類別 | 適用情境 |
|---|---|---|
| ④ | JVM metrics（heap、GC、thread） | 懷疑 OOM / 記憶體洩漏 |
| ⑤ | ActiveMQ Artemis metrics（message count、expired） | 懷疑 MQ 積壓 |

### instana_context JSON Schema

```json
{
  "available": true,
  "collected_at": "<ISO8601>",
  "application_id": "XdhrXfC4QYqm2ixHSlY5dQ",
  "query_window": { "from": "<ISO8601>", "to": "<ISO8601>" },
  "events": [
    {
      "event_id": "<str>",
      "type": "issue|incident",
      "severity": 5,
      "problem": "<str>",
      "entity_label": "<str>",
      "start": "<ISO8601>",
      "end": "<ISO8601>|null",
      "state": "open|closed"
    }
  ],
  "endpoint_metrics": [
    {
      "endpoint": "<str>",
      "calls": 0,
      "errors": 0,
      "error_rate_pct": 0.0,
      "latency_p95_ms": 0.0
    }
  ],
  "trace_summary": [
    {
      "service": "<str>",
      "error_traces": 0,
      "total_traces": 0,
      "error_rate_pct": 0.0
    }
  ],
  "infra_metrics": {
    "jvm": { "heap_used_mb": 0.0, "gc_pause_ms": 0.0, "thread_count": 0 },
    "artemis": { "message_count": 0, "messages_expired": 0 }
  }
}
```

## event_sequence 合併後 Schema（preprocessor 輸出）

現有結構保持不變，新增頂層 `instana_context` 欄位：

```json
{
  "job_id": "<str>",
  "query_window": { "from": "<ISO8601>", "to": "<ISO8601>" },
  "total_raw_count": 0,
  "total_filtered_count": 0,
  "event_chains": [ "..." ],
  "instana_context": {
    "available": true,
    "events": [ "..." ],
    "endpoint_metrics": [ "..." ],
    "trace_summary": [ "..." ],
    "infra_metrics": {}
  }
}
```

`instana_context.available = false` 時表示無 Instana 資料，`event_chains` 仍照常產生。

## log-analyst prompt 更新

`bob_bridge._ANALYSIS_PROMPT` 加入：

> 若事件序列中包含 `instana_context`（available=true），請將其與 event_chains 交叉比對：
> - `events`：將 Instana Issues/Incidents 與對應時間的日誌錯誤關聯，說明是否同步發生
> - `endpoint_metrics`：使用 error_rate 與 latency P95 驗證或強化根因判斷，在 root_cause 中引用具體數值
> - `trace_summary`：補充因果鏈的跨服務傳播路徑（Liberty → bankdb）

## PPTX 新增頁面（Slide 3.5）

`report_builder.py` 在 Slide 3（執行摘要）後插入 **Instana APM 概覽**（僅 available=true）：

- KPI 卡片：Total Calls、Error Rate %、P95 Latency ms
- Instana Events 列表（每條含時間、entity、problem、severity 色碼）
- Trace 錯誤率（per service）

---

## 子任務清單

### Sub-task 1：instana_collector.py — Instana 歷史資料收集模組
**Status**: `[ ] pending`

**Intent**: 建立 `bob-analyser/instana_collector.py`，封裝三類 Instana REST API 呼叫，
用已知時間窗口查詢歷史資料，回傳符合 instana_context schema 的 dict。
由 `api.py` 在 pipeline 前同步呼叫，不需定時排程。

**Expected Outcomes**:
- `instana_collector.collect(from_time, to_time) -> dict` 主函式
- 回傳符合 instana_context JSON Schema 的 dict
- 任何子項目失敗只 log warning，仍回傳部分資料（available=true）
- 全部失敗時回傳 `{ "available": false, "error": "..." }`
- `config.yaml` 新增 `instana:` 區塊（base_url、api_token、application_id、enabled）

**Todo List**:
- [ ] 建立 `bob-analyser/instana_collector.py`
- [ ] 實作 `collect_events(session, from_ms, to_ms)` — GET `/api/events` 篩同時間窗口
- [ ] 實作 `collect_endpoint_metrics(session, from_ms, to_ms, app_id)` — grouped calls metrics
- [ ] 實作 `collect_trace_summary(session, from_ms, to_ms, app_id)` — trace groups
- [ ] 實作 `collect_infra_metrics(session, from_ms, to_ms)` — JVM + Artemis（config 開關控制）
- [ ] 實作 `collect(from_time, to_time) -> dict` 主函式，呼叫以上四個子函式
- [ ] 更新 `config.yaml` 新增 `instana:` 區塊
- [ ] 更新 `bob-analyser/config_loader.py` 支援新 instana 設定

**Relevant Context**:
- Instana Application ID: `XdhrXfC4QYqm2ixHSlY5dQ`（banking-app-liberty）
- Instana API base: `https://ibmdevsandbox-instanaibm.instana.io`
- `bob-analyser/analyser/extractor.py` 的 `_query_elasticsearch()` 是錯誤處理模式參考
- `bob-analyser/gen_fake_data.py` 已有 `requests.Session` 使用範例

---

### Sub-task 2：api.py 整合 + AnalyseRequest 擴充 + preprocessor 注入
**Status**: `[ ] pending`

**Intent**: 在 `api.py` 的 `POST /analyse` 處理中，依 `trigger` 欄位判斷是否呼叫
`instana_collector.collect()`，將結果傳入 `run_analysis()`，最終由 `preprocessor.py`
注入 `event_sequence.instana_context`。

**Expected Outcomes**:
- `trigger: kibana_alert_instana` 或 `webui_instana` 時，`api.py` 自動 collect Instana
- `AnalyseRequest` 保留 `instana_context` 與 `instana_context_path` 作為隱藏 dev/test 欄位
- `run_analysis()` 新增 `instana_context: dict = None` 參數
- `preprocess()` 回傳的 dict 包含 `instana_context` 頂層欄位
- 現有不帶 instana 的呼叫行為完全不變（backward compatible）

**Todo List**:
- [ ] `api.py`：`AnalyseRequest` 新增 `instana_context`、`instana_context_path`（隱藏欄位）
- [ ] `api.py`：`_resolve_instana_context()` 私有函式，實作四段優先序邏輯
- [ ] `api.py`：`analyse()` 與 `_run_analysis_bg()` 呼叫 `_resolve_instana_context()`
- [ ] `analysis_pipeline.run_analysis()` 新增 `instana_context: dict = None` 參數
- [ ] `preprocessor.preprocess()` 新增 `instana_context: dict = None` 參數
- [ ] `preprocessor._build_instana_context(raw)` 正規化並驗證 instana_context 結構
- [ ] 注入 instana_context 至輸出 dict 頂層
- [ ] `cli.py`：新增 `--with-instana` flag，觸發 collect 後傳入 pipeline

**Relevant Context**:
- `bob-analyser/api.py:72-88` AnalyseRequest、`api.py:220-261` analyse()
- `bob-analyser/analysis_pipeline.py:22-94` run_analysis()
- `bob-analyser/analyser/preprocessor.py:116-200` preprocess()
- `bob-analyser/cli.py` CLI 入口

---

### Sub-task 3：bob_bridge prompt + custom_modes.yaml 更新
**Status**: `[ ] pending`

**Intent**: 更新 `_ANALYSIS_PROMPT` 與 `log-analyst` customInstructions，
讓 AI 知道如何解讀 `instana_context`，並在 `root_cause` 中引用 Instana 數值佐證。

**Expected Outcomes**:
- `_ANALYSIS_PROMPT` 包含 instana_context 三個子區塊的分析指示
- `root_cause` 輸出能引用如「Instana 顯示 POST /api/login error_rate 達 35%」
- `instana_context.available=false` 時 AI 不受影響，正常輸出

**Todo List**:
- [ ] 更新 `bob-analyser/analyser/bob_bridge.py` 的 `_ANALYSIS_PROMPT`
- [ ] 更新 `bob-analyser/bob-custom-modes/custom_modes.yaml` 的 `log-analyst` customInstructions
- [ ] customInstructions 說明 instana_context 的結構與各欄位語意

**Relevant Context**:
- `bob-analyser/analyser/bob_bridge.py:39-46` `_ANALYSIS_PROMPT`
- `bob-analyser/bob-custom-modes/custom_modes.yaml:41-74` customInstructions

---

### Sub-task 4：report_builder 新增 Instana APM 頁面
**Status**: `[ ] pending`

**Intent**: 在 PPTX 中新增 Slide 3.5（Instana APM 概覽），僅在 `available=true` 時插入。

**Expected Outcomes**:
- `available=true`：Slide 3 後插入 APM 概覽頁，含 KPI 卡片 + Events 列表
- `available=false`：報告結構完全不變
- 使用現有 `add_rect`、`add_text` helper 與色彩常數

**Todo List**:
- [ ] `report_builder.build_report()` 讀取 `analysis.get("instana_context", {})`
- [ ] 新增 `_add_instana_slide(prs, blank, instana_ctx, W, H, ...)` 私有函式
- [ ] 渲染 KPI 卡片（total calls、error_rate_pct、latency_p95_ms）
- [ ] 渲染 Instana Events 列表（severity 色點 + problem 描述 + entity）
- [ ] `available=false` 時直接 return，不插入任何頁面

**Relevant Context**:
- `bob-analyser/analyser/report_builder.py` 整體，尤其 `add_rect`、`add_text`、`add_left_bar`
- 色彩常數：`_CRITICAL`、`_HIGH`、`_MEDIUM`、`_CARD_BG`、`_LT_BLUE`、`_SILVER`
- report_builder.py 已被外部修改（external_changes），實作前需先讀取最新版本

---

### Sub-task 5：端對端驗證
**Status**: `[ ] pending`

**Intent**: 驗證完整的 ELK + Instana 合併分析路徑（Web UI 含 Instana 觸發），
以及純 ELK 降級路徑，確保兩條路徑都正常產出 PPTX 報告。

**Expected Outcomes**:
- `trigger: webui_instana` 觸發後，PPTX 包含 Instana APM 概覽頁（Slide 3.5）
- `root_cause` 引用 Instana 的 error_rate 或 events
- 純 ELK 觸發後，PPTX 結構與現有一致（無 Slide 3.5）
- Kibana Webhook 2（`trigger: kibana_alert_instana`）路徑驗證

**Todo List**:
- [ ] 啟動 `banking-app-liberty` 並產生測試流量
- [ ] Web UI 觸發「含 Instana 分析」，確認 PPTX 含 APM 頁
- [ ] Web UI 觸發「立即分析」（純 ELK），確認 PPTX 無 APM 頁
- [ ] 模擬 Kibana Webhook 2 呼叫（curl），確認 Instana 資料注入
- [ ] Instana collect 失敗時（如 API key 錯誤），確認降級為純 ELK 不報錯
- [ ] 更新 `AGENTS.md`：補充 Instana 整合說明、新 config 項目、觸發路徑

**Relevant Context**:
- Instana Application ID: `XdhrXfC4QYqm2ixHSlY5dQ`
- `bob-analyser/config/config.yaml` 需新增 `instana:` 區塊（含 api_token）
- `bob-analyser/.env.example` 需新增 `INSTANA_API_TOKEN` 說明

# AI Token 優化策略計畫
<!-- Created: 2026-09-17 16:13:55 +0800 -->
<!-- Updated: 2026-09-17 16:51:48 +0800 -->

## 背景

本文件記錄 ELK Analyser 專案中，**在資料進入 Bob LLM 之前的兩個數據源**的 Token 優化策略評估結果：

1. **Elasticsearch 查詢層**：在 ES 回傳資料時就過濾與精簡，降低網路傳輸量、Python 記憶體峰值與 LLM token 消耗
2. **Instana APM 整合層**：以正確的資料結構讓 Instana 與 ES 互補，達到「上帝視角」的根因分析

---

## Part 1：Elasticsearch 查詢優化（4 大策略）

### 策略評估總覽

| # | 策略 | 有效性 | 現況 | 優先級 |
|---|------|--------|------|--------|
| ES-1 | 欄位精簡投影（Source Filtering） | ✅ 高效益 | **未實作** | P0 |
| ES-2 | Aggregations 去重計數 | ✅ 高效益 | 架構存在但不完整 | P1 |
| ES-3 | Range + Terms 硬過濾 | ✅ 已完整實作 | — | 維持現狀 |
| ES-4 | 鄰近脈絡撈取法（Context Fetching） | ⚠️ 謹慎評估 | trace.id grouping 已覆蓋 | 暫緩 |

---

### ES-1：欄位精簡與投影（Source Filtering）

#### 問題

`extractor._query_elasticsearch()` 目前無 `_source` 參數，ES 回傳完整 `_source`。
Logstash 吃進來的 banking 文件通常含 40–60 個欄位（K8s 節點、Agent 版本、ECS 元數據等），
大部分對 AI 分析無用，卻在以下環節佔用資源：

- **網路傳輸**：ES → Python 的 payload
- **Python 記憶體**：`raw_logs` list 在 `preprocess()` 完成前持有所有原始文件
- **LLM token**：若未來直接把 raw 欄位傳給 AI

#### 解決方案

在 `extractor.py` 的 `query_body` 加入 `_source` 白名單，從 `field_mapping` 動態組裝核心欄位，
並允許 `config.yaml` 透過 `source_fields.extra` 補充 banking demo 特定欄位。

**config.yaml 新增區塊：**

```yaml
# ── 欄位精簡投影（Source Filtering）────────────────────────────────
source_fields:
  extra:
    - "module"           # was-liberty 模組判斷（SystemErr/SystemOut 過濾）
    - "thread"           # 執行緒名稱（堆疊關聯）
    - "transaction.id"   # 交易 ID（banking 業務追蹤）
    - "error.message"    # ECS 標準錯誤欄位
    - "error.type"       # 例外類型
```

**extractor.py 修改點（`_query_elasticsearch` 內）：**

```python
core_fields = list(fm.values())  # ["@timestamp", "log.level", "message", ...]
extra_fields = []
if hasattr(config, "source_fields"):
    extra_fields = config.source_fields.get("extra", [])
elif isinstance(config, dict):
    extra_fields = config.get("source_fields", {}).get("extra", [])

source_includes = list(dict.fromkeys(core_fields + extra_fields))  # 去重保序

query_body = {
    "_source": source_includes,
    "query": {"bool": {"must": must_clauses, "must_not": must_not_clauses}},
    "sort": [{fm["timestamp"]: "asc"}],
}
```

#### 注意事項

- `must_not_clauses` 裡的 `service.keyword`、`module.keyword` 是 filter 欄位，不需放入 `_source`（ES filter 與 `_source` 投影相互獨立）
- Scroll 後續呼叫 `es.scroll()` 無需重複指定 `_source`，ES 自動沿用原始查詢設定
- Mock 模式（`_load_mock_logs`）讀本機 JSON，不受此修改影響

#### 效益估算

| | 欄位數 | 單筆大小 | 5000 筆 payload |
|---|---|---|---|
| 修改前（全 `_source`） | ~50 | ~2–4 KB | ~10–20 MB |
| 修改後（白名單 ~10 欄位） | ~10 | ~0.3–0.5 KB | ~1.5–2.5 MB |
| **節省** | **80%** | **↓ 85%** | **↓ 85%** |

---

### ES-2：Aggregations 去重計數

#### 問題

目前 `es_agg_summary` 由**外部呼叫端傳入**（依賴 Kibana Alert Webhook 帶來聚合數據）。
當 Web UI 手動觸發時，`es_agg_summary` 為 `None`，AI 只能從有限的 `event_chains` 樣本推斷分布。

系統出錯時同一個錯誤可能刷 5,000+ 條，原始日誌送給 AI 效果遠不如統計摘要。

#### 解決方案

在 `extractor._query_elasticsearch()` 的 ES 查詢中**同時帶入 aggregation**，
讓 `extract()` 回傳值包含聚合摘要，由 `analysis_pipeline.py` 填入 `es_agg_summary`。

**ES Query 修改：**

```python
query_body = {
    "_source": source_includes,
    "query": {"bool": {"must": must_clauses, "must_not": must_not_clauses}},
    "sort": [{fm["timestamp"]: "asc"}],
    "aggs": {
        "error_by_service": {"terms": {"field": "service.keyword", "size": 10}},
        "error_by_message": {"terms": {"field": "message.keyword", "size": 20}},
        "errors_over_time": {
            "date_histogram": {"field": fm["timestamp"], "calendar_interval": "1m"}
        }
    }
}
```

**extract() 回傳值調整（改為 tuple）：**

```python
def _query_elasticsearch(...) -> tuple[list[dict], dict]:
    ...
    agg_summary = {}
    if "aggregations" in resp:
        agg_summary = {
            "error_by_service": resp["aggregations"]["error_by_service"]["buckets"],
            "error_by_message": resp["aggregations"]["error_by_message"]["buckets"],
            "errors_over_time": resp["aggregations"]["errors_over_time"]["buckets"],
        }
    return results, agg_summary
```

**受影響的呼叫鏈：**

```
extractor.extract()
  → analysis_pipeline.run_analysis()  # 解包 agg_summary
    → preprocessor.preprocess()       # 注入 es_agg_summary（現有介面不變）
```

---

### ES-3：Range + Terms 硬過濾（已完整實作）

以下過濾機制均已在 `extractor.py` 實作，無需修改：

| 過濾項目 | 實作位置 |
|---------|---------|
| 時間 Range | `extractor.py:106–114` — `range` query |
| 錯誤等級 Terms | `extractor.py:115–121` — `terms` query（大小寫相容） |
| Liberty SystemOut/Err 噪音排除 | `extractor.py:143–167` — `must_not` |
| 最大筆數上限 | `extractor.py:175` — `max_hits: 5000` |
| 層二服務限縮 | `extractor.py:124–126` — `spike_service` terms filter |

---

### ES-4：鄰近脈絡撈取法（暫緩）

#### 評估結論

兩步撈取法（第一步找 ERROR + trace.id → 第二步撈鄰近 INFO）有以下架構風險：

1. **兩次 ES round-trip**：Scroll API 成本加倍
2. **INFO 日誌不在 filter.levels 白名單**：`config.yaml` 只允許 ERROR / FATAL / CRITICAL
3. **INFO 日誌量巨大**：banking demo 每秒數十條，前後 10 秒可能數百筆，反而增加噪音

#### 現有替代方案

`preprocessor._group_by_trace_id()` 已把同一 `trace.id` 的所有事件集中在同一 chain，提供等效的因果脈絡。
若 `trace.id` 未填充，5 分鐘時間窗口 fallback 亦覆蓋前後事件。

**真正值得做的改善**：提升 Logstash pipeline 對 `trace.id` 的填充率（目前 banking demo 的 MDB / PostgreSQL 日誌未必帶 trace.id），使 trace-based grouping 覆蓋率從約 40% 提升至 80%+。

---

## Part 2：Instana APM 整合優化（4 大策略）

### 策略評估總覽

| # | 策略 | 有效性 | 現況 | 優先級 |
|---|------|--------|------|--------|
| IN-1 | traceId 作為終極黏著劑 | ✅ 最高效益 | ⚠️ 核心機制缺失 | P0 |
| IN-2 | 動態拓撲結構（Context Guide） | ✅ 高效益 | ❌ 未實作 | P1 |
| IN-3 | 只抓異常事件與突變點 | ✅ 已實作 | 需加嚴重度過濾 | P2 |
| IN-4 | 雙源結構化 Prompt 模板 | ✅ 已實作 | JSON key 分離，效果相近 | 可優化 |

---

### IN-1：用 traceId 作為「終極黏著劑」

> ⚠️ **API 驗證結論（2026-09-17）**：Instana Events API 回傳的 Issue/Incident 物件**不含任何 traceId 欄位**（已實測確認）。原方案「從 Events 提取 associatedTraces」不可行，需改從 **Trace API** 主動查詢 error trace。

#### 問題

目前 `instana_collector.py` 收集的是 Issues 清單、service 層級指標統計、trace 數量摘要——
三者都**沒有 traceId 欄位**，與 ES 日誌的 `trace.id` 完全脫節。

目前兩個數據源的關聯方式只靠**時間窗口對齊**（都在同一個 `query_window` 內），
不是真正的 trace 級黏合。

#### API 驗證結果

**Events API 物件結構**（實測）：

```json
{
  "eventId": "n4wRviG1QPi3oiXHeFShUA",
  "type": "issue",
  "severity": 5,
  "problem": "host status down",
  "entity": { "type": "INFRASTRUCTURE", "label": "sleep" },
  "affectedMetrics": ["online_metric"]
}
```
→ **無 traceId**：Events 是基礎設施層異常（host down、pod not ready），在 Instana 內部不與 APM trace 掛鉤。

**Trace API 物件結構**（實測）：

```json
{
  "trace": {
    "id": "b3b3bd579d14e1ff",     ← traceId 在此
    "erroneous": false,
    "duration": 276,
    "service": { "label": "ibm-streamsets-astersecurity" }
  }
}
```

**Trace Detail（call tree）結構**（實測）：

```json
{
  "id": "b3b3bd579d14e1ff",        ← root span = traceId
  "parentId": null,
  "destination": {
    "service": { "label": "was-liberty" },
    "endpoint": { "label": "POST /transfer", "type": "HTTP" }
  },
  "duration": 4800,
  "errorCount": 1
}
```

#### 修正後的解決方案

**正確入口：從 Trace API 主動查 error trace（Push → Pull 反轉）**

```
修正後流程：
  Step 1: POST /api/application-monitoring/analyze/traces
          filter: erroneous=true, timeFrame=查詢窗口
          → 取出 top N error trace 的 traceId（已實測回傳 trace.id 欄位）

  Step 2: 用 traceId 呼叫 get_trace_details
          → 取得完整 call tree（service 呼叫順序、各節點耗時、錯誤點）

  Step 3: 用同一批 traceId 去 ES 做 terms filter
          ES query 加: {"terms": {"trace.id": [t1, t2, t3]}}
          → 精準撈取對應這幾條 trace 的應用層日誌

  Bob 收到: 同一 traceId 的 ES 程式報錯 + Instana Call Tree（完整因果鏈）
```

**Step 1：新增 `collect_error_traces()`**

```python
def collect_error_traces(
    session,
    base_url: str,
    from_ms: int,
    to_ms: int,
    app_id: str,
    max_traces: int = 5,
) -> list[dict]:
    """
    查詢時間窗口內的 error trace，回傳 traceId 清單與基本摘要。
    最多取 max_traces 筆，控制後續 token 量。
    """
    try:
        payload = {
            "tagFilterExpression": {
                "type": "EXPRESSION",
                "logicalOperator": "AND",
                "elements": [
                    {
                        "type": "TAG_FILTER",
                        "name": "application.name",
                        "operator": "EQUALS",
                        "entity": "DESTINATION",
                        "value": "banking-app-liberty",
                    },
                    {
                        "type": "TAG_FILTER",
                        "name": "trace.error",
                        "operator": "EQUALS",
                        "entity": "NOT_APPLICABLE",
                        "value": "true",
                    },
                ],
            },
            "timeFrame": {"to": to_ms, "windowSize": to_ms - from_ms},
            "pagination": {"retrievalSize": max_traces},
            "order": {"by": "startTime", "direction": "DESC"},
        }
        resp = session.post(
            f"{base_url}/api/application-monitoring/analyze/traces",
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        result = []
        for item in items:
            t = item.get("trace", {})
            if not t.get("id"):
                continue
            result.append({
                "trace_id": t["id"],
                "duration_ms": t.get("duration", 0),
                "erroneous": t.get("erroneous", True),
                "service": (t.get("service") or {}).get("label", ""),
                "start_time": t.get("startTime"),
            })
        logger.info("collect_error_traces：取得 %d 筆 error trace", len(result))
        return result
    except Exception as exc:
        logger.warning("collect_error_traces 失敗（忽略）：%s", exc)
        return []
```

**Step 2：新增 `collect_trace_details()`**

```python
def collect_trace_details(
    session,
    base_url: str,
    trace_ids: list[str],
) -> list[dict]:
    """
    針對指定 traceId 清單，逐一取得 call tree（呼叫鏈詳情）。
    回傳每條 trace 的服務呼叫順序、耗時與錯誤點。
    """
    results = []
    for tid in trace_ids[:5]:
        try:
            resp = session.get(
                f"{base_url}/api/application-monitoring/analyze/traces/{tid}",
                timeout=10,
            )
            resp.raise_for_status()
            calls_raw = resp.json().get("items", [])
            calls = []
            for c in calls_raw[:10]:          # 最多 10 個 span，控制 token 量
                dest = c.get("destination") or {}
                calls.append({
                    "span_id": c.get("id", ""),
                    "parent_id": c.get("parentId"),
                    "service": (dest.get("service") or {}).get("label", ""),
                    "endpoint": (dest.get("endpoint") or {}).get("label", ""),
                    "duration_ms": c.get("duration", 0),
                    "error_count": c.get("errorCount", 0),
                })
            results.append({"trace_id": tid, "calls": calls})
        except Exception as exc:
            logger.warning("collect_trace_details trace=%s 失敗（忽略）：%s", tid, exc)
    return results
```

**Step 3：`collect()` 主函式整合，並回傳 trace_ids 供 ES 使用**

```python
# instana_collector.collect() 內新增
error_traces = collect_error_traces(session, base_url, from_ms, to_ms, app_id)
ctx["error_traces"] = error_traces

trace_ids = [t["trace_id"] for t in error_traces]
if trace_ids:
    ctx["trace_details"] = collect_trace_details(session, base_url, trace_ids)
    ctx["trace_ids"] = trace_ids   # 回傳給 api.py，傳入 extractor 做 ES terms filter

# extractor._query_elasticsearch() 加入（api.py 傳入）：
if trace_ids:
    must_clauses.append({"terms": {fm.get("trace_id", "trace.id"): trace_ids}})
```

#### 效益

AI 收到的是「同一個 traceId 的 ES 程式報錯 + Instana Call Tree 呼叫鏈」，
既看得到 `Connection pool exhausted` 的錯誤訊息，也看得到 `bankdb 呼叫耗時 4800ms`，
診斷準確度大幅提升。

---

### IN-2：動態拓撲結構（Context Guide）

#### 問題

`collect_endpoint_metrics()` 回傳的是**個別 service 的指標列表**，不是**上下游呼叫關係**：

```json
[
  {"endpoint": "was-liberty", "error_rate_pct": 70, "latency_p95_ms": 4800},
  {"endpoint": "bankdb",      "error_rate_pct": 68, "latency_p95_ms": 120}
]
```

AI 需要自己推斷「was-liberty 呼叫 bankdb」的因果方向，而不是直接看到明確的拓撲箭頭。

#### 解決方案

**方案 A（輕量）**：從現有 `endpoint_metrics` + `trace_summary` 的 service 順序，
在 `collect()` 組裝線性拓撲字串注入 `instana_context`：

```python
# instana_collector.collect() 末段組裝
if endpoint_metrics:
    sorted_by_error = sorted(endpoint_metrics, key=lambda x: x["error_rate_pct"], reverse=True)
    topo_parts = []
    for svc in sorted_by_error:
        topo_parts.append(
            f"{svc['endpoint']} (Error: {svc['error_rate_pct']}%, P95: {svc['latency_p95_ms']}ms)"
        )
    ctx["topology_summary"] = " -> ".join(topo_parts)
    # 例：was-liberty (Error: 70%, P95: 4800ms) -> bankdb (Error: 68%, P95: 120ms)
```

**方案 B（精確）**：呼叫 Instana `/api/application-monitoring/dependencies` API，
取得真實的上下游依賴關係圖（需要確認 API 端點可用性）。

#### 效益

```
[HTTP Entry] -> was-liberty (Error Rate: 70%) -> bankdb (Latency: 4800ms) -> PostgreSQL (Connections: Maxed)
```

這短短一行文字讓 AI 立即確認根因方向（DB 連線滿 → 下游逾時），
省去翻閱大量基礎設施 log 的時間。

---

### IN-3：只抓異常事件與突變點（已實作，需小修）

> ✅ **Instana ActiveMQ Artemis Sensor 已啟用（2026-09-17）**
> 修改的檔案：
> - [`japp-demo/container-japp/broker.xml`](../japp-demo/container-japp/broker.xml) — 加入 `<jmx-management-enabled>true</jmx-management-enabled>`
> - [`japp-demo/container-japp/supervisord.conf`](../japp-demo/container-japp/supervisord.conf) — Artemis `JAVA_ARGS` 加入 JMX Remote 5 個 JVM 參數（port 1099/2099）
> - [`japp-demo/container-japp/Containerfile`](../japp-demo/container-japp/Containerfile) — `EXPOSE 1099 2099`，新增 `ARTEMIS_JMX_OPTS` 環境變數
> - [`podman-compose.yml`](../podman-compose.yml) — `banking-app` 新增 ports `127.0.0.1:1099:1099` / `127.0.0.1:2099:2099`；`instana-agent` volume 改為 bind mount `./instana-agent-package`
> - [`instana-agent-package/configuration.yaml`](../instana-agent-package/configuration.yaml) — 新建，指定 `host: 10.89.2.20`、`port: 1099`、`monitorQueues: [bankingQueue, bankingReplyQueue, DLQ, ExpiryQueue]`
>
> **重建 & 重啟指令：**
> ```bash
> podman-compose build banking-app
> podman-compose up -d banking-app instana-agent
> ```
> 啟用後 `collect_infra_metrics()` 的 `messageCount`、`messagesExpired`、`consumerCount` 將有真實數值。

#### 現況

`collect_events()` 已實作：只抓 `/api/events` Issues/Incidents，
加入時間窗口過濾與正規化，並截斷至 top 50。
`collect_infra_metrics()` 由 config `collect_infra_metrics=false` 控制，預設關閉。

**符合策略精神**：不抓每秒 CPU% 流水帳，讓 Instana 先做第一層異常檢測。

#### 需要修正

`collect_events()` 目前沒有嚴重度過濾，低嚴重度的 CHANGE 事件（`severity=-1`）
可能混入 top 50，消耗 Bob context。

**建議在 `instana_collector.py` L86 的正規化迴圈加入：**

```python
# 只保留 WARNING(5) 以上的事件，排除純 CHANGE 事件（severity=-1）
if int(ev.get("severity", 5)) < 5:
    continue
```

---

### IN-4：雙源結構化 Prompt 模板（已實作）

#### 現況

`bob_bridge._ANALYSIS_PROMPT` 已明確要求 Bob 區分兩個數據源：

```
（1）events：將 Instana Issues/Incidents 與對應時間的日誌錯誤關聯
（2）endpoint_metrics：service 層級指標，在 root_cause 中引用具體數值
（3）trace_summary：補充因果鏈的跨服務傳播路徑（如 Liberty → bankdb）
```

#### 現況 vs 策略建議

| 面向 | 目前實作 | 策略建議 |
|------|---------|---------|
| 資料分隔方式 | JSON 頂層 key（`instana_context` / `event_chains`） | XML 標籤（`<instana_apm_data>` / `<elasticsearch_application_logs>`） |
| 分析順序指引 | Prompt 末段有「先看 Instana → 再對照 ES Log」指引 | 明確的三步驟思考限制 |
| 效果 | 實測已能正確分辨兩個來源 | XML 標籤對部分 LLM 有額外優勢 |

**建議**：可在下一版 `custom_modes.yaml` 的 `customInstructions` 中，
將送給 Bob 的 `instana_context` 和 `event_chains` 改以 XML 標籤包裹做 A/B 測試，
驗證是否進一步提升根因判斷準確度。

---

## 綜合實作順序

```
P0（立即）:
  ES-1  extractor.py 加 _source 白名單 + config.yaml 新增 source_fields
  IN-1  collect_events() 提取 traceId + 新增 collect_trace_details()
        + extractor 加 trace_ids terms filter

P1（次週）:
  ES-2  extractor._query_elasticsearch() 加 aggs，extract() 回傳 tuple
  IN-2  collect() 組裝 topology_summary 字串（方案 A 輕量版）

P2（本月）:
  IN-3  collect_events() 加 severity < 5 過濾條件

可選優化:
  IN-4  custom_modes.yaml 改 XML 標籤分隔，A/B 測試 Prompt 效果
  ES-4  先提升 Logstash trace.id 填充率，再評估是否需要兩步撈
```

---

## 相關檔案

| 檔案 | 說明 |
|------|------|
| [`bob-analyser/analyser/extractor.py`](../bob-analyser/analyser/extractor.py) | ES-1、ES-2、IN-1 主要修改目標 |
| [`bob-analyser/analyser/preprocessor.py`](../bob-analyser/analyser/preprocessor.py) | `es_agg_summary` 注入（介面不變） |
| [`bob-analyser/analysis_pipeline.py`](../bob-analyser/analysis_pipeline.py) | `run_analysis()` 解包 agg_summary、trace_ids |
| [`bob-analyser/instana_collector.py`](../bob-analyser/instana_collector.py) | IN-1、IN-2、IN-3 主要修改目標 |
| [`bob-analyser/analyser/bob_bridge.py`](../bob-analyser/analyser/bob_bridge.py) | IN-4 Prompt 模板（可選優化） |
| [`bob-analyser/config/config.yaml`](../bob-analyser/config/config.yaml) | 新增 `source_fields` 區塊 |

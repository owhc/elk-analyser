# ELK Analyser — 動態多系統根因分析設計計畫
<!-- Created: 2026-09-16 23:50:00 +0800 -->
<!-- Updated: 2026-09-17 11:47:59 +0800 -->

> **目標**：面對銀行複雜且數量不定的多系統（Liberty、MQ、DB、K8s、Redis…），
> 能動態新增受監控系統、精準觸發分析、並在不超過 Bob Shell context 上限的前提下，
> 對任意數量系統的日誌執行根因分析，輸出跨服務因果鏈報告。

---

## 設計原則

| 原則 | 說明 |
|------|------|
| **零程式碼新增系統** | 新系統只改 `config.yaml`（或 UI 操作），程式碼不動 |
| **精準觸發優於全量掃描** | Kibana 觸發時帶入事故時間點與聚合摘要，縮小 ES 查詢範圍 |
| **Python 即 Orchestrator** | 每個 `subprocess.run("bob run ...")` 是獨立 270k context window，Python 負責切割與合併 |
| **向後相容** | 所有新參數均有預設值，現有單一系統路徑行為不變 |

---

## 架構全覽

```
Kibana Alert Webhook
  { event_time, spike_service, es_agg_summary, trigger }
          │
          ▼
  api.py  ─── _resolve_query_window()
              ├─ event_time → 精準 ±7 分鐘視窗  ← 層一
              ├─ lookback_minutes → 寬鬆視窗
              └─ Checkpoint fallback
          │
          ▼
  analysis_pipeline.run_analysis()
          │
          ├─ cfg.sources[]  ← 動態 N 個系統（config.yaml 或 UI 設定）
          │
          ▼
  ┌─────────────────────────────────────────────────────┐
  │  per-source 平行 extract（ThreadPoolExecutor）        │
  │  extractor.extract(source_cfg, spike_service)        │  ← 層二
  └─────────────────────────────────────────────────────┘
          │
          ▼
  ┌─────────────────────────────────────────────────────┐
  │  per-source preprocessor                            │
  │  preprocess(es_agg_summary, source_id, source_label)│  ← 層三
  └─────────────────────────────────────────────────────┘
          │
          ▼
  token 路由決策（TokenEstimator）
  ├─ 全部 sources 合計 ≤ 60k → 合併單次 bob run
  └─ 全部 sources 合計 > 60k → per-source 平行 bob run × N
        │
        ▼
  bob_bridge（兩種執行路徑）
  ├─ _analyse_single()     現有路徑，不改
  └─ _analyse_parallel()   N 個獨立 bob run，各自 context 隔離
          │
          ▼
  report_builder.build_report()
```


---

## 第一層：ELK 精準觸發限縮（已實作）

> **目的**：Kibana 偵測到 spike 時，不再盲目回溯 60 分鐘全量日誌，
> 而是以事故時間點為中心、只拉 spike 服務、並附帶聚合統計，
> 將傳給 Bob 的 tokens 從 ~60k 壓縮至 ~8k。

### 三層精準限縮

| 層 | 機制 | 實作位置 | token 節省 |
|----|------|---------|-----------|
| **層一** | `event_time` 精準視窗：以事故時間點為中心，前 5 分 + 後 2 分 | [`api.py` `_resolve_query_window()`](../bob-analyser/api.py) | 查詢範圍 60min → 7min，縮 88% |
| **層二** | `spike_service` terms filter：ES 查詢只拉 spike 服務的日誌 | [`extractor.py` `_query_elasticsearch()`](../bob-analyser/analyser/extractor.py) | 多服務場景減少 50–90% 筆數 |
| **層三** | `es_agg_summary` 注入：Kibana 帶來的聚合統計直接傳給 Bob | [`preprocessor.py` `preprocess()`](../bob-analyser/analyser/preprocessor.py) | Bob 不需從樣本重算統計 |

### Kibana Webhook Body 設定

Kibana Alert → Webhook 的 body 加入以下欄位（均選填，向後相容）：

```json
{
  "trigger": "kibana_alert",
  "lookback_minutes": 60,
  "event_time": "{{date}}",
  "spike_service": "liberty",
  "es_agg_summary": {
    "total_errors": 872,
    "error_by_service": { "liberty": 847, "mq": 12, "db": 13 },
    "spike_service": "liberty",
    "first_error_time": "{{context.date}}",
    "rule_name": "{{rule.name}}"
  }
}
```

### `AnalyseRequest` 新欄位（五階段優先序）

```
1. from_time       → 直接使用（Web UI / CLI 手動指定）
2. event_time      → 精準小視窗（前 5 分 / 後 2 分）← 新增
3. lookback_minutes → to_time - lookback_minutes
4. Checkpoint      → 接續上次分析結束時間
5. Fallback        → now - default_lookback_minutes
```

### token 對比

| | 現在 | 三層限縮後 |
|---|---|---|
| 查詢時間範圍 | 60 分鐘 | **7 分鐘** |
| 拉取服務範圍 | 所有 service | **只有 spike service** |
| 傳給 Bob 的內容 | 5000 筆原始事件 | **ES 聚合摘要 + 代表性樣本** |
| 估計 tokens | ~60k | **~8k** |

---

## 第二層：動態多系統管理

> **目的**：系統種類與數量不定，新增受監控系統只改設定，程式碼零修改。

### `config.yaml` schema 升級：`sources[]` 陣列

現有的單一 `elasticsearch` + `field_mapping` 改為 `sources` 陣列，
每個系統獨立設定自己的 ES 連線、欄位映射、過濾規則與分析提示。
**舊格式保留作為向後相容 fallback**。

```yaml
sources:
  - id: liberty
    label: "WAS Liberty"
    enabled: true
    elasticsearch:
      host: "elasticsearch"
      port: 9200
      index_pattern: "banking-logs-liberty-*"
      timeout: 30
    field_mapping:
      timestamp: "@timestamp"
      level: "log.level"
      message: "message"
      service: "service"
      trace_id: "trace.id"
      host: "host.name"
    filter:
      levels: [ERROR, FATAL, CRITICAL]
      max_hits: 1000
    analysis_hint: "重點關注 JDBC connection pool 耗盡、MDB 消費積壓、JVM heap"

  - id: mq
    label: "ActiveMQ Artemis"
    enabled: true
    elasticsearch:
      index_pattern: "banking-logs-mq-*"
      # ... 其餘連線設定
    field_mapping:
      trace_id: ""    # Artemis 無 trace_id
      # ...
    filter:
      max_hits: 500
    analysis_hint: "重點關注佇列積壓、consumer 斷線、message TTL 超時"

  # 未來新增系統：只加這裡，程式碼零修改
  # - id: k8s
  #   label: "Kubernetes"
  #   enabled: false
  #   ...
```

### 改動清單（面向動態 sources）

| 檔案 | 改動類型 | 說明 |
|------|---------|------|
| [`config.yaml`](../bob-analyser/config/config.yaml) | schema 升級（向後相容） | 舊 `elasticsearch` + `field_mapping` 保留為 fallback |
| [`config_loader.py`](../bob-analyser/config_loader.py) | 純加法 | 新增 `sources` property，向後相容包裝舊格式 |
| [`extractor.py`](../bob-analyser/analyser/extractor.py) | 介面調整 | 加 `source_cfg` 參數，有則優先用 source 自己的設定 |
| [`preprocessor.py`](../bob-analyser/analyser/preprocessor.py) | 純加法 | 加 `source_id`、`source_label` 參數，注入每個 event_chain |
| [`bob_bridge.py`](../bob-analyser/analyser/bob_bridge.py) | 純加法 | `_build_prompt(analysis_hint)` 動態注入系統分析提示 |
| [`custom_modes.yaml`](../bob-analyser/bob-custom-modes/custom_modes.yaml) | 純加法 | 說明 `chain.source_id` 欄位意義 |
| [`analysis_pipeline.py`](../bob-analyser/analysis_pipeline.py) | 核心改動 | 動態迭代 sources、平行 extract、token 路由決策 |

**唯一需謹慎測試的是 `analysis_pipeline.py`**；其他六個改動均是加法，
不影響現有的單 source 路徑。

### Web UI 系統管理（Sources CRUD）

UI 可新增/啟停/刪除 source，需配套後端 API：

| Endpoint | 說明 |
|----------|------|
| `GET /config/sources` | 讀取現有 sources 清單（密碼遮蔽） |
| `POST /config/sources` | 新增 source，寫入 `config.yaml` |
| `PUT /config/sources/{id}` | 更新（含啟用/停用 toggle） |
| `DELETE /config/sources/{id}` | 刪除 |
| `POST /config/sources/test` | 測試 ES 連線（不寫入） |

**重要前提**：UI CRUD 可先上線讓用戶管理設定，
但要等 `analysis_pipeline.py` 完成動態 source 迭代後，新增的系統才會真正被分析。

---

## 第三層：多 Agent 分析路由（平行 bob run）

> **目的**：當 N 個系統的日誌合計超過 60k tokens 時，
> 以 Python 作為 Orchestrator，對每個系統各自執行獨立 bob run，
> 各自擁有完整 270k context window，再由 Python merge 層整合結果。

### Bob Shell Context Window 限制

| 指標 | 數值 |
|------|------|
| 總上限 | 270,000 tokens |
| 安全觸頂 | ~220,000 tokens |
| 系統固定開銷 | ~8,800 tokens |
| Bob 保留給回覆 | 20,000 tokens |
| **可用於事件序列輸入** | **~190,000 tokens** |
| 換算最大正規化事件數 | ~12,000 筆（欄位剪裁後，6 欄位/筆） |

### Python 即 Orchestrator

Bob Shell 的 `spawn_subagent` 工具在 headless `bob run` 模式下需要 approval 確認，
無法在自動化 pipeline 中使用。
但每個 `subprocess.run("bob run ...")` 本身就是獨立的 270k context window：

> **每次 `bob run` subprocess = 一個獨立的 Bob Shell 實例，等同於 Subagent。**
> Python 的 `ThreadPoolExecutor` 負責並行排程，`ResultMerger` 負責聚合。

### token 路由決策

```
全部 sources 合計 token 估算
  │
  ├─ ≤ 60k   → 合併單次 bob run（現有路徑）
  │
  ├─ 60k–150k → per-source 平行 bob run × N
  │             各自 context 隔離，Python merge 層時序關聯
  │
  └─ > 150k  → per-source 平行 bob run × N（Map）
               + 整合 bob run --mode log-analyst-merge（Reduce）
               跨系統語義因果推斷
```

### Token 估算公式

```
total_tokens = 8,800（固定開銷）
             + 20,000（Bob 回覆保留）
             + len(json.dumps(event_sequence)) / 4（粗估）
             + 3,000（若 instana_context.available=true）
```

正規化後每筆事件（6 欄位）約 **16 tokens**；
`max_hits: 1000` per source × 16 = 16,000 tokens，
三個 source 合計約 48,000 tokens → 60k 以下走合併單次路徑。

### 子任務清單

---

#### 子任務 A — `config.yaml` + `config_loader.py` 升級

**Status**：`[ ] pending`

1. `config.yaml`：新增 `sources[]` 區塊（保留舊 `elasticsearch` + `field_mapping` 向後相容）
2. `config.yaml` `bob_bridge` 下新增平行分析設定：
   ```yaml
   parallel:
     enabled: true
     max_parallel_workers: 3   # 平行 bob run 最大數量
     worker_timeout: 150       # 單個 bob run timeout（秒）
     token_threshold: 60000    # 超過此值改走平行路由
   ```
3. `config_loader.py` 新增 `sources`、`parallel_config` property，缺少時套用預設值
4. 向後相容：無 `sources` 時包裝舊格式為單一 source；`parallel.enabled=false` 時走現有路徑

**相關檔案**：[`config.yaml`](../bob-analyser/config/config.yaml)、[`config_loader.py`](../bob-analyser/config_loader.py)

---

#### 子任務 B — `extractor.py` + `preprocessor.py` 動態 source 支援

**Status**：`[ ] pending`

1. `extractor.extract()` 加 `source_cfg: dict = None` 參數，有則用 source 自己的 ES/field/filter 設定
2. `preprocessor.preprocess()` 加 `source_id: str = ""`、`source_label: str = ""` 參數，
   每個 event_chain 加上 `source_id`、`source_label` 欄位
3. 所有新參數有預設值，現有呼叫方式不變

**相關檔案**：[`extractor.py`](../bob-analyser/analyser/extractor.py)、[`preprocessor.py`](../bob-analyser/analyser/preprocessor.py)

---

#### 子任務 C — `bob_bridge.py` token 估算 + 兩路由分析

**Status**：`[ ] pending`

1. 新增 `_estimate_tokens(event_sequence: dict) -> int`
2. 新增 `_build_prompt(analysis_hint: str = "") -> str`，hint 非空時附加系統分析提示
3. 新增 `_analyse_parallel(preprocessed_by_source, job_id, config) -> dict`：
   - `ThreadPoolExecutor` 平行執行 N 個 bob run，各自 context 隔離
   - Python merge 層做時序關聯，整合 event_chains 與 summary
   - 單一 source 失敗時記錄警告，不中斷其他 source 的分析
4. 修改 `analyse()` 主入口：依 token 估算選擇路由（單次 / 平行）

**相關檔案**：[`bob_bridge.py`](../bob-analyser/analyser/bob_bridge.py)

---

#### 子任務 D — `analysis_pipeline.py` 動態 source 迭代

**Status**：`[ ] pending`

1. `run_analysis()` 改為動態迭代 `cfg.sources`：
   - 平行 extract（各 source 各自查 ES）
   - 平行 preprocess（各 source 用自己的 field_mapping）
2. token 路由決策後呼叫對應的 `bob_bridge` 函式（單次 / 平行）
3. 平行模式下 pipeline timeout 延長：
   ```python
   parallel_timeout = worker_timeout + 30  # 平行執行，timeout 以最慢的單個為準
   ```
4. `_analysis_lock` 機制不變（保護 API 層單工）

**相關檔案**：[`analysis_pipeline.py`](../bob-analyser/analysis_pipeline.py)

---

#### 子任務 E — `custom_modes.yaml` 補充 `source_id` 說明

**Status**：`[ ] pending`

1. `log-analyst` 的 `customInstructions` 補充 `chain.source_id` / `chain.source_label` 使用說明：
   - `source_id` 代表產生此 chain 的系統（如 `liberty`、`mq`、`db`）
   - 跨 source 因果鏈應在 `root_cause` 中明確說明傳播路徑
   - 若多個 source 的 chain 在相近時間點出現 CRITICAL，說明可能的 cascade 關係

**相關檔案**：[`custom_modes.yaml`](../bob-analyser/bob-custom-modes/custom_modes.yaml)

---

#### 子任務 F — Web UI Sources 管理介面

**Status**：`[ ] pending`

1. `api.py` 新增 5 個 endpoint（`GET/POST/PUT/DELETE /config/sources`、`POST /config/sources/test`）
2. `AppConfig` 加 `save_sources()` 寫入方法
3. `index.html` 新增 Sources 管理面板：
   - Source 卡片列表（id / label / index_pattern / enabled toggle）
   - 新增表單（id、label、ES 連線、field_mapping、filter、analysis_hint）
   - 測試連線按鈕（呼叫 `/config/sources/test`）
   - 儲存後即時生效（寫入 `config.yaml`，下次分析時使用新設定）

**前提**：此任務可先上線做 CRUD，但需等子任務 D 完成後新系統才真正被分析。

**相關檔案**：[`api.py`](../bob-analyser/api.py)、[`web/index.html`](../web/index.html)

---

#### 子任務 G — 整合測試

**Status**：`[ ] pending`

1. 單次路徑（tokens ≤ 60k）：行為與現有完全一致，通過現有測試
2. 平行路由（tokens > 60k）：N 個 bob run 平行執行，Python merge 正確整合 event_chains
3. 單一 source 失敗降級：其他 source 正常完成，報告標記 `partial_analysis: true`
4. 向後相容：無 `sources` 區塊的舊 config 正常運作

---

## 子任務依賴關係

```
子任務 A（config + config_loader）
    ├─→ 子任務 B（extractor + preprocessor）
    │       └─→ 子任務 D（pipeline 動態 source）
    └─→ 子任務 C（bob_bridge 兩路由）
            └─→ 子任務 D
子任務 E（custom_modes source_id 說明）← 可與 B/C 平行進行
子任務 F（Web UI）← 可獨立進行，等 D 完成後 source 才生效
    └─→ 子任務 G（整合測試）
```

---

## 能力邊界

| 場景 | 是否能負擔 | 說明 |
|------|-----------|------|
| japp-demo（Liberty + MQ + DB） | ✅ 現有架構已夠 | 三層限縮後 ~8k tokens |
| 5–8 個系統（加 K8s / Redis） | ✅ 子任務完成後 | per-source 平行，各自 context 隔離 |
| 真實銀行核心（COBOL + CICS + MQ + Batch） | ⚠️ 需重複事件去重 | 同一錯誤重複 800 筆應合併為 1 筆 + count |
| 全行實時監控（數十系統、百萬 logs/分鐘） | ❌ 超出定位 | elk-analyser 是**觸發式事故分析**，非 stream processing |

### 最高優先的補強點

1. **`preprocessor.py` 重複事件去重**：
   相同 `(service, message)` 的重複事件合併為一筆，加 `count` 與 `last_seen` 欄位，
   800 筆重複錯誤 → 1 筆，同等語義資訊量，是 token 壓縮最有效的手段。

---

## Bobcoins 消耗估算

| 模式 | bob run 次數 | 估算 Bobcoins | 適用情境 |
|------|-------------|--------------|---------|
| 單次（三層限縮後） | 1 | ~5 | 精準觸發，~8k tokens |
| 三 source 平行 | 3 | ~15 | 60k–150k tokens |
| 三 source + Merge | 4 | ~20 | > 150k tokens |
| 八 source 平行 + Merge | 9 | ~45 | 大型多系統事故 |

> 實測值待子任務 G 完成後填入。

---

## 非目標（Out of Scope）

- **Map-Reduce + Merge bob run**：改動成本高（3.5 倍於平行路由），三層精準限縮後觸發率極低，延後評估
- 修改 `report_builder.py`：平行路由輸出格式與現有一致，報告生成無需改動
- 修改 `api.py` 的 `_analysis_lock`：外層 API 互鎖保留，不引入 API 層並行
- Bob IDE `spawn_subagent`：headless `bob run` 需 auto-approve，非自動化 pipeline 首選
- 全行實時 stream processing：elk-analyser 定位為觸發式事故根因分析

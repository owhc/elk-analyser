# ADR 0006：使用 Kibana Alerting Webhook 觸發分析
<!-- Updated: 2026-08-26 23:41:32 +0800 -->

## 狀態

已採用（Accepted）— 修訂：Kibana Alerting 為主要方案

---

## 背景

elk-analyser 需要一個機制，在 Elasticsearch 中偵測到異常日誌時，自動觸發根因分析流程。需要決定「誰來判斷何時該觸發分析」以及「如何通知 elk-analyser」。

---

## 決策

使用 **Kibana Alerting**（Rules & Connectors）設定告警規則，當符合條件時透過 **Webhook** 呼叫 `POST /analyse` API。

elk-analyser 本身**不主動輪詢 ES**，只被動等待外部觸發（Kibana Alert、手動 API 呼叫、Web UI、CLI）。

---

## 考慮過的選項

### 選項 A：定時輪詢（Scheduled Polling）— 已拒絕

elk-analyser 自行設定定時任務，每隔 N 分鐘查詢一次 ES，判斷是否有新異常。

**拒絕原因：**
- 浪費資源：大多數查詢結果為空（無異常），消耗 ES 查詢額度與 Bob Shell API token
- 重複分析：同一批異常可能被多個時間窗口重複查詢
- 時效性差：最大延遲等於輪詢間隔
- 已於 ADR-0005 決定移除 supercronic 排程器

### 選項 B：Logstash Filter Plugin — 已拒絕

在 Logstash pipeline 中加入 filter，遇到 CRITICAL 日誌時直接呼叫 HTTP output。

**拒絕原因：**
- 每筆日誌都觸發一次 API 呼叫，流量過大
- 無法做批次聚合（同一事件鏈的多條日誌應一起分析）
- Logstash 是資料管道，不應承擔業務邏輯判斷

### 選項 C：Kibana Alerting Webhook — 已採用（主要方案）

Kibana 8.x 內建 Rules & Connectors，設定查詢條件與 Webhook action。

**優點：**
- 視覺化設定，維運人員友善，無需手動寫 JSON
- 支援 throttle（避免重複觸發）
- 與 ES 查詢引擎深度整合
- Alert rule 的評估視窗可直接對應 `lookback_minutes` 參數，分析精準

**Webhook payload 設計：**

```json
{
  "to_time":          "{{date}}",
  "lookback_minutes": 5,
  "trigger":          "kibana_alert"
}
```

- `to_time`：Kibana 評估觸發當下時間
- `lookback_minutes`：對應 Kibana Alert rule 的評估視窗長度（由維運人員設定，與 rule 保持一致）
- `trigger`：記錄觸發來源，存入 `analysis_jobs.trigger_mode`

elk-analyser 收到後計算：`from_time = to_time - lookback_minutes`，確保分析範圍精準對應 Alert 評估視窗，不分析多餘的日誌。

### 選項 D：Elasticsearch Watcher API — 備選（進階場景）

透過 REST API 直接建立 Watch，適用於需要精細控制或舊版 ES（無 Kibana）環境。

**優點：**
- 支援複雜條件（multi-index、chained input、script condition）
- 可程式化部署（Infrastructure as Code）
- 不依賴 Kibana

**Watcher payload 範例（使用 ctx 時間變數）：**

```json
"body": "{\"from_time\": \"{{ctx.trigger.scheduled_time}}\", \"to_time\": \"{{ctx.execution_time}}\", \"trigger\": \"es_watcher\"}"
```

---

## 決策結果

**正式環境**：使用 **Kibana Alerting**（選項 C）作為主要觸發方式。

維運人員在 Kibana 建立 Log threshold rule，將 Alert 評估視窗長度同步填入 `lookback_minutes`，確保 elk-analyser 分析的時間範圍與 Alert 條件完全一致，避免分析過多無關日誌。

**進階場景**：若需要複雜查詢條件或 IaC 部署，使用 Elasticsearch Watcher（選項 D）作為備選。

---

## 觸發來源優先順序

| 觸發來源 | 場景 | payload 範例 |
|---------|------|-------------|
| Kibana Alert（自動） | 正式環境自動化，條件成立觸發 | `{"to_time":"{{date}}","lookback_minutes":5,"trigger":"kibana_alert"}` |
| Web UI（手動） | 臨時查詢、問題追蹤，user 自選時間區間 | `{"from_time":"2026-08-26 14:00","to_time":"2026-08-26 15:00"}` |
| CLI（`python cli.py run`） | 容器內手動執行 | `--from "2026-08-26 14:00" --to "2026-08-26 15:00"` |
| ES Watcher（備選） | 舊版 ES / 複雜條件 / IaC | `{"from_time":"{{ctx.trigger.scheduled_time}}","to_time":"{{ctx.execution_time}}","trigger":"es_watcher"}` |
| Demo inject API | 開發測試 | 注入模擬資料後立即分析 |

---

## `POST /analyse` 查詢視窗解析優先序

| 優先順序 | 條件 | from_time 來源 |
|---------|------|---------------|
| 1 | `from_time` 有值 | 直接使用（Web UI / CLI 手動指定） |
| 2 | `lookback_minutes` 有值 | `to_time - lookback_minutes`（Kibana Alert 自動觸發） |
| 3 | 兩者皆無，Checkpoint 有值 | Checkpoint 接續上次分析結束時間 |
| 4 | 三者皆無 | `now - config.checkpoint.default_lookback_minutes`（預設 60 分鐘）|

---

## 影響

- `POST /analyse` 接受 `from_time`、`to_time`、`lookback_minutes`、`trigger` 四個可選參數
- Kibana Alert throttle 設定（建議與評估視窗相同，如 `5m`）避免短時間內重複觸發
- elk-analyser 無內建去重邏輯，由 Kibana throttle 控制觸發頻率
- Web UI 手動觸發行為不變：帶 `from_time` / `to_time` 直接指定視窗

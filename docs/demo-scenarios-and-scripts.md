<!-- Created: 2026-09-16 08:18:03 +0800 -->
# Demo Scenarios & Scripts Guide

本文檔整合 `elk-analyser` 與銀行演示微服務環境的展示情境（Demo Scenarios）與執行腳本說明，涵蓋正常流量模擬、混合因果鏈異常注入、Instana APM 整合分析以及 PPTX 報告產出。

---

## 1. 系統環境與架構總覽

### 服務拓撲
- **Web UI** (`http://localhost:3001`): 前端觸發與報告檢視介面。
- **ELK Analyser Core** (`http://localhost:8080`): 分析引擎與報告生成核心（支援 REST API & CLI）。
- **Banking App** (`http://localhost:9080/banking-app`): Open Liberty + ActiveMQ Artemis (JMS MDB) + PostgreSQL。
- **PostgreSQL Database** (`banking-db:5432`): 銀行交易資料庫。
- **Elasticsearch** (`http://localhost:9200`): 結構化日誌儲存與搜尋。
- **Kibana** (`http://localhost:5601`): 視覺化監控與 Webhook 告警觸發。
- **Instana Agent** (`http://localhost:42699`): APM Traces、Call Metrics 與事件收集。

### 核心管道（Pipeline）
```
[User / Script] 
       │
       ▼
[Banking App (Liberty)] ───► [Artemis MQ] ───► [MDB] ───► [PostgreSQL]
       │
       ├─► [Logstash] ──► [Elasticsearch]
       └─► [Instana Agent] ──► [Instana Backend]
                                     │
                                     ▼
[Trigger: Web / CLI / Kibana] ──► [ELK Analyser]
                                     │
                                     ├─► Extractor (ES Logs)
                                     ├─► Instana Collector (APM Traces & Metrics)
                                     ├─► Preprocessor (Event Sequence)
                                     ├─► Bob Bridge (AI Root Cause Analysis)
                                     └─► Report Builder (PPTX with Instana Context)
```

---

## 2. 演示情境（Demo Scenarios）

### 情境 1：正常使用者旅程模擬（Happy Path Baseline）
- **目標**：模擬真實銀行用戶日常操作，產生基礎吞吐量與正常呼叫鏈（Liberty HTTP → JMS Queue → MDB → DB JDBC）。
- **流程**：
  1. 登入 (`POST /api/login`)
  2. 查詢帳戶餘額 (`GET /api/accounts/{account}`)
  3. 執行跨帳戶轉帳 (`POST /api/transfer`)
  4. 非同步佇列消費與資料庫寫入確認 (`GET /api/accounts/{target}`)
  5. 登出 (`POST /api/logout`)
- **執行腳本**：[`scripts/simulate-banking-journey.sh`](scripts/simulate-banking-journey.sh)

```bash
# 預設執行 5 次旅程，每次間隔 2 秒
bash scripts/simulate-banking-journey.sh 5 2
```

---

### 情境 2：非同步轉帳壓力與呼叫鏈測試
- **目標**：快速產生大量轉帳請求，驗證 ActiveMQ Artemis 佇列吞吐量與 MDB 非同步持久化能力。
- **執行腳本**：[`scripts/trigger-liberty-mq-db.sh`](scripts/trigger-liberty-mq-db.sh)

```bash
# 發起 10 筆轉帳請求，間隔 1 秒
bash scripts/trigger-liberty-mq-db.sh 10 1
```

---

### 情境 3：混合式可觀測性異常事件與 AI 根因分析（End-to-End Observability Incident）
- **目標**：注入跨應用層（HTTP 400）、非同步訊息層（MDB Rollback）、資料庫層（Transaction Rollback）與中介軟體（Artemis 累積錯誤）的故障鏈，並結合 Instana APM 遙測數據，透過 Bob AI 產出完整的根本原因分析（RCA）PPTX 報告。
- **事件鏈**：
  1. **HTTP 400 Validation Error**：負數或相同帳號轉帳引發驗證失敗。
  2. **HTTP 202 Accepted + MDB Exception**：傳入不存在帳戶，API 接受請求放入佇列，但 MDB 消費時觸發 `java.sql.SQLException: 來源帳號不存在` 導致交易 Rollback。
  3. **ELK 跨層關聯日誌注入**：寫入具相同 `incident_id` 與時間戳記的 Liberty、PostgreSQL 與 Artemis 告警事件。
  4. **Instana APM 收集**：Instana Agent 捕捉 Trace 錯誤與延遲指標。
  5. **觸發分析**：呼叫 `POST /analyse` 觸發 Bob AI 分析並產出投影片報告（含 Slide 3.5 Instana APM 遙測摘要）。
- **執行腳本**：[`scripts/generate-observability-incident.sh`](scripts/generate-observability-incident.sh)

```bash
# 執行故障注入（產生 12 筆異常請求並觸發分析）
bash scripts/generate-observability-incident.sh 12
```

---

## 3. 演示腳本清單與使用說明

| 腳本檔案 | 說明 | 主要參數 / 環境變數 |
|---|---|---|
| [`scripts/simulate-banking-journey.sh`](scripts/simulate-banking-journey.sh) | 模擬完整銀行登入、查帳、轉帳、確認、登出流程 | `$1`: 旅程次數 (預設 5)<br>`$2`: 間隔秒數 (預設 2)<br>`LIBERTY_BASE_URL`: API 端點 |
| [`scripts/trigger-liberty-mq-db.sh`](scripts/trigger-liberty-mq-db.sh) | 批次觸發轉帳 API，驅動 Liberty → MQ → DB 呼叫鏈 | `$1`: 轉帳次數 (預設 5)<br>`$2`: 間隔秒數 (預設 1) |
| [`scripts/generate-observability-incident.sh`](scripts/generate-observability-incident.sh) | 整合式故障注入：Java 異常 + ELK 因果鏈 + Instana 採集 + 觸發 PPTX 分析 | `$1`: 錯誤請求次數 (預設 12)<br>`INSTANA_WAIT_SECONDS`: 等待秒數 (預設 20)<br>`ANALYSE_TRIGGER`: 觸發模式 (預設 `webui_instana`) |
| [`scripts/setup-instana.sh`](scripts/setup-instana.sh) | 檢查並配置 Instana Agent 環境與組態檔 | - |
| [`scripts/export-openapi.sh`](scripts/export-openapi.sh) | 匯出 ELK Analyser 的 OpenAPI (Swagger) 規格文件 | - |

---

## 4. 常用測試與驗證指令

### 服務健康狀態檢查
```bash
# 檢查 Banking App
curl -s http://localhost:9080/banking-app/api/health | jq .

# 檢查 Elasticsearch
curl -s http://localhost:9200/_cluster/health | jq .

# 檢查 ELK Analyser
curl -s http://localhost:8080/health | jq .
```

### 透過 CLI 觸發分析
```bash
# 容器內部執行分析（指定時間範圍並啟用 Instana）
podman exec elk-analyser python cli.py run \
  --from "2026-09-15 08:00" \
  --to "2026-09-15 12:00" \
  --with-instana
```

### 透過 REST API 觸發分析
```bash
# 觸發包含 Instana 收集的分析任務
curl -sX POST http://localhost:8080/analyse \
  -H 'Content-Type: application/json' \
  -d '{
    "from_time": "2026-09-15 08:00",
    "to_time": "2026-09-15 12:00",
    "trigger": "webui_instana"
  }' | jq .
```

### 查詢任務歷史與下載報告
```bash
# 取得任務清單
curl -s http://localhost:8080/jobs | jq .

# 下載指定 Job ID 產生的 PPTX 報告
curl -O http://localhost:8080/jobs/1/report
```

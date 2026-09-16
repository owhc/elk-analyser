<!-- Updated: 2026-09-16 08:06:45 +0800 -->
# ELK Analyser

自動化日誌分析與報告生成系統。從 Elasticsearch 提取日誌，透過 Bob CLI AI 進行根因分析，自動產出 Morandi Blue Tech 主題的 PPTX 診斷報告。支援 Instana APM 整合，實現 ELK + APM 聯合可觀測性診斷。

## 功能特色

- **多元觸發方式**：Kibana Alert Webhook（自動）/ Web UI / CLI / REST API（手動）
- **AI 因果鏈推理**：Bob CLI `log-analyst` mode，跨事件時序根因分析
- **Instana APM 整合**：同時間窗口採集 Events、Endpoint Metrics（P95 latency）、Trace Summary，強化根因判斷
- **Checkpoint 接續**：無觸發排程時可接續上次分析結束點，避免遺漏
- **結構化報告**：PPTX，含圓餅圖、趨勢圖、模組排行、根因卡片；啟用 Instana 時額外生成 Slide 3.5（APM 摘要）
- **可配置欄位映射**：`config.yaml` 驅動，相容各種 ELK schema
- **Web Dashboard**：Morandi 主題深色儀表板，支援任務歷史、報告下載、Demo 注資

## 架構

```
觸發層
  Kibana Alert Webhook  POST /analyse?trigger=kibana_alert_instana
  Web UI (port 3001)    手動查詢 / ELK+Instana 合併分析 / Demo inject
  CLI (pod exec)        python cli.py run --from ... --to ... [--with-instana]
        ↓
    [可選] instana_collector.py
        Instana REST API（Events / Endpoint Metrics / Trace Summary）
        ↓
analysis_pipeline.run_analysis()
  ├── extractor.py       Elasticsearch 查詢 + Checkpoint 讀寫
  ├── preprocessor.py    事件序列建構（Trace ID / 時間窗口）+ Instana context 注入
  ├── bob_bridge.py      bob run --mode log-analyst（直接在 container 內執行）
  └── report_builder.py  python-pptx PPTX 報告生成（含 Slide 3.5 Instana APM 摘要）
        ↓
SQLite（/db/history.db — 任務歷史 + Checkpoint）
/reports/*.pptx（診斷報告，透過 GET /jobs/{id}/report 下載）
```

## 快速開始

### 1. 環境準備

```bash
cp bob-analyser/.env.example bob-analyser/.env
# 編輯 bob-analyser/.env，填入：
#   BOB_API_KEY          — Bob CLI 認證（必填）
#   INSTANA_API_TOKEN    — Instana REST API 金鑰（啟用 Instana 整合時必填）
```

### 2. 啟動 Container（Podman）

```bash
# 完整 stack（ELK + Banking Demo + Instana Agent + ELK Analyser）
bash scripts/setup-instana.sh
podman-compose -f podman-compose.yml -f podman-compose.override.yml up -d

# 或僅啟動 ELK Analyser（使用 mock 模式）
podman-compose up --build -d

# Web UI:  http://localhost:3001
# API:     http://localhost:8080
```

### 3. 觸發分析（純 ELK）

```bash
# Web UI 手動查詢（含時間選擇器）
# 開啟 http://localhost:3001

# CLI（容器內）
podman exec elk-analyser python cli.py run \
  --from "2026-01-15 08:00" --to "2026-01-15 12:00"

# REST API 手動查詢
curl -X POST http://localhost:8080/analyse \
  -H "Content-Type: application/json" \
  -d '{"from_time":"2026-01-15 08:00","to_time":"2026-01-15 12:00"}'

# REST API 模擬 Kibana Alert（lookback_minutes）
curl -X POST http://localhost:8080/analyse \
  -H "Content-Type: application/json" \
  -d '{"to_time":"2026-01-15 12:00","lookback_minutes":5,"trigger":"kibana_alert"}'
```

### 4. Instana APM 整合分析

需先確認 `config.yaml` 中 `instana.enabled: true` 且 `.env` 中 `INSTANA_API_TOKEN` 已設定。

```bash
# CLI（--with-instana flag）
podman exec elk-analyser python cli.py run \
  --from "2026-01-15 08:00" --to "2026-01-15 12:00" \
  --with-instana

# REST API（trigger 設為 webui_instana）
curl -X POST http://localhost:8080/analyse \
  -H "Content-Type: application/json" \
  -d '{"from_time":"2026-01-15 08:00","to_time":"2026-01-15 12:00","trigger":"webui_instana"}'

# Web UI — 點擊「ELK + Instana 合併分析」按鈕
# 開啟 http://localhost:3001
```

Instana 整合啟用後，PPTX 報告將包含額外的 **Slide 3.5（APM 診斷摘要）**，顯示：
- Instana Issues / Incidents（同時間窗口）
- Endpoint Metrics（calls / error rate / P95 latency）
- Trace Summary（跨服務錯誤率）

若 Instana API 不可用，系統自動降級為純 ELK 模式，報告結構不受影響。

### 5. Demo 資料注入

```bash
# 注入 WAS/MQ/PostgreSQL 模擬事故資料並立即觸發分析（時間窗口動態計算為 now-1h → now）
curl -X POST http://localhost:8080/demo/inject \
  -H "Content-Type: application/json" \
  -d '{"trigger_analyse":true}'

# 指定特定時間窗口
curl -X POST http://localhost:8080/demo/inject \
  -H "Content-Type: application/json" \
  -d '{"from_time":"2026-01-15 08:00","to_time":"2026-01-15 09:00","trigger_analyse":true}'

# 端對端驗證腳本
bash scripts/generate-observability-incident.sh 1
```

### 6. 查詢歷史 / 下載報告

```bash
# CLI
podman exec elk-analyser python cli.py history --limit 10

# API
curl http://localhost:8080/jobs
curl http://localhost:8080/jobs/1
curl http://localhost:8080/jobs/1/status   # 輪詢非同步任務狀態
curl -O http://localhost:8080/jobs/1/report  # 下載 PPTX
```

### 7. 健康檢查

```bash
curl http://localhost:8080/health
```

## 設定說明

編輯 [`bob-analyser/config/config.yaml`](bob-analyser/config/config.yaml)：

| 區塊 | 說明 |
|------|------|
| `elasticsearch.mode` | `mock`（開發）/ `local`（本機 ES）/ `remote`（生產） |
| `field_mapping` | 對應你的 ELK schema 欄位名稱（不改程式碼即可適配） |
| `filter.levels` | 分析的日誌等級（預設 ERROR / FATAL / CRITICAL）|
| `bob_bridge.mode` | Bob CLI custom mode slug（預設 `log-analyst`） |
| `checkpoint.default_lookback_minutes` | 無 Checkpoint 時的預設回溯分鐘數 |
| `instana.enabled` | `true` = 啟用 Instana APM 整合；`false` = 純 ELK 模式 |
| `instana.base_url` | Instana SaaS 端點（例：`https://ibmdevsandbox-instanaibm.instana.io`） |
| `instana.application_id` | Instana Application Perspective ID |
| `webhook.url` | 選用：分析完成後通知 Slack / Teams |

## API 端點

| 方法 | 路徑 | 說明 |
|------|------|------|
| `POST` | `/analyse` | 同步觸發分析（409 if busy，208 if 5m 內已完成） |
| `POST` | `/analyse/async` | 非同步觸發，立即回傳 `job_id` |
| `GET`  | `/jobs` | 列出分析歷史（`?limit=N`） |
| `GET`  | `/jobs/{id}` | 取得任務詳情 |
| `GET`  | `/jobs/{id}/report` | 下載 PPTX 報告 |
| `GET`  | `/jobs/{id}/status` | 輪詢非同步任務狀態 |
| `DELETE` | `/jobs/{id}` | 刪除單筆任務（同時刪除報告檔） |
| `DELETE` | `/jobs` | 批次刪除（body: `{"ids":[1,2]}`） |
| `POST` | `/demo/inject` | 注入 WAS/MQ/PostgreSQL 模擬資料（時間預設動態計算） |
| `GET`  | `/health` | 健康檢查 |

**觸發模式（`trigger` 欄位）說明：**

| trigger 值 | 說明 |
|---|---|
| `kibana_alert` | 純 ELK 分析，由 Kibana Alert 自動觸發 |
| `kibana_alert_instana` | ELK + Instana 整合分析，由 Kibana Alert 自動觸發 |
| `webui_instana` | ELK + Instana 整合分析，由 Web UI 手動觸發 |
| `on_demand` | 純 ELK 手動查詢（預設） |

## 目錄結構

```
elk-analyser/                        ← repo 根目錄（Bob workspace）
├── podman-compose.yml               # 完整 stack（ELK Analyser + japp-demo + Instana）
├── podman-compose.override.yml      # Instana Agent 擴充設定
├── AGENTS.md                        # AI agent 協作規則
├── CONTEXT.md                       # 領域詞彙表
├── README.md
├── scripts/
│   ├── setup-instana.sh             # Instana Agent 環境設定腳本
│   ├── generate-observability-incident.sh  # 端對端驗證腳本
│   ├── simulate-banking-journey.sh  # 銀行交易流程模擬
│   └── trigger-liberty-mq-db.sh    # Liberty/MQ/DB 觸發腳本
├── docs/
│   ├── adr/                         # 架構決策紀錄（ADR 0001-0006）
│   ├── arch-review/                 # 架構審查報告
│   ├── architecture-elk-integration.md
│   ├── instana-integration-plan.md  # [IMPLEMENTED] Instana 整合計劃
│   └── fake-data-sample.md          # Mock 模擬日誌資料（WAS/MQ/PostgreSQL 4 場景）
├── web/                             ← Nginx 靜態前端（port 3001）
│   ├── Dockerfile
│   ├── index.html                   # Morandi 主題 Dashboard
│   └── nginx.conf
├── japp-demo/                       ← Banking Demo 應用（WAS Liberty + Artemis + PostgreSQL）
└── bob-analyser/                    ← elk-analyser container build context
    ├── Dockerfile
    ├── podman-entrypoint.sh
    ├── requirements.txt
    ├── .env.example
    ├── analysis_pipeline.py         # 四階段 pipeline 協調器
    ├── api.py                       # FastAPI REST API（port 8080）
    ├── cli.py                       # Click CLI 入口
    ├── instana_collector.py         # Instana REST API 封裝（Events/Metrics/Traces）
    ├── config_loader.py             # AppConfig — config.yaml 唯一解析 seam
    ├── gen_fake_data.py             # Mock 資料生成工具
    ├── analyser/
    │   ├── extractor.py             # ES 日誌提取 + Checkpoint
    │   ├── preprocessor.py          # 事件序列建構（含 Instana context 注入）
    │   ├── bob_bridge.py            # Bob CLI 橋接（subprocess）
    │   └── report_builder.py        # PPTX 報告生成（含 Slide 3.5 Instana APM）
    ├── bob-custom-modes/
    │   ├── custom_modes.yaml        # bob 2.x globalScopeRoot（image build 時複製）
    │   └── log-analyst.yaml         # log-analyst mode 定義
    ├── config/
    │   └── config.yaml              # 主設定檔（含 instana: 區塊）
    ├── db/
    │   └── schema.sql               # SQLite schema
    ├── mock/
    │   └── sample_logs.json         # 模擬 ELK 日誌資料
    └── logs/                        # 事件序列 JSON 暫存（bob_workspace volume）
```

## Bob Custom Mode 部署

`bob-analyser/bob-custom-modes/custom_modes.yaml` 在 image build 時複製到
`/root/.bob/settings/custom_modes.yaml`（bob 2.x `globalScopeRoot`）。
更新 `log-analyst` mode 定義後需 **重新 build container**：

```bash
podman-compose up --build -d elk-analyser
```

## 架構決策紀錄

| ADR | 決策 |
|-----|------|
| [0001](docs/adr/0001-python-bob-bridge-architecture.md) | Python 作資料管道、Bob CLI 作 AI 推理引擎 |
| [0002](docs/adr/0002-sqlite-for-analysis-history.md) | SQLite 儲存分析歷史與 Checkpoint |
| [0003](docs/adr/0003-checkpoint-over-sliding-window.md) | Checkpoint 接續而非滑動視窗 |
| [0004](docs/adr/0004-shared-volume-for-bob-bridge.md) | Bob Bridge 透過共享 Volume + @filename 傳遞 |
| [0005](docs/adr/0005-container-crond-for-scheduler.md) | 移除 supercronic，改為外部事件觸發 |
| [0006](docs/adr/0006-elasticsearch-alert-trigger.md) | Kibana Alerting Webhook 作為主要自動觸發機制 |

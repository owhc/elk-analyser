# Grafana 整合架構規劃 (Grafana Integration Plan)
<!-- Created: 2026-09-16 10:15:00 +0800 -->

## Top-Level Overview
本計劃旨在將 **Grafana** 深度整合至現有 ELK Analyser 系統中。
- **單一統一入口**：透過現有 Nginx (`elk-analyser-web` port 3001) 作為唯一前端 Gateway，反向代理 `/grafana/`、`/web/` (原 Web UI 保留備用與說明) 及 `/api/` (後端 API)。
- **全自動 Provisioning**：於 Podman/Docker 啟動時自動預置（Provision）所有 Data Sources（Elasticsearch、PostgreSQL/SQLite、Instana）與預設監控/AI 診斷儀表板。
- **閉環告警分析（Alerting ➔ AI Root Cause Analysis）**：配置 Grafana Alerting Contact Point Webhook 直接對接 `POST http://elk-analyser:8080/analyse`，當系統異常時自動觸發 Bob AI 診斷並產出報告。

---

## Sub-Tasks

### Task 1: Nginx 統一入口配置與 Web UI 路徑調整
- **Intent**: 將 Nginx 設定為統一反向代理入口，支援 `/grafana/`、`/web/` 以及 `/api/` 路由，並修正原 Web UI 靜態資源路徑以支援子路徑運作。
- **Expected Outcomes**:
  - 存取 `http://localhost:3001/` 自動導向 `/grafana/`（或首頁入口）。
  - 存取 `http://localhost:3001/web/` 可正常使用原本的 Web UI 進行備用與說明。
  - 存取 `http://localhost:3001/grafana/` 可直接開啟 Grafana。
  - 存取 `http://localhost:3001/api/*` 正確轉發至 `http://elk-analyser:8080/*`。
- **Todo List**:
  1. [ ] 更新 `web/nginx.conf` 新增 `/grafana/` 代理規則（包含 WebSocket `Upgrade` 支援）與 `/web/` 靜態路徑。
  2. [ ] 檢視並微調 `web/index.html` 內 API 呼叫路徑與資源引用，確保在 `/web/` 子目錄下正常運作。
- **Relevant Context**:
  - `web/nginx.conf`
  - `web/index.html`
- **Status**: `[ ] pending`

---

### Task 2: Grafana Provisioning 配置（Data Sources & Alerting Webhook）
- **Intent**: 建立 Grafana 自動預置配置檔，讓容器一啟動即完成所有資料源與告警聯絡點設定，達到 Zero-Config 啟動。
- **Expected Outcomes**:
  - 自動載入 Elasticsearch (`http://elasticsearch:9200`, index: `banking-logs-*`)。
  - 自動載入 PostgreSQL (`banking-db:5432`, database: `bankdb`) 與 SQLite / Infinity 插件（如適用）。
  - 自動配置 Contact Point Webhook 指向 `http://elk-analyser:8080/analyse`。
  - 自動配置 Notification Policy 與預設 Alert Rule（如日誌錯誤率過高、回應延遲超標）。
- **Todo List**:
  1. [ ] 建立 `grafana/provisioning/datasources/datasources.yaml`（定義 ES、PostgreSQL、Instana 等連線）。
  2. [ ] 建立 `grafana/provisioning/alerting/alerting.yaml`（定義 Webhook contact point 與 alert rules）。
- **Relevant Context**:
  - `bob-analyser/config/config.yaml`（資料庫連線與 ES 配置）
  - `docs/arch-review/architecture-review-20260916.html`
- **Status**: `[ ] pending`

---

### Task 3: 預設整合儀表板設計與 Provisioning
- **Intent**: 預置一站式可觀測性與 AI 分析儀表板，將日誌趨勢、APM 指標、歷史 AI 診斷任務與報告下載連結統一呈現。
- **Expected Outcomes**:
  - 提供預置儀表板 `grafana/dashboards/banking-observability-ai.json`。
  - 儀表板包含：
    1. Banking App 交易日誌趨勢與錯誤分佈（Elasticsearch）。
    2. WAS Liberty & PostgreSQL 資源監控指標（PostgreSQL / Instana）。
    3. ELK Analyser 任務歷史清單與一鍵下載 PPTX 報告的 Data Link。
    4. 手動觸發分析與 Demo 注入操作連結。
- **Todo List**:
  1. [ ] 建立 `grafana/provisioning/dashboards/dashboards.yaml`（指定載入目錄）。
  2. [ ] 建立 `grafana/dashboards/banking-observability-ai.json` 儀表板定義。
- **Relevant Context**:
  - `grafana/provisioning/dashboards/`
  - `web/index.html`（提取原有任務清單與指標需求）
- **Status**: `[ ] pending`

---

### Task 4: Podman / Docker Compose 與環境變數整合
- **Intent**: 將 Grafana 服務正式加入容器編排中，掛載 Provisioning 設定並串接網路。
- **Expected Outcomes**:
  - 在 `podman-compose.yml` 新增 `grafana` 服務（指定內部 IP/網路，設定 `GF_SERVER_ROOT_URL=%(protocol)s://%(domain)s:%(http_port)s/grafana/` 與 `GF_SERVER_SERVE_FROM_SUB_PATH=true`）。
  - 讓 `web` (Nginx) 容器與 `grafana` 容器在 `elk-analyser-network` 內部通信。
  - 執行 `podman-compose up -d` 即可一鍵啟動全套架構（Web + Grafana + API + ELK + Banking Demo）。
- **Todo List**:
  1. [ ] 修改 `podman-compose.yml` 新增 `grafana` 服務定義、環境變數與 volume 掛載。
  2. [ ] 確保 `web` 服務的 depends_on 包含 `grafana`。
  3. [ ] 檢查相關網路配置（`elk-analyser-network` / `japp-network`）。
- **Relevant Context**:
  - `podman-compose.yml`
  - `podman-compose.override.yml`
- **Status**: `[ ] pending`

---

### Task 5: 端對端驗證與文檔更新
- **Intent**: 驗證全鏈路運作（Nginx 路由、Grafana 儀表板、Alert Webhook 觸發 AI 診斷、Web UI 備用存取），並更新相關架構與操作文件。
- **Expected Outcomes**:
  - 透過 `scripts/generate-observability-incident.sh` 驗證異常觸發 Grafana Alert ➔ ELK Analyser 自動產出 PPTX 報告。
  - 驗證 Web UI 在 `http://localhost:3001/web/` 正常提供操作與說明。
  - 更新 `README.md` 與 `AGENTS.md` 反映 Grafana 整合架構。
- **Todo List**:
  1. [ ] 測試 `/grafana/`、`/web/`、`/api/` 各路由連通性。
  2. [ ] 模擬觸發告警，驗證 Grafana Alert Webhook 成功叫起 `POST /analyse` 且產出報告。
  3. [ ] 更新 `README.md` 與 `AGENTS.md` 中的 Quick Reference 與架構說明。
- **Relevant Context**:
  - `README.md`
  - `AGENTS.md`
  - `scripts/generate-observability-incident.sh`
- **Status**: `[ ] pending`

# Banking Demo — Podman 多容器示範專案

> **整合部署說明：** 本專案已與 elk-analyser 整合至根目錄的 `podman-compose.yml`，
> 一次 `podman-compose up -d` 即可啟動全部 7 個服務。
> 本目錄（`japp-demo/`）僅存放原始碼與設定檔，**不需要**在此目錄執行 podman-compose。
<!-- Created: 2026-09-02 16:20:51 +0800 -->

繁體中文銀行示範應用程式，整合 WAS Liberty、ActiveMQ Artemis（IBM MQ Light 相容）、PostgreSQL 16 與 ELK Stack 日誌分析平台，以 Podman Compose 編排。

---

## 架構概覽

```
┌─────────────────────────────────────────────────────────────┐
│  container-japp（10.89.2.20）                               │
│  ┌─────────────┐   JMS    ┌──────────────┐  JDBC  ┌──────────┐ │
│  │ WAS Liberty │ ──────→  │  ActiveMQ    │ ─────→ │PostgreSQL│ │
│  │  :9080      │          │   Artemis    │        │  :5432   │ │
│  │ banking-app │          │  :5672/:8161 │        └──────────┘ │
│  └─────────────┘          └──────────────┘                  │
│         │ JSON 結構化日誌                                    │
└─────────┼───────────────────────────────────────────────────┘
          │ shared volume: app_logs
┌─────────┼───────────────────────────────────────────────────┐
│  ELK Stack（japp-network）                                  │
│  ┌──────────────┐  ┌──────────┐  ┌──────────────────────┐  │
│  │Elasticsearch │  │ Logstash │  │       Kibana         │  │
│  │   :9200      │  │  :5044   │  │       :5601          │  │
│  └──────────────┘  └──────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
          ↑ elk-analyser（10.89.1.10）透過 japp-network 讀取 ES
```

### 服務清單

| 服務 | 容器名稱 | 主機端口 | 說明 |
|------|----------|----------|------|
| WAS Liberty | container-japp | 9080, 9443 | 銀行網站 HTTP/HTTPS |
| ActiveMQ Artemis | container-japp | 5672 | JMS / AMQP broker |
| Artemis 管理 Web | container-japp | 8161 | 管理 Web UI |
| PostgreSQL 16 | banking-db | 50000 | JDBC（容器內 5432） |
| Elasticsearch | japp-elasticsearch | 9200 | REST API |
| Logstash | japp-logstash | 5044 | Beats / 日誌收集輸入 |
| Kibana | japp-kibana | 5601 | 視覺化 Web UI |

---

## 先決條件

| 工具 | 最低版本 | 安裝確認 |
|------|----------|----------|
| Podman | 4.x | `podman --version` |
| podman-compose | 1.x | `podman-compose --version` |
| Maven | 3.9+ | `mvn --version` |
| Java | 17+ | `java --version` |
| 可用記憶體 | 4 GB+ | — |

---

## 快速啟動

### 步驟 1：編譯 banking-app WAR

```bash
cd banking-app
mvn clean package -DskipTests
cd ..
```

產出位置：`banking-app/target/banking-app.war`

> Liberty 以 `dropins` volume mount 方式熱部署，**無需重建 image**。

---

### 步驟 2：建立共享 Podman Network（首次啟動）

```bash
podman network create japp-network --subnet 10.89.2.0/24
```

驗證：

```bash
podman network ls | grep japp-network
```

---

### 步驟 3：設定環境變數（選用）

預設值已在 `podman-compose.yml` 的 `${VAR:-default}` 中設定，無需另建 `.env`。
如需自訂，複製範本：

```bash
# 目前無獨立 .env 範本；直接編輯以下變數或 export
export DB_PASS=your-secure-password
export ARTEMIS_PASS=your-mq-password
```

主要可設定變數：

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `LIBERTY_HTTP_PORT` | `9080` | Liberty HTTP 端口 |
| `LIBERTY_HTTPS_PORT` | `9443` | Liberty HTTPS 端口 |
| `ARTEMIS_PORT` | `5672` | Artemis AMQP 端口 |
| `ARTEMIS_MGMT_PORT` | `8161` | Artemis 管理端口 |
| `DB_PORT` | `50000` | PostgreSQL 主機端口（映射至 5432） |
| `DB_NAME` | `bankdb` | 資料庫名稱 |
| `DB_USER` | `db2inst1` | 資料庫使用者 |
| `DB_PASS` | `db2admin123` | 資料庫密碼 |
| `ARTEMIS_USER` | `admin` | Artemis 使用者 |
| `ARTEMIS_PASS` | `admin123` | Artemis 密碼 |
| `ES_JAVA_OPTS` | `-Xms256m -Xmx512m` | Elasticsearch JVM 堆大小 |
| `ES_PORT` | `9200` | Elasticsearch 端口 |
| `KIBANA_PORT` | `5601` | Kibana 端口 |

---

### 步驟 4：啟動所有服務

```bash
cd japp-demo
podman-compose up -d
```

建議啟動順序（podman-compose `depends_on` 已設定）：

```
banking-db → container-japp
elasticsearch → logstash → (kibana 平行啟動)
```

---

### 步驟 5：等待服務就緒

| 服務 | 預計啟動時間 | 驗證指令 |
|------|-------------|----------|
| PostgreSQL 16 | 10–30 秒 | `podman logs banking-db \| tail -5` |
| Elasticsearch | 30–60 秒 | `curl http://localhost:9200/_cluster/health` |
| WAS Liberty | 60–120 秒 | `curl http://localhost:9080/banking-app/api/health` |
| Kibana | 60–120 秒 | `curl http://localhost:5601/api/status` |

---

### 步驟 6：驗證銀行應用程式

```bash
# API 健康檢查
curl http://localhost:9080/banking-app/api/health

# 開啟銀行網站（瀏覽器）
# http://localhost:9080/banking-app/static/login.html
```

---

## 初始測試帳號

| 帳號 | 密碼 | 銀行帳號 | 戶名 | 餘額 |
|------|------|----------|------|------|
| user001 | pass001 | ACC001 | 王大明 | NT$100,000 |
| user002 | pass002 | ACC002 | 李小美 | NT$50,000 |
| user003 | pass003 | ACC003 | 張志偉 | NT$75,000 |

---

## 銀行功能說明

| 功能 | 路徑 | 說明 |
|------|------|------|
| 登入 | `/banking-app/static/login.html` | 帳號密碼驗證，Session 管理 |
| 帳戶總覽 | `/banking-app/static/dashboard.html` | 顯示餘額與最近 10 筆交易 |
| 轉帳 | `/banking-app/static/transfer.html` | 發起 MQ 訊息驅動轉帳 |
| 健康檢查 | `/banking-app/api/health` | Liberty 狀態回傳 |

### MQ 訊息驅動架構

```
HTTP 請求 → Spring MVC Controller
                │
                ▼
        JmsTemplate.send()
        （透過 Liberty JNDI ConnectionFactory）
                │ AMQP
                ▼
        ActiveMQ Artemis 佇列
        （banking.transfer.queue）
                │
                ▼
        @MessageDriven MDB
        BankingTransferMDB
                │ JDBC
                ▼
        PostgreSQL 16 bankdb
        （accounts / transactions 表格）
```

---

## 日誌結構

所有服務的日誌統一寫入 `app_logs` named volume（掛載至 `/logs`），由 Logstash 讀取並解析：

| 日誌來源 | 路徑（容器內） | Index Pattern |
|----------|--------------|---------------|
| Liberty Access Log | `/logs/liberty/access.log` | `banking-logs-liberty-*` |
| Liberty Messages Log | `/logs/liberty/messages.log` | `banking-logs-liberty-*` |
| Artemis MQ Log | `/logs/artemis/artemis.log` | `banking-logs-mq-*` |
| PostgreSQL Server Log | `/logs/postgres/postgresql-*.log` | `banking-logs-postgres-*` |

Logstash 為每筆日誌加入以下 metadata 欄位：

| 欄位 | 說明 | 範例值 |
|------|------|--------|
| `service` | 日誌來源服務 | `liberty`, `artemis`, `postgresql` |
| `container` | 容器名稱 | `container-japp` |
| `log_type` | 日誌類型 | `access`, `application`, `diagnostic` |

---

## Kibana 設定（首次使用）

1. 開啟 **http://localhost:5601**
2. 進入 **Stack Management → Index Patterns（或 Data Views）**
3. 建立 Index Pattern：`banking-logs-*`，Time Field：`@timestamp`
4. 進入 **Discover** 查看即時日誌

---

## Instana Agent 注入（選用）

`podman-compose.override.yml` 提供 Instana agent 注入設定，無需重建 image：

```bash
# 準備 Instana agent JAR（由 Instana 後台下載）
mkdir -p /opt/instana/agent
cp instana-java-agent.jar /opt/instana/agent/

# 以 override 啟動
podman-compose -f podman-compose.yml -f podman-compose.override.yml up -d
```

注入原理：透過 `JAVA_TOOL_OPTIONS` 環境變數在 JVM 啟動時載入 Instana Java agent JAR。

---

## 整合現有 ELK Analyser

`elk-analyser` 服務（根目錄 `podman-compose.yml`）可透過 `japp-network` 直接查詢
`japp-demo` 的 Elasticsearch。

詳細步驟請參閱：[docs/connect-elk-analyser-to-container-b.md](../docs/connect-elk-analyser-to-container-b.md)

快速摘要：

```bash
# elk-analyser 已更新 config.yaml：
#   elasticsearch.host: "elasticsearch"
#   elasticsearch.index_pattern: "banking-logs-*"

# 從根目錄重啟 elk-analyser 使其加入 japp-network
cd ..
podman-compose restart elk-analyser

# 驗證連線
podman exec elk-analyser wget -qO- http://elasticsearch:9200/_cluster/health
```

---

## 停止服務

```bash
# 停止並保留 volumes（資料不刪除）
podman-compose down

# 停止並刪除所有 volumes（清除資料）
podman-compose down -v
```

---

## 目錄結構

```
japp-demo/
├── podman-compose.yml          # 主 Compose 文件
├── podman-compose.override.yml # Instana agent 注入設定（選用）
├── banking-app/                # Spring Boot WAR 原始碼（Maven 專案）
│   ├── pom.xml
│   └── src/
├── container-japp/
│   ├── Containerfile           # WAS Liberty + Artemis image 建置
│   ├── liberty-config/
│   │   ├── server.xml          # Liberty JMS/JDBC/log 設定
│   │   └── bootstrap.properties
│   ├── mq-config/              # Artemis broker 設定
│   ├── postgres-config/
│   │   └── schema-postgres.sql # 銀行資料庫初始化 SQL
│   └── supervisord.conf        # 多行程管理（Liberty + Artemis）
└── container-b/
    ├── elasticsearch/
    │   └── elasticsearch.yml
    ├── logstash/
    │   ├── pipeline.conf       # 日誌解析 pipeline
    │   └── logstash.yml
    └── kibana/
        └── kibana.yml
```

---

## 故障排除

### PostgreSQL 啟動失敗

```bash
podman logs banking-db
```

### Liberty 找不到 WAR 檔案

確認 WAR 已編譯：

```bash
ls -la banking-app/target/banking-app.war
# 若不存在：
cd banking-app && mvn clean package -DskipTests
```

### Elasticsearch 啟動失敗（`max virtual memory areas`）

```bash
# 提升 vm.max_map_count（需 root）
sudo sysctl -w vm.max_map_count=262144

# 永久生效
echo 'vm.max_map_count=262144' | sudo tee -a /etc/sysctl.conf
```

### 查看服務日誌

```bash
podman logs container-japp        # Liberty + Artemis
podman logs banking-db            # PostgreSQL
podman logs japp-elasticsearch    # Elasticsearch
podman logs japp-logstash         # Logstash
podman logs japp-kibana           # Kibana
```

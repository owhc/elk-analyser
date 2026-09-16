# Container-B — ELK 日誌堆疊說明文件
<!-- Updated: 2026-09-15 19:56:07 +0800 -->

本目錄包含 container-b 的三個服務設定：Elasticsearch、Logstash、Kibana，
共同組成銀行示範專案的日誌收集與視覺化平台。

---

## 服務架構

```
container-japp                container-b
   │                             │
   │  共享 volume: app_logs      │
   ├── /logs/liberty/  ───────→ Logstash (file input)
   ├── /logs/artemis/  ───────→     │
   └── /logs/postgres/ ───────→     ↓
                               Elasticsearch :9200
                                      ↑
                               Kibana :5601
                                      ↑
                          elk-analyser (外部連線)
```

---

## 服務說明

### 1. Elasticsearch

| 項目 | 說明 |
|------|------|
| 官方 Image | `docker.elastic.co/elasticsearch/elasticsearch:8.13.4` |
| 對外 Port | `9200`（HTTP REST API） |
| 設定檔 | `elasticsearch/elasticsearch.yml` |
| 模式 | 單節點（`discovery.type: single-node`） |
| 安全性 | 已停用（`xpack.security.enabled: false`，Demo 環境） |
| 記憶體 | `ES_JAVA_OPTS="-Xms256m -Xmx512m"`（透過 .env 設定） |
| 停用功能 | 機器學習（ML）、監控收集（monitoring.collection） |

**索引命名規則：**

| 索引 | 對應日誌來源 |
|------|------------|
| `banking-logs-liberty-YYYY.MM.dd` | WAS Liberty 應用程式日誌 |
| `banking-logs-artemis-YYYY.MM.dd` | ActiveMQ Artemis 訊息佇列日誌 |
| `banking-logs-postgres-YYYY.MM.dd` | PostgreSQL 16 資料庫伺服器日誌 |

---

### 2. Logstash

| 項目 | 說明 |
|------|------|
| 官方 Image | `docker.elastic.co/logstash/logstash:8.13.4` |
| 對外 Port | `5044`（Beats 輸入，備用）、`9600`（API/健康檢查） |
| Pipeline 設定 | `logstash/pipeline.conf` |
| 主要設定 | `logstash/logstash.yml` |

**日誌解析邏輯：**

| 來源 | 格式 | 時間欄位 | Grok Pattern |
|------|------|---------|-------------|
| Liberty | JSON（每行一個事件） | `ibm_datetime` | 直接 codec json 解析 |
| Artemis | Log4j 文字 | `log_time` | `TIMESTAMP_ISO8601 LOGLEVEL [logger] message` |
| PostgreSQL | logging_collector stderr | `postgres_timestamp` | `TIMESTAMP_ISO8601 TIMEZONE [PID] user@db LEVEL: detail` |

**共通輸出欄位：**

| 欄位 | 說明 |
|------|------|
| `@timestamp` | 事件發生時間（從原始日誌解析） |
| `container` | 固定值 `container-japp` |
| `environment` | 固定值 `demo` |
| `service` | `was-liberty` / `activemq-artemis` / `postgresql` |
| `log_type` | `application` / `messaging` / `database` |
| `[log][level]` | 日誌等級（ELK Analyser field mapping 對應） |
| `message` | 日誌主要訊息 |

---

### 3. Kibana

| 項目 | 說明 |
|------|------|
| 官方 Image | `docker.elastic.co/kibana/kibana:8.13.4` |
| 對外 Port | `5601`（Web UI） |
| 設定檔 | `kibana/kibana.yml` |
| ES 連線 | `http://elasticsearch:9200`（內部服務名稱解析） |
| 安全性 | 已停用（Demo 環境） |

---

## Kibana 初始設定步驟

容器啟動後，首次訪問 Kibana（http://localhost:5601）需完成以下設定：

### 建立 Index Pattern

1. 前往 **Management → Stack Management → Index Patterns**
2. 點擊 **Create index pattern**
3. 輸入 `banking-logs-*`（萬用字元匹配所有銀行日誌索引）
4. 時間欄位選擇 `@timestamp`
5. 點擊 **Create index pattern**

### 確認 Field Mapping

建立 Index Pattern 後，確認以下欄位存在於 Kibana Fields 列表：

| 欄位名稱 | 類型 | 說明 |
|---------|------|------|
| `@timestamp` | date | 事件時間，所有視覺化的時間軸基準 |
| `log.level` | keyword | 日誌等級（INFO/WARN/ERROR） |
| `message` | text | 日誌訊息內容 |
| `service` | keyword | 服務名稱，用於過濾不同來源 |
| `log_type` | keyword | 日誌類型（application/messaging/database） |
| `container` | keyword | 固定值 container-japp |
| `environment` | keyword | 固定值 demo |

### 建立基本 Dashboard（可選）

前往 **Analytics → Dashboard → Create dashboard**，建議加入：

- **長條圖**：按 `service` 分組的日誌數量
- **時間序列折線圖**：每分鐘日誌事件數
- **資料表**：最近 20 筆 ERROR 等級日誌

---

## ELK Analyser 對接設定

現有 ELK Analyser 的 `bob-analyser/config/config.yaml` 需更新 field_mapping 對應 Logstash 輸出欄位：

```yaml
elasticsearch:
  mode: local
  host: "localhost"          # 或 Podman 網路內的服務名稱 elasticsearch
  port: 9200
  index_pattern: "banking-logs-*"
  username: ""               # 安全性已停用，留空
  password: ""

field_mapping:
  timestamp: "@timestamp"
  level: "log.level"         # Logstash 統一輸出至此欄位
  message: "message"
  service: "service"         # was-liberty / activemq-artemis / db2-express
```

> **注意：** Logstash pipeline 已確保所有來源的 `log.level` 欄位統一命名，  
> 與 ELK Analyser 預設 `field_mapping` 完全對應，無需修改 ELK Analyser 程式碼。

---

## 常見問題排解

### Elasticsearch 未啟動

```bash
# 確認 ES 狀態
curl http://localhost:9200/_cluster/health?pretty

# 查看容器日誌
podman logs container-b-elasticsearch-1
```

### Logstash 未收到日誌

確認：
1. `app_logs` volume 是否已掛載於 container-japp（`/logs/`）
2. Liberty 是否已啟動並產生 `/logs/liberty/messages.log`
3. Logstash sincedb 檔案位置：`/usr/share/logstash/data/sincedb_*`

```bash
# 手動確認 volume 掛載
podman volume inspect app_logs

# 查看 Logstash pipeline 狀態
curl http://localhost:9600/_node/stats/pipelines?pretty
```

### Kibana 無法連線到 Elasticsearch

確認 `kibana.yml` 的 `elasticsearch.hosts` 與 Podman 網路內的服務名稱一致：

```bash
# 確認服務名稱解析
podman exec container-b-kibana-1 curl http://elasticsearch:9200/
```

---

## 端口參考

| 服務 | 容器端口 | 主機映射端口 | 說明 |
|------|---------|------------|------|
| Elasticsearch | 9200 | 9200 | REST API |
| Logstash | 9600 | 9600 | 監控 API |
| Logstash | 5044 | 5044 | Beats 輸入（備用） |
| Kibana | 5601 | 5601 | Web UI |

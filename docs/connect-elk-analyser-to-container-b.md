# 將 ELK Analyser 對接至 japp-demo ELK Stack
<!-- Created: 2026-09-02 16:20:51 +0800 -->
<!-- status: IMPLEMENTED — podman-compose.yml 整合完成；elk-analyser 已加入 japp-network，ES mode=local -->

## 概覽

> **整合部署已完成**：根目錄 `podman-compose.yml` 已整合所有服務，
> 一次 `podman-compose up -d` 即可啟動全部 7 個服務，包含 elk-analyser 與 japp-demo ELK Stack。
> 本文件僅供排查個別服務問題或手動驗證連線時參考。

本文件說明 `elk-analyser` 如何透過 `japp-network` 連接至 japp-demo 的 Elasticsearch，
讓 `elk-analyser` 可以分析 banking-demo 應用程式產生的日誌。

```
elk-analyser（10.89.1.10）
      │  japp-network（共享橋接網路）
      ▼
elasticsearch（japp-demo 服務，:9200）
      ▲
logstash（解析 app_logs volume 日誌後寫入）
      ▲
container-japp（Liberty / Artemis / PostgreSQL 日誌寫入 app_logs volume）
```

---

## 先決條件

| 要求 | 說明 |
|------|------|
| Podman >= 4.x | `podman --version` |
| podman-compose >= 1.x | `podman-compose --version` |
| `japp-demo` 服務已啟動 | Elasticsearch、Logstash、Kibana 正常運作 |
| `elk-analyser` 服務已啟動或待啟動 | 本根目錄 `podman-compose.yml` |

---

## 步驟

### 步驟 1：一鍵啟動（整合版）

根目錄的 `podman-compose.yml` 已包含所有服務，直接執行：

```bash
# 在 elk-analyser 根目錄執行
podman-compose up -d
```

Podman Compose 會自動建立 `japp-network`（subnet 10.89.2.0/24）及 `elk-analyser-network`，
無需手動建立 network。

---

### 步驟 2：等待 Elasticsearch 就緒

```bash
# 在根目錄
cd /path/to/elk-analyser
```

等待 Elasticsearch 就緒（約 30–60 秒）：

```bash
curl -s http://localhost:9200/_cluster/health | python3 -m json.tool
# "status" 應顯示 "green" 或 "yellow"
```

---

### 步驟 3：啟動（或重啟）elk-analyser

根目錄的 `podman-compose.yml` 已修改，`elk-analyser` service 會加入 `japp-network`：

```bash
# 從專案根目錄執行
podman-compose up -d

# 若服務已在運行，重啟以套用新網路設定
podman-compose restart elk-analyser
```

---

### 步驟 4：驗證容器網路連線

```bash
# 確認 elk-analyser 可解析 elasticsearch hostname
podman exec elk-analyser wget -qO- http://elasticsearch:9200/_cluster/health

# 確認 banking-logs-* 索引已存在（需 container-japp 已產生日誌）
podman exec elk-analyser wget -qO- "http://elasticsearch:9200/_cat/indices/banking-logs-*?v"
```

---

### 步驟 5：在 Kibana 建立 Index Pattern（首次使用）

開啟 Kibana：**http://localhost:5601**

1. 進入 **Stack Management** → **Index Patterns**（或 **Data Views**，視版本而定）
2. 點擊 **Create index pattern**
3. Index pattern name 輸入：`banking-logs-*`
4. Time field 選擇：`@timestamp`
5. 點擊 **Create index pattern** 儲存

> japp-demo 的 Logstash pipeline 已自動為每個日誌來源加上 `service`、`container`、
> `log_type` 等 metadata 欄位，無需額外設定。

---

### 步驟 6：確認 ELK Analyser 設定

`bob-analyser/config/config.yaml` 已更新為以下設定（已套用，無需手動修改）：

```yaml
elasticsearch:
  mode: local
  host: "elasticsearch"        # japp-network 內的 ES service hostname
  port: 9200
  scheme: "http"
  index_pattern: "banking-logs-*"
  timeout: 30

field_mapping:
  timestamp: "@timestamp"
  level: "log.level"
  message: "message"
  service: "service"           # 對應 Logstash mutate add_field 的欄位名稱
  trace_id: "trace.id"
  host: "host.name"
```

---

### 步驟 7：觸發分析（範例）

```bash
# 分析最近 1 小時的日誌
curl -s -X POST http://localhost:8080/analyse \
  -H "Content-Type: application/json" \
  -d '{
    "from_time": "'$(date -d '1 hour ago' '+%Y-%m-%d %H:%M')'",
    "to_time":   "'$(date '+%Y-%m-%d %H:%M')'"
  }' | python3 -m json.tool

# 查看分析結果
curl -s http://localhost:8080/history | python3 -m json.tool
```

---

## 端口對照表

| 服務 | 容器名稱 | 主機端口 | 容器端口 | 說明 |
|------|----------|----------|----------|------|
| WAS Liberty | container-japp | 9080 | 9080 | 銀行網站 HTTP |
| WAS Liberty HTTPS | container-japp | 9443 | 9443 | 銀行網站 HTTPS |
| ActiveMQ Artemis | container-japp | 5672 | 5672 | JMS / AMQP |
| Artemis 管理 Web | container-japp | 8161 | 8161 | Artemis 管理 UI |
| PostgreSQL | banking-db | 50000 | 5432 | JDBC |
| Elasticsearch | elasticsearch | 9200 | 9200 | REST API |
| Kibana | kibana | 5601 | 5601 | Web UI |
| Logstash Beats | logstash | 5044 | 5044 | Filebeat 輸入 |
| elk-analyser API | elk-analyser | 8080 | 8080 | FastAPI REST |
| elk-analyser Web | web | 3001 | 80 | Nginx 靜態前端 |

---

## 初始測試帳號（banking-app）

| 帳號 | 密碼 | 銀行帳號 | 戶名 |
|------|------|----------|------|
| user001 | pass001 | ACC001 | 王大明 |
| user002 | pass002 | ACC002 | 李小美 |
| user003 | pass003 | ACC003 | 張志偉 |

---

## 故障排除

### elk-analyser 無法解析 `elasticsearch` hostname

確認兩個 compose 服務都已加入 `japp-network`：

```bash
podman network inspect japp-network | grep -A5 '"Containers"'
```

應同時看到 `elk-analyser` 與 `elasticsearch` 容器。

若缺少，手動將容器連接至網路：

```bash
podman network connect japp-network elk-analyser
```

---

### Kibana 顯示 `banking-logs-*` 無資料

1. 確認 Logstash 正在讀取日誌：
   ```bash
   podman logs japp-demo_logstash_1 2>&1 | tail -50
   ```

2. 確認 `app_logs` volume 有日誌檔案：
   ```bash
   podman volume inspect japp-demo_app_logs
   # 找到 Mountpoint 後 ls 查看
   sudo ls $(podman volume inspect japp-demo_app_logs --format '{{.Mountpoint}}')
   ```

3. 確認 container-japp 正常運作並產生日誌：
   ```bash
   podman logs container-japp 2>&1 | tail -30
   ```

---

### ES 回傳 401 Unauthorized

`japp-demo` 的 Elasticsearch 已在 `podman-compose.yml` 中設定
`xpack.security.enabled=false`，正常情況下不需要驗證。

若仍出現 401，請確認 `japp-demo/.env` 中的 ES 安全設定，
或在 `bob-analyser/config/config.yaml` 中補充 `username` / `password`。

# Multi-Container Banking Demo + ELK Integration Plan
<!-- Created: 2026-08-28 00:00:00 +0800 -->
<!-- status: IMPLEMENTED — japp-demo（WAS Liberty/Artemis/PostgreSQL/ELK Stack）完整整合至 podman-compose.yml -->

## 頂層概覽

**目標：** 建立一個以 Podman Compose 編排的多容器專案，包含：
- **container-japp**：WAS Liberty + ActiveMQ Artemis（MQ Light 相容）+ Db2 Express，部署一個繁體中文銀行網站 Spring Boot WAR 應用程式
- **container-b**：ELK Stack（Elasticsearch + Logstash + Kibana），收集 container-japp 的所有日誌

整合現有 ELK Analyser（`elk-analyser` 容器）使其對接 container-b 的 Elasticsearch/Kibana，透過修改 `config.yaml` 的 `elasticsearch` 區塊實現，無需改動程式碼。

**關鍵約束：**
- WAS Liberty 以 volume mount 方式部署 WAR，不重建 Liberty image
- MQ 通訊：Web layer → MQ producer → MDB consumer → Db2 JDBC（不允許 servlet 直接 JDBC）
- Instana agent 以環境變數 + volume mount 方式注入，不重建 image
- ELK 版本統一為 8.x，記憶體保守配置
- 所有 UI 語言使用繁體中文
- **Spring Boot 3.x（Java 17, Jakarta EE 10）**
- **Db2 Express**：使用 `icr.io/db2_community/db2`，允許 `privileged: true`
- **實作策略：所有 sub-task 由平行 subagent 實作，main agent 最後整合**

---

## 架構圖（文字描述）

```
┌─────────────────────────────────────────────────────┐
│  container-japp (10.89.1.20)                        │
│  ┌───────────┐  MQ  ┌──────────┐  JDBC  ┌────────┐ │
│  │ WAS       │ ──→  │ IBM MQ   │ ──→    │  Db2   │ │
│  │ Liberty   │      │  Light   │        │Express │ │
│  │ :9080     │      │  :5672   │        │ :50000 │ │
│  └───────────┘      └──────────┘        └────────┘ │
│         │ logs                                      │
└─────────┼───────────────────────────────────────────┘
          │ shared volume: app_logs
┌─────────┼───────────────────────────────────────────┐
│  container-b (10.89.1.30)                           │
│  ┌───────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │   ES      │  │ Logstash │  │    Kibana        │ │
│  │  :9200    │  │  :5044   │  │     :5601        │ │
│  └───────────┘  └──────────┘  └──────────────────┘ │
└─────────────────────────────────────────────────────┘
          ↑ elk-analyser 讀取 ES :9200
```

---

## Sub-Task 1 — 專案目錄骨架與 .env / podman-compose.yml

**狀態：** `[ ] pending`

### Intent
建立 `japp-demo/` 目錄作為整個新專案的根目錄，包含 podman-compose.yml、.env、以及後續所有子任務所需的目錄結構骨架。

### Expected Outcomes
- `japp-demo/podman-compose.yml` 定義 container-japp、container-b、共享 volume 與 network
- `japp-demo/.env` 包含所有可配置環境變數
- `japp-demo/podman-compose.override.yml` 預留 Instana agent 掛載設定
- 目錄骨架建立完成（各子目錄存在，可供後續任務填入內容）

### Todo List
1. 建立 `japp-demo/` 根目錄與以下子目錄：
   - `japp-demo/container-japp/` — WAS Liberty 容器相關設定
   - `japp-demo/container-japp/liberty-config/` — server.xml, bootstrap.properties
   - `japp-demo/container-japp/mq-config/` — ActiveMQ Artemis broker 設定
   - `japp-demo/container-japp/db2-config/` — Db2 初始化 SQL
   - `japp-demo/container-b/` — ELK 容器相關設定
   - `japp-demo/container-b/logstash/` — pipeline.conf
   - `japp-demo/banking-app/` — Maven Spring Boot WAR 專案
2. 撰寫 `japp-demo/.env`：定義所有 port、帳號密碼、heap size、app name、Kibana URL 變數
3. 撰寫 `japp-demo/podman-compose.yml`：
   - `container-japp` service：使用自訂 Containerfile，掛載 liberty-config、war dropins 目錄、logs volume
   - ELK services（elasticsearch、logstash、kibana）：各自使用官方 image，掛載 logstash pipeline、logs volume
   - Named volumes：`app_logs`（共享日誌）、`db2_data`、`es_data`
   - Network：`japp-network`（bridge，subnet 10.89.2.0/24）
   - Health checks for ES, Kibana, WAS Liberty, Db2
   - **Podman 注意**：`podman-compose` 不支援 `depends_on.condition: service_healthy`，改用 `healthcheck` + 啟動腳本內輪詢等待
4. 撰寫 `japp-demo/podman-compose.override.yml`：
   - 說明如何 mount Instana agent JAR volume
   - 展示如何設定 `JAVA_TOOL_OPTIONS` 環境變數注入 agent

### Relevant Context
- 現有 `podman-compose.yml` 使用 bridge network `elk-analyser-network`（10.89.1.0/24），新網段應使用 10.89.2.0/24 避免衝突
- 現有 `.env.example` 僅有 BOB_API_KEY，新 `.env` 應完整獨立
- WAS Liberty 使用 volume mount 部署：`dropins/` 目錄掛載，Liberty 自動偵測並熱部署 WAR
- **Podman rootless**：Db2 Express 需要 `--privileged` 或 `userns_mode: keep-id`；如 rootless 環境無法使用，計劃中備選方案為 **PostgreSQL 16**（schema 相容 Db2 基本語法）
- 檔案命名：Podman 習慣用 `Containerfile` 而非 `Dockerfile`，但兩者均可接受

---

## Sub-Task 2 — container-japp Dockerfile（WAS Liberty + MQ + Db2）

**狀態：** `[ ] pending`

### Intent
建立 `japp-demo/container-japp/Containerfile`，以 WAS Liberty 為基礎 image，額外安裝 ActiveMQ Artemis（MQ broker）並在同一容器以 supervisord 管理多個進程。Db2 以 **獨立 Podman service** 運行（同 `japp-network`），避免 privileged 模式問題。

### Expected Outcomes
- `japp-demo/container-japp/Containerfile` 可成功 build（`podman build`）
- 容器啟動後，Liberty（9080）、Artemis MQ（5672/8161）在同一容器運作
- Db2 以獨立 service（`db2` 或備選 `postgres`）運行於同網路
- WAS Liberty 透過 `dropins/` volume mount 自動部署 WAR
- 日誌輸出至 `app_logs` 共享 volume

### Todo List
1. 基底 image：`icr.io/appcafe/websphere-liberty:kernel-java17-openj9-ubi`
   - 選用 `kernel` 變體（最小），透過 `featureUtility installFeature` 只安裝所需 features
2. 安裝層（在 Containerfile 中）：
   - 下載 ActiveMQ Artemis 二進位包（`apache-artemis-x.x.x-bin.tar.gz`），解壓至 `/opt/artemis`
   - 建立 broker instance：`/opt/artemis/bin/artemis create /var/lib/artemis-broker --user admin --password admin --allow-anonymous`
   - 安裝 `supervisord`（`pip3 install supervisor` 或 `apt-get install supervisor`）
3. 撰寫 `supervisord.conf`：管理 `liberty`（`server run`）與 `artemis`（`artemis-broker/bin/artemis-service run`）
4. 設定 Liberty `dropins/` 監看掛載目錄（`/opt/ibm/wlp/usr/servers/defaultServer/dropins`）
5. 暴露端口：`9080`（HTTP）、`9443`（HTTPS）、`5672`（AMQP）、`8161`（Artemis 管理 UI）
6. 設定 `JAVA_TOOL_OPTIONS=""` 為空預設值（可由 `podman-compose.override.yml` 覆寫注入 Instana agent）
7. 日誌重定向：
   - Liberty → `/logs/liberty/` → `app_logs` volume
   - Artemis → `/logs/artemis/` → `app_logs` volume

### Relevant Context
- **Db2 獨立 service**：`icr.io/db2_community/db2` 需要 `privileged: true`；在 `podman-compose.yml` 中為 `db2` service 單獨設定 `security_opt: [label:disable]` 與 `privileged: true`，不影響其他 service
- **Podman rootless 替代方案**：若環境不允許 privileged，改用 `postgres:16-alpine`（schema.sql 使用 PostgreSQL 語法，邏輯一致）；計劃文件中兩種方案均提供，以環境變數 `DB_MODE=db2|postgres` 切換
- Liberty `kernel` image 需在 build 時執行 `RUN installUtility install --acceptLicense servlet-5.0 jms-2.0 mdb-3.2 jdbc-4.2 ejb-3.2 jsonLogging-1.0`
- **Podman build context**：`podman build -f container-japp/Containerfile .`，注意 build context 路徑

---

## Sub-Task 3 — WAS Liberty server.xml 與 bootstrap.properties

**狀態：** `[ ] pending`

### Intent
撰寫 WAS Liberty 的完整 server.xml 與 bootstrap.properties，使其能載入銀行應用 WAR、連接 MQ（AMQP）JMS 資源、配置 Db2 DataSource（給 MDB 使用）、並輸出結構化 JSON 日誌至共享 volume。

### Expected Outcomes
- `japp-demo/container-japp/liberty-config/server.xml` 配置完整
- `japp-demo/container-japp/liberty-config/bootstrap.properties` 配置完整
- Liberty 啟動時自動掛載 `dropins/banking-app.war`
- JMS ConnectionFactory 指向 MQ broker（localhost:5672 或 Artemis）
- Db2 DataSource 指向 Db2（localhost:50000）
- 日誌以 JSON 格式輸出至 `/logs/liberty/`

### Todo List
1. 撰寫 `server.xml`：
   - `<featureManager>`：啟用 `servlet-5.0`（Spring Boot WAR 需要）、`jms-2.0`、`mdb-3.2`、`jdbc-4.2`、`jsonLogging-1.0`、`ejb-3.2`
   - `<httpEndpoint>` port 9080/9443
   - `<jmsConnectionFactory>` 指向 localhost:5672（AMQP）
   - `<jmsQueue>` 定義 `bankingQueue`
   - `<dataSource>` 配置 Db2 JDBC（Class4 driver）
   - `<applicationManager>` 指向 dropins 目錄
   - `<logging>` 配置 JSON 格式輸出至 `/logs/liberty/messages.log`
2. 撰寫 `bootstrap.properties`：
   - 設定 MQ host/port/queue 名稱（來自環境變數）
   - 設定 Db2 host/port/dbname/user/pass
   - 設定日誌路徑

### Relevant Context
- Liberty JSON logging feature 名稱為 `logFormat=json` 或透過 `<logging messageFormat="json"/>`
- Db2 JDBC driver JAR 需放在 Liberty shared resources 目錄

---

## Sub-Task 4 — 銀行應用程式原始碼（Spring Boot WAR + Liberty）

**狀態：** `[ ] pending`

### Intent
以 **Spring Boot 3.x（最輕量設定）打包成 WAR** 部署至 WAS Liberty，用 Spring MVC REST API 取代 JSP/Servlet 層，前端為純靜態 HTML/JS（無需 JSP engine）。MQ 訊息層保留 Spring JMS（底層仍是 Liberty 的 JMS 資源）+ Liberty MDB 消費者執行 Db2 JDBC。

**架構原則：**
- Spring Boot 作為「Web 層 + JMS producer」框架，**不內嵌 Tomcat**（`spring-boot-starter-tomcat` scope=provided），WAR 交由 Liberty container 運行
- MDB（`@MessageDriven`）仍為 Jakarta EE 規範 bean，由 Liberty EJB container 管理，Spring context 不管理 MDB
- 前端：三個靜態 HTML 頁面（繁體中文）+ fetch API 呼叫 Spring REST endpoint

### Expected Outcomes
- `japp-demo/banking-app/pom.xml` 可執行 `mvn package` 產生 `banking-app.war`
- Spring Boot 應用啟動於 Liberty（context root `/banking`）
- REST API：`POST /banking/api/login`、`GET /banking/api/accounts/{id}`、`POST /banking/api/transfer`
- 前端三頁面：`/banking/login.html`、`/banking/dashboard.html`、`/banking/transfer.html`（全繁體中文）
- 資料流：REST Controller → `JmsTemplate` producer → `bankingQueue` → Liberty MDB → Db2 JDBC
- `japp-demo/container-japp/db2-config/schema.sql` 建立 `accounts` 與 `transactions` 表格

### Todo List
1. 建立 Maven 專案骨架：
   ```
   banking-app/
   ├── pom.xml
   └── src/main/
       ├── java/com/demo/banking/
       │   ├── BankingApplication.java        # SpringBootServletInitializer
       │   ├── controller/
       │   │   ├── AuthController.java        # POST /api/login, POST /api/logout
       │   │   ├── AccountController.java     # GET /api/accounts/{id}
       │   │   └── TransferController.java    # POST /api/transfer
       │   ├── service/
       │   │   └── BankingJmsProducer.java    # JmsTemplate 發送訊息
       │   ├── mdb/
       │   │   └── BankingMDB.java            # @MessageDriven Liberty MDB
       │   └── model/
       │       ├── Account.java
       │       └── Transaction.java
       ├── resources/
       │   └── application.properties         # Spring 設定（不含 DB，DB 由 MDB 透過 Liberty DataSource）
       └── webapp/
           ├── WEB-INF/
           │   └── web.xml                    # Servlet 3.1 minimal descriptor
           └── static/
               ├── login.html      (繁體中文)
               ├── dashboard.html  (繁體中文)
               ├── transfer.html   (繁體中文)
               └── app.js          (fetch API 呼叫 REST)
   ```
2. 撰寫 `pom.xml`：
   - Parent：`spring-boot-starter-parent 3.x`
   - 依賴：`spring-boot-starter-web`、`spring-boot-starter-jms`（tomcat scope=provided）
   - 依賴：`jakarta.ejb-api 4.0`（provided，給 MDB `@MessageDriven`）
   - 依賴：`org.apache.qpid:qpid-jms-client`（AMQP JMS provider）
   - 依賴：`com.ibm.db2:jcc`（Db2 JDBC，MDB 內使用，provided 讓 Liberty 提供）
   - 打包：`<packaging>war</packaging>`
3. 撰寫 `BankingApplication.java`：繼承 `SpringBootServletInitializer`，覆寫 `configure()`
4. 撰寫 REST Controllers（回傳 JSON）：
   - `AuthController`：mock 認證（hardcoded 3 組帳號），回傳 `{"accountId": "..."}` 或 401
   - `AccountController`：透過 `BankingJmsProducer` 發送 QUERY 訊息，同步等待回應（`JmsTemplate.sendAndReceive`）
   - `TransferController`：fire-and-forget JMS 訊息，立即回傳 202 Accepted
5. 撰寫 `BankingJmsProducer`：使用 `JmsTemplate`（連接 Liberty `jmsConnectionFactory`），訊息格式為 JSON（`{"op":"QUERY/TRANSFER","payload":{...}}`）
6. 撰寫 `BankingMDB`（Jakarta EE MDB，不納入 Spring context）：
   - `@MessageDriven(activationConfig = {@ActivationConfigProperty(propertyName="destinationLookup", propertyValue="jms/bankingQueue")})`
   - 解析 JSON，對應 `QUERY` → `SELECT`、`TRANSFER` → `INSERT + UPDATE`
   - 透過 Liberty JNDI 取得 `jdbc/bankingDS` DataSource
   - 查詢結果回寫 reply-to queue
7. 撰寫靜態 HTML 頁面（Bootstrap 5 CDN，繁體中文）：
   - `login.html`：帳號密碼表單
   - `dashboard.html`：顯示帳號餘額 + 最近 10 筆交易
   - `transfer.html`：轉帳表單（來源帳號/目標帳號/金額）
8. 撰寫 `schema.sql`：
   ```sql
   CREATE TABLE accounts (account_id VARCHAR(20) PRIMARY KEY, owner_name VARCHAR(100), balance DECIMAL(15,2), created_at TIMESTAMP);
   CREATE TABLE transactions (tx_id VARCHAR(36) PRIMARY KEY, from_account VARCHAR(20), to_account VARCHAR(20), amount DECIMAL(15,2), tx_time TIMESTAMP, status VARCHAR(20));
   INSERT INTO accounts VALUES ('ACC001','王大明',100000.00,CURRENT_TIMESTAMP),
                               ('ACC002','李小美',50000.00,CURRENT_TIMESTAMP),
                               ('ACC003','張志偉',75000.00,CURRENT_TIMESTAMP);
   ```

### Relevant Context
- Spring Boot WAR 部署至外部容器需：1) `<packaging>war</packaging>` 2) `tomcat` scope=provided 3) 繼承 `SpringBootServletInitializer`
- MDB 為 Liberty EJB bean，**不能**由 Spring `@Component` 掃描管理；需在 `server.xml` 配置 activation spec
- `JmsTemplate.sendAndReceive()` 需配置 `receiveTimeout`（避免無限等待）；設定 5 秒 timeout，超時回傳 fallback
- Liberty 的 JNDI 名稱：`jms/bankingConnectionFactory`、`jms/bankingQueue`、`jdbc/bankingDS`

---

## Sub-Task 5 — container-b：ELK Dockerfile 與 Logstash Pipeline

**狀態：** `[ ] pending`

### Intent
建立 `japp-demo/container-b/Dockerfile.elk`，整合 Elasticsearch 8.x、Logstash 8.x、Kibana 8.x，並撰寫 Logstash pipeline 配置，從共享 volume 收集三種日誌來源（Liberty、MQ、Db2），解析並輸出至 ES。

### Expected Outcomes
- `japp-demo/container-b/Dockerfile.elk` 可 build（或使用 Compose multi-service 官方 image）
- `japp-demo/container-b/logstash/pipeline.conf` 定義三個 file input + grok/mutate filter + ES output
- ES 索引以 `banking-logs-liberty-*`、`banking-logs-mq-*`、`banking-logs-db2-*` 命名
- Kibana 啟動後可瀏覽上述索引

### Todo List
1. 決定 container-b 架構：**三個獨立 service 各用官方 image**（不合並 Dockerfile）— ES、Logstash、Kibana 各自一個 Compose service，共用 `japp-network`
2. 撰寫 `pipeline.conf`：
   - `input { file { path => "/logs/liberty/*.log", type => "liberty" } ... }`
   - `input { file { path => "/logs/mq/*.log", type => "mq" } ... }`
   - `input { file { path => "/logs/db2/*.log", type => "db2" } ... }`
   - `filter { mutate { add_field => { "service" => "%{type}", "container" => "container-japp" } } }`
   - `output { elasticsearch { hosts => ["elasticsearch:9200"], index => "banking-logs-%{type}-%{+YYYY.MM.dd}" } }`
3. ES 設定：single-node，security 關閉（xpack.security.enabled: false，方便 demo），低記憶體 heap
4. Kibana 設定：`ELASTICSEARCH_HOSTS=http://elasticsearch:9200`
5. 建立 `japp-demo/container-b/elasticsearch/elasticsearch.yml`：
   - `cluster.name: banking-demo`
   - `xpack.ml.enabled: false`
   - `xpack.monitoring.enabled: false`

### Relevant Context
- ELK 8.x 預設啟用 TLS/Security；demo 環境需明確關閉 `xpack.security.enabled: false` 否則 Kibana 無法連接
- Logstash 8.x 需要 `pipeline.ecs_compatibility: disabled` 避免 schema 不相容警告
- ES heap：ES_JAVA_OPTS="-Xms256m -Xmx512m"

---

## Sub-Task 6 — 整合現有 ELK Analyser 至 japp-demo ELK Stack

**狀態：** `[ ] pending`

### Intent
修改現有 ELK Analyser（`bob-analyser/config/config.yaml`）的 Elasticsearch 連線設定，使其指向 container-b 的 Elasticsearch（和 Kibana），並在 `podman-compose.yml` 中加入 container-b 的服務依賴或 external network 宣告。

### Expected Outcomes
- `bob-analyser/config/config.yaml` 的 `elasticsearch` 區塊指向 container-b ES endpoint
- `podman-compose.yml` 或新增 override 文件宣告 `japp-network` 為 external network，使 elk-analyser 容器可解析 `elasticsearch` hostname
- `bob-analyser/.env.example` 加入 ES 連線相關變數說明
- 撰寫 `docs/connect-elk-analyser-to-container-b.md` 操作說明文件

### Todo List
1. 確認現有 `config.yaml` elasticsearch 區塊（已調查：mode/host/port/index_pattern）
2. 修改 `config.yaml`：
   - `mode: local`（從 mock 改為 local）
   - `host: elasticsearch`（或 container-b 服務名稱）
   - `port: 9200`
   - `index_pattern: "banking-logs-*"`
   - `username: ""`、`password: ""`（security 關閉，無需認證）
3. 修改根目錄 `podman-compose.yml`（現有 elk-analyser 的 compose 文件）：
   - 為 `elk-analyser` service 加入 `networks` 宣告，連接 `japp-network`（`external: true`）
   - 或設定 elk-analyser container 的 `extra_hosts`：`elasticsearch:10.89.2.x`（japp-network 內 ES 的固定 IP）
   - **Podman network 跨 compose 連通**：兩個 podman-compose 專案需共用同一 `podman network`，執行 `podman network create japp-network` 後，兩個 compose 文件均宣告 `external: true`
4. 更新 `bob-analyser/.env.example`：加入 `ES_HOST`、`ES_PORT`、`ES_INDEX` 說明
5. 撰寫 `docs/connect-elk-analyser-to-container-b.md`：
   - 步驟說明
   - Kibana URL（http://localhost:5601）
   - 確認 index pattern 需在 Kibana 建立（`banking-logs-*`）
   - Logstash 中對應 field mapping 說明

### Relevant Context
- 現有 `config.yaml` 的 `field_mapping` 預設使用 ECS 格式（`@timestamp`、`log.level`、`message`、`service.name`）
- Logstash pipeline 需輸出相同 field 名稱，或 `config.yaml` field_mapping 需配合調整
- `elk-analyser` 容器固定 IP 10.89.1.10，`elasticsearch` 在 10.89.2.x，需確保路由可達

---

## Sub-Task 7 — 整合驗證與說明文件

**狀態：** `[ ] pending`

### Intent
撰寫最終整合說明與使用指引，確保所有組件可一鍵啟動並相互連通。

### Expected Outcomes
- `japp-demo/README.md`：完整啟動與驗證步驟（繁體中文）
- 所有 deliverables 清單核對完成

### Todo List
1. 撰寫 `japp-demo/README.md`（繁體中文）：
   - 先決條件（Podman, podman-compose, Maven, Java 17+）
   - 啟動順序：`cd japp-demo && podman-compose up -d`
   - 驗證步驟：
     - 銀行網站：http://localhost:9080/banking-app
     - Kibana：http://localhost:5601
     - ELK Analyser API：http://localhost:8080/health
   - 初始化帳號密碼（從 schema.sql 預設資料）
   - Instana agent 注入說明（docker-compose.override.yml 使用方式）
2. 核對 deliverables 清單（8 項）

### Relevant Context
- Db2 Express 初始化需要 `DBNAME`, `DB2INST1_PASSWORD` 環境變數
- 啟動順序依賴：Db2 → MQ → Liberty（需等 Db2 就緒才能讓 MDB 連線）

---

## Deliverables 清單

| # | 項目 | 位置 |
|---|------|------|
| 1 | container-japp Containerfile | `japp-demo/container-japp/Containerfile` |
| 2 | container-b：確認使用官方 image（無獨立 Containerfile） | `japp-demo/podman-compose.yml` 內宣告 |
| 3 | podman-compose.yml | `japp-demo/podman-compose.yml` |
| 4 | .env 環境變數文件 | `japp-demo/.env` |
| 5 | server.xml + bootstrap.properties | `japp-demo/container-japp/liberty-config/` |
| 6 | Logstash pipeline.conf | `japp-demo/container-b/logstash/pipeline.conf` |
| 7 | Spring Boot WAR 銀行應用程式原始碼（Maven） | `japp-demo/banking-app/` |
| 8 | podman-compose.override.yml（Instana） | `japp-demo/podman-compose.override.yml` |
| 9 | ELK Analyser 整合說明 | `docs/connect-elk-analyser-to-container-b.md` |

---

## 技術決策記錄

| 決策 | 選擇 | 原因 |
|------|------|------|
| Container runtime | Podman + podman-compose | 用戶環境需求；檔案命名用 `Containerfile`，compose 文件用 `podman-compose.yml` |
| MQ 實作 | Apache ActiveMQ Artemis（AMQP 1.0） | IBM MQ Light 已停產；Artemis 支援相同 AMQP 協議，JMS API 完全相容 |
| Db2 部署方式 | 獨立 Podman service（`icr.io/db2_community/db2`） + privileged | 避免與 Liberty/Artemis 混在同一容器造成複雜性；privileged 僅限 db2 service |
| Db2 備選方案 | PostgreSQL 16（rootless 環境） | 若不允許 privileged，`DB_MODE=postgres` 切換 |
| container-b 架構 | 三個獨立 Podman service（官方 image） | 官方 image 易維護，各自可獨立設定記憶體 |
| ES Security | `xpack.security.enabled: false` | Demo 環境，省去 TLS 設定複雜度 |
| WAS Liberty WAR 部署 | `dropins/` volume mount | 不重建 image；Liberty 自動偵測並熱部署 |
| Instana 注入 | `JAVA_TOOL_OPTIONS` 環境變數 + volume | 標準 JVM agent 注入，不碰 Containerfile |
| 日誌收集 | Logstash file input（shared named volume） | 無需 Filebeat sidecar；Podman named volume 在多個 service 間共享 |
| Web 框架 | Spring Boot 3.x WAR（tomcat scope=provided） | Liberty 提供 servlet container，Spring 提供 DI/MVC；最輕量 |
| 跨 compose 網路 | `podman network create japp-network`（external） | Podman 跨 compose 專案需手動建立共享 network |

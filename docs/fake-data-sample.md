<!-- Updated: 2026-09-16 08:24:38 +0800 -->
# Mock 模擬日誌資料參考

> 由 [`bob-analyser/gen_fake_data.py`](../bob-analyser/gen_fake_data.py) 產生。
> 基準時間：`2026-01-15 08:00 UTC`，共 **31 筆**記錄，涵蓋 4 個真實故障場景。

## 使用方式

```bash
# 在容器內執行（產生資料並觸發分析）
podman exec elk-analyser python gen_fake_data.py

# 僅產生資料，不觸發分析
podman exec elk-analyser python gen_fake_data.py --no-trigger

# 自訂時間範圍
podman exec elk-analyser python gen_fake_data.py \
  --from "2026-01-15 08:00" --to "2026-01-15 09:00"
```

執行後會同時寫入：
- `/workspace/logs/was_mq_postgres_fake.json` — 供 `bob_bridge` 讀取
- `/app/mock/sample_logs.json` — 供 `extractor` mock 模式讀取

---

## 各服務日誌統計

| 服務 | ERROR | CRITICAL | FATAL | 小計 |
|------|------:|---------:|------:|-----:|
| `postgres-server` | 5 | 2 | 1 | 8 |
| `mq-manager` | 6 | 3 | 0 | 9 |
| `was-inventory-svc` | 3 | 1 | 1 | 5 |
| `was-order-svc` | 3 | 0 | 3 | 6 |
| `was-payment-svc` | 1 | 1 | 1 | 3 |
| **合計** | **18** | **7** | **6** | **31** |

---

## 場景 A — TRC-PG-001：PostgreSQL 連線池耗盡 → WAS FATAL → MQ 訊息堆積

> 時間窗口：`08:00:05` – `08:03:20`（約 3 分 15 秒）

| # | timestamp | level | service | host | message |
|---|-----------|-------|---------|------|---------|
| 1 | 08:00:05 | ERROR | postgres-server | postgres-prod-01 | `FATAL: remaining connection slots are reserved for non-replication superuser connections. Max connections=200 reached. Waiting queue=145.` |
| 2 | 08:00:20 | ERROR | postgres-server | postgres-prod-01 | `FATAL: sorry, too many clients already. Active connections=200/200. New requests are being rejected.` |
| 3 | 08:00:35 | **CRITICAL** | postgres-server | postgres-prod-01 | `ERROR: deadlock detected on table orders. Process 4821 waits for ShareLock on transaction 10023. Rolling back transaction TXN-4821.` |
| 4 | 08:01:10 | ERROR | was-order-svc | was-prod-01 | `SRVE0777E: javax.resource.ResourceException: Unable to obtain a JDBC connection. Pool exhausted after 30000ms timeout. Application: order-processing-ear` |
| 5 | 08:01:25 | **FATAL** | was-order-svc | was-prod-01 | `WSVR0501E: Error creating component com.example.order.OrderProcessor. Root cause: Cannot get a connection, pool error Timeout waiting for connection from pool` |
| 6 | 08:01:45 | ERROR | was-order-svc | was-prod-01 | `TRAS0017I: Transaction rolled back due to FFDC: org.postgresql.util.PSQLException: FATAL: sorry, too many clients already. OrderID=ORD-20260115-8821` |
| 7 | 08:02:05 | ERROR | mq-manager | mq-prod-01 | `AMQ9208E: Error on channel 'ORDER.CHANNEL'. Channel is closing. MQRC=2009 (MQRC_CONNECTION_BROKEN). Channel will retry after 5 seconds.` |
| 8 | 08:02:30 | ERROR | mq-manager | mq-prod-01 | `AMQ4038E: IBM MQ queue 'ORDER.INPUT.QUEUE' is full. Current depth=50000/50000. Messages being rejected from producer.` |
| 9 | 08:03:00 | **CRITICAL** | mq-manager | mq-prod-01 | `AMQ8004E: IBM MQ queue manager MQPROD01 unable to process messages. Dead letter queue depth=12450. Immediate intervention required.` |
| 10 | 08:03:20 | **FATAL** | was-order-svc | was-prod-01 | `WSVR0601E: WebSphere Application Server entering STOPPED state. JVM heap utilization: 98.7% (3891MB/3940MB). OutOfMemoryError imminent.` |

**根因鏈**：PostgreSQL 連線池耗盡 → WAS 無法取得 JDBC 連線 → MQ 通道斷線、佇列滿載 → WAS 進入 STOPPED 狀態

---

## 場景 B — TRC-WAS-002：WAS JVM 記憶體洩漏 → GC 過載 → 服務降級

> 時間窗口：`08:10:00` – `08:13:05`（約 3 分 5 秒）

| # | timestamp | level | service | host | message |
|---|-----------|-------|---------|------|---------|
| 11 | 08:10:00 | ERROR | was-inventory-svc | was-prod-02 | `JVMJ9VM015W: SystemOutOfMemory. Application has allocated 2.1GB. GC overhead limit exceeded. JVM flags: -Xmx4g -Xms2g` |
| 12 | 08:10:30 | ERROR | was-inventory-svc | was-prod-02 | `TRAS0015I: FFDC - Exception: java.lang.OutOfMemoryError: Java heap space. Package: com.example.inventory.cache.CacheManager.loadAll. ThreadId: 00000082` |
| 13 | 08:11:00 | ERROR | was-inventory-svc | was-prod-02 | `WSVR0024E: Application InventoryApp has exceeded JVM heap threshold (85%). Full GC triggered. Response time degraded: avg=8500ms (normal: 200ms)` |
| 14 | 08:11:45 | **CRITICAL** | was-inventory-svc | was-prod-02 | `WSVR0605E: EJB method timeout. ejb/InventorySessionBean.getStockLevel() exceeded 30000ms. Thread dump initiated.` |
| 15 | 08:12:15 | ERROR | postgres-server | postgres-prod-01 | `LOG: duration: 285000.123 ms  statement: SELECT * FROM INVENTORY WHERE STATUS='ACTIVE' (rows scanned: 2.3M)` |
| 16 | 08:12:45 | **FATAL** | was-inventory-svc | was-prod-02 | `JVMXE010: JVM requested operating system signal. Heap dump generated: /tmp/heapdump.20260115.120000.phd. Process will restart.` |
| 17 | 08:13:05 | ERROR | mq-manager | mq-prod-01 | `AMQ9503E: Channel 'INVENTORY.REQUEST' partner application stopped abnormally. MQRC=2009. Unprocessed messages: 3421` |

**根因鏈**：JVM 記憶體洩漏（CacheManager）→ GC 過載、EJB 超時 → PostgreSQL 長查詢 → JVM crash + MQ 未處理訊息堆積

---

## 場景 C — TRC-MQ-003：MQ SSL 憑證過期 → 連線斷線 → 訊息投遞失敗

> 時間窗口：`08:20:00` – `08:22:30`（約 2 分 30 秒）

| # | timestamp | level | service | host | message |
|---|-----------|-------|---------|------|---------|
| 18 | 08:20:00 | ERROR | mq-manager | mq-prod-02 | `AMQ9641E: Remote channel 'REMOTE.PAYMENT.CHANNEL' SSL/TLS handshake failed: Certificate expired at 2026-01-15T00:00:00Z. CN=mq-prod-02.example.com` |
| 19 | 08:20:15 | ERROR | mq-manager | mq-prod-02 | `AMQ9208E: Error on channel 'REMOTE.PAYMENT.CHANNEL'. MQRC=2381 (MQRC_SSL_CERTIFICATE_REVOCATION_STATUS_CHECK_FAILED). Channel disabled.` |
| 20 | 08:20:40 | **CRITICAL** | mq-manager | mq-prod-02 | `AMQ4042E: Dead letter queue 'PAYMENT.DLQ' reached 80% capacity (40000/50000). Messages from PAYMENT.INPUT.QUEUE being rerouted to DLQ due to delivery failure.` |
| 21 | 08:21:05 | ERROR | was-payment-svc | was-prod-03 | `SRVE0315E: JMS connection factory lookup failed. MQ host=mq-prod-02:1415. JMSException: MQJMS2005: Failed to create MQQueueManager for 'MQPROD02'. Linked: JMSCMQ0001 MQ RC=2381` |
| 22 | 08:21:30 | **FATAL** | was-payment-svc | was-prod-03 | `WSVR0501E: Payment processing service unavailable. All outbound payment messages queued locally (10234 messages). Disk usage: /var/spool/payment=94%. Risk of data loss.` |
| 23 | 08:22:00 | ERROR | postgres-server | postgres-prod-01 | `ERROR: canceling statement due to lock timeout on table PAYMENT_TRANSACTIONS. Uncommitted transactions from was-payment-svc piling up.` |
| 24 | 08:22:30 | **CRITICAL** | was-payment-svc | was-prod-03 | `JVMJ9GC025I: Excessive GC activity. Payment service JVM spending >60% time in GC. Heap: 3820MB/4096MB. Application throughput: 3.2% (normal: 100%). SLA breach.` |

**根因鏈**：MQ SSL 憑證到期 → 通道 SSL 握手失敗 → DLQ 快滿 → WAS 付款服務無法投遞 → PostgreSQL 死鎖 + JVM GC 過載（SLA breach）

---

## 場景 D — TRC-PG-004：PostgreSQL 磁碟滿 → WAL 交易日誌滿 → 全服務阻塞

> 時間窗口：`08:35:00` – `08:37:20`（約 2 分 20 秒）

| # | timestamp | level | service | host | message |
|---|-----------|-------|---------|------|---------|
| 25 | 08:35:00 | ERROR | postgres-server | postgres-prod-01 | `PANIC: could not write to log file /var/lib/postgresql/data/pg_wal/000000010000000100000002: No space left on device. Available log space=0KB.` |
| 26 | 08:35:20 | **CRITICAL** | postgres-server | postgres-prod-01 | `FATAL: terminating connection due to administrator command: disk full on WAL directory. All write transactions are being rolled back.` |
| 27 | 08:35:45 | ERROR | was-order-svc | was-prod-01 | `TRAS0017I: FFDC Exception chain: PSQLException [PostgreSQL WAL full / disk full] -> ResourceException [connection pool] -> EJBException [OrderService.createOrder()]. 8823 orders failed in last 5 minutes.` |
| 28 | 08:36:10 | ERROR | mq-manager | mq-prod-01 | `AMQ7472E: Log extent file not available. MQPROD01 queue manager log disk /mqlog: 99.8% utilized (499.9GB/500GB). Queue manager operations suspended.` |
| 29 | 08:36:30 | **FATAL** | postgres-server | postgres-prod-01 | `POSTGRESQL SERVER IS IN RECOVERY/EMERGENCY STOP: Database is in read-only mode due to WAL disk full.` |
| 30 | 08:37:00 | **FATAL** | was-order-svc | was-prod-01 | `WSVR0601E: WebSphere cluster member was-prod-01 removed from cluster ORDERSVC_CLUSTER. Failover triggered to was-prod-02. Active sessions: 4821 (transferred).` |
| 31 | 08:37:20 | **CRITICAL** | mq-manager | mq-prod-01 | `AMQ8004E: Queue manager MQPROD01 ending immediately. Queue depth at shutdown: ORDER.INPUT=48290, PAYMENT.INPUT=21840, INVENTORY.INPUT=9321.` |

**根因鏈**：PostgreSQL 交易日誌磁碟寫滿 → 所有寫入交易強制回滾 → WAS 訂單 FFDC 鏈式失敗 → MQ log 磁碟也滿 → PostgreSQL 全面暫停 → WAS cluster failover

---

## JSON 欄位結構

每筆記錄包含以下欄位：

| 欄位 | 型別 | 說明 |
|------|------|------|
| `@timestamp` | string | ISO 8601，帶時區（`+00:00`） |
| `log.level` | string | `ERROR` / `CRITICAL` / `FATAL` |
| `service.name` | string | 服務識別（`postgres-server` / `mq-manager` / `was-*-svc`） |
| `host.name` | string | 主機名稱（`postgres-prod-01` / `mq-prod-01` / `was-prod-01~03`） |
| `trace.id` | string | 跨服務追蹤 ID（場景標識） |
| `message` | string | 原始日誌訊息（含錯誤碼） |

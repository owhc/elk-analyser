# Updated: 2026-09-16 08:24:38 +0800
"""
gen_fake_data.py
────────────────
產生 WAS / MQ / PostgreSQL 模擬日誌並放置至共享 Volume，供 elk-analyser 主動分析。

使用方式：
  # 1. 放入 volume 並立即觸發分析
  podman exec elk-analyser python gen_fake_data.py

  # 2. 僅產生資料（不觸發分析）
  podman exec elk-analyser python gen_fake_data.py --no-trigger

  # 3. 調整時間範圍
  podman exec elk-analyser python gen_fake_data.py --from "2026-01-15 08:00" --to "2026-01-15 09:00"

包含四個真實場景：
  場景 A (TRC-PG-001)：PostgreSQL 連線池耗盡 → WAS FATAL → MQ 訊息堆積
  場景 B (TRC-WAS-002)：WAS JVM 記憶體洩漏 → GC 過載 → 服務降級
  場景 C (TRC-MQ-003)：MQ SSL 憑證過期 → 連線斷線 → 訊息投遞失敗
  場景 D (TRC-PG-004)：PostgreSQL 磁碟滿 → WAL 交易日誌滿 → 全服務阻塞
"""

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── 共享 Volume 路徑（與 bob_bridge 一致）────────────────────────────
VOLUME_LOGS_DIR = Path("/workspace/logs")
MOCK_LOGS_PATH  = Path("/app/mock/sample_logs.json")
API_BASE        = "http://localhost:8080"


def _build_logs(base_time: datetime) -> list[dict]:
    """依基準時間建構 31 筆 WAS/MQ/PostgreSQL 模擬日誌。"""

    def ts(offset_min: int, offset_sec: int = 0) -> str:
        return (base_time + timedelta(minutes=offset_min, seconds=offset_sec)
                ).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    return [
        # ── 場景 A：PostgreSQL 連線池耗盡 → WAS FATAL → MQ 訊息堆積 ──────────
        {"@timestamp": ts(0,5),  "log.level": "ERROR",    "service.name": "postgres-server",
         "host.name": "postgres-prod-01",  "trace.id": "TRC-PG-001",
         "message": "FATAL: remaining connection slots are reserved for non-replication superuser connections. Max connections=200 reached. Waiting queue=145."},
        {"@timestamp": ts(0,20), "log.level": "ERROR",    "service.name": "postgres-server",
         "host.name": "postgres-prod-01",  "trace.id": "TRC-PG-001",
         "message": "FATAL: sorry, too many clients already. Active connections=200/200. New requests are being rejected."},
        {"@timestamp": ts(0,35), "log.level": "CRITICAL", "service.name": "postgres-server",
         "host.name": "postgres-prod-01",  "trace.id": "TRC-PG-001",
         "message": "ERROR: deadlock detected on table orders. Process 4821 waits for ShareLock on transaction 10023. Rolling back transaction TXN-4821."},
        {"@timestamp": ts(1,10), "log.level": "ERROR",    "service.name": "was-order-svc",
         "host.name": "was-prod-01",  "trace.id": "TRC-PG-001",
         "message": "SRVE0777E: javax.resource.ResourceException: Unable to obtain a JDBC connection. Pool exhausted after 30000ms timeout. Application: order-processing-ear"},
        {"@timestamp": ts(1,25), "log.level": "FATAL",    "service.name": "was-order-svc",
         "host.name": "was-prod-01",  "trace.id": "TRC-PG-001",
         "message": "WSVR0501E: Error creating component com.example.order.OrderProcessor. Root cause: Cannot get a connection, pool error Timeout waiting for connection from pool"},
        {"@timestamp": ts(1,45), "log.level": "ERROR",    "service.name": "was-order-svc",
         "host.name": "was-prod-01",  "trace.id": "TRC-PG-001",
         "message": "TRAS0017I: Transaction rolled back due to FFDC: org.postgresql.util.PSQLException: FATAL: sorry, too many clients already. OrderID=ORD-20260115-8821"},
        {"@timestamp": ts(2,5),  "log.level": "ERROR",    "service.name": "mq-manager",
         "host.name": "mq-prod-01",   "trace.id": "TRC-PG-001",
         "message": "AMQ9208E: Error on channel 'ORDER.CHANNEL'. Channel is closing. MQRC=2009 (MQRC_CONNECTION_BROKEN). Channel will retry after 5 seconds."},
        {"@timestamp": ts(2,30), "log.level": "ERROR",    "service.name": "mq-manager",
         "host.name": "mq-prod-01",   "trace.id": "TRC-PG-001",
         "message": "AMQ4038E: IBM MQ queue 'ORDER.INPUT.QUEUE' is full. Current depth=50000/50000. Messages being rejected from producer."},
        {"@timestamp": ts(3,0),  "log.level": "CRITICAL", "service.name": "mq-manager",
         "host.name": "mq-prod-01",   "trace.id": "TRC-PG-001",
         "message": "AMQ8004E: IBM MQ queue manager MQPROD01 unable to process messages. Dead letter queue depth=12450. Immediate intervention required."},
        {"@timestamp": ts(3,20), "log.level": "FATAL",    "service.name": "was-order-svc",
         "host.name": "was-prod-01",  "trace.id": "TRC-PG-001",
         "message": "WSVR0601E: WebSphere Application Server entering STOPPED state. JVM heap utilization: 98.7% (3891MB/3940MB). OutOfMemoryError imminent."},

        # ── 場景 B：WAS JVM 記憶體洩漏 → GC 過載 → 服務降級 ───────────
        {"@timestamp": ts(10,0),  "log.level": "ERROR",    "service.name": "was-inventory-svc",
         "host.name": "was-prod-02",  "trace.id": "TRC-WAS-002",
         "message": "JVMJ9VM015W: SystemOutOfMemory. Application has allocated 2.1GB. GC overhead limit exceeded. JVM flags: -Xmx4g -Xms2g"},
        {"@timestamp": ts(10,30), "log.level": "ERROR",    "service.name": "was-inventory-svc",
         "host.name": "was-prod-02",  "trace.id": "TRC-WAS-002",
         "message": "TRAS0015I: FFDC - Exception: java.lang.OutOfMemoryError: Java heap space. Package: com.example.inventory.cache.CacheManager.loadAll. ThreadId: 00000082"},
        {"@timestamp": ts(11,0),  "log.level": "ERROR",    "service.name": "was-inventory-svc",
         "host.name": "was-prod-02",  "trace.id": "TRC-WAS-002",
         "message": "WSVR0024E: Application InventoryApp has exceeded JVM heap threshold (85%). Full GC triggered. Response time degraded: avg=8500ms (normal: 200ms)"},
        {"@timestamp": ts(11,45), "log.level": "CRITICAL", "service.name": "was-inventory-svc",
         "host.name": "was-prod-02",  "trace.id": "TRC-WAS-002",
         "message": "WSVR0605E: EJB method timeout. ejb/InventorySessionBean.getStockLevel() exceeded 30000ms. Thread dump initiated."},
        {"@timestamp": ts(12,15), "log.level": "ERROR",    "service.name": "postgres-server",
         "host.name": "postgres-prod-01",  "trace.id": "TRC-WAS-002",
         "message": "LOG: duration: 285000.123 ms  statement: SELECT * FROM INVENTORY WHERE STATUS='ACTIVE' (rows scanned: 2.3M)"},
        {"@timestamp": ts(12,45), "log.level": "FATAL",    "service.name": "was-inventory-svc",
         "host.name": "was-prod-02",  "trace.id": "TRC-WAS-002",
         "message": "JVMXE010: JVM requested operating system signal. Heap dump generated: /tmp/heapdump.20260115.120000.phd. Process will restart."},
        {"@timestamp": ts(13,5),  "log.level": "ERROR",    "service.name": "mq-manager",
         "host.name": "mq-prod-01",   "trace.id": "TRC-WAS-002",
         "message": "AMQ9503E: Channel 'INVENTORY.REQUEST' partner application stopped abnormally. MQRC=2009. Unprocessed messages: 3421"},

        # ── 場景 C：MQ SSL 憑證過期 → 連線斷線 → 訊息投遞失敗 ──────────
        {"@timestamp": ts(20,0),  "log.level": "ERROR",    "service.name": "mq-manager",
         "host.name": "mq-prod-02",   "trace.id": "TRC-MQ-003",
         "message": "AMQ9641E: Remote channel 'REMOTE.PAYMENT.CHANNEL' SSL/TLS handshake failed: Certificate expired at 2026-01-15T00:00:00Z. CN=mq-prod-02.example.com"},
        {"@timestamp": ts(20,15), "log.level": "ERROR",    "service.name": "mq-manager",
         "host.name": "mq-prod-02",   "trace.id": "TRC-MQ-003",
         "message": "AMQ9208E: Error on channel 'REMOTE.PAYMENT.CHANNEL'. MQRC=2381 (MQRC_SSL_CERTIFICATE_REVOCATION_STATUS_CHECK_FAILED). Channel disabled."},
        {"@timestamp": ts(20,40), "log.level": "CRITICAL", "service.name": "mq-manager",
         "host.name": "mq-prod-02",   "trace.id": "TRC-MQ-003",
         "message": "AMQ4042E: Dead letter queue 'PAYMENT.DLQ' reached 80% capacity (40000/50000). Messages from PAYMENT.INPUT.QUEUE being rerouted to DLQ due to delivery failure."},
        {"@timestamp": ts(21,5),  "log.level": "ERROR",    "service.name": "was-payment-svc",
         "host.name": "was-prod-03",  "trace.id": "TRC-MQ-003",
         "message": "SRVE0315E: JMS connection factory lookup failed. MQ host=mq-prod-02:1415. JMSException: MQJMS2005: Failed to create MQQueueManager for 'MQPROD02'. Linked: JMSCMQ0001 MQ RC=2381"},
        {"@timestamp": ts(21,30), "log.level": "FATAL",    "service.name": "was-payment-svc",
         "host.name": "was-prod-03",  "trace.id": "TRC-MQ-003",
         "message": "WSVR0501E: Payment processing service unavailable. All outbound payment messages queued locally (10234 messages). Disk usage: /var/spool/payment=94%. Risk of data loss."},
        {"@timestamp": ts(22,0),  "log.level": "ERROR",    "service.name": "postgres-server",
         "host.name": "postgres-prod-01",  "trace.id": "TRC-MQ-003",
         "message": "ERROR: canceling statement due to lock timeout on table PAYMENT_TRANSACTIONS. Uncommitted transactions from was-payment-svc piling up."},
        {"@timestamp": ts(22,30), "log.level": "CRITICAL", "service.name": "was-payment-svc",
         "host.name": "was-prod-03",  "trace.id": "TRC-MQ-003",
         "message": "JVMJ9GC025I: Excessive GC activity. Payment service JVM spending >60% time in GC. Heap: 3820MB/4096MB. Application throughput: 3.2% (normal: 100%). SLA breach."},

        # ── 場景 D：PostgreSQL 磁碟滿 → WAL 交易日誌滿 → 全服務阻塞 ──────────────
        {"@timestamp": ts(35,0),  "log.level": "ERROR",    "service.name": "postgres-server",
         "host.name": "postgres-prod-01",  "trace.id": "TRC-PG-004",
         "message": "PANIC: could not write to log file /var/lib/postgresql/data/pg_wal/000000010000000100000002: No space left on device. Available log space=0KB."},
        {"@timestamp": ts(35,20), "log.level": "CRITICAL", "service.name": "postgres-server",
         "host.name": "postgres-prod-01",  "trace.id": "TRC-PG-004",
         "message": "FATAL: terminating connection due to administrator command: disk full on WAL directory. All write transactions are being rolled back."},
        {"@timestamp": ts(35,45), "log.level": "ERROR",    "service.name": "was-order-svc",
         "host.name": "was-prod-01",  "trace.id": "TRC-PG-004",
         "message": "TRAS0017I: FFDC Exception chain: PSQLException [PostgreSQL WAL full / disk full] -> ResourceException [connection pool] -> EJBException [OrderService.createOrder()]. 8823 orders failed in last 5 minutes."},
        {"@timestamp": ts(36,10), "log.level": "ERROR",    "service.name": "mq-manager",
         "host.name": "mq-prod-01",   "trace.id": "TRC-PG-004",
         "message": "AMQ7472E: Log extent file not available. MQPROD01 queue manager log disk /mqlog: 99.8% utilized (499.9GB/500GB). Queue manager operations suspended."},
        {"@timestamp": ts(36,30), "log.level": "FATAL",    "service.name": "postgres-server",
         "host.name": "postgres-prod-01",  "trace.id": "TRC-PG-004",
         "message": "POSTGRESQL SERVER IS IN RECOVERY/EMERGENCY STOP: Database is in read-only mode due to WAL disk full."},
        {"@timestamp": ts(37,0),  "log.level": "FATAL",    "service.name": "was-order-svc",
         "host.name": "was-prod-01",  "trace.id": "TRC-PG-004",
         "message": "WSVR0601E: WebSphere cluster member was-prod-01 removed from cluster ORDERSVC_CLUSTER. Failover triggered to was-prod-02. Active sessions: 4821 (transferred)."},
        {"@timestamp": ts(37,20), "log.level": "CRITICAL", "service.name": "mq-manager",
         "host.name": "mq-prod-01",   "trace.id": "TRC-PG-004",
         "message": "AMQ8004E: Queue manager MQPROD01 ending immediately. Queue depth at shutdown: ORDER.INPUT=48290, PAYMENT.INPUT=21840, INVENTORY.INPUT=9321."},
    ]


@click.command()
@click.option("--from", "from_time", default="2026-01-15 08:00",
              help="日誌基準開始時間 (YYYY-MM-DD HH:MM)")
@click.option("--to",   "to_time",   default="2026-01-15 09:00",
              help="觸發分析的結束時間 (YYYY-MM-DD HH:MM)")
@click.option("--no-trigger", is_flag=True, default=False,
              help="僅寫入資料，不呼叫 API 觸發分析")
@click.option("--volume-dir", default=str(VOLUME_LOGS_DIR),
              help="共享 Volume 日誌目錄路徑")
def main(from_time: str, to_time: str, no_trigger: bool, volume_dir: str):
    """產生 WAS/MQ/PostgreSQL 模擬日誌並（可選）觸發分析。"""

    # 解析時間
    try:
        base_dt = datetime.strptime(from_time, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        logger.error("from_time 格式錯誤，請使用 YYYY-MM-DD HH:MM")
        sys.exit(1)

    # 產生 logs
    logs = _build_logs(base_dt)
    logger.info("已產生 %d 筆模擬日誌（WAS/MQ/PostgreSQL，4 個場景）", len(logs))

    # 寫入共享 Volume（供 bob_bridge 讀取）
    vol_dir = Path(volume_dir)
    vol_dir.mkdir(parents=True, exist_ok=True)
    vol_path = vol_dir / "was_mq_postgres_fake.json"
    vol_path.write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8")
    # 同時相容舊檔名
    (vol_dir / "was_mq_db2_fake.json").write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("已寫入 Volume：%s", vol_path)

    # 同步更新 mock 資料（供 extractor mock 模式讀取）
    MOCK_LOGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    MOCK_LOGS_PATH.write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("已更新 Mock 資料：%s", MOCK_LOGS_PATH)

    # 印出摘要
    services = {}
    for log in logs:
        svc = log.get("service.name", "unknown")
        lvl = log.get("log.level", "?")
        services.setdefault(svc, {}).setdefault(lvl, 0)
        services[svc][lvl] += 1

    logger.info("── 各服務日誌統計 ──")
    for svc, levels in sorted(services.items()):
        logger.info("  %-25s %s", svc, "  ".join(f"{k}={v}" for k, v in sorted(levels.items())))

    if no_trigger:
        logger.info("--no-trigger 已設定，跳過 API 觸發")
        return

    # 呼叫 REST API 觸發分析
    logger.info("呼叫 /analyse API 觸發分析...")
    try:
        resp = requests.post(
            f"{API_BASE}/analyse",
            json={"from_time": from_time, "to_time": to_time},
            timeout=300,
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info("分析完成！")
        logger.info("  Job ID     : %s", result.get("job_id"))
        logger.info("  Status     : %s", result.get("status"))
        logger.info("  Report     : %s", result.get("report_path"))
        if result.get("error"):
            logger.warning("  Error      : %s", result.get("error"))
        summary = result.get("summary", {})
        if summary:
            logger.info("  總異常數   : %s", summary.get("total_errors"))
            logger.info("  CRITICAL   : %s", summary.get("critical_count"))
    except requests.exceptions.ConnectionError:
        logger.error("無法連線至 elk-analyser API（%s）。請確認 container 已啟動。", API_BASE)
        logger.info("資料已寫入，可手動觸發：")
        logger.info("  curl -X POST %s/analyse \\", API_BASE)
        logger.info('    -H "Content-Type: application/json" \\')
        logger.info('    -d \'{"from_time": "%s", "to_time": "%s"}\'', from_time, to_time)
    except Exception as exc:
        logger.error("API 呼叫失敗：%s", exc)


if __name__ == "__main__":
    main()

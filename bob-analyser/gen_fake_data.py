# Updated: 2026-11-03 10:13:11 +0800
"""
gen_fake_data.py
────────────────
產生與目前實際環境一致的 Demo 模擬日誌，直接寫入 Elasticsearch 專用索引
（FAKE_INDEX），供 elk-analyser 以正常 ES 查詢路徑分析。

實際環境元件（與 japp-demo 一致）：
  - was-liberty        (host: banking-app)  WAS Liberty + BankingMDB
  - activemq-artemis   (host: banking-app)  Artemis bankingQueue
  - postgresql         (host: banking-db)   PostgreSQL 16 / bankdb

每次執行會先刪除 FAKE_INDEX 再重建，確保不殘留舊資料；
不會寫入 /workspace/logs，避免 Bob CLI 讀到非當次分析的檔案。

使用方式：
  podman exec elk-analyser python gen_fake_data.py
  podman exec elk-analyser python gen_fake_data.py --no-trigger
  podman exec elk-analyser python gen_fake_data.py --from "2026-01-15 08:00" --to "2026-01-15 09:00"
  podman exec elk-analyser python gen_fake_data.py --clean      # 只清除，不匯入
  podman exec elk-analyser python gen_fake_data.py --scenario A # 模擬 Kibana Alert：event_time 精準視窗 + 模擬 Instana 資料

四個場景：
  場景 A (TRC-PG-001)：PostgreSQL 連線數耗盡 → Liberty JDBC 連線池逾時 → MDB rollback → Artemis 訊息積壓
  場景 B (TRC-WAS-002)：Liberty JVM 記憶體洩漏 → GC 過載 → 服務降級
  場景 C (TRC-PG-003)：來源帳號不存在 → FK 違反 → MDB rollback → Artemis 重送進 DLQ
  場景 D (TRC-PG-004)：PostgreSQL 磁碟滿 → WAL 無法寫入 → 唯讀 → 全服務阻塞
"""

import json
import logging
import sys
from datetime import datetime, timedelta, timezone

import click
import requests

from config_loader import AppConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

API_BASE = "http://localhost:8080"

# 專用索引：符合 index_pattern "banking-logs-*"，固定名稱方便整批刪除
FAKE_INDEX = "banking-logs-fake-demo"

LIBERTY = ("was-liberty", "banking-app")
ARTEMIS = ("activemq-artemis", "banking-app")
POSTGRES = ("postgresql", "banking-db")


def _build_logs(base_time: datetime) -> list[dict]:
    """依基準時間建構 Liberty / Artemis / PostgreSQL 模擬日誌（巢狀欄位，同真實 ES schema）。"""

    def ev(offset_min: int, offset_sec: int, level: str, comp: tuple, trace: str, msg: str) -> dict:
        ts = (base_time + timedelta(minutes=offset_min, seconds=offset_sec)).astimezone(timezone.utc)
        return {
            "@timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "log": {"level": level},
            "service": comp[0],
            "host": {"name": comp[1]},
            "trace": {"id": trace},
            "message": msg,
            "environment": "demo",
            "synthetic": True,
        }

    a, b, c, d = "TRC-PG-001", "TRC-WAS-002", "TRC-PG-003", "TRC-PG-004"
    return [
        # ── 場景 A：PostgreSQL 連線數耗盡 → Liberty 連線池逾時 → Artemis 積壓 ──
        ev(0, 5,  "ERROR",    POSTGRES, a, "FATAL: remaining connection slots are reserved for non-replication superuser connections. max_connections=100 reached."),
        ev(0, 20, "ERROR",    POSTGRES, a, "FATAL: sorry, too many clients already. active connections=100/100, new requests are being rejected."),
        ev(0, 35, "CRITICAL", POSTGRES, a, "ERROR: deadlock detected on table banking.accounts. Process 4821 waits for ShareLock on transaction 10023; rolling back."),
        ev(1, 10, "ERROR",    LIBERTY,  a, "J2CA1010E: Connection not available, timed out waiting for 30 seconds on datasource jdbc/BankingDS. Pool exhausted."),
        ev(1, 25, "ERROR",    LIBERTY,  a, "BankingMDB TRANSFER rolled back: java.sql.SQLTransientConnectionException: Cannot get a connection, pool error Timeout waiting for connection."),
        ev(2, 5,  "ERROR",    ARTEMIS,  a, "AMQ224016: message processing failed repeatedly on bankingQueue; consumer rollback count=120, redelivery in progress."),
        ev(2, 30, "ERROR",    ARTEMIS,  a, "AMQ222061: bankingQueue message count=48210 and growing; consumer bankingMDB is not acknowledging messages."),
        ev(3, 0,  "CRITICAL", ARTEMIS,  a, "AMQ224016: bankingQueue messages exceeded max-delivery-attempts and were moved to DLQ. DLQ depth=12450."),
        ev(3, 20, "FATAL",    LIBERTY,  a, "Banking transfer processing unavailable: JDBC pool exhausted and bankingQueue consumers stalled. Immediate intervention required."),

        # ── 場景 B：Liberty JVM 記憶體洩漏 → GC 過載 → 服務降級 ──────────────
        ev(10, 0,  "ERROR",    LIBERTY,  b, "JVMJ9VM015W: Initialization error. java.lang.OutOfMemoryError: GC overhead limit exceeded. JVM flags: -Xmx512m -Xms256m"),
        ev(10, 30, "ERROR",    LIBERTY,  b, "SRVE0777E: Exception thrown by application class com.example.banking.cache.AccountCache.loadAll: java.lang.OutOfMemoryError: Java heap space"),
        ev(11, 0,  "ERROR",    LIBERTY,  b, "Heap usage above 90% threshold; full GC triggered repeatedly. Transfer API latency avg=8500ms (normal: 200ms)."),
        ev(11, 45, "CRITICAL", LIBERTY,  b, "BankingMDB onMessage exceeded 30000ms; transaction timeout. Thread dump initiated."),
        ev(12, 15, "ERROR",    POSTGRES, b, "LOG: duration: 285000.123 ms statement: SELECT * FROM banking.transactions WHERE status='PENDING' (rows scanned: 2.3M)"),
        ev(12, 45, "FATAL",    LIBERTY,  b, "JVMDUMP039I: Processing dump event systhrow, detail java/lang/OutOfMemoryError. Heap dump written; server process will restart."),
        ev(13, 5,  "ERROR",    ARTEMIS,  b, "AMQ222061: bankingQueue consumer disconnected unexpectedly. Unacknowledged messages=3421 will be redelivered."),

        # ── 場景 C：來源帳號不存在 → FK 違反 → MDB rollback → DLQ ────────────
        ev(20, 0,  "ERROR",    LIBERTY,  c, "SRVE0293E: REST transfer validation rejected repeated invalid requests with HTTP 400. count=12."),
        ev(20, 15, "ERROR",    POSTGRES, c, 'ERROR: insert or update on table "transactions" violates foreign key constraint "transactions_from_account_fkey"'),
        ev(20, 30, "ERROR",    LIBERTY,  c, "BankingMDB TRANSFER rolled back: java.sql.SQLException: 來源帳號不存在. failed_messages=12."),
        ev(20, 45, "CRITICAL", POSTGRES, c, "ERROR: transaction rollback spike detected for banking.transactions; foreign account lookup failed."),
        ev(21, 5,  "ERROR",    ARTEMIS,  c, "AMQ224016: message processing failed repeatedly on bankingQueue; consumer rollback count=12."),
        ev(21, 30, "FATAL",    LIBERTY,  c, "Banking transfer error rate exceeded demo threshold: invalid requests and MDB database rollbacks correlated."),

        # ── 場景 D：PostgreSQL 磁碟滿 → WAL 無法寫入 → 全服務阻塞 ────────────
        ev(35, 0,  "ERROR",    POSTGRES, d, "PANIC: could not write to file pg_wal/000000010000000100000002: No space left on device."),
        ev(35, 20, "CRITICAL", POSTGRES, d, "FATAL: terminating connection due to administrator command: disk full on WAL directory. All write transactions rolled back."),
        ev(35, 45, "ERROR",    LIBERTY,  d, "BankingMDB TRANSFER rolled back: org.postgresql.util.PSQLException: ERROR: could not extend file: No space left on device. failed_orders=8823."),
        ev(36, 10, "ERROR",    ARTEMIS,  d, "AMQ224016: bankingQueue backlog growing; consumer rollback count=8823. Message count=21840."),
        ev(36, 30, "FATAL",    POSTGRES, d, "PostgreSQL is in read-only/emergency mode: WAL volume 100% full. Database writes are blocked."),
        ev(37, 0,  "FATAL",    LIBERTY,  d, "Banking transfer service unavailable: datasource jdbc/BankingDS failing health check, all write requests return HTTP 500."),
        ev(37, 20, "CRITICAL", ARTEMIS,  d, "AMQ224016: bankingQueue depth=48290 reached address-full-policy limit; producers are being blocked."),
    ]


# ── Alert 情境：event_time（相對 base_time）與模擬 Instana 資料 ─────────────
# event_time 以後端視窗「前 5 分 / 後 2 分」（api._resolve_query_window）涵蓋該場景全部日誌為準。
_SCENARIO_ALERTS: dict[str, dict] = {
    "A": {"trace": "TRC-PG-001", "event": (1, 30), "spike_service": "postgresql",
          "rule_name": "banking-error-spike (PostgreSQL 連線數耗盡)"},
    "B": {"trace": "TRC-WAS-002", "event": (11, 30), "spike_service": "was-liberty",
          "rule_name": "banking-error-spike (Liberty JVM OOM)"},
    "C": {"trace": "TRC-PG-003", "event": (20, 30), "spike_service": "postgresql",
          "rule_name": "banking-error-spike (FK 違反 / MDB rollback)"},
    "D": {"trace": "TRC-PG-004", "event": (36, 0), "spike_service": "postgresql",
          "rule_name": "banking-error-spike (PostgreSQL 磁碟滿)"},
}

_ERROR_LEVELS = ("ERROR", "CRITICAL", "FATAL")


def _iso_utc(base_time: datetime, minutes: int, seconds: int = 0) -> str:
    return (base_time + timedelta(minutes=minutes, seconds=seconds)).astimezone(timezone.utc).isoformat()


def _build_instana_contexts(base_time: datetime) -> dict[str, dict]:
    """
    依場景建構模擬的 instana_context（schema 同 preprocessor._build_instana_context）。
    Instana 事件刻意早於第一筆 log，讓分析可呈現「Instana 先偵測、log 隨後出現」的時序；
    各項數值與 _build_logs() 的訊息一致，且可區分根因層級（DB / JVM / 業務規則 / 磁碟）。
    """
    def ctx(scenario: str, events: list, endpoint_metrics: list,
            trace_summary: list, jvm: dict, artemis: dict) -> dict:
        em, es = _SCENARIO_ALERTS[scenario]["event"]
        return {
            "available": True,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "application_id": "banking-app-liberty",
            "query_window": {"from": _iso_utc(base_time, em - 5, es),
                             "to": _iso_utc(base_time, em + 2, es)},
            "events": events,
            "endpoint_metrics": endpoint_metrics,
            "trace_summary": trace_summary,
            "infra_metrics": {"jvm": jvm, "artemis": artemis},
        }

    def evt(eid: str, typ: str, sev: int, problem: str, label: str, start: tuple) -> dict:
        return {"event_id": eid, "type": typ, "severity": sev, "problem": problem,
                "entity_label": label, "start": _iso_utc(base_time, *start),
                "end": None, "state": "open"}

    def ep(name: str, calls: int, errors: int, p95: float) -> dict:
        return {"endpoint": name, "calls": calls, "errors": errors,
                "error_rate_pct": round(errors / calls * 100, 2), "latency_p95_ms": p95}

    def tr(svc: str, total: int, errors: int) -> dict:
        return {"service": svc, "error_traces": errors, "total_traces": total,
                "error_rate_pct": round(errors / total * 100, 2)}

    return {
        # A：DB 連線耗盡；JVM 正常 → 排除 JVM，指向 DB 連線數
        "A": ctx("A",
            [evt("demo-a-1", "incident", 10, "PostgreSQL active connections 100/100 (saturation)", "banking-db / postgresql", (0, -40)),
             evt("demo-a-2", "issue", 5, "JDBC pool wait time high on jdbc/BankingDS", "was-liberty", (0, 50))],
            [ep("postgresql", 5200, 4100, 30000.0), ep("was-liberty", 3100, 2300, 31200.0),
             ep("activemq-artemis", 4800, 3900, 1200.0)],
            [tr("was-liberty", 3100, 2300), tr("postgresql", 5200, 4100)],
            {"heap_used_mb": 310.5, "gc_pause_ms": 45.0, "thread_count": 412},
            {"message_count": 48210, "messages_expired": 12450}),
        # B：Liberty heap 接近 -Xmx512m、GC 長暫停；PG 本身錯誤率低 → 根因在 JVM
        "B": ctx("B",
            [evt("demo-b-1", "issue", 5, "JVM heap usage above 90% on was-liberty", "was-liberty", (9, 20)),
             evt("demo-b-2", "incident", 10, "Transfer API latency 8500ms (baseline 200ms)", "was-liberty", (10, 40))],
            [ep("was-liberty", 2600, 1100, 12000.0), ep("postgresql", 1900, 40, 4200.0),
             ep("activemq-artemis", 1500, 210, 900.0)],
            [tr("was-liberty", 2600, 1100), tr("postgresql", 1900, 40)],
            {"heap_used_mb": 505.0, "gc_pause_ms": 3800.0, "thread_count": 380},
            {"message_count": 3421, "messages_expired": 0}),
        # C：低延遲但 DB 業務規則（FK）錯誤 → 非資源問題，是不合法請求
        "C": ctx("C",
            [evt("demo-c-1", "issue", 5, "Erroneous call rate high on POST /transfer (HTTP 400)", "was-liberty", (19, 40)),
             evt("demo-c-2", "issue", 5, "DB constraint violation spike on banking.transactions", "banking-db / postgresql", (19, 55))],
            [ep("was-liberty", 640, 12, 180.0), ep("postgresql", 640, 12, 35.0),
             ep("activemq-artemis", 640, 12, 60.0)],
            [tr("was-liberty", 640, 12), tr("postgresql", 640, 12)],
            {"heap_used_mb": 280.0, "gc_pause_ms": 30.0, "thread_count": 150},
            {"message_count": 12, "messages_expired": 0}),
        # D：DB 磁碟滿 → PostgreSQL 100% 錯誤，所有寫入阻塞
        "D": ctx("D",
            [evt("demo-d-1", "incident", 10, "Disk usage 100% on banking-db (pg_wal volume)", "banking-db", (34, 10)),
             evt("demo-d-2", "incident", 10, "PostgreSQL write failures: No space left on device", "banking-db / postgresql", (34, 50))],
            [ep("postgresql", 9100, 9100, 5.0), ep("was-liberty", 8900, 8823, 200.0),
             ep("activemq-artemis", 8900, 8823, 150.0)],
            [tr("was-liberty", 8900, 8823), tr("postgresql", 9100, 9100)],
            {"heap_used_mb": 340.0, "gc_pause_ms": 50.0, "thread_count": 300},
            {"message_count": 48290, "messages_expired": 0}),
    }


def _build_alert_payload(scenario: str, base_time: datetime, logs: list[dict]) -> dict:
    """
    模擬 Kibana Alert 觸發：event_time（精準視窗）+ es_agg_summary（由該場景日誌彙總）
    + 模擬 instana_context。trigger 刻意不用 *_instana，避免走真實 Instana collector。
    spike_service 只放在 es_agg_summary，不放頂層（頂層會讓 ES 只拉單一服務、失去跨服務日誌）。
    """
    alert = _SCENARIO_ALERTS[scenario]
    em, es = alert["event"]
    event_dt = (base_time + timedelta(minutes=em, seconds=es))
    errs = [l for l in logs if l["trace"]["id"] == alert["trace"] and l["log"]["level"] in _ERROR_LEVELS]
    by_service: dict[str, int] = {}
    for l in errs:
        by_service[l["service"]] = by_service.get(l["service"], 0) + 1
    return {
        "trigger": "kibana_alert",
        "event_time": event_dt.isoformat(),
        "es_agg_summary": {
            "rule_name": alert["rule_name"],
            "spike_service": alert["spike_service"],
            "total_errors": len(errs),
            "error_by_service": by_service,
            "first_error_time": min(l["@timestamp"] for l in errs),
        },
        "instana_context": _build_instana_contexts(base_time)[scenario],
    }


def _es_base() -> tuple[str, dict]:
    """由 AppConfig 取得 ES base URL 與 requests 共用參數（不直接讀 config.yaml）。"""
    es = AppConfig.load().es_config
    kwargs: dict = {"timeout": es.get("timeout", 30)}
    if es.get("username"):
        kwargs["auth"] = (es["username"], es.get("password", ""))
    return f"{es['scheme']}://{es['host']}:{es['port']}", kwargs


def _delete_fake_index(base: str, kw: dict) -> None:
    resp = requests.delete(f"{base}/{FAKE_INDEX}", **kw)
    if resp.status_code not in (200, 404):
        resp.raise_for_status()
    logger.info("已清除索引 %s（HTTP %d）", FAKE_INDEX, resp.status_code)


def _bulk_index(base: str, kw: dict, logs: list[dict]) -> None:
    lines = []
    for doc in logs:
        lines.append(json.dumps({"index": {"_index": FAKE_INDEX}}))
        lines.append(json.dumps(doc, ensure_ascii=False))
    resp = requests.post(
        f"{base}/_bulk?refresh=wait_for",
        data=("\n".join(lines) + "\n").encode("utf-8"),
        headers={"Content-Type": "application/x-ndjson"},
        **kw,
    )
    resp.raise_for_status()
    if resp.json().get("errors"):
        raise RuntimeError(f"ES bulk 部分失敗：{resp.text[:500]}")
    logger.info("已寫入 %d 筆至索引 %s", len(logs), FAKE_INDEX)


@click.command()
@click.option("--from", "from_time", default="2026-01-15 08:00",
              help="日誌基準開始時間 (YYYY-MM-DD HH:MM，容器本地時區)")
@click.option("--to",   "to_time",   default="2026-01-15 09:00",
              help="觸發分析的結束時間 (YYYY-MM-DD HH:MM)")
@click.option("--no-trigger", is_flag=True, default=False,
              help="僅寫入資料，不呼叫 API 觸發分析")
@click.option("--clean", is_flag=True, default=False,
              help="僅清除 Demo 資料，不匯入")
@click.option("--scenario", type=click.Choice(list(_SCENARIO_ALERTS)), default=None,
              help="模擬 Kibana Alert 觸發：以該場景的 event_time 精準視窗 + 模擬 Instana 資料分析"
                   "（未指定則維持整段 from/to 分析、不帶 Instana）")
def main(from_time: str, to_time: str, no_trigger: bool, clean: bool, scenario: str | None):
    """清除舊 Demo 資料，匯入與實際環境一致的 Liberty/Artemis/PostgreSQL 模擬日誌。"""

    try:
        base_dt = datetime.strptime(from_time, "%Y-%m-%d %H:%M").replace(
            tzinfo=datetime.now().astimezone().tzinfo)  # 容器本地時區，與 API 解析一致
    except ValueError:
        logger.error("from_time 格式錯誤，請使用 YYYY-MM-DD HH:MM")
        sys.exit(1)

    try:
        base, kw = _es_base()
        _delete_fake_index(base, kw)
        if clean:
            return
        logs = _build_logs(base_dt)
        _bulk_index(base, kw, logs)
    except Exception as exc:
        logger.error("寫入 Elasticsearch 失敗：%s", exc)
        sys.exit(1)

    services: dict = {}
    for log in logs:
        lvl = log["log"]["level"]
        services.setdefault(log["service"], {}).setdefault(lvl, 0)
        services[log["service"]][lvl] += 1
    logger.info("── 各服務日誌統計 ──")
    for svc, levels in sorted(services.items()):
        logger.info("  %-25s %s", svc, "  ".join(f"{k}={v}" for k, v in sorted(levels.items())))

    if no_trigger:
        logger.info("--no-trigger 已設定，跳過 API 觸發")
        return

    if scenario:
        body = _build_alert_payload(scenario, base_dt, logs)
        logger.info("模擬 Kibana Alert（場景 %s）：event_time=%s，附模擬 instana_context（events=%d）",
                    scenario, body["event_time"], len(body["instana_context"]["events"]))
    else:
        body = {"from_time": from_time, "to_time": to_time}

    logger.info("呼叫 /analyse API 觸發分析...")
    try:
        resp = requests.post(f"{API_BASE}/analyse", json=body, timeout=300)
        resp.raise_for_status()
        result = resp.json()
        logger.info("分析完成！Job ID=%s Status=%s Report=%s",
                    result.get("job_id"), result.get("status"), result.get("report_path"))
        if result.get("error"):
            logger.warning("  Error      : %s", result.get("error"))
    except requests.exceptions.ConnectionError:
        logger.error("無法連線至 elk-analyser API（%s）。請確認 container 已啟動。", API_BASE)
    except Exception as exc:
        logger.error("API 呼叫失敗：%s", exc)


if __name__ == "__main__":
    main()

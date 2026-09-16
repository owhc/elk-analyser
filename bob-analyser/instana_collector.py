# Created: 2026-09-15 19:58:12 +0800
# Updated: 2026-09-15 21:43:39 +0800
"""
instana_collector.py
────────────────────
封裝 Instana REST API 歷史資料收集。
由 api.py 在 pipeline 前同步呼叫，不需定時排程。

collect(from_time, to_time) -> dict

回傳符合 instana_context JSON Schema 的 dict：
  {
    "available": true,
    "collected_at": "<ISO8601>",
    "application_id": "<str>",
    "query_window": { "from": "<ISO8601>", "to": "<ISO8601>" },
    "events": [...],
    "endpoint_metrics": [...],
    "trace_summary": [...],
    "infra_metrics": { "jvm": {...}, "artemis": {...} }
  }

任何子項目失敗只 log warning，仍回傳部分資料（available=true）。
全部失敗時回傳 { "available": false, "error": "..." }。
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _load_config(config_path: str = "/app/config/config.yaml") -> dict:
    """載入 instana 設定區塊，API token 優先使用環境變數。"""
    import yaml
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = dict(yaml.safe_load(f).get("instana", {}))
    env_token = os.environ.get("INSTANA_API_TOKEN")
    if env_token:
        cfg["api_token"] = env_token
    return cfg


def _to_ms(dt: datetime) -> int:
    """datetime → Unix milliseconds。"""
    return int(dt.timestamp() * 1000)


def _make_session(api_token: str):
    """建立帶認證標頭的 requests.Session。"""
    import requests
    s = requests.Session()
    s.headers.update({
        "Authorization": f"apiToken {api_token}",
        "Content-Type": "application/json",
    })
    return s


def collect_events(
    session,
    base_url: str,
    from_ms: int,
    to_ms: int,
) -> list:
    """
    GET /api/events — 同時間窗口的 Issues/Incidents。
    回傳正規化後的事件列表，失敗回傳空列表。
    """
    try:
        resp = session.get(
            f"{base_url}/api/events",
            params={
                "from": from_ms,
                "to": to_ms,
                "windowSize": to_ms - from_ms,
            },
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json()
        items = raw if isinstance(raw, list) else raw.get("events", raw.get("items", []))
        result = []
        for ev in items:
            # 正規化 Instana event 格式，並排除不在查詢窗口內的事件。
            start_ms = ev.get("start") or ev.get("startTime") or 0
            end_ms = ev.get("end") or ev.get("endTime")
            if start_ms > to_ms or (end_ms and end_ms < from_ms):
                continue
            result.append({
                "event_id": str(ev.get("id", "")),
                "type": ev.get("type", "issue").lower(),
                "severity": int(ev.get("severity", 5)),
                "problem": ev.get("problem") or ev.get("title") or ev.get("name", ""),
                "entity_label": ev.get("entityLabel") or ev.get("fixedInApplicationIds", [""])[0]
                    if isinstance(ev.get("fixedInApplicationIds"), list)
                    else ev.get("entityLabel", ""),
                "start": datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat()
                    if start_ms else None,
                "end": datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).isoformat()
                    if end_ms else None,
                "state": ev.get("state", "open"),
            })
        result.sort(key=lambda ev: ev.get("start") or "", reverse=True)
        result = result[:50]
        logger.info("Instana events 收集完成：%d 筆", len(result))
        return result
    except Exception as exc:
        logger.warning("collect_events 失敗（忽略，繼續）：%s", exc)
        return []


def collect_endpoint_metrics(
    session,
    base_url: str,
    from_ms: int,
    to_ms: int,
    app_id: str,
) -> list:
    """
    POST /api/application-monitoring/metrics/applications — grouped calls metrics。
    回傳 endpoint 層級的 calls/errors/latency P95，失敗回傳空列表。
    """
    try:
        payload = {
            "metrics": [
                {"metric": "calls", "aggregation": "SUM"},
                {"metric": "erroneousCalls", "aggregation": "SUM"},
                {"metric": "latency", "aggregation": "P95"},
            ],
            "group": {
                "groupbyTag": "endpoint.name",
                "groupbyTagEntity": "DESTINATION",
            },
            "tagFilterExpression": {
                "type": "TAG_FILTER",
                "name": "application.name",
                "operator": "EQUALS",
                "entity": "DESTINATION",
                "value": "banking-app-liberty",
            },
            "timeFrame": {
                "to": to_ms,
                "windowSize": to_ms - from_ms,
            },
            "pagination": {"page": 1, "pageSize": 50},
            "includeInternal": False,
            "includeSynthetic": False,
        }
        resp = session.post(
            f"{base_url}/api/application-monitoring/metrics/applications",
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        result = []
        for item in items:
            metrics = item.get("metrics", {})
            calls_vals = metrics.get("calls.SUM") or metrics.get("calls.sum", [[0, 0]])
            errors_vals = metrics.get("erroneousCalls.SUM") or metrics.get("erroneousCalls.sum", [[0, 0]])
            latency_vals = metrics.get("latency.P95") or metrics.get("latency.p95", [[0, 0]])
            calls = sum(v[1] for v in calls_vals if isinstance(v, list) and len(v) > 1) if calls_vals else 0
            errors = sum(v[1] for v in errors_vals if isinstance(v, list) and len(v) > 1) if errors_vals else 0
            latency = latency_vals[-1][1] if latency_vals and isinstance(latency_vals[-1], list) and len(latency_vals[-1]) > 1 else 0.0
            error_rate = round((errors / calls * 100) if calls > 0 else 0.0, 2)
            endpoint_name = (item.get("tags") or {}).get("endpoint.name", "unknown")
            result.append({
                "endpoint": endpoint_name,
                "calls": int(calls),
                "errors": int(errors),
                "error_rate_pct": error_rate,
                "latency_p95_ms": round(float(latency or 0), 2),
            })
        logger.info("Instana endpoint metrics 收集完成：%d 個 endpoint", len(result))
        return result
    except Exception as exc:
        logger.warning("collect_endpoint_metrics 失敗（忽略，繼續）：%s", exc)
        return []


def collect_trace_summary(
    session,
    base_url: str,
    from_ms: int,
    to_ms: int,
    app_id: str,
) -> list:
    """
    POST /api/application-monitoring/analyze/traces/groups — error trace 彙總。
    按 service 分組，回傳 error_traces / total_traces / error_rate_pct，失敗回傳空列表。
    """
    try:
        payload = {
            "group": {
                "groupbyTag": "trace.service.name",
                "groupbyTagEntity": "DESTINATION",
            },
            "metrics": [
                {"metric": "traces", "aggregation": "SUM"},
            ],
            "timeFrame": {
                "to": to_ms,
                "windowSize": to_ms - from_ms,
            },
            "tagFilterExpression": {
                "type": "TAG_FILTER",
                "name": "application.name",
                "operator": "EQUALS",
                "entity": "DESTINATION",
                "value": "banking-app-liberty",
            },
            "includeInternal": False,
            "includeSynthetic": False,
            "pagination": {"page": 1, "pageSize": 20},
        }
        resp = session.post(
            f"{base_url}/api/application-monitoring/analyze/traces/groups",
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        result = []
        for item in items:
            metrics = item.get("metrics", {})
            total_vals = metrics.get("traces.SUM", [[0, 0]])
            total = sum(v[1] for v in total_vals if isinstance(v, list) and len(v) > 1) if total_vals else 0
            service_name = (item.get("tags") or {}).get("trace.service.name", "unknown")
            # Instana trace groups API does not directly expose error count per group;
            # we estimate from the erroneousCallsPercentage tag if available
            error_rate = float(
                (item.get("tags") or {}).get("erroneousCallsPercentage", 0.0) or 0.0
            )
            error_traces = int(total * error_rate / 100) if total > 0 else 0
            result.append({
                "service": service_name,
                "error_traces": error_traces,
                "total_traces": int(total),
                "error_rate_pct": round(error_rate, 2),
            })
        logger.info("Instana trace summary 收集完成：%d 個 service", len(result))
        return result
    except Exception as exc:
        logger.warning("collect_trace_summary 失敗（忽略，繼續）：%s", exc)
        return []


def collect_infra_metrics(
    session,
    base_url: str,
    from_ms: int,
    to_ms: int,
) -> dict:
    """
    收集 JVM + Artemis infra metrics（config 開關控制）。
    失敗時回傳空 dict。
    """
    result = {}

    # JVM heap / GC / thread（查 jvmRuntimePlatform plugin）
    try:
        jvm_payload = {
            "type": "jvmRuntimePlatform",
            "metrics": [
                {"metric": "memory.heapUsed", "granularity": (to_ms - from_ms) // 1000 or 3600, "aggregation": "MAX"},
                {"metric": "gc.pause", "granularity": (to_ms - from_ms) // 1000 or 3600, "aggregation": "MAX"},
                {"metric": "threads.total", "granularity": (to_ms - from_ms) // 1000 or 3600, "aggregation": "MAX"},
            ],
            "timeFrame": {"to": to_ms, "windowSize": to_ms - from_ms},
            "tagFilterExpression": {"type": "EXPRESSION", "logicalOperator": "AND", "elements": []},
            "pagination": {"retrievalSize": 5},
        }
        resp = session.post(
            f"{base_url}/api/infrastructure-monitoring/analyze/infrastructure-entities",
            json=jvm_payload,
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if items:
            m = items[0].get("metrics", {})
            def _last_val(series, divisor=1):
                vals = series or []
                for v in reversed(vals):
                    if isinstance(v, list) and len(v) > 1 and v[1] is not None:
                        return round(v[1] / divisor, 2)
                return 0.0
            result["jvm"] = {
                "heap_used_mb": _last_val(m.get("memory.heapUsed.MAX"), 1024 * 1024),
                "gc_pause_ms": _last_val(m.get("gc.pause.MAX")),
                "thread_count": int(_last_val(m.get("threads.total.MAX"))),
            }
    except Exception as exc:
        logger.warning("collect_infra_metrics JVM 失敗（忽略）：%s", exc)

    # Artemis message count（查 activemqartemis plugin）
    try:
        artemis_payload = {
            "type": "activemqartemis",
            "metrics": [
                {"metric": "messageCount", "granularity": (to_ms - from_ms) // 1000 or 3600, "aggregation": "MAX"},
                {"metric": "messagesExpired", "granularity": (to_ms - from_ms) // 1000 or 3600, "aggregation": "SUM"},
            ],
            "timeFrame": {"to": to_ms, "windowSize": to_ms - from_ms},
            "tagFilterExpression": {"type": "EXPRESSION", "logicalOperator": "AND", "elements": []},
            "pagination": {"retrievalSize": 5},
        }
        resp = session.post(
            f"{base_url}/api/infrastructure-monitoring/analyze/infrastructure-entities",
            json=artemis_payload,
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if items:
            m = items[0].get("metrics", {})
            def _sum_val(series):
                vals = series or []
                return sum(v[1] for v in vals if isinstance(v, list) and len(v) > 1 and v[1] is not None)
            result["artemis"] = {
                "message_count": int(_sum_val(m.get("messageCount.MAX"))),
                "messages_expired": int(_sum_val(m.get("messagesExpired.SUM"))),
            }
    except Exception as exc:
        logger.warning("collect_infra_metrics Artemis 失敗（忽略）：%s", exc)

    return result


def collect(
    from_time: datetime,
    to_time: datetime,
    config_path: str = "/app/config/config.yaml",
) -> dict:
    """
    主函式：用已知時間窗口查詢 Instana 歷史 API，回傳 instana_context dict。

    成功：{ "available": true, "events": [...], "endpoint_metrics": [...], ... }
    失敗：{ "available": false, "error": "..." }
    """
    instana_cfg = _load_config(config_path)

    if not instana_cfg.get("enabled", False):
        return {"available": False, "error": "Instana 整合未啟用（instana.enabled=false）"}

    api_token = instana_cfg.get("api_token", "")
    if not api_token:
        return {"available": False, "error": "Instana API token 未設定"}

    base_url = instana_cfg.get("base_url", "").rstrip("/")
    app_id = instana_cfg.get("application_id", "")
    collect_infra = instana_cfg.get("collect_infra_metrics", False)

    from_ms = _to_ms(from_time)
    to_ms = _to_ms(to_time)

    session = _make_session(api_token)

    ctx = {
        "available": True,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "application_id": app_id,
        "query_window": {
            "from": from_time.isoformat(),
            "to": to_time.isoformat(),
        },
    }

    collected_any = False

    events = collect_events(session, base_url, from_ms, to_ms)
    ctx["events"] = events
    if events:
        collected_any = True

    endpoint_metrics = collect_endpoint_metrics(session, base_url, from_ms, to_ms, app_id)
    ctx["endpoint_metrics"] = endpoint_metrics
    if endpoint_metrics:
        collected_any = True

    trace_summary = collect_trace_summary(session, base_url, from_ms, to_ms, app_id)
    ctx["trace_summary"] = trace_summary
    if trace_summary:
        collected_any = True

    if collect_infra:
        infra = collect_infra_metrics(session, base_url, from_ms, to_ms)
        ctx["infra_metrics"] = infra
    else:
        ctx["infra_metrics"] = {}

    if not collected_any:
        logger.warning("Instana 所有收集項目均為空，降級為 available=false")
        ctx["available"] = False
        ctx["error"] = "所有 Instana API 呼叫均回傳空資料"

    logger.info(
        "Instana 收集完成：events=%d endpoint_metrics=%d trace_summary=%d",
        len(ctx.get("events", [])),
        len(ctx.get("endpoint_metrics", [])),
        len(ctx.get("trace_summary", [])),
    )
    return ctx

# Updated: 2026-09-17 08:54:33 +0800
"""
analyser/preprocessor.py
─────────────────────────
將原始日誌列表轉換為結構化事件序列，供 Bob Bridge 傳給 AI 分析。

分組策略（ADR-0001）：
  1. 優先依 Trace ID 分組（若欄位存在且非空）
  2. Fallback：依時間窗口（5 分鐘內的事件視為同一群組）

輸出格式對應 Bob Shell 輸入契約：
  {
    "job_id": str,
    "query_window": { "from": str, "to": str },
    "total_raw_count": int,
    "event_chains": [
      {
        "chain_id": str,          # trace_id 或 time_window_xxx
        "events": [
          { "timestamp": str, "level": str, "service": str,
            "message": str, "host": str }
        ]
      }
    ]
  }
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# 時間窗口分組的間隔（Fallback 策略）
TIME_WINDOW_MINUTES = 5


def _safe_get(doc: dict, field_path: str) -> str:
    """
    支援巢狀欄位路徑（如 "log.level"）的安全取值。
    找不到欄位時回傳空字串，不拋出例外。
    """
    parts = field_path.split(".")
    val = doc
    for part in parts:
        if not isinstance(val, dict):
            return ""
        val = val.get(part, "")
    return str(val) if val is not None else ""


def _normalise_event(doc: dict, fm: dict, idx: int = 0) -> Optional[dict]:
    """
    將原始 ES 文件正規化為統一的事件字典。
    若時間欄位無法解析，回傳 None（由上層過濾）。
    idx 為來源列表的枚舉序號，用於 trace_event_indices 的 O(n) 集合判斷（F-04）。
    """
    ts_str = _safe_get(doc, fm["timestamp"])
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        logger.debug("無法解析時間欄位，跳過此筆：%s", ts_str)
        return None

    return {
        "timestamp": ts.isoformat(),
        "level": _safe_get(doc, fm["level"]).upper(),
        "service": _safe_get(doc, fm.get("service", "service.name")),
        "message": _safe_get(doc, fm["message"]),
        "host": _safe_get(doc, fm.get("host", "host.name")),
        "trace_id": _safe_get(doc, fm.get("trace_id", "trace.id")),
        "_ts": ts,    # 內部排序用，最終輸出時移除
        "_idx": idx,  # 來源序號，用於 O(n) 歸屬判斷，最終輸出時移除
    }


def _group_by_trace_id(events: list[dict]) -> dict[str, list[dict]]:
    """依 Trace ID 分組，忽略 trace_id 為空的事件（留給 Fallback）。"""
    groups: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        tid = ev.get("trace_id", "")
        if tid:
            groups[tid].append(ev)
    return dict(groups)


def _group_by_time_window(events: list[dict]) -> dict[str, list[dict]]:
    """
    將沒有 Trace ID 的事件，依時間窗口（TIME_WINDOW_MINUTES）分組。
    同一個窗口內的事件合併為一個 chain。
    """
    # 先依時間排序
    sorted_events = sorted(events, key=lambda e: e["_ts"])
    groups: dict[str, list[dict]] = defaultdict(list)

    window_start: Optional[datetime] = None
    window_key: Optional[str] = None

    for ev in sorted_events:
        ts: datetime = ev["_ts"]
        if window_start is None or (ts - window_start) > timedelta(minutes=TIME_WINDOW_MINUTES):
            # 開啟新視窗
            window_start = ts
            window_key = f"time_window_{ts.strftime('%Y%m%d_%H%M%S')}"
        groups[window_key].append(ev)  # type: ignore[index]

    return dict(groups)


def _clean_event(ev: dict) -> dict:
    """移除內部輔助欄位（_ts、_idx），回傳乾淨的輸出事件。"""
    return {k: v for k, v in ev.items() if k not in ("_ts", "_idx")}


def _build_instana_context(raw: Optional[dict]) -> dict:
    """
    正規化並驗證 instana_context 結構。
    raw 為 None 時回傳 { available: false }。
    raw 已含 available=false 時直接回傳。
    """
    if not raw:
        return {"available": False}
    if not raw.get("available", True):
        # 明確標記失敗（如 collect 失敗）
        return {"available": False, "error": raw.get("error", "unknown")}
    return {
        "available": True,
        "collected_at": raw.get("collected_at", ""),
        "application_id": raw.get("application_id", ""),
        "query_window": raw.get("query_window", {}),
        "events": raw.get("events", []),
        "endpoint_metrics": raw.get("endpoint_metrics", []),
        "trace_summary": raw.get("trace_summary", []),
        "infra_metrics": raw.get("infra_metrics", {}),
    }


def preprocess(
    raw_logs: list[dict],
    job_id: str,
    query_from: datetime,
    query_to: datetime,
    config=None,
    instana_context: Optional[dict] = None,
    es_agg_summary: Optional[dict] = None,
) -> dict:
    """
    主要預處理函式。

    參數：
        raw_logs         extractor.extract() 回傳的原始日誌列表
        job_id           分析任務 ID（用於產出 JSON 的標識）
        query_from       查詢視窗開始時間
        query_to         查詢視窗結束時間
        config           AppConfig 實例（或含 field_mapping 的 dict，向後相容）
        instana_context  由 api.py 解析後傳入的 Instana 資料 dict；
                         None 表示純 ELK 模式（向後相容）
        es_agg_summary   層三精準限縮：Kibana 帶來的 ES 聚合摘要；
                         非 None 時直接注入 event_sequence 頂層，
                         供 Bob 在分析前優先參考統計全貌，不需再從原始事件重算

    回傳：
        符合 Bob Shell 輸入契約的事件序列字典（含頂層 instana_context）
    """
    if config is None:
        from config_loader import AppConfig
        config = AppConfig.load()
    # 支援 AppConfig 物件或原始 dict
    fm = config.field_mapping if hasattr(config, "field_mapping") else config["field_mapping"]

    # Step 1：正規化，過濾無法解析的紀錄；_idx 攜帶來源序號供後續 O(n) 判斷
    events: list[dict] = []
    skipped = 0
    for i, doc in enumerate(raw_logs):
        ev = _normalise_event(doc, fm, idx=i)
        if ev is None:
            skipped += 1
        else:
            events.append(ev)

    if skipped:
        logger.warning("跳過 %d 筆無法解析的日誌", skipped)

    # Step 2：依 Trace ID 分組（優先）
    trace_groups = _group_by_trace_id(events)
    # 以 _idx 集合（O(n)）識別已歸入 trace group 的事件，避免 O(n²) 值比對（F-04）
    trace_event_indices = {
        ev["_idx"]
        for evs in trace_groups.values()
        for ev in evs
    }

    # Step 3：剩餘無 Trace ID 的事件，改用時間窗口分組
    no_trace_events = [ev for ev in events if ev["_idx"] not in trace_event_indices]
    time_groups = _group_by_time_window(no_trace_events)

    all_groups = {**trace_groups, **time_groups}

    # Step 4：組裝輸出結構
    event_chains = []
    for chain_id, chain_events in all_groups.items():
        sorted_chain = sorted(chain_events, key=lambda e: e["_ts"])
        event_chains.append({
            "chain_id": chain_id,
            "events": [_clean_event(ev) for ev in sorted_chain],
        })

    # 依第一個事件的時間排序 chains
    event_chains.sort(key=lambda c: c["events"][0]["timestamp"] if c["events"] else "")

    result = {
        "job_id": job_id,
        "query_window": {
            "from": query_from.isoformat(),
            "to": query_to.isoformat(),
        },
        "total_raw_count": len(raw_logs),
        "total_filtered_count": len(events),
        "event_chains": event_chains,
        "instana_context": _build_instana_context(instana_context),
    }

    # 層三精準限縮：將 Kibana 聚合摘要注入頂層
    # Bob 分析時可直接參考全局統計（error_count_by_service、spike_service 等），
    # 不需從有限的 event_chains 樣本重新推斷分布。
    if es_agg_summary:
        result["es_agg_summary"] = es_agg_summary
        logger.info("層三精準限縮：注入 es_agg_summary（keys: %s）",
                    ", ".join(es_agg_summary.keys()))

    logger.info(
        "預處理完成：%d 筆有效事件，分為 %d 個事件鏈（Trace ID: %d，時間窗口: %d）%s",
        len(events),
        len(event_chains),
        len(trace_groups),
        len(time_groups),
        f"，含 es_agg_summary" if es_agg_summary else "",
    )
    return result

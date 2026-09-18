# Updated: 2026-09-17 14:41:25 +0800
"""
analyser/extractor.py
─────────────────────
從 Elasticsearch（或 Mock 資料）提取日誌，並管理 Checkpoint。

主要功能：
  - 依 config.yaml 的 field_mapping 動態對應欄位
  - 支援 mock / local / remote 三種模式
  - Checkpoint 讀寫：確保排程模式下查詢視窗無縫接續
  - 每次查詢分頁（scroll）回傳，避免大量資料 OOM
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Mock 資料提取 ────────────────────────────────────────────────────

def _load_mock_logs(
    config,
    query_from: datetime,
    query_to: datetime,
) -> list[dict]:
    """
    從 mock/sample_logs.json 載入模擬日誌，
    並依查詢視窗與等級過濾回傳。
    """
    mock_path = Path("/app/mock/sample_logs.json")
    if not mock_path.exists():
        logger.warning("Mock 資料檔案不存在：%s", mock_path)
        return []

    with open(mock_path, "r", encoding="utf-8") as f:
        all_logs: list[dict] = json.load(f)

    fm = config.field_mapping if hasattr(config, "field_mapping") else config["field_mapping"]
    filter_cfg = config.filter_config if hasattr(config, "filter_config") else config["filter"]
    allowed_levels = {lv.upper() for lv in filter_cfg["levels"]}
    results = []

    for doc in all_logs:
        # 時間過濾
        ts_str = doc.get(fm["timestamp"], "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if not (query_from <= ts <= query_to):
            continue
        # 等級過濾
        level = str(doc.get(fm["level"], "")).upper()
        if level not in allowed_levels:
            continue
        results.append(doc)

    logger.info("Mock 模式：過濾後共 %d 筆日誌", len(results))
    return results


# ── Elasticsearch 真實查詢 ───────────────────────────────────────────

def _query_elasticsearch(
    config,
    query_from: datetime,
    query_to: datetime,
    spike_service: str = "",
) -> list[dict]:
    """
    透過 elasticsearch-py 查詢真實 ES，支援 scroll 分頁。
    若 ES 套件未安裝或連線失敗，記錄錯誤並回傳空列表。

    spike_service：若非空，加入 terms filter 只拉該服務的日誌（層二精準限縮）。
    """
    try:
        from elasticsearch import Elasticsearch  # type: ignore
    except ImportError:
        logger.error("elasticsearch 套件未安裝，請執行 pip install elasticsearch")
        return []

    es_cfg     = config.es_config      if hasattr(config, "es_config")      else config["elasticsearch"]
    fm         = config.field_mapping  if hasattr(config, "field_mapping")  else config["field_mapping"]
    filter_cfg = config.filter_config  if hasattr(config, "filter_config")  else config["filter"]
    allowed_levels = [lv.upper() for lv in filter_cfg["levels"]]

    # 建立 ES 客戶端
    conn_kwargs: dict = {
        "hosts": [{"host": es_cfg["host"], "port": es_cfg["port"], "scheme": es_cfg["scheme"]}],
        "request_timeout": es_cfg.get("timeout", 30),
    }
    if es_cfg.get("username"):
        conn_kwargs["http_auth"] = (es_cfg["username"], es_cfg["password"])

    try:
        es = Elasticsearch(**conn_kwargs)
    except Exception as exc:
        logger.error("建立 Elasticsearch 連線失敗：%s", exc)
        return []

    # 建構查詢
    must_clauses = [
        {
            "range": {
                fm["timestamp"]: {
                    "gte": query_from.isoformat(),
                    "lte": query_to.isoformat(),
                }
            }
        },
        {
            "terms": {
                fm["level"]: allowed_levels
                + [lv.lower() for lv in allowed_levels]  # 相容小寫
            }
        },
    ]

    # 層二精準限縮：spike_service 非空時，只拉該服務的日誌
    if spike_service:
        must_clauses.append({"term": {fm.get("service", "service.name"): spike_service}})
        logger.info("層二精準限縮：只查詢 service=%s 的日誌", spike_service)

    # 額外關鍵字過濾
    for kw in filter_cfg.get("keywords", []):
        must_clauses.append({"match": {fm["message"]: kw}})

    # 排除 Liberty SystemErr/SystemOut 的「假 ERROR」噪音：
    # Logstash 將 Liberty 的 SystemErr → ERROR，但這些是 Java stderr 輸出，
    # 大量包含正常 INFO 業務日誌而非真正的錯誤。
    # 排除策略：
    #   - module=SystemOut：全排除（Logstash 已轉 INFO，不應出現在 ERROR 查詢中）
    #   - module=SystemErr：排除不含真實錯誤關鍵字的文件（保留含 Exception/SEVERE/violates 的）
    _liberty_error_keywords = [
        "Exception", "SEVERE", "violates", "FAILED", "SQLException",
        "NullPointerException", "OutOfMemoryError", "StackOverflowError",
        "ConnectException", "TimeoutException", "java.lang.Error",
    ]
    must_not_clauses = [
        # 排除 SystemOut（全部）
        {
            "bool": {
                "must": [
                    {"term": {"service.keyword": "was-liberty"}},
                    {"term": {"module.keyword": "SystemOut"}},
                ]
            }
        },
        # 排除 SystemErr 中不含真實錯誤關鍵字的文件
        {
            "bool": {
                "must": [
                    {"term": {"service.keyword": "was-liberty"}},
                    {"term": {"module.keyword": "SystemErr"}},
                ],
                "must_not": [
                    {"bool": {"should": [
                        {"match_phrase": {"message": kw}} for kw in _liberty_error_keywords
                    ], "minimum_should_match": 1}},
                ],
            }
        },
    ]

    query_body = {
        "query": {"bool": {"must": must_clauses, "must_not": must_not_clauses}},
        "sort": [{fm["timestamp"]: "asc"}],
    }

    results = []
    max_hits = filter_cfg.get("max_hits", 5000)

    try:
        # 使用 scroll 分頁，每批 500 筆（F-05）
        # 注意：Scroll API 在 ES 8.x 中已建議改用 search_after + PIT；
        # 若正式環境為 ES 8.x 且出現 deprecation warning，
        # 請規劃遷移至 search_after（無需修改程式邏輯，僅替換此區塊）。
        # elasticsearch-py v8 的 body= 參數亦已棄用，升版時請改用 keyword args。
        resp = es.search(
            index=es_cfg["index_pattern"],
            body=query_body,
            scroll="2m",
            size=min(500, max_hits),
        )
        scroll_id = resp["_scroll_id"]

        while True:
            hits = resp["hits"]["hits"]
            if not hits:
                break
            for hit in hits:
                results.append(hit["_source"])
                if len(results) >= max_hits:
                    break
            if len(results) >= max_hits:
                break
            resp = es.scroll(scroll_id=scroll_id, scroll="2m")

        es.clear_scroll(scroll_id=scroll_id)
        logger.info("Elasticsearch 查詢：共取得 %d 筆日誌", len(results))
    except Exception as exc:
        logger.error("Elasticsearch 查詢失敗：%s", exc)

    return results


# ── 公開 API ─────────────────────────────────────────────────────────

def extract(
    query_from: datetime,
    query_to: datetime,
    config=None,
    spike_service: str = "",
) -> list[dict]:
    """
    主要提取函式。依 config.yaml 的 mode 決定使用 Mock 或真實 ES。

    參數：
        query_from    查詢視窗開始時間（含時區資訊）
        query_to      查詢視窗結束時間（含時區資訊）
        config        AppConfig 實例（或原始 dict，向後相容）
        spike_service 層二精準限縮：非空時 ES 查詢加入 service terms filter

    回傳：
        原始日誌文件列表，每個元素為 ES _source 字典
    """
    if config is None:
        from config_loader import AppConfig
        config = AppConfig.load()
    mode = config.es_mode if hasattr(config, "es_mode") else config["elasticsearch"].get("mode", "mock")
    logger.info(
        "開始提取日誌 [模式=%s] 查詢視窗：%s → %s%s",
        mode,
        query_from.isoformat(),
        query_to.isoformat(),
        f" spike_service={spike_service}" if spike_service else "",
    )

    if mode == "mock":
        return _load_mock_logs(config, query_from, query_to)
    else:
        return _query_elasticsearch(config, query_from, query_to, spike_service=spike_service)

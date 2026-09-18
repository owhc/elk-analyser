# Updated: 2026-09-17 15:13:26 +0800
"""
api.py
──────
REST API 入口（FastAPI）。

端點：
  POST /analyse          觸發主動查詢分析（同步，回傳分析結果）
  POST /analyse/async    觸發主動查詢分析（非同步，立即回傳 job_id）
  GET  /jobs             列出分析任務歷史
  GET  /jobs/{id}        取得特定任務詳細資訊
  DELETE /jobs/{id}      刪除單筆分析任務
  DELETE /jobs           批次刪除多筆任務（body: {"ids": [1,2,3]}）
  GET  /jobs/{id}/report 下載 PPTX 報告檔
  GET  /jobs/{id}/status 輪詢任務執行狀態（供非同步模式使用）
  POST /demo/inject      注入 WAS/MQ/PostgreSQL Demo 模擬資料
  GET  /health           健康檢查
"""

import json
import logging
import queue
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from config_loader import AppConfig
from db.job_repository import JobRepository

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# AppConfig 單例：process 啟動時讀一次，所有請求共用
_cfg = AppConfig.load()

# ── 分析任務 Queue（F-01）────────────────────────────────────────────
# 所有分析請求均放入 _job_queue，由單一 worker thread 序列執行。
# /analyse 與 /analyse/async 皆立即接受（202），不再回傳 409。
# worker thread 在 process 啟動時啟動，daemon=True 隨主程序結束。
_job_queue: queue.Queue = queue.Queue()


def _queue_worker() -> None:
    """單一 worker thread：從 _job_queue 取出任務依序執行。"""
    from analysis_pipeline import run_analysis
    while True:
        item = _job_queue.get()
        if item is None:          # shutdown 訊號（目前未使用，保留）
            break
        req, row_id, result_event, result_box = item
        try:
            qf, qt = _resolve_query_window(req)
        except Exception as exc:
            logger.error("Queue worker 時間解析失敗 [row_id=%s]：%s", row_id, exc)
            if result_event:
                result_box.append({"job_id": str(row_id), "status": "failed",
                                   "report_path": None, "summary": {}, "error": str(exc)})
                result_event.set()
            _job_queue.task_done()
            continue
        trigger_mode = _resolve_trigger_mode(req.trigger, async_mode=(result_event is None))
        instana_ctx  = _resolve_instana_context(req, qf, qt)
        try:
            result = run_analysis(
                qf, qt, trigger_mode,
                instana_context=instana_ctx,
                row_id=row_id,
                spike_service=req.spike_service or "",
                es_agg_summary=req.es_agg_summary,
            )
        except Exception as exc:
            logger.error("Queue worker 分析失敗 [row_id=%s]：%s", row_id, exc, exc_info=True)
            result = {"job_id": str(row_id), "status": "failed",
                      "report_path": None, "summary": {}, "error": str(exc)}
        if result_event:
            result_box.append(result)
            result_event.set()
        _job_queue.task_done()


_worker_thread = threading.Thread(target=_queue_worker, daemon=True, name="analysis-worker")
_worker_thread.start()

# ── Startup 殭屍 job 清理 ─────────────────────────────────────────────
# 容器重啟時，將上次未完成的 running job 標為 failed，防止永久卡死。
def _cleanup_orphaned_jobs() -> None:
    import sqlite3 as _sqlite3
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn = _sqlite3.connect(_cfg.db_path)
        cur = conn.execute(
            "UPDATE analysis_jobs SET status='failed', error_message=?, completed_at=?"
            " WHERE status='running'",
            ("container restarted (orphaned job)", now),
        )
        conn.commit()
        conn.close()
        if cur.rowcount:
            logger.warning("Startup: %d 個殭屍 running job 已標為 failed", cur.rowcount)
    except Exception as exc:
        logger.error("Startup 殭屍清理失敗：%s", exc)

_cleanup_orphaned_jobs()

app = FastAPI(
    title="ELK Analyser API",
    description="自動化日誌分析與報告生成系統 REST API",
    version="1.0.0",
)

# CORS：允許 Web UI（elk-analyser-web container）跨域呼叫
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Nginx proxy 同網路，實際流量不出 Podman network
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


def _repo() -> JobRepository:
    """每次呼叫回傳綁定 db_path 的 JobRepository 實例。"""
    return JobRepository(_cfg.db_path)


# ── Request / Response Models ────────────────────────────────────────

class AnalyseRequest(BaseModel):
    """主動查詢分析請求體。

    from_time 五階段優先序：
      1. from_time 有值         → 直接使用（Web UI / CLI 手動指定）
      2. event_time 有值        → 以事故時間點為中心取精準視窗（Kibana Alert 精準觸發）
      3. lookback_minutes 有值  → to_time - lookback_minutes（Kibana Alert 寬鬆觸發）
      4. 兩者皆無，Checkpoint 有值 → Checkpoint 接續上次分析結束時間
      5. 三者皆無               → now - config.checkpoint.default_lookback_minutes
    trigger 為選填：記錄觸發來源，存入 analysis_jobs.trigger_mode。

    精準限縮欄位（Kibana Webhook 帶入）：
      event_time      Kibana 偵測到 spike 的精確時間點，用於計算以事故為中心的小視窗
      spike_service   Kibana 偵測到的高錯誤率服務名稱，用於 ES 查詢 terms filter
      es_agg_summary  Kibana 已執行的 ES 聚合摘要，直接注入 event_sequence 供 Bob 參考
    """
    from_time: Optional[str] = None        # 格式：YYYY-MM-DDTHH:MM:SS+00:00 或 YYYY-MM-DD HH:MM
    to_time: Optional[str] = None
    lookback_minutes: Optional[int] = None # Kibana Alert 評估視窗長度（分鐘），需與 rule 設定一致
    trigger: Optional[str] = None          # 觸發來源，如 "kibana_alert" / "kibana_alert_instana" / "webui_instana"
    # ── 精準限縮欄位（Kibana Webhook 帶入，三層精準限縮）────────────────
    event_time: Optional[str] = None       # 層一：Kibana 偵測到 spike 的精確時間點
    spike_service: Optional[str] = None    # 層二：spike 的服務名稱，用於 ES terms filter
    es_agg_summary: Optional[dict] = None  # 層三：Kibana 帶來的 ES 聚合摘要
    # ── 可選覆蓋設定（未來擴充）──────────────────────────────────────────
    index_pattern: Optional[str] = None
    levels: Optional[list[str]] = None
    # ── 開發 / 測試用隱藏欄位（不在 Web UI 呈現，不列入正式使用文件）──
    instana_context: Optional[dict] = None        # 單元測試注入假資料
    instana_context_path: Optional[str] = None    # 重播特定 Instana JSON 檔案


class AnalyseResponse(BaseModel):
    job_id: str
    status: str
    report_path: Optional[str]
    report_download_url: Optional[str]   # 前端可直接拿來下載的相對 URL
    summary: dict
    error: Optional[str]


class DemoInjectRequest(BaseModel):
    """Demo 資料注入請求體。

    from_time / to_time 預設動態計算為「當前時間往前 1 小時」，
    確保 Demo 展示時報告時間戳記與現實時間一致。
    傳入明確時間字串（格式 'YYYY-MM-DD HH:MM'）可覆蓋預設值。
    """
    from_time: Optional[str] = None   # None = 動態計算 now - 1h
    to_time:   Optional[str] = None   # None = 動態計算 now
    trigger_analyse: bool = True       # 注入後是否立即觸發分析

    def resolved_window(self) -> tuple[str, str]:
        """回傳最終使用的時間窗口（字串格式 YYYY-MM-DD HH:MM）。"""
        _fmt = "%Y-%m-%d %H:%M"
        if self.to_time and self.from_time:
            return self.from_time, self.to_time
        now = datetime.now(timezone.utc)
        to   = self.to_time   or now.strftime(_fmt)
        frm  = self.from_time or (now - timedelta(hours=1)).strftime(_fmt)
        return frm, to


# ── 工具函式 ──────────────────────────────────────────────────────────

# 系統本地時區：優先讀 TZ 環境變數（與 Dockerfile ENV TZ= 同步），
# 回退到 datetime.now().astimezone().tzinfo。
# 使用 ZoneInfo 確保 replace(tzinfo=...) 行為正確（tzfile 物件不可直接 replace）。
import os as _os
_TZ_NAME = _os.environ.get("TZ") or "UTC"
try:
    _LOCAL_TZ = ZoneInfo(_TZ_NAME)
except Exception:
    _LOCAL_TZ = datetime.now().astimezone().tzinfo


def _parse_time(time_str: str) -> Optional[datetime]:
    """
    將時間字串解析為 aware datetime，失敗回傳 None。
    支援格式：
      - YYYY-MM-DDTHH:MM:SS+HH:MM  (with tz offset，直接使用，不做轉換)
      - YYYY-MM-DDTHH:MM:SS.ffffff+HH:MM  (Kibana {{date}} 含毫秒，F-01)
      - YYYY-MM-DDTHH:MM:SSZ / .ffffffZ  (Kibana，視為 UTC)
      - YYYY-MM-DDTHH:MM:SS  (無時區 ISO，視為系統本地時區)
      - YYYY-MM-DD HH:MM     (Web UI / CLI 短格式，視為系統本地時區)
      - YYYY-MM-DD HH:MM:SS  (Web UI 含秒，視為系統本地時區)

    時區規則：
      - 字串本身帶有 offset（+HH:MM）或 Z → 直接使用，不假設時區
      - 無時區的 naive 字串 → 視為系統本地時區（_LOCAL_TZ）
    """
    normalized = time_str.strip()

    # Z 結尾明確是 UTC，替換為 +00:00 讓 fromisoformat 可接受
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    # 嘗試 fromisoformat()：Python 3.11+ 支援所有 ISO 8601 含毫秒變體
    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_LOCAL_TZ)
        return dt
    except ValueError:
        pass

    # Fallback：YYYY-MM-DD HH:MM / HH:MM:SS（fromisoformat 不接受無秒短格式）
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(normalized, fmt)
            return dt.replace(tzinfo=_LOCAL_TZ)
        except ValueError:
            pass

    return None


# 以 event_time 為中心的精準視窗：前 5 分鐘 + 後 2 分鐘（涵蓋前導徵兆與影響尾端）
_EVENT_TIME_BEFORE_MINUTES = 5
_EVENT_TIME_AFTER_MINUTES  = 2


def _resolve_query_window(req: "AnalyseRequest") -> tuple[datetime, datetime]:
    """
    依請求內容解析查詢視窗（五階段優先序）：
      1. from_time 有值        → 直接使用（Web UI / CLI 手動指定）
      2. event_time 有值       → 以事故時間點為中心取精準小視窗（層一精準限縮）
      3. lookback_minutes 有值 → to_time - lookback_minutes（Kibana Alert 寬鬆觸發）
      4. 兩者皆無，Checkpoint 有值 → Checkpoint 接續
      5. 三者皆無              → now - config.checkpoint.default_lookback_minutes
    回傳 (query_from, query_to)，失敗時拋出 HTTPException。
    """
    now = datetime.now(_LOCAL_TZ)

    # 解析 to_time（event_time 模式下由精準視窗決定，此處作為備用）
    if req.to_time:
        qt = _parse_time(req.to_time)
        if qt is None:
            raise HTTPException(status_code=400, detail=f"無法解析 to_time：{req.to_time}")
    else:
        qt = now

    # 解析 from_time（五階段優先序）
    if req.from_time:
        # 優先序 1：from_time 有值，直接使用（Web UI / CLI）
        qf = _parse_time(req.from_time)
        if qf is None:
            raise HTTPException(status_code=400, detail=f"無法解析 from_time：{req.from_time}")
    elif req.event_time:
        # 優先序 2：event_time 有值，以事故時間點為中心取精準小視窗（層一）
        et = _parse_time(req.event_time)
        if et is None:
            raise HTTPException(status_code=400, detail=f"無法解析 event_time：{req.event_time}")
        qf = et - timedelta(minutes=_EVENT_TIME_BEFORE_MINUTES)
        qt = et + timedelta(minutes=_EVENT_TIME_AFTER_MINUTES)
        logger.info(
            "使用 event_time 精準視窗（前 %d 分 / 後 %d 分）：%s → %s",
            _EVENT_TIME_BEFORE_MINUTES, _EVENT_TIME_AFTER_MINUTES,
            qf.isoformat(), qt.isoformat(),
        )
    elif req.lookback_minutes is not None:
        # 優先序 3：lookback_minutes 有值，from_time = to_time - lookback_minutes（Kibana Alert）
        qf = qt - timedelta(minutes=req.lookback_minutes)
        logger.info("使用 lookback_minutes=%d，from_time：%s", req.lookback_minutes, qf.isoformat())
    else:
        # 優先序 4：嘗試從 Checkpoint 取得
        checkpoint = _repo().get_checkpoint()
        if checkpoint is not None:
            qf = checkpoint
            logger.info("from_time 未提供，使用 Checkpoint：%s", qf.isoformat())
        else:
            # 優先序 5：Fallback — config 設定的回溯分鐘數
            lookback = _cfg.default_lookback_minutes
            qf = now - timedelta(minutes=lookback)
            logger.info("from_time 未提供且無 Checkpoint，fallback 回溯 %d 分鐘：%s",
                        lookback, qf.isoformat())

    return qf, qt


def _resolve_trigger_mode(trigger: Optional[str], async_mode: bool = False) -> str:
    """將 request.trigger 對應到 trigger_mode 字串。"""
    if trigger:
        return trigger  # 直接使用（如 "kibana_alert" / "kibana_alert_instana"）
    return "on_demand_async" if async_mode else "on_demand"


def _resolve_instana_context(
    req: "AnalyseRequest",
    query_from: "datetime",
    query_to: "datetime",
) -> "Optional[dict]":
    """
    依四段優先序決定 instana_context：
      1. trigger in (kibana_alert_instana, webui_instana) → 呼叫 instana_collector.collect()
      2. req.instana_context inline dict → dev/test 隱藏欄位
      3. req.instana_context_path 磁碟路徑 → dev/test 隱藏欄位
      4. 無 → 純 ELK 降級（回傳 None）
    """
    _instana_triggers = {"kibana_alert_instana", "webui_instana"}

    if req.trigger in _instana_triggers:
        try:
            import instana_collector
            ctx = instana_collector.collect(query_from, query_to)
            logger.info("Instana 收集完成（trigger=%s）available=%s", req.trigger, ctx.get("available"))
            return ctx
        except Exception as exc:
            logger.warning("Instana collect 失敗，降級為純 ELK：%s", exc)
            return {"available": False, "error": str(exc)}

    if req.instana_context:
        logger.debug("使用 inline instana_context（dev/test）")
        return req.instana_context

    if req.instana_context_path:
        try:
            import json as _json
            with open(req.instana_context_path, "r", encoding="utf-8") as f:
                ctx = _json.load(f)
            logger.debug("從磁碟載入 instana_context：%s", req.instana_context_path)
            return ctx
        except Exception as exc:
            logger.warning("載入 instana_context_path 失敗：%s", exc)
            return {"available": False, "error": str(exc)}

    return None


def _enqueue_analysis(req: "AnalyseRequest", row_id: int,
                      result_event: Optional[threading.Event] = None,
                      result_box: Optional[list] = None) -> None:
    """將分析任務放入 _job_queue。worker thread 負責實際執行。"""
    _job_queue.put((req, row_id, result_event, result_box))
    logger.info("分析任務已入隊 [row_id=%d] queue_size=%d", row_id, _job_queue.qsize())


# ── 端點 ─────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """健康檢查端點。"""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/analyse", response_model=AnalyseResponse)
def analyse(req: AnalyseRequest):
    """
    觸發主動查詢分析（同步）。
    任務放入 Queue 後阻塞等待 worker 完成，再回傳結果。
    同視窗 5 分鐘內已有 completed job 時，回傳去重結果（F-06）。
    """
    # F-06：軟性去重
    _instana_triggers = {"kibana_alert_instana", "webui_instana"}
    qf, qt = _resolve_query_window(req)
    recent = None if req.trigger in _instana_triggers else _repo().find_recent_completed(qf, qt, within_minutes=5)
    if recent:
        logger.info("同視窗任務在 5 分鐘內已完成（job_id=%s），跳過重複分析", recent["id"])
        import json as _json
        try:
            recent_summary = _json.loads(recent.get("summary_json") or "{}")
        except Exception:
            recent_summary = {}
        recent_summary["_deduplicated"] = True
        return AnalyseResponse(
            job_id=str(recent["id"]),
            status="completed",
            report_path=recent.get("report_path"),
            report_download_url=(
                f"/api/jobs/{recent['id']}/report" if recent.get("report_path") else None
            ),
            summary=recent_summary,
            error=None,
        )

    trigger_mode = _resolve_trigger_mode(req.trigger)
    row_id = _repo().create_job(trigger_mode, qf, qt)
    result_event: threading.Event = threading.Event()
    result_box: list = []
    _enqueue_analysis(req, row_id, result_event, result_box)
    result_event.wait()          # 阻塞直到 worker 完成
    result = result_box[0]
    result["report_download_url"] = (
        f"/api/jobs/{result['job_id']}/report" if result.get("report_path") else None
    )
    return AnalyseResponse(**result)


@app.post("/analyse/async")
def analyse_async(req: AnalyseRequest):
    """
    非同步觸發分析。立即回傳 job_id，分析在 Queue worker 背景執行。
    不再回傳 409；所有請求均接受並排隊。
    """
    try:
        qf, qt = _resolve_query_window(req)
    except HTTPException:
        raise
    trigger_mode = _resolve_trigger_mode(req.trigger, async_mode=True)
    row_id = _repo().create_job(trigger_mode, qf, qt)
    _enqueue_analysis(req, row_id)
    return {
        "status": "accepted",
        "job_id": str(row_id),
        "message": "分析已加入佇列，請透過 GET /jobs/{}/status 查詢結果".format(row_id),
    }


@app.get("/analyse/trigger")
def analyse_trigger(
    var_from: Optional[int] = None,
    to: Optional[int] = None,
    time: Optional[int] = None,
    trigger: str = "grafana_datalink",
):
    """
    Grafana dataLink 專用 GET 觸發端點。
    瀏覽器點擊 dataLink 時以 GET 開啟，內部轉發至非同步分析邏輯。
    Grafana 時間變數映射（優先使用數據點時間）：
      ?time=<epoch_ms>  → 數據點時間戳（${__value.time}），以此為中心前後各 10 分鐘
      ?from=<epoch_ms>  → 視窗開始時間；若無 &to，以 from 為中心取 ±10 分鐘
      &to=<epoch_ms>    → 視窗結束時間（與 from 搭配使用）
      &trigger=grafana_datalink
    回傳 HTML 頁面，顯示 job_id 並自動輪詢 /jobs/{id}/status。
    Updated: 2026-09-17 15:28:19 +0800
    """
    from fastapi.responses import HTMLResponse

    # 將 epoch ms 轉換為 "YYYY-MM-DD HH:MM" 字串（本地時區，與 _parse_time 一致）
    def _epoch_ms_to_str(epoch_ms: int) -> str:
        return datetime.fromtimestamp(epoch_ms / 1000, tz=_LOCAL_TZ).strftime("%Y-%m-%d %H:%M")

    if time:
        # 以數據點時間為中心，前後各 10 分鐘
        center_ms = time
        from_time_str = _epoch_ms_to_str(center_ms - 10 * 60 * 1000)
        to_time_str   = _epoch_ms_to_str(center_ms + 10 * 60 * 1000)
    elif var_from and to:
        # Grafana 明確帶了 from + to，直接使用
        from_time_str = _epoch_ms_to_str(var_from)
        to_time_str   = _epoch_ms_to_str(to)
    elif var_from:
        # 只有 from（點擊數據點時 Grafana 只帶 ${__value.time} alias 為 from），
        # 以此為中心取 ±10 分鐘，確保視窗對稱涵蓋事故前後
        from_time_str = _epoch_ms_to_str(var_from - 10 * 60 * 1000)
        to_time_str   = _epoch_ms_to_str(var_from + 10 * 60 * 1000)
    else:
        from_time_str = None
        to_time_str   = None

    req = AnalyseRequest(
        from_time=from_time_str,
        to_time=to_time_str,
        trigger=trigger,
    )

    try:
        qf, qt = _resolve_query_window(req)
    except HTTPException as e:
        return HTMLResponse(
            f"<h3>參數錯誤</h3><pre>{e.detail}</pre>",
            status_code=e.status_code,
        )

    trigger_mode = _resolve_trigger_mode(req.trigger, async_mode=True)
    row_id = _repo().create_job(trigger_mode, qf, qt)
    _enqueue_analysis(req, row_id)

    status_url  = f"/api/jobs/{row_id}/status"
    jobs_url    = f"/api/jobs/{row_id}"
    from_label  = from_time_str or "checkpoint"
    to_label    = to_time_str   or "now"

    html = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <title>Bob AI 分析啟動</title>
  <style>
    body {{ font-family: sans-serif; background:#111; color:#eee; padding:2rem; max-width:640px; }}
    .badge {{ display:inline-block; padding:.2rem .6rem; border-radius:4px; font-size:.85rem; font-weight:600; }}
    .running  {{ background:#1a3a5c; color:#60a5fa; }}
    .done     {{ background:#1a4a2e; color:#4ade80; }}
    .failed   {{ background:#4a1a1a; color:#f87171; }}
    #status-line {{ margin:.8rem 0; font-size:1rem; }}
    #report-link {{ display:none; margin-top:1rem; }}
    #report-link a {{ color:#4ade80; font-weight:600; font-size:1.1rem; }}
    a {{ color:#60a5fa; }}
    #spinner {{ display:inline-block; animation:spin 1s linear infinite; }}
    @keyframes spin {{ to {{ transform:rotate(360deg); }} }}
  </style>
</head>
<body>
  <h2>🤖 Bob AI 分析已啟動</h2>
  <p>
    <span id="badge" class="badge running">⏳ 執行中</span>
    &nbsp;Job ID: <strong>{row_id}</strong>
  </p>
  <p>時間視窗：{from_label} → {to_label}</p>
  <p id="status-line"><span id="spinner">⟳</span> 正在分析，每 3 秒輪詢狀態…</p>

  <div id="report-link">
    ✅ 分析完成！<br><br>
    <a id="dl-link" href="#" target="_blank">📥 下載 PPTX 報告</a>
    &nbsp;｜&nbsp;
    <a href="{jobs_url}" target="_blank">任務詳情</a>
  </div>

  <ul style="margin-top:1.5rem;">
    <li><a href="{status_url}" target="_blank">即時狀態 /jobs/{row_id}/status</a></li>
    <li><a href="{jobs_url}" target="_blank">任務詳情 /jobs/{row_id}</a></li>
    <li><a href="/api/jobs" target="_blank">所有任務列表</a></li>
  </ul>
  <hr>
  <small>觸發來源：{trigger} | 分析視窗：{from_label} → {to_label}</small>

  <script>
    (function() {{
      var jobId   = {row_id};
      var pollUrl = "/api/jobs/" + jobId + "/status";
      var timer;

      function poll() {{
        fetch(pollUrl)
          .then(function(r) {{ return r.json(); }})
          .then(function(d) {{
            var status = d.status || "unknown";
            var badge  = document.getElementById("badge");
            var line   = document.getElementById("status-line");

            if (status === "completed") {{
              clearInterval(timer);
              badge.className = "badge done";
              badge.textContent = "✅ 完成";
              document.getElementById("spinner").style.display = "none";
              line.textContent = "分析完成。";
              var reportUrl = d.report_download_url || ("/api/jobs/" + jobId + "/report");
              var rl = document.getElementById("report-link");
              document.getElementById("dl-link").href = reportUrl;
              rl.style.display = "block";
            }} else if (status === "failed") {{
              clearInterval(timer);
              badge.className = "badge failed";
              badge.textContent = "❌ 失敗";
              document.getElementById("spinner").style.display = "none";
              line.textContent = "分析失敗：" + (d.error || "未知錯誤");
            }} else {{
              badge.className = "badge running";
              badge.textContent = "⏳ " + status;
              line.innerHTML = '<span id="spinner" style="display:inline-block;animation:spin 1s linear infinite">⟳</span> ' + status + "，繼續等待…";
            }}
          }})
          .catch(function() {{
            /* 網路短暫失敗，下次繼續輪詢 */
          }});
      }}

      timer = setInterval(poll, 3000);
      poll(); /* 立即執行一次 */
    }})();
  </script>
</body>
</html>"""
    return HTMLResponse(html, status_code=202)


@app.get("/jobs")
def list_jobs(limit: int = 10):
    """列出最近 N 筆分析任務歷史。

    每筆 job 額外包含扁平化欄位供 Grafana Infinity datasource 使用：
      - primary_root_cause   : summary.incident_status.title
      - immediate_action     : summary.event_chains[0].short_term_fix 前 120 字
      - report_download_url  : 可直接下載的相對路徑
    """
    try:
        jobs = _repo().list_jobs(limit)
        for job in jobs:
            try:
                summary = json.loads(job.pop("summary_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                summary = {}
            job["summary"] = summary
            # 扁平化欄位：Grafana Infinity datasource 用 top-level selector 更穩定
            job["primary_root_cause"] = (
                summary.get("incident_status", {}).get("title") or ""
            )
            chains = summary.get("event_chains", [])
            job["immediate_action"] = (
                (chains[0].get("short_term_fix") or "")[:120] if chains else ""
            )
            report = job.get("report_path")
            job["report_download_url"] = (
                f"/api/jobs/{job['id']}/report" if report else None
            )
        return jobs
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"資料庫查詢失敗：{exc}")


@app.get("/jobs/{job_id}")
def get_job(job_id: int):
    """取得特定任務的詳細分析結果。"""
    try:
        row = _repo().get_job(job_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"資料庫查詢失敗：{exc}")

    if not row:
        raise HTTPException(status_code=404, detail=f"找不到任務 ID：{job_id}")

    summary = {}
    if row.get("summary_json"):
        try:
            summary = json.loads(row["summary_json"])
        except Exception:
            pass

    report = row.get("report_path")
    return {
        **{k: row[k] for k in ["id", "trigger_mode", "query_from", "query_to",
                                "status", "started_at", "completed_at", "error_message"]},
        "report_path": report,
        "report_download_url": f"/api/jobs/{row['id']}/report" if report else None,
        "summary": summary,
    }


@app.get("/jobs/{job_id}/report")
def download_report(job_id: int):
    """
    下載指定任務的 PPTX 報告檔。
    回傳 PPTX 二進位串流，瀏覽器會直接觸發下載。
    """
    try:
        row = _repo().get_job(job_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"資料庫查詢失敗：{exc}")

    if not row:
        raise HTTPException(status_code=404, detail=f"找不到任務 ID：{job_id}")

    report_path = row.get("report_path")
    if not report_path:
        raise HTTPException(status_code=404, detail="此任務尚未產生報告")

    path = Path(report_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"報告檔案不存在：{report_path}")

    return FileResponse(
        path=str(path),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=path.name,
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
    )


@app.delete("/jobs/{job_id}")
def delete_job(job_id: int):
    """刪除單筆分析任務（同時刪除對應報告檔）。"""
    try:
        report_path = _repo().delete_job(job_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"資料庫刪除失敗：{exc}")

    if report_path is None:
        raise HTTPException(status_code=404, detail=f"找不到任務 ID：{job_id}")

    if report_path:
        try:
            Path(report_path).unlink(missing_ok=True)
        except Exception:
            pass  # 檔案刪除失敗不影響 DB 記錄

    return {"deleted": job_id}


class BulkDeleteRequest(BaseModel):
    ids: list[int]


@app.delete("/jobs")
def delete_jobs(req: BulkDeleteRequest):
    """批次刪除多筆分析任務。"""
    if not req.ids:
        return {"deleted": []}
    try:
        report_paths = _repo().delete_jobs(req.ids)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"資料庫刪除失敗：{exc}")

    for report_path in report_paths:
        try:
            Path(report_path).unlink(missing_ok=True)
        except Exception:
            pass

    return {"deleted": req.ids}


@app.get("/jobs/{job_id}/status")
def job_status(job_id: int):
    """輪詢任務執行狀態（供非同步分析模式使用）。"""
    try:
        row = _repo().get_job(job_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"資料庫查詢失敗：{exc}")

    if not row:
        raise HTTPException(status_code=404, detail=f"找不到任務 ID：{job_id}")

    return {
        "id": row["id"],
        "status": row["status"],
        "report_download_url": f"/api/jobs/{row['id']}/report" if row.get("report_path") else None,
        "error": row.get("error_message"),
    }


@app.post("/demo/inject")
def demo_inject(req: DemoInjectRequest):
    """
    注入 WAS/MQ/PostgreSQL Demo 模擬資料。
    兩段式執行（F-10）：
      1. 同步：呼叫 gen_fake_data.py --no-trigger 純寫入資料（快速，不阻塞 worker）
      2. 若 trigger_analyse=True，enqueue 分析任務（非同步排隊執行）

    from_time / to_time 未傳入時動態計算為 now-1h / now，確保 Demo 時間戳記正確。
    """
    # 解析最終時間窗口（動態或明確傳入）
    from_str, to_str = req.resolved_window()

    # ── Step 1：純資料注入（--no-trigger，只寫檔案，秒級完成）──────────
    try:
        inject_cmd = [
            "python", "/app/gen_fake_data.py",
            "--from", from_str,
            "--to",   to_str,
            "--no-trigger",
        ]
        result = subprocess.run(inject_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"Demo 資料注入失敗：{result.stderr[:500]}"
            )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Demo 資料寫入逾時")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # ── Step 2：若需要，非同步觸發分析（enqueue，不阻塞當前請求）─────
    if req.trigger_analyse:
        analyse_req = AnalyseRequest(from_time=from_str, to_time=to_str)
        qf, qt = _resolve_query_window(analyse_req)
        trigger_mode = _resolve_trigger_mode(analyse_req.trigger, async_mode=True)
        row_id = _repo().create_job(trigger_mode, qf, qt)
        _enqueue_analysis(analyse_req, row_id)

    return {
        "status": "ok",
        "message": f"已注入 WAS/MQ/PostgreSQL Demo 資料（{from_str} → {to_str}）",
        "triggered": req.trigger_analyse,
        "note": "分析已在背景執行，請透過 GET /jobs 查詢結果" if req.trigger_analyse else "僅注入資料，未觸發分析",
        "log": result.stdout[-1000:],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=_cfg.api_host, port=_cfg.api_port)

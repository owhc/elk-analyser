# Updated: 2026-08-27 18:08:17 +0800
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
  POST /demo/inject      注入 WAS/MQ/DB2 Demo 模擬資料
  GET  /health           健康檢查
"""

import json
import logging
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
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

# ── 分析任務互斥鎖（F-01）────────────────────────────────────────────
# run_analysis() 是同步阻塞呼叫（含 bob run subprocess），以 threading.Lock
# 確保同一時刻最多只有一個分析任務執行，防止 Checkpoint 競態與資源耗盡。
_analysis_lock = threading.Lock()

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

    from_time 四階段優先序：
      1. from_time 有值         → 直接使用（Web UI / CLI 手動指定）
      2. lookback_minutes 有值  → to_time - lookback_minutes（Kibana Alert 自動觸發）
      3. 兩者皆無，Checkpoint 有值 → Checkpoint 接續上次分析結束時間
      4. 三者皆無               → now - config.checkpoint.default_lookback_minutes
    trigger 為選填：記錄觸發來源，存入 analysis_jobs.trigger_mode。
    """
    from_time: Optional[str] = None        # 格式：YYYY-MM-DDTHH:MM:SS+00:00 或 YYYY-MM-DD HH:MM
    to_time: Optional[str] = None
    lookback_minutes: Optional[int] = None # Kibana Alert 評估視窗長度（分鐘），需與 rule 設定一致
    trigger: Optional[str] = None          # 觸發來源，如 "kibana_alert"
    # 可選覆蓋設定（未來擴充）
    index_pattern: Optional[str] = None
    levels: Optional[list[str]] = None


class AnalyseResponse(BaseModel):
    job_id: str
    status: str
    report_path: Optional[str]
    report_download_url: Optional[str]   # 前端可直接拿來下載的相對 URL
    summary: dict
    error: Optional[str]


class DemoInjectRequest(BaseModel):
    """Demo 資料注入請求體。"""
    from_time: str = "2025-01-15 08:00"
    to_time:   str = "2025-01-15 09:00"
    trigger_analyse: bool = True         # 注入後是否立即觸發分析


# ── 工具函式 ──────────────────────────────────────────────────────────

def _parse_time(time_str: str) -> Optional[datetime]:
    """
    將時間字串解析為 aware datetime，失敗回傳 None。
    支援格式：
      - YYYY-MM-DDTHH:MM:SS+HH:MM  (with tz offset)
      - YYYY-MM-DDTHH:MM:SS.ffffff+HH:MM  (Kibana {{date}} 含毫秒，F-01)
      - YYYY-MM-DDTHH:MM:SSZ
      - YYYY-MM-DDTHH:MM:SS.ffffffZ  (Kibana {{date}} 含毫秒 Z 尾綴，F-01)
      - YYYY-MM-DD HH:MM  (Web UI / CLI 手動輸入)
    """
    # 優先嘗試 fromisoformat()：Python 3.11+ 原生支援含毫秒與 Z 尾綴的所有 ISO 8601 變體
    normalized = time_str.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    # Fallback：YYYY-MM-DD HH:MM（Web UI / CLI，fromisoformat 不接受無秒的短格式）
    try:
        dt = datetime.strptime(time_str.strip(), "%Y-%m-%d %H:%M")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    return None


def _resolve_query_window(req: "AnalyseRequest") -> tuple[datetime, datetime]:
    """
    依請求內容解析查詢視窗：
      1. from_time 有值 → parse 使用
      2. from_time 為 None → 從 Checkpoint 讀取；無 Checkpoint → fallback config 回溯分鐘數
      3. to_time 為 None → 使用 now(UTC)
    回傳 (query_from, query_to)，失敗時拋出 HTTPException。
    """
    now = datetime.now(timezone.utc)

    # 解析 to_time
    if req.to_time:
        qt = _parse_time(req.to_time)
        if qt is None:
            raise HTTPException(status_code=400, detail=f"無法解析 to_time：{req.to_time}")
    else:
        qt = now

    # 解析 from_time（四階段優先序）
    if req.from_time:
        # 優先序 1：from_time 有值，直接使用（Web UI / CLI）
        qf = _parse_time(req.from_time)
        if qf is None:
            raise HTTPException(status_code=400, detail=f"無法解析 from_time：{req.from_time}")
    elif req.lookback_minutes is not None:
        # 優先序 2：lookback_minutes 有值，from_time = to_time - lookback_minutes（Kibana Alert）
        qf = qt - timedelta(minutes=req.lookback_minutes)
        logger.info("使用 lookback_minutes=%d，from_time：%s", req.lookback_minutes, qf.isoformat())
    else:
        # 優先序 3：嘗試從 Checkpoint 取得
        checkpoint = _repo().get_checkpoint()
        if checkpoint is not None:
            qf = checkpoint
            logger.info("from_time 未提供，使用 Checkpoint：%s", qf.isoformat())
        else:
            # 優先序 4：Fallback — config 設定的回溯分鐘數
            lookback = _cfg.default_lookback_minutes
            qf = now - timedelta(minutes=lookback)
            logger.info("from_time 未提供且無 Checkpoint，fallback 回溯 %d 分鐘：%s",
                        lookback, qf.isoformat())

    return qf, qt


def _resolve_trigger_mode(trigger: Optional[str], async_mode: bool = False) -> str:
    """將 request.trigger 對應到 trigger_mode 字串。"""
    if trigger:
        return trigger  # 直接使用（如 "kibana_alert"）
    return "on_demand_async" if async_mode else "on_demand"


def _run_analysis_bg(req: "AnalyseRequest", row_id: Optional[int] = None) -> None:
    """
    在背景執行分析（由 BackgroundTasks 調用）。
    - row_id：由 analyse_async 預先建立的 job 記錄 rowid（F-02）
    - 互斥鎖由此函式持有，無 TOCTOU 問題（F-04）
    """
    from analysis_pipeline import run_analysis
    try:
        qf, qt = _resolve_query_window(req)
    except HTTPException as exc:
        logger.error("背景分析時間解析失敗：%s", exc.detail)
        return
    trigger_mode = _resolve_trigger_mode(req.trigger, async_mode=True)
    if not _analysis_lock.acquire(blocking=False):
        logger.warning("分析任務已在執行中，跳過本次背景觸發（trigger=%s）", trigger_mode)
        return
    try:
        run_analysis(qf, qt, trigger_mode, row_id=row_id)
    finally:
        _analysis_lock.release()


# ── 端點 ─────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """健康檢查端點。"""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/analyse", response_model=AnalyseResponse)
def analyse(req: AnalyseRequest, background_tasks: BackgroundTasks):
    """
    觸發主動查詢分析（同步）。
    from_time / to_time 選填：不帶時間時從 Checkpoint 接續，或 fallback config 回溯分鐘數。
    trigger 選填：記錄觸發來源（如 "kibana_alert"）。
    若已有分析任務執行中，回傳 409 Conflict（F-01）。
    同視窗 5 分鐘內已有 completed job 時，回傳 208 Already Reported（F-06 軟性去重）。
    """
    from analysis_pipeline import run_analysis

    # F-06：軟性去重 — 同視窗 5 分鐘內已完成，直接回傳既有結果
    qf, qt = _resolve_query_window(req)
    recent = _repo().find_recent_completed(qf, qt, within_minutes=5)
    if recent:
        logger.info("同視窗任務在 5 分鐘內已完成（job_id=%s），跳過重複分析", recent["id"])
        return AnalyseResponse(
            job_id=str(recent["id"]),
            status="completed",
            report_path=recent.get("report_path"),
            report_download_url=(
                f"/api/jobs/{recent['id']}/report" if recent.get("report_path") else None
            ),
            summary={},
            error=None,
        )

    if not _analysis_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="分析任務已在執行中，請稍後再試或使用 GET /jobs 查詢進行中的任務"
        )
    try:
        trigger_mode = _resolve_trigger_mode(req.trigger)
        result = run_analysis(qf, qt, trigger_mode)
    finally:
        _analysis_lock.release()

    result["report_download_url"] = (
        f"/api/jobs/{result['job_id']}/report" if result.get("report_path") else None
    )
    return AnalyseResponse(**result)


@app.post("/analyse/async")
def analyse_async(req: AnalyseRequest, background_tasks: BackgroundTasks):
    """
    非同步觸發分析。立即回傳，分析在背景執行。
    from_time / to_time 選填：不帶時間時從 Checkpoint 接續，或 fallback config 回溯分鐘數。
    trigger 選填：記錄觸發來源（如 "kibana_alert"）。
    若已有分析任務執行中，回傳 409 Conflict。
    回傳 job_id 供前端透過 GET /jobs/{id}/status 輪詢（F-02）。
    """
    # 預先建立 job 記錄取得 row_id，讓前端可立即開始輪詢（F-02）
    # 鎖的實際持有在 _run_analysis_bg() 內，消除 TOCTOU 競態（F-04）
    try:
        qf, qt = _resolve_query_window(req)
    except HTTPException:
        raise
    trigger_mode = _resolve_trigger_mode(req.trigger, async_mode=True)
    row_id = _repo().create_job(trigger_mode, qf, qt)
    background_tasks.add_task(_run_analysis_bg, req, row_id)
    return {
        "status": "accepted",
        "job_id": str(row_id),
        "message": "分析已加入佇列，請透過 GET /jobs/{} /status 查詢結果".format(row_id),
    }


@app.get("/jobs")
def list_jobs(limit: int = 10):
    """列出最近 N 筆分析任務歷史。"""
    try:
        return _repo().list_jobs(limit)
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
def demo_inject(req: DemoInjectRequest, background_tasks: BackgroundTasks):
    """
    注入 WAS/MQ/DB2 Demo 模擬資料。
    兩段式執行（F-10）：
      1. 同步：呼叫 gen_fake_data.py --no-trigger 純寫入資料（快速，不阻塞 worker）
      2. 若 trigger_analyse=True，以 BackgroundTasks 非同步觸發分析，立即回傳
    """
    # ── Step 1：純資料注入（--no-trigger，只寫檔案，秒級完成）──────────
    try:
        inject_cmd = [
            "python", "/app/gen_fake_data.py",
            "--from", req.from_time,
            "--to",   req.to_time,
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

    # ── Step 2：若需要，非同步觸發分析（不阻塞當前請求）──────────────
    if req.trigger_analyse:
        analyse_req = AnalyseRequest(from_time=req.from_time, to_time=req.to_time)
        background_tasks.add_task(_run_analysis_bg, analyse_req)

    return {
        "status": "ok",
        "message": f"已注入 WAS/MQ/DB2 Demo 資料（{req.from_time} → {req.to_time}）",
        "triggered": req.trigger_analyse,
        "note": "分析已在背景執行，請透過 GET /jobs 查詢結果" if req.trigger_analyse else "僅注入資料，未觸發分析",
        "log": result.stdout[-1000:],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=_cfg.api_host, port=_cfg.api_port)

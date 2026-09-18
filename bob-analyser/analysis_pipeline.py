# Updated: 2026-09-17 08:54:33 +0800
"""
analysis_pipeline.py
────────────────────
分析任務執行核心。由 api.py 與 cli.py 呼叫。
分析由外部觸發（Web UI / CLI / Elasticsearch Alert），無排程模式。
"""

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime

from config_loader import AppConfig
from db.job_repository import JobRepository
from analyser.extractor import extract
from analyser.preprocessor import preprocess
from analyser.bob_bridge import analyse
from analyser.report_builder import build_report

# Bob CLI subprocess 使用獨立的 ThreadPoolExecutor（max_workers=1）。
# 同一時刻最多一個 Bob 任務在跑，與 _analysis_lock 行為一致；
# 但透過 thread 執行，不阻塞 FastAPI 的 uvicorn event loop，
# 使 GET /health 等非阻塞請求在分析期間仍能正常回應。
_bob_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bob-worker")

logger = logging.getLogger(__name__)


def run_analysis(
    query_from: datetime,
    query_to: datetime,
    trigger_mode: str = "on_demand",
    config_path: str = "/app/config/config.yaml",
    row_id: int = None,
    instana_context: dict = None,
    spike_service: str = "",
    es_agg_summary: dict = None,
) -> dict:
    """
    執行一次完整的分析流程：
      extract → preprocess → bob_bridge.analyse → report_builder.build

    參數：
        row_id          若已由呼叫端（如 analyse_async）預建 job 記錄，直接傳入 rowid
                        避免重複建立（F-02）；為 None 時自行建立。
        instana_context 由 api.py _resolve_instana_context() 解析後傳入；
                        None 表示純 ELK 模式（向後相容）。
        spike_service   層二精準限縮：非空時 ES 查詢加 service terms filter。
        es_agg_summary  層三精準限縮：Kibana 聚合摘要，注入 event_sequence 頂層。

    回傳：
        { "job_id": str, "status": str, "report_path": str|None,
          "summary": dict, "error": str|None }
    """
    cfg = AppConfig.load(config_path)
    repo = JobRepository(cfg.db_path)

    if row_id is None:
        row_id = repo.create_job(trigger_mode, query_from, query_to)
    # job_id 給業務層使用（Bob Bridge 產生事件序列檔名等）
    job_id = str(row_id)

    logger.info(
        "分析任務開始 [rowid=%d] 模式=%s 視窗：%s → %s",
        row_id, trigger_mode,
        query_from.isoformat(), query_to.isoformat(),
    )

    try:
        # Step 1：提取（層二：spike_service 非空時只拉該服務日誌）
        raw_logs = extract(query_from, query_to, cfg, spike_service=spike_service)
        logger.info("提取完成：%d 筆", len(raw_logs))

        # Step 2：預處理（含 Instana context 與層三 es_agg_summary 注入）
        event_sequence = preprocess(raw_logs, job_id, query_from, query_to, cfg,
                                    instana_context=instana_context,
                                    es_agg_summary=es_agg_summary)

        # Step 3：Bob CLI 分析（在 ThreadPoolExecutor 中執行，不阻塞 event loop）
        # bob_timeout + 15s 緩衝對應 _run_bob 的 timeout+10 設定
        bob_timeout = cfg.bob_timeout + 20
        future = _bob_executor.submit(analyse, event_sequence, job_id, cfg)
        try:
            analysis = future.result(timeout=bob_timeout)
        except FuturesTimeoutError:
            logger.error("Bob CLI 分析等待逾時（pipeline timeout=%ds）[rowid=%d]", bob_timeout, row_id)
            analysis = {"error": f"Bob CLI 分析逾時（{bob_timeout}s）"}

        # 正規化 AI 輸出，避免錯誤型別造成報告或 Web UI 失敗
        if not isinstance(analysis, dict):
            analysis = {"error": "Bob 分析結果格式錯誤"}
        summary = analysis.get("summary")
        if not isinstance(summary, dict):
            summary = {}
        incident = summary.get("incident_status")
        summary["incident_status"] = incident if isinstance(incident, dict) else {}
        impact = summary.get("impact_analysis")
        impact = impact if isinstance(impact, dict) else {}
        if not isinstance(impact.get("affected_services"), list):
            impact["affected_services"] = []
        summary["impact_analysis"] = impact
        if not isinstance(summary.get("affected_modules"), list):
            summary["affected_modules"] = []
        chains = analysis.get("event_chains")
        analysis["event_chains"] = chains if isinstance(chains, list) else []
        analysis["summary"] = summary

        # Step 4：生成報告
        report_path = build_report(analysis, job_id, query_from, query_to, cfg)
        if isinstance(report_path, dict):
            raise RuntimeError(report_path.get("error", "報告生成失敗"))

        # 更新任務紀錄（事件鏈一併放入摘要，供 Web UI 顯示完整事故資訊）
        summary = dict(summary)
        summary["event_chains"] = analysis["event_chains"]
        repo.complete_job(row_id, summary, report_path, analysis)

        # 寫入 Checkpoint（只在 completed 時更新，失敗不寫）
        repo.set_checkpoint(query_to)

        logger.info("分析任務完成 [rowid=%d] 報告：%s", row_id, report_path)
        return {
            "job_id": str(row_id),
            "status": "completed",
            "report_path": report_path,
            "summary": summary,
            "error": None,
        }

    except Exception as exc:
        logger.error("分析任務失敗 [rowid=%d]：%s", row_id, exc, exc_info=True)
        repo.fail_job(row_id, str(exc))
        return {
            "job_id": str(row_id),
            "status": "failed",
            "report_path": None,
            "summary": {},
            "error": str(exc),
        }

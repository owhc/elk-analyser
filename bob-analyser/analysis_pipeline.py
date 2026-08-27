# Updated: 2026-08-27 18:08:17 +0800
"""
analysis_pipeline.py
────────────────────
分析任務執行核心。由 api.py 與 cli.py 呼叫。
分析由外部觸發（Web UI / CLI / Elasticsearch Alert），無排程模式。
"""

import logging
from datetime import datetime

from config_loader import AppConfig
from db.job_repository import JobRepository
from analyser.extractor import extract
from analyser.preprocessor import preprocess
from analyser.bob_bridge import analyse
from analyser.report_builder import build_report

logger = logging.getLogger(__name__)


def run_analysis(
    query_from: datetime,
    query_to: datetime,
    trigger_mode: str = "on_demand",
    config_path: str = "/app/config/config.yaml",
    row_id: int = None,
) -> dict:
    """
    執行一次完整的分析流程：
      extract → preprocess → bob_bridge.analyse → report_builder.build

    參數：
        row_id  若已由呼叫端（如 analyse_async）預建 job 記錄，直接傳入 rowid
                避免重複建立（F-02）；為 None 時自行建立。

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
        # Step 1：提取
        raw_logs = extract(query_from, query_to, cfg)
        logger.info("提取完成：%d 筆", len(raw_logs))

        # Step 2：預處理
        event_sequence = preprocess(raw_logs, job_id, query_from, query_to, cfg)

        # Step 3：Bob Shell 分析
        analysis = analyse(event_sequence, job_id, cfg)

        # Step 4：生成報告
        report_path = build_report(analysis, job_id, query_from, query_to, cfg)

        # 更新任務紀錄（以明確 row_id 更新，不使用 rowid DESC 競態模式）
        summary = analysis.get("summary", {})
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

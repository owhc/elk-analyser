# Updated: 2026-09-17 12:03:11 +0800
"""
db/job_repository.py
─────────────────────
深模組：集中所有分析任務（analysis_jobs）與 Checkpoint 的 SQLite 操作。

介面（外部可見）：
  create_job(trigger_mode, query_from, query_to) -> int
  complete_job(row_id, summary, report_path, bob_raw)
  fail_job(row_id, error_message)
  get_job(job_id) -> dict | None
  list_jobs(limit) -> list[dict]
  delete_job(job_id) -> str | None          # 回傳 report_path（刪除前）
  delete_jobs(ids) -> list[str]             # 回傳 report_path 列表
  get_checkpoint() -> datetime | None
  set_checkpoint(analysed_to)

所有 SQL、連線管理、事務邊界均藏在實作內；呼叫端不接觸 sqlite3。
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path("/app/db/schema.sql")

# 列查詢時的欄位清單（與 schema.sql 對齊）
_JOB_COLS_SHORT = ["id", "trigger_mode", "query_from", "query_to",
                   "status", "started_at", "completed_at", "report_path"]
_JOB_COLS_FULL  = _JOB_COLS_SHORT + ["summary_json", "error_message"]


class JobRepository:
    """
    JobRepository 是唯一的 seam，所有 analysis_jobs / checkpoint 操作均透過它。

    db_path 傳入建構式；每個方法自行開連線、執行、關閉，
    避免長持連線導致 SQLite locked 問題。
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._init_db()

    # ── 初始化 ───────────────────────────────────────────────────────

    def _init_db(self) -> None:
        """確保 schema 已建立（idempotent）。"""
        conn = sqlite3.connect(self.db_path)
        try:
            if _SCHEMA_PATH.exists():
                conn.executescript(_SCHEMA_PATH.read_text())
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── 分析任務 CRUD ─────────────────────────────────────────────────

    def create_job(
        self,
        trigger_mode: str,
        query_from: datetime,
        query_to: datetime,
    ) -> int:
        """
        建立新的分析任務紀錄，回傳資料庫 rowid（INTEGER PRIMARY KEY）。
        使用 lastrowid 而非 SELECT last_insert_rowid()，確保拿到本次插入的 id。
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            cur = conn.execute(
                """INSERT INTO analysis_jobs
                   (trigger_mode, query_from, query_to, status, started_at)
                   VALUES (?, ?, ?, 'running', ?)""",
                (trigger_mode, query_from.isoformat(), query_to.isoformat(), now),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def complete_job(
        self,
        row_id: int,
        summary: dict,
        report_path: str,
        bob_raw: dict,
    ) -> None:
        """標記任務為 completed，寫入摘要、報告路徑與 Bob 原始輸出。"""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            conn.execute(
                """UPDATE analysis_jobs SET
                       status='completed', completed_at=?,
                       summary_json=?, report_path=?, bob_raw_json=?
                   WHERE id=?""",
                (
                    now,
                    json.dumps(summary, ensure_ascii=False),
                    report_path,
                    json.dumps(bob_raw, ensure_ascii=False)[:10000],
                    row_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def fail_job(self, row_id: int, error_message: str) -> None:
        """標記任務為 failed，寫入錯誤訊息。"""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            conn.execute(
                """UPDATE analysis_jobs SET
                       status='failed', completed_at=?, error_message=?
                   WHERE id=?""",
                (now, error_message, row_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_job(self, job_id: int) -> Optional[dict]:
        """取得單筆任務詳細資訊；找不到回傳 None。"""
        conn = self._conn()
        try:
            row = conn.execute(
                f"SELECT {', '.join(_JOB_COLS_FULL)} FROM analysis_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_running_job(self) -> Optional[dict]:
        """回傳目前 status='running' 的最新一筆任務，沒有則回傳 None。
        供 409 回應提供可輪詢的 job_id。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT id, trigger_mode, started_at FROM analysis_jobs "
                "WHERE status='running' ORDER BY id DESC LIMIT 1",
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def find_recent_completed(
        self,
        query_from: datetime,
        query_to: datetime,
        within_minutes: int = 5,
    ) -> Optional[dict]:
        """
        查詢是否已有重疊視窗且 within_minutes 分鐘內完成的任務。
        用於 POST /analyse 的軟性去重防護（F-06）。

        「不帶時間」的主動觸發每次都會產生略不同的 now()，不能做精確字串比對。
        改用視窗重疊判斷：已完成任務的 (query_from, query_to) 與本次請求的視窗
        有重疊（overlap），且兩端差距均在 overlap_tolerance_seconds 秒內，即視為
        同一視窗。這樣同一個「近 1 小時」請求在 5 分鐘內不會重複分析。

        回傳最新一筆符合條件的 completed job，找不到回傳 None。
        """
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=within_minutes)).isoformat()
        # 容許兩端各偏移 within_minutes 分鐘（動態視窗每次 now() 都略不同）
        tolerance_sec = within_minutes * 60
        qf_iso = query_from.isoformat()
        qt_iso = query_to.isoformat()
        conn = self._conn()
        try:
            row = conn.execute(
                """SELECT id, query_from, query_to, completed_at, report_path, summary_json
                   FROM analysis_jobs
                   WHERE status = 'completed'
                     AND completed_at >= ?
                     AND query_from <= ?
                     AND query_to   >= ?
                     AND CAST((julianday(?) - julianday(query_from)) * 86400 AS INTEGER) <= ?
                     AND CAST((julianday(query_to) - julianday(?)) * 86400 AS INTEGER) <= ?
                   ORDER BY id DESC LIMIT 1""",
                (
                    cutoff,
                    qt_iso, qf_iso,        # 視窗重疊：existing.from <= new.to AND existing.to >= new.from
                    qf_iso, tolerance_sec, # |existing.from - new.from| <= tolerance
                    qt_iso, tolerance_sec, # |existing.to   - new.to  | <= tolerance
                ),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_jobs(self, limit: int = 10) -> list[dict]:
        """列出最近 N 筆任務（id 降序）。"""
        conn = self._conn()
        try:
            rows = conn.execute(
                f"SELECT {', '.join(_JOB_COLS_FULL)} FROM analysis_jobs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def delete_job(self, job_id: int) -> Optional[str]:
        """
        刪除單筆任務，回傳被刪除的 report_path（若有）供呼叫端刪檔。
        找不到回傳 None（不拋出例外；由呼叫端決定是否為 404）。
        """
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT report_path FROM analysis_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM analysis_jobs WHERE id=?", (job_id,))
            conn.commit()
            return row["report_path"]
        finally:
            conn.close()

    def delete_jobs(self, ids: list[int]) -> list[str]:
        """批次刪除，回傳所有非空 report_path 列表。"""
        if not ids:
            return []
        conn = self._conn()
        try:
            placeholders = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT report_path FROM analysis_jobs WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
            conn.execute(
                f"DELETE FROM analysis_jobs WHERE id IN ({placeholders})", ids
            )
            conn.commit()
            return [r["report_path"] for r in rows if r["report_path"]]
        finally:
            conn.close()

    # ── Checkpoint ────────────────────────────────────────────────────

    def get_checkpoint(self) -> Optional[datetime]:
        """讀取 Checkpoint；無紀錄或失敗回傳 None。"""
        try:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT last_analysed_to FROM checkpoint WHERE id=1"
                ).fetchone()
                if row:
                    return datetime.fromisoformat(row["last_analysed_to"])
                return None
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("讀取 Checkpoint 失敗：%s", exc)
            return None

    def set_checkpoint(self, analysed_to: datetime) -> None:
        """寫入 Checkpoint（只在 completed 後呼叫）。"""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO checkpoint (id, last_analysed_to, updated_at)
                   VALUES (1, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       last_analysed_to = excluded.last_analysed_to,
                       updated_at = excluded.updated_at""",
                (analysed_to.isoformat(), now),
            )
            conn.commit()
            logger.info("Checkpoint 已更新至 %s", analysed_to.isoformat())
        finally:
            conn.close()

# Updated: 2026-09-15 21:57:10 +0800
"""
analyser/bob_bridge.py
──────────────────────
直接在 container 內執行 bob CLI 進行日誌根因分析。

資料流：
  1. 將事件序列 JSON 寫入 /workspace/logs/job-{id}.json
  2. 執行：bob run --mode log-analyst "@/workspace/logs/job-{id}.json"
  3. 解析 bob run --format json 的 stdout，取出 last_message
  4. 將 last_message 解析為根因分析 JSON，回傳給 report_builder

Bob Shell JSON 輸出格式（--format json）：
  { "type": "result", "status": "completed", "last_message": "..." }

Bob Shell 分析結果契約（last_message 內的 JSON）：
  {
    "summary": { "total_errors": int, "critical_count": int,
                 "warning_count": int, "affected_modules": [...] },
    "event_chains": [
      { "chain_id": str, "severity": str, "title": str,
        "timeline": [...], "root_cause": str,
        "short_term_fix": str, "long_term_fix": str }
    ]
  }
"""

import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# 傳給 log-analyst 的固定 prompt 前綴
_ANALYSIS_PROMPT = (
    "請分析以上事件序列，識別系統錯誤的根本原因與因果鏈。"
    "針對每個 event chain，輸出：chain_id、severity（CRITICAL/HIGH/MEDIUM）、"
    "title（中文一句話摘要）、timeline（時序事件列表）、root_cause（中文技術根因）、"
    "short_term_fix（短期應急處理）、long_term_fix（長期架構改善）。"
    "同時輸出整體 summary，包含 total_errors、critical_count、warning_count、affected_modules，"
    "以及 incident_status（status、severity、title、description）與 impact_analysis"
    "（affected_services、customer_impact、business_impact、operational_impact）。"
    "若事件序列頂層包含 instana_context 且 available=true，"
    "請將其與 event_chains 交叉比對以強化根因判斷："
    "（1）events：將 Instana Issues/Incidents 與對應時間的日誌錯誤關聯，說明是否同步發生；"
    "（2）endpoint_metrics：使用 error_rate 與 latency P95 驗證或強化根因，在 root_cause 中引用具體數值；"
    "（3）trace_summary：補充因果鏈的跨服務傳播路徑（如 Liberty → bankdb）。"
    "若 instana_context.available=false 或不存在，忽略 Instana 分析，僅使用 event_chains。"
    "僅回傳符合契約的 JSON，不要包含任何說明文字。"
)


def _write_event_sequence(
    payload: dict,
    workspace_path: str,
    logs_subdir: str,
    job_id: str,
) -> Path:
    """將事件序列 JSON 寫入本地路徑，bob run 透過 @filename 讀取。"""
    logs_dir = Path(workspace_path) / logs_subdir
    logs_dir.mkdir(parents=True, exist_ok=True)
    target = logs_dir / f"job-{job_id}.json"
    with open(target, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info("事件序列已寫入：%s", target)
    return target


def _run_bob(
    file_path: Path,
    mode: str,
    timeout: int,
    extra_prompt: str = "",
) -> subprocess.CompletedProcess:
    """
    直接在 container 內執行 bob run。
    不需要 SSH，bob CLI 已安裝在本 container。
    """
    # BOB_API_KEY 優先，BOBSHELL_API_KEY 向後相容
    bobshell_api_key = (
        os.environ.get("BOB_API_KEY")
        or os.environ.get("BOBSHELL_API_KEY")
        or ""
    )

    cmd = [
        "bob", "run",
        "--accept-license",
        "--workspace", "/",
        "--disable-tool-groups", "read",
        "--mode", mode,
        "--format", "json",
        "--max-turns", "5",
        f"@{file_path}",
    ]
    event_sequence = file_path.read_text(encoding="utf-8")
    prompt = (
        "不得呼叫工具或讀取其他檔案。以下 JSON 是唯一要分析的事件序列：\n"
        f"{event_sequence}"
    )
    if extra_prompt:
        prompt = f"{prompt}\n\n{extra_prompt}"
    cmd.append(prompt)

    # 白名單 env：只傳必要的環境變數，不洩漏父程序所有 env
    _safe_keys = {"HOME", "PATH", "USER", "TMPDIR", "LANG", "TERM"}
    env = {k: os.environ[k] for k in _safe_keys if k in os.environ}
    env["BOB_API_KEY"] = bobshell_api_key
    env["BOBSHELL_API_KEY"] = bobshell_api_key  # 向後相容

    logger.info("執行 bob run --mode %s，檔案：%s", mode, file_path)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout + 10,
        env=env,
        cwd="/",
    )


def _parse_bob_output(stdout: str) -> dict:
    """
    解析 bob run --format json 的輸出，取出 last_message。
    last_message 應為純 JSON 字串（由 log-analyst system prompt 保證）。
    若解析失敗，回傳帶有 error 欄位的字典，不拋出例外。
    """
    stdout = stdout.strip()
    try:
        bob_result = json.loads(stdout)
    except json.JSONDecodeError:
        # 嘗試從 stdout 中找最後一個 JSON block
        matches = re.findall(r"\{.*\}", stdout, re.DOTALL)
        if matches:
            try:
                bob_result = json.loads(matches[-1])
            except json.JSONDecodeError:
                logger.error("無法解析 Bob Shell 輸出：%s", stdout[:500])
                return {"error": "無法解析 Bob Shell 輸出", "raw": stdout[:2000]}
        else:
            logger.error("Bob Shell 未回傳任何 JSON：%s", stdout[:500])
            return {"error": "Bob Shell 未回傳 JSON", "raw": stdout[:2000]}

    # 取出 last_message
    last_message = bob_result.get("last_message", "")
    if not last_message:
        return {"error": "Bob Shell last_message 為空", "raw": bob_result}

    # last_message 本身應為 JSON 字串
    try:
        # 嘗試 1：去除 markdown code fence 後直接 parse
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", last_message.strip())
        analysis = json.loads(clean)
        logger.info("Bob Shell 分析解析成功，共 %d 個 event_chains",
                    len(analysis.get("event_chains", [])))
        return analysis
    except json.JSONDecodeError:
        pass

    # 嘗試 2：last_message 內含前置說明文字，從中抽取 JSON block（```json ... ``` 或第一個 {...}）
    try:
        # 先找 fenced code block
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", last_message, re.DOTALL)
        if fence_match:
            analysis = json.loads(fence_match.group(1))
            logger.info("Bob Shell 分析從 fenced block 解析成功，共 %d 個 event_chains",
                        len(analysis.get("event_chains", [])))
            return analysis
        # 再找裸 JSON object（取最後一個最大的）
        json_matches = re.findall(r"\{.*\}", last_message, re.DOTALL)
        if json_matches:
            analysis = json.loads(json_matches[-1])
            logger.info("Bob Shell 分析從裸 JSON 解析成功，共 %d 個 event_chains",
                        len(analysis.get("event_chains", [])))
            return analysis
    except json.JSONDecodeError:
        pass

    logger.error("last_message 不是有效 JSON：%s", last_message[:500])
    return {"error": "last_message JSON 解析失敗", "raw": last_message}


def analyse(
    event_sequence: dict,
    job_id: str,
    config=None,
) -> dict:
    """
    主要分析函式。直接在 container 內執行 bob run。

    參數：
        event_sequence  preprocessor.preprocess() 的輸出
        job_id          分析任務 ID
        config          AppConfig 實例（或含 bob_bridge key 的 dict，向後相容）

    回傳：
        根因分析結果字典（符合 Bob Shell 輸出契約）
        失敗時回傳含 "error" 欄位的字典，不拋出例外
    """
    if config is None:
        from config_loader import AppConfig
        config = AppConfig.load()

    if hasattr(config, "bob_workspace"):
        workspace   = config.bob_workspace
        logs_subdir = config.bob_logs_subdir
        mode        = config.bob_mode
        timeout     = config.bob_timeout
    else:
        bob_cfg     = config.get("bob_bridge", {})
        workspace   = bob_cfg.get("workspace_path", "/workspace")
        logs_subdir = bob_cfg.get("logs_subdir", "logs")
        mode        = bob_cfg.get("mode", "log-analyst")
        timeout     = bob_cfg.get("timeout", 120)

    # Step 1：將事件序列寫入本地路徑
    file_path = _write_event_sequence(event_sequence, workspace, logs_subdir, job_id)

    # Step 2：直接執行 bob run（無 SSH）
    try:
        result = _run_bob(file_path, mode, timeout, _ANALYSIS_PROMPT)
    except subprocess.TimeoutExpired:
        logger.error("Bob CLI 分析逾時，任務 ID：%s", job_id)
        return {"error": f"Bob CLI 分析逾時（{timeout}s）"}
    except FileNotFoundError:
        logger.error("bob CLI 未安裝或不在 PATH 中")
        return {"error": "bob CLI 未找到，請確認已安裝"}
    except Exception as exc:
        logger.error("bob run 執行失敗：%s", exc)
        return {"error": str(exc)}

    if result.returncode != 0:
        logger.error("Bob CLI 執行失敗（exit %d）：%s", result.returncode, result.stderr[:500])
        return {
            "error": f"Bob CLI 執行失敗（exit code {result.returncode}）",
            "stderr": result.stderr[:2000],
        }

    # Step 3：解析輸出
    analysis = _parse_bob_output(result.stdout)

    # 保留 instana_context（從 event_sequence 透傳至 report_builder）
    if "instana_context" in event_sequence and "instana_context" not in analysis:
        analysis["instana_context"] = event_sequence["instana_context"]

    # 清理暫存檔
    try:
        file_path.unlink(missing_ok=True)
    except Exception:
        pass

    return analysis

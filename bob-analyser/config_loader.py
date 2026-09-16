# Updated: 2026-09-15 19:58:12 +0800
"""
config_loader.py
────────────────
深模組：在 process 啟動時讀取一次 config.yaml，對外暴露型別化屬性。

呼叫端只需：
    from config_loader import AppConfig
    cfg = AppConfig.load()           # 使用預設路徑或 ELK_CONFIG 環境變數
    cfg = AppConfig.load("/custom/path/config.yaml")

業務函式不再接受 config_path 字串參數；
所有模組的 _load_config() helper 均以 AppConfig 取代。
"""

import logging
import os
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = "/app/config/config.yaml"


class AppConfig:
    """
    AppConfig 是 config.yaml 的唯一解析 seam。

    屬性名稱對應 config.yaml 中的 key，並提供型別保證。
    呼叫端不需要知道 YAML 結構。
    """

    def __init__(self, raw: dict, path: str) -> None:
        self._raw = raw
        self._path = path

    # ── 工廠 ──────────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: Optional[str] = None) -> "AppConfig":
        """
        讀取並解析設定檔。

        優先序：
          1. 明確傳入的 path 參數
          2. 環境變數 ELK_CONFIG
          3. 預設路徑 /app/config/config.yaml
        """
        resolved = path or os.environ.get("ELK_CONFIG") or _DEFAULT_CONFIG_PATH
        with open(resolved, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        logger.debug("AppConfig 已從 %s 載入", resolved)
        return cls(raw, resolved)

    # ── 資料庫 ────────────────────────────────────────────────────────

    @property
    def db_path(self) -> str:
        return self._raw["database"]["path"]

    # ── Elasticsearch ─────────────────────────────────────────────────

    @property
    def es_mode(self) -> str:
        return self._raw["elasticsearch"].get("mode", "mock")

    @property
    def es_config(self) -> dict:
        """回傳 ES 連線設定，密碼以環境變數 ES_PASSWORD 覆蓋（F-02）。"""
        cfg = dict(self._raw["elasticsearch"])
        env_pw = os.environ.get("ES_PASSWORD")
        if env_pw:
            cfg["password"] = env_pw
        return cfg

    # ── 欄位映射 ──────────────────────────────────────────────────────

    @property
    def field_mapping(self) -> dict:
        return self._raw["field_mapping"]

    # ── 過濾 ──────────────────────────────────────────────────────────

    @property
    def filter_config(self) -> dict:
        return self._raw["filter"]

    # ── Bob Bridge ────────────────────────────────────────────────────

    @property
    def bob_workspace(self) -> str:
        return self._raw.get("bob_bridge", {}).get("workspace_path", "/workspace")

    @property
    def bob_logs_subdir(self) -> str:
        return self._raw.get("bob_bridge", {}).get("logs_subdir", "logs")

    @property
    def bob_mode(self) -> str:
        return self._raw.get("bob_bridge", {}).get("mode", "log-analyst")

    @property
    def bob_timeout(self) -> int:
        return int(self._raw.get("bob_bridge", {}).get("timeout", 120))

    # ── 報告 ──────────────────────────────────────────────────────────

    @property
    def report_output_dir(self) -> str:
        return self._raw.get("report", {}).get("output_dir", "/reports")

    @property
    def report_filename_prefix(self) -> str:
        return self._raw.get("report", {}).get("filename_prefix", "elk_analysis")

    # ── Checkpoint ────────────────────────────────────────────────────

    @property
    def default_lookback_minutes(self) -> int:
        return int(self._raw.get("checkpoint", {}).get("default_lookback_minutes", 60))

    # ── API ───────────────────────────────────────────────────────────

    @property
    def api_host(self) -> str:
        return self._raw.get("api", {}).get("host", "0.0.0.0")

    @property
    def api_port(self) -> int:
        return int(self._raw.get("api", {}).get("port", 8080))

    # ── Instana ───────────────────────────────────────────────────────

    @property
    def instana_config(self) -> dict:
        """回傳 Instana 整合設定，api_token 以環境變數 INSTANA_API_TOKEN 覆蓋。"""
        cfg = dict(self._raw.get("instana", {}))
        env_token = os.environ.get("INSTANA_API_TOKEN")
        if env_token:
            cfg["api_token"] = env_token
        return cfg

    @property
    def instana_enabled(self) -> bool:
        return bool(self.instana_config.get("enabled", False))

    # ── 原始 dict（向後相容，逐步移除）──────────────────────────────

    @property
    def raw(self) -> dict:
        """回傳原始設定字典，供尚未遷移的呼叫端暫時使用。"""
        return self._raw

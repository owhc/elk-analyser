# scripts/_export_openapi_helper.py
# Created: 2026-09-16 08:06:45 +0800
#
# 被 export-openapi.sh 呼叫的 Python helper。
# 以 FastAPI app.openapi() 生成 OpenAPI schema（無需啟動 uvicorn server）
# 並寫入指定路徑。
#
# 執行目錄：bob-analyser/（確保模組 import 路徑正確）
# 用法：python _export_openapi_helper.py <output_path>

import json
import os
import pathlib
import sys
import tempfile

# ── 處理本機開發環境：/db / /app/config 可能不存在 ──────────────────────
_cfg_candidates = [
    os.environ.get("ELK_CONFIG", ""),
    "config/config.yaml",               # 相對於 bob-analyser/
    "/app/config/config.yaml",          # container 路徑
]
_cfg_path = next((p for p in _cfg_candidates if p and pathlib.Path(p).exists()), None)

if _cfg_path is None:
    print("ERROR: 找不到 config.yaml，請設定 ELK_CONFIG 環境變數", file=sys.stderr)
    sys.exit(1)

# 若 db.path 指向的目錄不存在，改為臨時路徑（僅供 schema export，不影響實際資料）
import yaml  # noqa: E402 — yaml 安裝於 requirements.txt

with open(_cfg_path, encoding="utf-8") as _f:
    _raw = yaml.safe_load(_f)

_db_path = _raw.get("database", {}).get("path", "/db/history.db")
if not pathlib.Path(_db_path).parent.exists():
    _tmp = tempfile.mkdtemp()
    _raw["database"]["path"] = str(pathlib.Path(_tmp) / "export_tmp.db")
    _tmp_cfg = str(pathlib.Path(_tmp) / "config_tmp.yaml")
    with open(_tmp_cfg, "w", encoding="utf-8") as _f:
        yaml.dump(_raw, _f, allow_unicode=True)
    os.environ["ELK_CONFIG"] = _tmp_cfg
else:
    os.environ["ELK_CONFIG"] = _cfg_path

# ── import FastAPI app（此時 AppConfig 已可正常載入）───────────────────
from api import app  # noqa: E402

# ── 生成 OpenAPI schema ───────────────────────────────────────────────
schema = app.openapi()

output_path = sys.argv[1] if len(sys.argv) > 1 else "docs/openapi.json"
pathlib.Path(output_path).parent.mkdir(parents=True, exist_ok=True)

with open(output_path, "w", encoding="utf-8") as f:
    json.dump(schema, f, ensure_ascii=False, indent=2)

print(f"OpenAPI spec 已匯出：{output_path}")
print(f"  title:   {schema.get('info', {}).get('title', '')}")
print(f"  version: {schema.get('info', {}).get('version', '')}")
print(f"  paths:   {len(schema.get('paths', {}))} 個端點")

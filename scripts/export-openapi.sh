#!/usr/bin/env bash
# scripts/export-openapi.sh
# Created: 2026-09-16 08:06:45 +0800
#
# 匯出 ELK Analyser FastAPI 的 OpenAPI spec 至 docs/openapi.json
#
# 用法（在 repo 根目錄執行）：
#   bash scripts/export-openapi.sh
#   bash scripts/export-openapi.sh /custom/output/path.json
#
# 先決條件：
#   Python 環境已安裝 bob-analyser/requirements.txt 依賴（pip install -r requirements.txt）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_FILE="${1:-${REPO_ROOT}/docs/openapi.json}"

echo "=== ELK Analyser OpenAPI Spec Export ==="
echo "Repo:   ${REPO_ROOT}"
echo "Output: ${OUTPUT_FILE}"

# ── 切至 bob-analyser，確保模組 import 正確 ───────────────────────────────
cd "${REPO_ROOT}/bob-analyser"

# ── 呼叫獨立 Python helper ────────────────────────────────────────────────
python "${SCRIPT_DIR}/_export_openapi_helper.py" "${OUTPUT_FILE}"

echo ""
echo "✅  完成。可將 docs/openapi.json 加入 git 追蹤供 CI/CD 或外部整合使用。"

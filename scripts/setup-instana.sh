#!/usr/bin/env bash
# Created: 2026-09-03 17:37:42 +0800
# Updated: 2026-11-02 21:46:00 +0800
# scripts/setup-instana.sh — Instana Java Agent 準備腳本
# ================================================================
# 執行：bash scripts/setup-instana.sh
# bootstrap JAR 已隨 repo 提供並由 override 直接 bind mount，
# 不再下載（舊版下載失敗會把 404 JSON 當 JAR，導致 Liberty 無法啟動）。
# ================================================================
set -euo pipefail

JAR_PATH="$(dirname "$0")/../instana-agent-package/instana-javaagent-bootstrap.jar"

echo "=== [1/2] 驗證 bootstrap JAR ==="
if ! unzip -tq "${JAR_PATH}" >/dev/null 2>&1; then
  echo "❌ ${JAR_PATH} 不存在或不是有效 JAR，請 git checkout 還原該檔案" >&2
  exit 1
fi
echo "✓ JAR 有效：${JAR_PATH}"

echo ""
echo "=== [2/2] PostgreSQL pg_monitor role ==="
echo "   全新 db_data volume 首次啟動會由 02-grant-pg-monitor.sh 自動授予。"
echo "   沿用既有 volume 時才需手動：podman exec banking-db psql -U bankuser -d bankdb -c \"GRANT pg_monitor TO bankuser;\""

echo ""
echo "========================================================"
echo "✅ 準備完成！啟動指令："
echo ""
echo "  podman-compose -f podman-compose.yml -f podman-compose.override.yml up -d"
echo ""
echo "驗證："
echo "  podman logs bankingdemo-elk-analyzer-agent --tail 30"
echo "  podman logs banking-app 2>&1 | grep -i instana"
echo "========================================================"

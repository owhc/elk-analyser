#!/usr/bin/env bash
# Created: 2026-09-03 17:37:42 +0800
# Updated: 2026-09-15 10:43:17 +0800
# scripts/setup-instana.sh — Instana Java Agent 一鍵準備腳本
# ================================================================
# 執行：bash scripts/setup-instana.sh
# ================================================================
set -euo pipefail

AGENT_KEY="uBp4GXpZQpKrHxMXNcvInQ"
DOWNLOAD_KEY="uBp4GXpZQpKrHxMXNcvInQ"
ENDPOINT="ingress-orange-saas.instana.io"
JAR_PATH="$(pwd)/instana-agent.jar"
VOLUME_NAME="instana-java-agent"

echo "=== [1/4] 下載 Instana Java Trace Agent JAR ==="
curl -sSLo "${JAR_PATH}" \
  "https://${ENDPOINT}/downloads/instana-agent.jar" \
  -H "x-instana-agent-key: ${DOWNLOAD_KEY}" \
  || {
    echo "⚠  自動下載失敗（SaaS 端點需登入）"
    echo "   請手動前往 Instana UI → More > Agents > Install Agent > Java"
    echo "   下載 instana-agent.jar 並放置於：${JAR_PATH}"
    echo "   完成後重新執行此腳本的步驟 2~4"
    exit 1
  }
echo "✓ JAR 下載完成：${JAR_PATH}"

echo ""
echo "=== [2/4] 建立 Podman named volume: ${VOLUME_NAME} ==="
podman volume create "${VOLUME_NAME}" 2>/dev/null \
  && echo "✓ volume 已建立" \
  || echo "✓ volume 已存在，跳過"

echo ""
echo "=== [3/4] 將 JAR 複製至 volume ==="
podman run --rm \
  -v "${VOLUME_NAME}:/opt/instana-agent" \
  -v "${JAR_PATH}:/tmp/instana-agent.jar:ro" \
  busybox cp /tmp/instana-agent.jar /opt/instana-agent/instana-agent.jar
echo "✓ JAR 已放入 volume ${VOLUME_NAME}"

echo ""
echo "=== [4/4] 授予 PostgreSQL pg_monitor role ==="
echo "   （需等 banking-db 容器啟動後執行）"
echo "   指令：podman exec -it banking-db psql -U db2inst1 -d bankdb -c \"GRANT pg_monitor TO db2inst1;\""

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

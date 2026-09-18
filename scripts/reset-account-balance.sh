#!/usr/bin/env bash
# Created: 2026-09-17 13:48:56 +0800
# reset-account-balance.sh — 重置 banking-db 帳戶餘額
#
# 用法：
#   bash scripts/reset-account-balance.sh [OPTIONS]
#
# Options:
#   -b <金額>   每個帳號的重置金額（預設 150000）
#   -c <容器>   PostgreSQL 容器名稱（預設 banking-db）
#   -u <用戶>   DB 用戶（預設 db2inst1）
#   -d <DB>     資料庫名稱（預設 bankdb）
#   --show      只顯示目前餘額，不重置
#   -h          顯示說明
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# 確保 podman 可被找到（macOS /opt/podman/bin，Linux /usr/bin）
PODMAN=$(command -v podman 2>/dev/null \
  || command -v /opt/podman/bin/podman 2>/dev/null \
  || { echo "[ERR] 找不到 podman 指令" >&2; exit 1; })

BALANCE=150000
CONTAINER="banking-db"
DB_USER="db2inst1"
DB_NAME="bankdb"
SHOW_ONLY=false

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC}  $*"; }
info() { echo -e "${CYAN}[>>]${NC}  $*"; }
warn() { echo -e "${YELLOW}[!!]${NC}  $*"; }
err()  { echo -e "${RED}[ERR]${NC} $*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -b) BALANCE="$2";   shift 2 ;;
    -c) CONTAINER="$2"; shift 2 ;;
    -u) DB_USER="$2";   shift 2 ;;
    -d) DB_NAME="$2";   shift 2 ;;
    --show) SHOW_ONLY=true; shift ;;
    -h|--help)
      sed -n '/^# 用法/,/^# ─/p' "$0" | head -n 15
      exit 0 ;;
    *) err "未知選項: $1"; exit 1 ;;
  esac
done

# ── 確認容器存在且運行中 ──────────────────────────────────────────────────────
if ! "$PODMAN" ps --format "{{.Names}}" | grep -q "^${CONTAINER}$"; then
  err "找不到運行中的容器：${CONTAINER}"
  err "請先啟動 stack：podman-compose up -d"
  exit 1
fi

psql() {
  "$PODMAN" exec "$CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -t -A -c "$1"
}

# ── 顯示目前餘額 ──────────────────────────────────────────────────────────────
echo ""
info "目前帳戶餘額："
psql "SELECT account_id, owner_name, balance FROM banking.accounts ORDER BY account_id;" \
  | column -t -s '|' | sed 's/^/  /'
echo ""

if $SHOW_ONLY; then
  exit 0
fi

# ── 重置餘額 ──────────────────────────────────────────────────────────────────
warn "即將將所有帳號餘額重置為 NT\$${BALANCE}..."
psql "UPDATE banking.accounts SET balance = ${BALANCE}.00;"
ok "重置完成"
echo ""

info "重置後餘額："
psql "SELECT account_id, owner_name, balance FROM banking.accounts ORDER BY account_id;" \
  | column -t -s '|' | sed 's/^/  /'
echo ""
ok "完成！可開始執行流量測試：bash scripts/generate-banking-traffic.sh --exhaust --exhaust-max 10"

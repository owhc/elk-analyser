#!/usr/bin/env bash
# Created: 2026-09-03 20:47:54 +0800
# Updated: 2026-09-16 14:40:44 +0800
# 模擬銀行使用者旅程：登入 → 查詢帳戶 → 轉帳（Liberty → MQ → DB）→ 確認帳戶 → 登出。
# 用法：scripts/simulate-banking-journey.sh [旅程次數] [旅程間隔秒數]
set -euo pipefail

BASE_URL="${LIBERTY_BASE_URL:-http://localhost:9080/banking-app}"
COUNT="${1:-5}"
INTERVAL_SECONDS="${2:-2}"
MQ_SETTLE_SECONDS="${MQ_SETTLE_SECONDS:-2}"

if ! [[ "$COUNT" =~ ^[1-9][0-9]*$ ]]; then
    echo "旅程次數必須是正整數：$COUNT" >&2
    exit 1
fi

if ! [[ "$INTERVAL_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]] || ! [[ "$MQ_SETTLE_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "間隔秒數必須是非負數。" >&2
    exit 1
fi

request() {
    local method="$1"
    local path="$2"
    local journey_id="$3"
    local body="${4:-}"
    local args=(--fail --silent --show-error --request "$method"
        --header 'Content-Type: application/json'
        --header "X-Journey-ID: ${journey_id}")

    if [[ -n "$body" ]]; then
        args+=(--data "$body")
    fi

    curl "${args[@]}" "${BASE_URL}${path}"
}

echo "檢查 Liberty 狀態：${BASE_URL}/api/health"
curl --fail --silent --show-error "${BASE_URL}/api/health" >/dev/null

echo "模擬 ${COUNT} 個銀行使用者旅程"
for ((i = 1; i <= COUNT; i++)); do
    journey_id="journey-$(date +%s)-${i}"

    if (( i % 2 )); then
        USERNAME="user001"
        AUTH_CREDENTIAL="${AUTH_CREDENTIAL_1:-pass001}"
        FROM_ACCOUNT="ACC001"
        TO_ACCOUNT="ACC002"
    else
        USERNAME="user002"
        AUTH_CREDENTIAL="${AUTH_CREDENTIAL_2:-pass002}"
        FROM_ACCOUNT="ACC002"
        TO_ACCOUNT="ACC001"
    fi

    AMOUNT=$((i * 10))
    echo "[$i/$COUNT] ${journey_id}: 登入 ${USERNAME}"
    request POST /api/login "$journey_id" "{\"username\":\"${USERNAME}\",\"password\":\"${AUTH_CREDENTIAL}\"}" >/dev/null

    echo "[$i/$COUNT] ${journey_id}: 查詢 ${FROM_ACCOUNT}"
    request GET "/api/accounts/${FROM_ACCOUNT}" "$journey_id" >/dev/null

    echo "[$i/$COUNT] ${journey_id}: 轉帳 NT$${AMOUNT}（${FROM_ACCOUNT} → ${TO_ACCOUNT}）"
    request POST /api/transfer "$journey_id" "{\"fromAccount\":\"${FROM_ACCOUNT}\",\"toAccount\":\"${TO_ACCOUNT}\",\"amount\":${AMOUNT},\"description\":\"流量旅程 ${journey_id}\"}" >/dev/null

    sleep "$MQ_SETTLE_SECONDS"

    echo "[$i/$COUNT] ${journey_id}: 確認 ${TO_ACCOUNT}"
    request GET "/api/accounts/${TO_ACCOUNT}" "$journey_id" >/dev/null

    echo "[$i/$COUNT] ${journey_id}: 登出 ${USERNAME}"
    request POST /api/logout "$journey_id" >/dev/null

    if (( i < COUNT )) && [[ "$INTERVAL_SECONDS" != "0" ]]; then
        sleep "$INTERVAL_SECONDS"
    fi
done

echo "完成 ${COUNT} 個旅程；每個旅程皆包含 Liberty HTTP、Artemis MQ 與 DB 存取。"

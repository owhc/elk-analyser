#!/usr/bin/env bash
# Created: 2026-09-03 20:41:59 +0800
# 透過 Liberty HTTP API 發起轉帳，觸發 Liberty → Artemis MQ → DB 呼叫鏈。
# 用法：scripts/trigger-liberty-mq-db.sh [次數] [間隔秒數]
set -euo pipefail

BASE_URL="${LIBERTY_BASE_URL:-http://localhost:9080/banking-app}"
COUNT="${1:-5}"
INTERVAL_SECONDS="${2:-1}"

if ! [[ "$COUNT" =~ ^[1-9][0-9]*$ ]]; then
    echo "次數必須是正整數：$COUNT" >&2
    exit 1
fi

if ! [[ "$INTERVAL_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    echo "間隔秒數必須是非負數：$INTERVAL_SECONDS" >&2
    exit 1
fi

echo "檢查 Liberty 狀態：${BASE_URL}/api/health"
curl --fail --silent --show-error "${BASE_URL}/api/health" >/dev/null

echo "觸發 ${COUNT} 筆 Liberty → MQ → DB 轉帳呼叫"
for ((i = 1; i <= COUNT; i++)); do
    if (( i % 2 )); then
        FROM_ACCOUNT="ACC001"
        TO_ACCOUNT="ACC002"
    else
        FROM_ACCOUNT="ACC002"
        TO_ACCOUNT="ACC001"
    fi

    AMOUNT=$((i * 10))
    RESPONSE=$(curl --fail --silent --show-error \
        --request POST "${BASE_URL}/api/transfer" \
        --header 'Content-Type: application/json' \
        --data "{\"fromAccount\":\"${FROM_ACCOUNT}\",\"toAccount\":\"${TO_ACCOUNT}\",\"amount\":${AMOUNT},\"description\":\"Instana 呼叫鏈測試 #${i}\"}")

    printf '[%d/%d] %s → %s，NT$%d\n%s\n' \
        "$i" "$COUNT" "$FROM_ACCOUNT" "$TO_ACCOUNT" "$AMOUNT" "$RESPONSE"

    if (( i < COUNT )) && [[ "$INTERVAL_SECONDS" != "0" ]]; then
        sleep "$INTERVAL_SECONDS"
    fi
done

echo "已送出 ${COUNT} 筆轉帳。MQ consumer 會非同步寫入資料庫。"

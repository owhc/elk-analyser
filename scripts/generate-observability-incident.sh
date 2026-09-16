#!/usr/bin/env bash
# Created: 2026-09-15 20:48:46 +0800
# 產生可由 Instana 觀測的 Java App 錯誤，注入同時間窗 ELK 事件，再觸發 PPTX 分析。
# 用法：scripts/generate-observability-incident.sh [錯誤請求次數]
set -euo pipefail

BANKING_URL="${BANKING_URL:-http://localhost:9080/banking-app}"
ELASTICSEARCH_URL="${ELASTICSEARCH_URL:-http://localhost:9200}"
ANALYSER_URL="${ANALYSER_URL:-http://localhost:8080}"
ERROR_COUNT="${1:-12}"
INSTANA_WAIT_SECONDS="${INSTANA_WAIT_SECONDS:-20}"
ANALYSE_TRIGGER="${ANALYSE_TRIGGER:-webui_instana}"

if ! [[ "$ERROR_COUNT" =~ ^[1-9][0-9]*$ ]]; then
    echo "錯誤請求次數必須是正整數：$ERROR_COUNT" >&2
    exit 1
fi

for value in "$INSTANA_WAIT_SECONDS"; do
    if ! [[ "$value" =~ ^[0-9]+$ ]]; then
        echo "等待秒數必須是非負整數：$value" >&2
        exit 1
    fi
done

require_service() {
    local name="$1"
    local url="$2"
    if ! curl --fail --silent --show-error --max-time 10 "$url" >/dev/null; then
        echo "$name 無法連線：$url" >&2
        exit 1
    fi
}

utc_time() {
    date -u -v"$1" '+%Y-%m-%dT%H:%M:%S.000Z'
}

json_escape() {
    python3 -c 'import json,sys; print(json.dumps(sys.argv[1], ensure_ascii=False))' "$1"
}

require_service "Banking App" "${BANKING_URL}/api/health"
require_service "Elasticsearch" "${ELASTICSEARCH_URL}/_cluster/health"
require_service "ELK Analyser" "${ANALYSER_URL}/health"

incident_id="OBS-$(date -u '+%Y%m%dT%H%M%SZ')"
from_iso="$(utc_time '-1M')"
to_iso="$(utc_time '+2M')"
from_api="$(date -u -v-1M '+%Y-%m-%d %H:%M')"
to_api="$(date -u -v+2M '+%Y-%m-%d %H:%M')"
index_name="banking-logs-synthetic-$(date -u '+%Y.%m.%d')"

printf '事件 %s，分析視窗 %s → %s\n' "$incident_id" "$from_iso" "$to_iso"
echo "1/4 觸發 Java App HTTP 400 與非同步 PostgreSQL rollback"

for ((i = 1; i <= ERROR_COUNT; i++)); do
    correlation_id="${incident_id}-${i}"

    # Controller validation error：由 Instana 記錄 HTTP 400 call。
    status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
        --request POST "${BANKING_URL}/api/transfer" \
        --header 'Content-Type: application/json' \
        --header "X-Journey-ID: ${correlation_id}" \
        --data '{"fromAccount":"ACC001","toAccount":"ACC001","amount":-1}')
    if [[ "$status" != "400" ]]; then
        echo "預期 HTTP 400，實際為 $status" >&2
        exit 1
    fi

    # 非同步錯誤：Controller 回 202，MDB 因來源帳號不存在而 rollback 並記錄 SEVERE。
    status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
        --request POST "${BANKING_URL}/api/transfer" \
        --header 'Content-Type: application/json' \
        --header "X-Journey-ID: ${correlation_id}" \
        --data "{\"fromAccount\":\"MISSING-${i}\",\"toAccount\":\"ACC001\",\"amount\":100,\"description\":\"${incident_id}\"}")
    if [[ "$status" != "202" ]]; then
        echo "預期 HTTP 202，實際為 $status" >&2
        exit 1
    fi
done

echo "2/4 注入與 Java 事件同時間窗、同 incident ID 的 ELK 因果鏈"
bulk_file=$(mktemp)
trap 'rm -f "$bulk_file"' EXIT

append_event() {
    local timestamp="$1"
    local level="$2"
    local service="$3"
    local host="$4"
    local message="$5"

    printf '{"index":{"_index":"%s"}}\n' "$index_name" >>"$bulk_file"
    printf '{"@timestamp":"%s","log":{"level":"%s"},"service":"%s","host":{"name":"%s"},"trace":{"id":"%s"},"incident":{"id":"%s"},"message":%s,"environment":"demo","synthetic":true}\n' \
        "$timestamp" "$level" "$service" "$host" "$incident_id" "$incident_id" \
        "$(json_escape "$message")" >>"$bulk_file"
}

append_event "$(utc_time '-20S')" "ERROR" "was-liberty" "banking-app" \
    "SRVE0293E: REST transfer validation rejected repeated invalid requests with HTTP 400. incident=${incident_id} count=${ERROR_COUNT}."
append_event "$(utc_time '-10S')" "ERROR" "was-liberty" "banking-app" \
    "BankingMDB TRANSFER rolled back: java.sql.SQLException: 來源帳號不存在. incident=${incident_id} failed_messages=${ERROR_COUNT}."
append_event "$(utc_time '+0S')" "CRITICAL" "postgresql" "banking-db" \
    "ERROR: transaction rollback spike detected for banking.transactions; foreign account lookup failed. incident=${incident_id}."
append_event "$(utc_time '+10S')" "ERROR" "activemq-artemis" "banking-app" \
    "AMQ224016: message processing failed repeatedly on bankingQueue; consumer rollback count=${ERROR_COUNT}. incident=${incident_id}."
append_event "$(utc_time '+20S')" "FATAL" "was-liberty" "banking-app" \
    "Banking transfer error rate exceeded demo threshold: invalid HTTP requests and MDB database rollbacks correlated. incident=${incident_id}."

bulk_response=$(curl --fail --silent --show-error \
    --request POST "${ELASTICSEARCH_URL}/_bulk?refresh=wait_for" \
    --header 'Content-Type: application/x-ndjson' \
    --data-binary "@${bulk_file}")
if [[ "$bulk_response" == *'"errors":true'* ]]; then
    echo "Elasticsearch bulk 寫入部分失敗：$bulk_response" >&2
    exit 1
fi

echo "已寫入 5 筆 ELK 異常事件至 ${index_name}"

if (( INSTANA_WAIT_SECONDS > 0 )); then
    echo "3/4 等待 ${INSTANA_WAIT_SECONDS} 秒，讓 Instana 與 Logstash 完成收集"
    sleep "$INSTANA_WAIT_SECONDS"
else
    echo "3/4 跳過 Instana 收集等待"
fi

echo "4/4 觸發 ELK + Instana PPTX 分析"
response=$(curl --fail --silent --show-error \
    --request POST "${ANALYSER_URL}/analyse" \
    --header 'Content-Type: application/json' \
    --data "{\"from_time\":\"${from_api}\",\"to_time\":\"${to_api}\",\"trigger\":\"${ANALYSE_TRIGGER}\"}")

printf '%s\n' "$response"
status=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("status", ""))' <<<"$response")
report_path=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("report_path") or "")' <<<"$response")
if [[ "$status" != "completed" || -z "$report_path" ]]; then
    echo "分析未完成或未產生報告。" >&2
    exit 1
fi

printf '完成：incident=%s report=%s\n' "$incident_id" "$report_path"
if [[ "$ANALYSE_TRIGGER" == "webui_instana" ]]; then
    echo "若報告沒有 Instana APM 頁，請確認 analyser 內 instana.enabled=true 且 INSTANA_API_TOKEN 已設定。"
fi

#!/usr/bin/env bash
# Updated: 2026-09-17 12:58:05 +0800
# generate-banking-traffic.sh — 對 banking-app 前台產生轉帳流量
#
# 用途：手動執行後對 Grafana / Kibana Dashboard 產生明顯的 log 量與 ES 索引數據
#
# 用法：
#   bash scripts/generate-banking-traffic.sh [OPTIONS]
#
# Options:
#   -n <次數>     每位用戶執行的轉帳次數（預設 20）
#   -d <秒>       每次請求之間的間隔（預設 0.3）
#   -b <主機>     Liberty 主機（預設 localhost）
#   -p <端口>     Liberty HTTP 端口（預設 9080）
#   --burst       使用 burst 模式（大量快速請求，適合壓測觀察）
#   --errors      額外插入若干錯誤請求（無效帳號、負金額），讓 error 圖表有數據
#   --slow        模擬 network jitter（每筆隨機延遲 1~5s），讓 Instana latency 指標異常
#   --exhaust     耗盡模式：同一帳號連續轉出直到餘額不足，觀察 cascade error 傳播
#   --exhaust-max <N>  耗盡模式最多嘗試次數上限（預設 50，防止 app 無餘額檢查時無限迴圈）
#   -h            顯示說明
#
# 範例：
#   bash scripts/generate-banking-traffic.sh                                                      # 預設 20 次
#   bash scripts/generate-banking-traffic.sh -n 50 --errors                                       # 50 次 + 錯誤流量
#   bash scripts/generate-banking-traffic.sh -n 100 --burst                                       # 快速 100 次壓測
#   bash scripts/generate-banking-traffic.sh -n 100 -d 0.1 -b localhost -p 9080 --burst --errors  # 全部參數
#   bash scripts/generate-banking-traffic.sh -n 30 --slow                                         # jitter 壓測
#   bash scripts/generate-banking-traffic.sh --exhaust                                             # 餘額耗盡 cascade
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── 預設值 ──────────────────────────────────────────────────────────────────
COUNT=20
DELAY=0.3
HOST="localhost"
PORT=9080
BURST=false
INJECT_ERRORS=false
SLOW=false
EXHAUST=false
EXHAUST_MAX=50

# ── 顏色輸出 ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC}  $*"; }
info() { echo -e "${CYAN}[>>]${NC}  $*"; }
warn() { echo -e "${YELLOW}[!!]${NC}  $*"; }
err()  { echo -e "${RED}[ERR]${NC} $*"; }

# ── 參數解析 ─────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    -n) COUNT="$2";   shift 2 ;;
    -d) DELAY="$2";   shift 2 ;;
    -b) HOST="$2";    shift 2 ;;
    -p) PORT="$2";    shift 2 ;;
    --burst)   BURST=true;         shift ;;
    --errors)  INJECT_ERRORS=true; shift ;;
    --slow)        SLOW=true;              shift ;;
    --exhaust)     EXHAUST=true;           shift ;;
    --exhaust-max) EXHAUST_MAX="$2";       shift 2 ;;
    -h|--help)
      sed -n '/^# 用法/,/^# ─/p' "$0" | head -n 20
      exit 0 ;;
    *) err "未知選項: $1"; exit 1 ;;
  esac
done

if $BURST; then DELAY=0.05; fi
if $SLOW;  then DELAY=0;    fi   # slow 模式每筆自行隨機 sleep，不用固定 DELAY

BASE_URL="http://${HOST}:${PORT}/banking-app/api"

# ── 工具函式 ─────────────────────────────────────────────────────────────────
require_cmd() {
  command -v "$1" &>/dev/null || { err "找不到指令: $1（請先安裝）"; exit 1; }
}
require_cmd curl
require_cmd jq

# ── 健康檢查 ─────────────────────────────────────────────────────────────────
info "檢查 banking-app 健康狀態 → ${BASE_URL}/health"
HTTP_STATUS=$(curl -s -o /dev/null -w '%{http_code}' "${BASE_URL}/health" || true)
if [[ "$HTTP_STATUS" != "200" ]]; then
  err "健康檢查失敗（HTTP ${HTTP_STATUS}）。請確認 banking-app 容器已啟動。"
  err "提示：podman logs container-japp | tail -20"
  exit 1
fi
ok "banking-app 健康（HTTP 200）"
echo ""

# ── 用戶帳號定義 ─────────────────────────────────────────────────────────────
# 使用 case 替代 declare -A，相容 macOS 內建 bash 3.x
user_info() {
  case "$1" in
    user001) echo "pass001:ACC001:王大明" ;;
    user002) echo "pass002:ACC002:李小美" ;;
    user003) echo "pass003:ACC003:張志偉" ;;
  esac
}

# 帳號列表（方便循環）
ACCOUNT_IDS=("ACC001" "ACC002" "ACC003")

# 轉帳描述語料庫
DESCRIPTIONS=(
  "薪資轉帳"
  "日常消費"
  "借款還款"
  "電費繳納"
  "房租轉帳"
  "醫療費用"
  "購物付款"
  "餐費分攤"
  "學費繳納"
  "投資入金"
  "保險費用"
  "家庭轉帳"
  "旅遊費用"
  "設備採購"
)

# 金額區間（NTD）
AMOUNTS=(500 1000 1500 2000 2500 3000 5000 8000 10000 15000 20000 30000)

# ── 計數器 ───────────────────────────────────────────────────────────────────
TOTAL_ATTEMPTS=0
TOTAL_SUCCESS=0
TOTAL_FAIL=0
TOTAL_ERR=0
TOTAL_EXHAUST_FAIL=0

# ── 登入並取得 accountId ──────────────────────────────────────────────────────
login() {
  local username="$1" password="$2"
  local resp
  resp=$(curl -s -X POST "${BASE_URL}/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"${username}\",\"password\":\"${password}\"}" \
    --max-time 10 || echo "")
  echo "$resp"
}

# ── 單筆轉帳 ─────────────────────────────────────────────────────────────────
do_transfer() {
  local from_acc="$1" to_acc="$2" amount="$3" desc="$4"
  local resp http_code
  resp=$(curl -s -o /tmp/_banking_resp.json -w '%{http_code}' \
    -X POST "${BASE_URL}/transfer" \
    -H "Content-Type: application/json" \
    -d "{\"fromAccount\":\"${from_acc}\",\"toAccount\":\"${to_acc}\",\"amount\":${amount},\"description\":\"${desc}\"}" \
    --max-time 10 || echo "000")
  echo "$resp"
}

# ── 查詢帳戶（產生 GET 流量）─────────────────────────────────────────────────
do_query() {
  local acc_id="$1"
  curl -s "${BASE_URL}/accounts/${acc_id}" \
    -H "Accept: application/json" \
    --max-time 10 -o /dev/null || true
}

# ── jitter sleep（--slow 模式：1~5 秒隨機延遲）───────────────────────────────
jitter_sleep() {
  local jitter=$(( (RANDOM % 5) + 1 ))
  warn "  jitter: 等待 ${jitter}s（模擬 network latency）"
  sleep "$jitter"
}

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1 — 正常轉帳流量
# ═══════════════════════════════════════════════════════════════════════════
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  PHASE 1 — 正常轉帳流量（每用戶 ${COUNT} 次）  ${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo ""

for USER in "user001" "user002" "user003"; do
  IFS=':' read -r PASS FROM_ACC OWNER <<< "$(user_info "$USER")"

  info "登入 ${USER}（${OWNER}，帳號 ${FROM_ACC}）"
  LOGIN_RESP=$(login "$USER" "$PASS")
  if ! echo "$LOGIN_RESP" | jq -e '.data.accountId' &>/dev/null; then
    warn "登入失敗 → 跳過 ${USER}"
    continue
  fi
  ok "登入成功"

  for ((i = 1; i <= COUNT; i++)); do
    # 隨機選擇目標帳號（排除自己）
    CANDIDATES=()
    for acc in "${ACCOUNT_IDS[@]}"; do
      [[ "$acc" != "$FROM_ACC" ]] && CANDIDATES+=("$acc")
    done
    TO_ACC="${CANDIDATES[$((RANDOM % ${#CANDIDATES[@]}))]}"

    # 隨機金額與描述
    AMOUNT="${AMOUNTS[$((RANDOM % ${#AMOUNTS[@]}))]}"
    DESC="${DESCRIPTIONS[$((RANDOM % ${#DESCRIPTIONS[@]}))]}"

    # --slow 模式：轉帳前先 jitter
    if $SLOW; then jitter_sleep; fi

    HTTP_CODE=$(do_transfer "$FROM_ACC" "$TO_ACC" "$AMOUNT" "$DESC")
    TOTAL_ATTEMPTS=$((TOTAL_ATTEMPTS + 1))

    if [[ "$HTTP_CODE" == "202" ]]; then
      TOTAL_SUCCESS=$((TOTAL_SUCCESS + 1))
      printf "  [%3d/%d] ${GREEN}✓${NC} %s → %s  NT\$%s  %s\n" \
        "$i" "$COUNT" "$FROM_ACC" "$TO_ACC" "$AMOUNT" "$DESC"
    else
      TOTAL_FAIL=$((TOTAL_FAIL + 1))
      printf "  [%3d/%d] ${RED}✗${NC} %s → %s  HTTP %s\n" \
        "$i" "$COUNT" "$FROM_ACC" "$TO_ACC" "$HTTP_CODE"
    fi

    # 同時查詢帳戶，產生更多 GET 流量
    do_query "$FROM_ACC" &
    do_query "$TO_ACC"   &
    wait

    if ! $SLOW; then sleep "$DELAY"; fi
  done

  echo ""
done

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2 — 錯誤請求（可選）
# ═══════════════════════════════════════════════════════════════════════════
if $INJECT_ERRORS; then
  echo -e "${YELLOW}══════════════════════════════════════════════════${NC}"
  echo -e "${YELLOW}  PHASE 2 — 注入錯誤請求（讓 error 圖表有數據）  ${NC}"
  echo -e "${YELLOW}══════════════════════════════════════════════════${NC}"
  echo ""

  ERROR_CASES=(
    # fromAccount  toAccount   amount  description
    "ACC001        ACC001      1000    自轉（應拒絕）"
    "ACC001        INVALID_99  500     無效目標帳號"
    "             ACC002      2000    空來源帳號"
    "ACC003        ACC001      -500    負金額"
    "ACC002        ACC003      0       零金額"
  )

  for CASE in "${ERROR_CASES[@]}"; do
    IFS=$'\t' read -r _ _ _ _ <<< "$CASE"
    # 直接用 curl 傳送各種壞請求
    HTTP_CODE=$(curl -s -o /tmp/_banking_err.json -w '%{http_code}' \
      -X POST "${BASE_URL}/transfer" \
      -H "Content-Type: application/json" \
      -d "$CASE" \
      --max-time 5 || echo "000")
    TOTAL_ATTEMPTS=$((TOTAL_ATTEMPTS + 1))
    TOTAL_ERR=$((TOTAL_ERR + 1))
    echo "  ${RED}ERR${NC} 預期錯誤 → HTTP ${HTTP_CODE}"
    sleep 0.1
  done

  # 明確的壞請求（格式正確但業務違規）
  for BADPAYLOAD in \
    '{"fromAccount":"ACC001","toAccount":"ACC001","amount":999,"description":"自轉"}' \
    '{"fromAccount":"","toAccount":"ACC002","amount":100,"description":"空帳號"}' \
    '{"fromAccount":"ACC002","toAccount":"ACC003","amount":-1,"description":"負數"}' \
    '{"fromAccount":"ACC001","toAccount":"GHOST999","amount":500,"description":"幽靈帳號"}' \
    '{"fromAccount":"ACC003","toAccount":"ACC001","amount":0,"description":"零元"}' ; do
    HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' \
      -X POST "${BASE_URL}/transfer" \
      -H "Content-Type: application/json" \
      -d "$BADPAYLOAD" \
      --max-time 5 || echo "000")
    TOTAL_ATTEMPTS=$((TOTAL_ATTEMPTS + 1))
    TOTAL_ERR=$((TOTAL_ERR + 1))
    printf "  ${RED}BAD${NC} %s → HTTP %s\n" \
      "$(echo "$BADPAYLOAD" | jq -r '.description' 2>/dev/null || echo '?')" "$HTTP_CODE"
    sleep 0.1
  done

  # 錯誤登入（產生 WARN log）
  echo ""
  info "注入錯誤登入嘗試（產生 WARN 日誌）"
  for BADLOGIN in \
    '{"username":"user001","password":"wrongpass"}' \
    '{"username":"hacker","password":"admin"}' \
    '{"username":"user002","password":""}' ; do
    HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' \
      -X POST "${BASE_URL}/login" \
      -H "Content-Type: application/json" \
      -d "$BADLOGIN" \
      --max-time 5 || echo "000")
    printf "  ${YELLOW}WARN${NC} 失敗登入 → HTTP %s\n" "$HTTP_CODE"
    sleep 0.1
  done

  echo ""
fi

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2.5 — --exhaust 餘額耗盡 cascade（可選）
# ═══════════════════════════════════════════════════════════════════════════
if $EXHAUST; then
  echo -e "${RED}══════════════════════════════════════════════════${NC}"
  echo -e "${RED}  PHASE 2.5 — 餘額耗盡模式（cascade error 觀察）  ${NC}"
  echo -e "${RED}══════════════════════════════════════════════════${NC}"
  echo ""
  warn "將對 ACC001 連續轉出大額，直到餘額扣不動或連續 3 次 MDB rollback（上限 ${EXHAUST_MAX} 筆）"
  warn "注意：轉帳為非同步（MQ→MDB），以餘額變化判斷是否真的扣成功"
  echo ""

  EXHAUST_FROM="ACC001"
  EXHAUST_TO="ACC002"
  EXHAUST_AMOUNT=30000   # 每筆大額，快速耗盡
  CONSECUTIVE_FAIL=0
  EXHAUST_ROUND=0
  MDB_SETTLE=1           # 等 MDB 非同步處理的秒數

  # 取得轉帳前餘額（用於比較）
  PREV_BALANCE=$(curl -s "${BASE_URL}/accounts/${EXHAUST_FROM}" \
    --max-time 5 | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['balance'])" 2>/dev/null || echo "-1")
  info "ACC001 起始餘額：NT\$ ${PREV_BALANCE}"
  echo ""

  while (( CONSECUTIVE_FAIL < 3 && EXHAUST_ROUND < EXHAUST_MAX )); do
    EXHAUST_ROUND=$((EXHAUST_ROUND + 1))
    HTTP_CODE=$(do_transfer "$EXHAUST_FROM" "$EXHAUST_TO" "$EXHAUST_AMOUNT" "耗盡測試 #${EXHAUST_ROUND}")
    TOTAL_ATTEMPTS=$((TOTAL_ATTEMPTS + 1))

    if [[ "$HTTP_CODE" != "202" ]]; then
      # HTTP 層直接拒絕（同步錯誤）
      CONSECUTIVE_FAIL=$((CONSECUTIVE_FAIL + 1))
      TOTAL_EXHAUST_FAIL=$((TOTAL_EXHAUST_FAIL + 1))
      printf "  [耗盡 %3d] ${RED}✗${NC} HTTP %s（同步拒絕，連續失敗 %d/3）\n" \
        "$EXHAUST_ROUND" "$HTTP_CODE" "$CONSECUTIVE_FAIL"
      sleep 0.2
      continue
    fi

    # HTTP 202：等 MDB 非同步處理後查餘額，判斷是否真的扣款
    sleep "$MDB_SETTLE"
    CURR_BALANCE=$(curl -s "${BASE_URL}/accounts/${EXHAUST_FROM}" \
      --max-time 5 | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['balance'])" 2>/dev/null || echo "$PREV_BALANCE")

    # python3 浮點比較：若餘額未減少，視為 MDB rollback
    BALANCE_DROPPED=$(python3 -c "print('yes' if float('${CURR_BALANCE}') < float('${PREV_BALANCE}') else 'no')" 2>/dev/null || echo "no")

    if [[ "$BALANCE_DROPPED" == "yes" ]]; then
      CONSECUTIVE_FAIL=0
      printf "  [耗盡 %3d] ${GREEN}✓${NC} ACC001 餘額 NT\$%.0f → NT\$%.0f（扣款成功）\n" \
        "$EXHAUST_ROUND" "$PREV_BALANCE" "$CURR_BALANCE"
      PREV_BALANCE="$CURR_BALANCE"
    else
      CONSECUTIVE_FAIL=$((CONSECUTIVE_FAIL + 1))
      TOTAL_EXHAUST_FAIL=$((TOTAL_EXHAUST_FAIL + 1))
      printf "  [耗盡 %3d] ${RED}✗${NC} 餘額未變（NT\$%.0f）→ MDB rollback（連續 %d/3）\n" \
        "$EXHAUST_ROUND" "$CURR_BALANCE" "$CONSECUTIVE_FAIL"
    fi
  done

  if (( EXHAUST_ROUND >= EXHAUST_MAX )); then
    warn "已達上限 ${EXHAUST_MAX} 筆，強制停止"
  fi
  ok "耗盡模式結束（共 ${EXHAUST_ROUND} 筆，MDB rollback ${TOTAL_EXHAUST_FAIL} 次）"
  warn "預期觀察：DB constraint violation → MDB rollback → Liberty SEVERE → ES ERROR → Grafana spike"
  echo ""
fi

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3 — 查詢輪詢（產生穩定的 GET 流量）
# ═══════════════════════════════════════════════════════════════════════════
QUERY_ROUNDS=5
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  PHASE 3 — 帳戶查詢（${QUERY_ROUNDS} 輪，產生 GET 流量）  ${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo ""

for ((r = 1; r <= QUERY_ROUNDS; r++)); do
  for acc in "${ACCOUNT_IDS[@]}"; do
    HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' \
      "${BASE_URL}/accounts/${acc}" --max-time 5 || echo "000")
    printf "  輪次 %d  GET /accounts/%s → HTTP %s\n" "$r" "$acc" "$HTTP_CODE"
  done
  sleep "$DELAY"
done

echo ""

# ═══════════════════════════════════════════════════════════════════════════
# 結果摘要
# ═══════════════════════════════════════════════════════════════════════════
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  執行結果摘要                                    ${NC}"
echo -e "${CYAN}══════════════════════════════════════════════════${NC}"
echo ""
printf "  總請求數   : %d\n" "$TOTAL_ATTEMPTS"
printf "  成功 (202) : ${GREEN}%d${NC}\n" "$TOTAL_SUCCESS"
printf "  失敗       : ${RED}%d${NC}\n" "$TOTAL_FAIL"
if $INJECT_ERRORS; then
  printf "  預期錯誤   : ${YELLOW}%d${NC}（刻意注入）\n" "$TOTAL_ERR"
fi
if $EXHAUST; then
  printf "  耗盡失敗   : ${RED}%d${NC}（餘額不足 cascade）\n" "$TOTAL_EXHAUST_FAIL"
fi
if $SLOW; then
  echo "  模式       : SLOW（jitter 1~5s/筆，Instana latency P99 應上升）"
fi
echo ""
echo -e "  ${GREEN}完成！請前往以下位置查看數據：${NC}"
echo "  ─────────────────────────────────────────────"
echo "  Grafana Dashboard : http://localhost:3001/grafana/"
echo "  Kibana Discover   : http://localhost:5601"
echo "  ES Index 數量     : curl -s 'http://localhost:9200/banking-logs-*/_count' | jq '.count'"
echo ""

# 清理暫存
rm -f /tmp/_banking_resp.json /tmp/_banking_err.json

<!-- Created: 2026-11-02 23:10:23 +0800 -->
# ELK + Instana 根因分析簡報生成指引

本文件說明當執行「ELK + Instana 合併分析」出現 **「異常 0 筆，CRITICAL 0 筆」** 時的原因剖析，以及如何生成包含完整故障診斷與 AI 根因事件鏈的 PPTX 簡報。

---

## 1. 現象與原因剖析

### 1.1 問題現象
在 Web UI 點擊「⚡ ELK + Instana 合併分析」或透過 API 觸發分析後，收到反饋：
```text
ELK + Instana 合併分析完成：異常 0 筆，CRITICAL 0 筆
```
且產生的診斷報告中缺乏具體的根因診斷與事件鏈內容。

### 1.2 根本原因
- **查詢時段無錯誤日誌**：系統依查詢時間視窗（`from_time` ～ `to_time`）向 Elasticsearch 查詢等級為 `ERROR` / `FATAL` / `CRITICAL` 的日誌。若當前系統處於正常運作或閒置狀態，ES 內無對應的異常日誌，提取筆數即為 0。
- **無事件鏈可供分析**：無異常日誌時，前處理模組無法建立事件鏈（`event_chains: []`），AI 引擎（Bob CLI）無法在缺乏因果證據的情境下推導出故障根因，導致簡報內的根因診斷與修復建議為空。

---

## 2. 如何生成根因分析簡報

您可以透過以下三種方式產生異常日誌或指定包含故障資料的時間區間：

### 方法一：Web UI 一鍵注入 Demo 故障資料（最快）

1. 開啟瀏覽器訪問 Web UI：`http://localhost:3001/web/`
2. 點擊 **「注入 Demo 資料」** 按鈕（呼叫 `POST /demo/inject`）。
3. 系統會自動將模擬故障日誌寫入 Elasticsearch（涵蓋資料庫死鎖、DLQ 滿載、EJB 交易逾時等場景），並自動觸發分析與生成 PPTX 診斷簡報。

---

### 方法二：執行端到端故障模擬腳本（產生真實 APM + ELK 事故）

在終端機中執行專案內建的事故模擬腳本：

```bash
bash scripts/generate-observability-incident.sh 1
```

- **執行內容**：
  1. 向 Banking App 發送負餘額提款請求，觸發 PostgreSQL `chk_balance_non_negative` 約束違反與交易 Rollback。
  2. 產生 Java App HTTP 400 錯誤與 Instana APM 觀測數據。
  3. 自動同步呼叫 ELK + Instana 合併分析 API，產出完整的根因分析報告。

---

### 方法三：手動指定含有歷史日誌的時間區間

若先前已注入過 Demo 數據（預設基準時間區間為 `2026-01-15 08:00` 至 `2026-01-15 09:00`），可直接指定該時間視窗進行分析：

#### 透過 Web UI 執行
- **開始時間**：`2026-01-15 08:00`
- **結束時間**：`2026-01-15 09:00`
- 點擊 **「⚡ ELK + Instana 合併分析」**

#### 透過 CLI 執行
```bash
podman exec elk-analyser python cli.py run \
  --from "2026-01-15 08:00" \
  --to "2026-01-15 09:00" \
  --with-instana
```

#### 透過 cURL 呼叫 API
```bash
curl -sX POST http://localhost:8080/analyse \
  -H 'Content-Type: application/json' \
  -d '{
    "from_time": "2026-01-15 08:00",
    "to_time": "2026-01-15 09:00",
    "trigger": "webui_instana"
  }'
```

---

## 3. 下載與檢視報告

分析任務完成後，可透過以下方式下載生成的 PPTX 診斷簡報：

1. **Web UI 下載**：
   - 在 Web UI 的「最近分析任務」列表中點擊該筆任務。
   - 於彈出的「任務詳情」視窗中點擊 **「↓ 下載 PPTX」** 按鈕。
2. **命令列下載**（假設 Job ID 為 `1`）：
   ```bash
   curl -O http://localhost:8080/jobs/1/report
   ```

### 報告內容重點
- **Slide 1~3**：封面、目錄與執行摘要（KPI、異常統計、受影響模組）。
- **Slide 3.5**：Instana APM 概覽（Issues/Incidents、服務 Error Rate、Latency P95、基礎設施狀態）。
- **Slide 4+**：AI 根因分析事件鏈（包含故障時序、技術根因、短期應急處置與長期架構改善建議）。

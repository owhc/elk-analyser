# Kibana Webhook Trigger — 整合計畫
<!-- Updated: 2026-08-26 23:41:32 +0800 -->

## 目標與範圍

讓 Kibana Alerting 透過 Webhook action 自動觸發 ELK Analyser 進行 AI 根因分析。
`POST /analyse` 的 `from_time` / `to_time` 改為選填：
- Kibana 提供時間 → 使用 Kibana 給定的查詢視窗
- Kibana 不提供時間 → 從 Checkpoint 接續；Checkpoint 為空時 fallback 至 config 設定的回溯分鐘數（預設 60 分鐘）

**不在範圍內**：
- Kibana 側的 Alert rule / Connector 設定（由維運人員操作）
- 排程模式（supercronic）
- 其他 endpoint（`/analyse/async`、`/jobs`…）的行為變更

---

## Sub-Task 1：`config.yaml` 新增 checkpoint 區塊

**Intent**
新增 `checkpoint.default_lookback_minutes` 設定，作為 Checkpoint 為空時的預設回溯時間。

**Expected Outcomes**
- `config.yaml` 中有 `checkpoint` 區塊
- `default_lookback_minutes` 預設值為 `60`

**Todo List**
- [ ] 在 `bob-analyser/config/config.yaml` 的適當位置新增以下區塊：
  ```yaml
  checkpoint:
    default_lookback_minutes: 60
  ```

**Relevant Context**
- 檔案：`bob-analyser/config/config.yaml`

**Status**: [x] done

---

## Sub-Task 2：`api.py` — Request Schema 與時間解析邏輯

**Intent**
- `from_time` / `to_time` 改為 `Optional[str]`，允許 Kibana 不帶時間呼叫
- 新增 `trigger` 選填欄位，記錄觸發來源（如 `kibana_alert`）
- 當 `from_time` 為空時，從 Checkpoint 讀取 `last_analysed_to` 作為 `from_time`；`to_time` 預設為 `now`
- 當 Checkpoint 也為空時，`from_time` fallback 為 `now - default_lookback_minutes`
- `trigger` 值對應到 `run_analysis()` 的 `trigger_mode`

**Expected Outcomes**
- `POST /analyse` 接受無時間參數的 Kibana webhook payload（如 `{"trigger":"kibana_alert"}`）
- `POST /analyse` 仍向後相容：帶時間參數時行為不變
- `trigger_mode` 正確存入 `analysis_jobs.trigger_mode`

**Todo List**
- [ ] `AnalyseRequest` 的 `from_time` / `to_time` 改為 `Optional[str] = None`
- [ ] 新增 `trigger: Optional[str] = None` 欄位
- [ ] 建立輔助函式 `_resolve_query_window(request, config)` 處理時間解析邏輯：
  1. 若 `from_time` 有值 → parse 成 datetime（現有邏輯）
  2. 若 `from_time` 為 None → 呼叫 `read_checkpoint(db_path)`
     - 有值 → 作為 `from_time`
     - 無值 → `now - default_lookback_minutes`
  3. `to_time` 為 None → 使用 `datetime.now(timezone.utc)`
- [ ] `/analyse` endpoint 呼叫 `_resolve_query_window()` 取代原本直接 parse 的邏輯
- [ ] `trigger_mode` 改為從 `request.trigger` 取值，預設 `on_demand`；若 `trigger` 為 `kibana_alert` 則 `trigger_mode = "kibana_alert"`
- [ ] `/analyse/async` endpoint 同步套用以上改動

**Relevant Context**
- 檔案：`bob-analyser/api.py`
- `AnalyseRequest` schema：`api.py:64-70`
- 現有時間 parse 邏輯：`api.py` 的 `/analyse` handler
- `read_checkpoint(db_path)`：`bob-analyser/analyser/extractor.py`
- config 中 db_path：`config["database"]["path"]`

**Status**: [x] done

---

## Sub-Task 3：`analysis_pipeline.py` — 完成後寫入 Checkpoint

**Intent**
分析任務成功完成後，將 `query_to` 寫入 Checkpoint，確保下次 Kibana 不帶時間觸發時能從正確位置接續。

**Expected Outcomes**
- `status = completed` 後，`checkpoint` 表的 `last_analysed_to` 更新為本次的 `query_to`
- `status = failed` 時不更新 Checkpoint（保留上次成功位置，讓下次重試覆蓋相同視窗）

**Todo List**
- [ ] 在 `run_analysis()` 的成功分支（`status='completed'` 寫入 DB 之後），呼叫 `write_checkpoint(db_path, query_to)`
- [ ] import `write_checkpoint` from `analyser.extractor`

**Relevant Context**
- 檔案：`bob-analyser/analysis_pipeline.py`
- `write_checkpoint(db_path, analysed_to)`：`bob-analyser/analyser/extractor.py:53`
- 成功分支位置：`analysis_pipeline.py:134-147`

**Status**: [x] done

---

## Sub-Task 4：`api.py` — 加入 `lookback_minutes` 欄位與解析邏輯

**Intent**
Kibana Alert 無法在 Webhook payload 中計算時間區間（`{{date}} - 5m`），改由 elk-analyser 根據 `lookback_minutes` 自行計算 `from_time = to_time - lookback_minutes`，確保分析範圍精準對應 Alert 評估視窗。

**Expected Outcomes**
- `AnalyseRequest` 新增 `lookback_minutes: Optional[int] = None`
- `_resolve_query_window()` 四階段優先序：`from_time` → `lookback_minutes` → Checkpoint → fallback
- Kibana payload `{"to_time":"{{date}}","lookback_minutes":5,"trigger":"kibana_alert"}` 可正確解析

**Todo List**
- [x] `AnalyseRequest` 加入 `lookback_minutes: Optional[int] = None`
- [x] `_resolve_query_window()` 在 `from_time` 為 None 時，優先檢查 `lookback_minutes`；有值時 `qf = qt - timedelta(minutes=req.lookback_minutes)`
- [x] 無 `lookback_minutes` 時繼續走現有 Checkpoint → fallback 邏輯

**Relevant Context**
- 檔案：`bob-analyser/api.py`
- `_resolve_query_window()`：`api.py:111`

**Status**: [x] done

---

## Kibana 側設定參考（維運人員操作，非程式碼改動）

### Webhook Connector 設定
```
URL:    http://elk-analyser:8080/analyse
Method: POST
Headers:
  Content-Type: application/json
```

### Webhook Action Payload（正式方式）

```json
{
  "to_time":          "{{date}}",
  "lookback_minutes": 5,
  "trigger":          "kibana_alert"
}
```

`lookback_minutes` 必須與 Kibana Alert rule 的評估視窗長度一致（rule 評估「過去 5 分鐘」→ 填 `5`）。

### Web UI 手動觸發（維運人員自選時間區間）

```json
{
  "from_time": "2026-08-26 14:00",
  "to_time":   "2026-08-26 15:00"
}
```

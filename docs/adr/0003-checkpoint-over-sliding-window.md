# ADR 0003：查詢視窗解析策略 — from_time 四階段優先序
<!-- Updated: 2026-08-26 23:41:32 +0800 -->

## 狀態

已採用（Accepted）— 修訂：加入 `lookback_minutes` 作為第二優先

---

## 背景

`POST /analyse` 的 `from_time` / `to_time` 為選填參數，支援多種觸發來源（Kibana Alert 自動觸發、Web UI 手動、CLI）。不同觸發來源提供的資訊量不同，需要一套明確的優先序來決定最終查詢視窗。

---

## 決策

`_resolve_query_window()` 依以下四階段優先序決定 `from_time`：

| 優先順序 | 條件 | from_time 來源 | 適用場景 |
|---------|------|---------------|---------|
| **1** | `from_time` 有值 | 直接使用 | Web UI / CLI 手動指定時間區間 |
| **2** | `lookback_minutes` 有值 | `to_time - lookback_minutes` | Kibana Alert 自動觸發，對應評估視窗 |
| **3** | 兩者皆無，Checkpoint 有值 | Checkpoint 接續上次分析結束時間 | 向後相容，無時間資訊的觸發 |
| **4** | 三者皆無 | `now - config.checkpoint.default_lookback_minutes` | 首次執行或 Checkpoint 遺失時的 fallback |

`to_time` 未提供時，預設為 `now(UTC)`。

---

## 各觸發來源的 payload 對應

### Kibana Alert（自動觸發）— 使用優先序 2

```json
{
  "to_time":          "{{date}}",
  "lookback_minutes": 5,
  "trigger":          "kibana_alert"
}
```

`lookback_minutes` 由維運人員設定，**必須與 Kibana Alert rule 的評估視窗長度保持一致**（例如 rule 評估「過去 5 分鐘」，則 `lookback_minutes: 5`）。這確保 elk-analyser 分析的時間範圍精準對應 Alert 條件，不分析多餘日誌。

### Web UI 手動觸發 — 使用優先序 1

```json
{
  "from_time": "2026-08-26 14:00",
  "to_time":   "2026-08-26 15:00"
}
```

### 無時間參數的觸發（向後相容）— 使用優先序 3 或 4

```json
{
  "trigger": "kibana_alert"
}
```

---

## Checkpoint 寫入規則

- **只在 `completed` 後寫入**：`write_checkpoint(db_path, query_to)` 在 `analysis_pipeline.run_analysis()` 成功分支呼叫
- **失敗不更新**：確保下次觸發時重試相同視窗，不遺漏任何日誌
- Checkpoint 與 `lookback_minutes` 優先序不衝突：Kibana Alert 帶 `lookback_minutes` 時走優先序 2，Checkpoint 自然被跳過

---

## 為何不只用 Checkpoint（滑動視窗的問題）

- 滑動視窗（每次固定往回看 N 分鐘）會產生**重疊分析**，同一批異常被多次查詢
- Checkpoint 從上次結束接續，無重疊
- 但 Kibana Alert 已知道異常發生在哪個時間視窗，直接用 `lookback_minutes` 比 Checkpoint 更精準，避免分析 Alert 視窗以外的無關日誌

---

## config.yaml 相關設定

```yaml
checkpoint:
  default_lookback_minutes: 60  # 優先序 4 的 fallback 回溯分鐘數
```

---

## 影響

- 停機後重啟，若有 Checkpoint：下次 Kibana Alert 觸發仍走優先序 2（`lookback_minutes`），Checkpoint 不影響自動觸發的分析範圍
- 停機後重啟，若無 Checkpoint 且首次 Kibana Alert 觸發：走優先序 4（`default_lookback_minutes`），分析最近 60 分鐘
- Web UI 使用者自選時間區間：不受 Checkpoint 影響，永遠走優先序 1

<!-- Updated: 2026-08-26 23:48:51 +0800 -->
# 移除排程器：改由外部事件觸發分析

## 狀態

已採用（Accepted）— 排程模式永久移除

---

## 決策

移除 container 內建排程器（supercronic / crond），改由外部事件觸發分析：

| 觸發方式 | 端點 / 指令 |
|---------|------------|
| Kibana Alert（自動，主要） | `POST /analyse`（Webhook，帶 `lookback_minutes`）|
| Web UI 手動觸發 | `POST /analyse`（帶 `from_time` / `to_time`）|
| CLI 主動查詢 | `podman exec elk-analyser python cli.py run --from ... --to ...` |
| ES Watcher（備選） | `POST /analyse`（Webhook，帶 `from_time` / `to_time`）|
| Demo 資料注入 | `POST /demo/inject` |

container 啟動後只執行 FastAPI（`uvicorn`），不啟動任何背景排程程序。

---

## 初始設計（已棄用）

初始版本使用 `supercronic` 驅動排程，crontab 定義在 `config/crontab`，由 `podman-entrypoint.sh` 啟動。選擇 supercronic 是因為 Podman rootless 模式下系統 crond 需要寫入 `/etc/crontabs/`，在非 root container 中會失敗。

---

## 為何改為事件驅動

- **降低成本**：排程輪詢大多查詢結果為空（無異常），浪費 ES 查詢額度與 Bob API token
- **時效性更好**：Watcher/Alert 在異常發生時立即觸發，無輪詢延遲
- **容器更簡單**：不需要 supercronic 二進位，image 更小，啟動流程更清晰
- **`run_analysis()` 位於 `analysis_pipeline.py`**：由 `api.py` / `cli.py` 直接呼叫

---

## Consequences

- `config/crontab` 已刪除；`scheduler.py` 已重新命名為 `analysis_pipeline.py`，`run_scheduled()` 已移除
- `config.yaml` 的 `scheduler` 區塊已移除
- Kibana Alert throttle 設定（建議與評估視窗相同，如 `5m`）控制觸發頻率，elk-analyser 本身無去重邏輯
- 主要觸發方式為 Kibana Alerting；ES Watcher 為備選（詳見 ADR-0006）
- 若日後需要排程，應透過 Kubernetes CronJob 或外部排程服務呼叫 `POST /analyse`，而非在 container 內建排程器

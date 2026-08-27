<!-- Updated: 2026-08-26 22:27:49 +0800 -->
# Bob Bridge 透過本地 @filename 傳遞事件序列

`bob_bridge.py` 將預處理後的事件序列寫入本地路徑（`/workspace/logs/job-{id}.json`），再透過 `subprocess.run` 以 `bob run --mode log-analyst @/workspace/logs/job-{id}.json` 方式直接在容器內呼叫 Bob CLI。

**與初始設計的差異**：初始方案透過 SSH 橋接外部 `bob-sandbox` container，現改為 bob CLI 直接安裝在 `elk-analyser` container 內（`node:22-slim` 基底 image），消除 SSH 依賴與 container 間耦合。`@filename` 是 Bob CLI 的原生輸入能力，適合傳入大型 JSON 而不受 shell argument 長度限制。

## 執行流程

```
preprocessor.py → event_sequence dict
  ↓
bob_bridge.py 寫入 /workspace/logs/job-{id}.json
  ↓
subprocess.run(["bob", "run", "--mode", "log-analyst", "--format", "json",
                "--max-turns", "5", "@/workspace/logs/job-{id}.json", ...prompt])
  ↓
解析 stdout JSON → 取出 last_message → strip markdown fence → json.loads
  ↓
report_builder.py
```

## Consequences

- 分析完成後刪除暫存 JSON 檔（`file_path.unlink(missing_ok=True)`）
- Bob CLI 超時（`timeout` 秒，預設 120）或失敗時回傳含 `error` 欄位的字典，不拋出例外
- `BOB_API_KEY` / `BOBSHELL_API_KEY` 透過 `env=` 注入子程序環境

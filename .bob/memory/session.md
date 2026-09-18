# Session Memory — elk-analyser
<!-- Updated: 2026-09-17 14:46:00 +0800 -->

## Project
ELK Analyser 是一個結合 ELK Stack、Instana APM 與 Bob AI 的自動化日誌分析與根因診斷系統，輸出主管級 PPTX 報告。

## Session goal
修復 REPORT 68/69 — ELK + Instana 無法分析 PostgreSQL check constraint 違反（chk_balance_non_negative）的問題。

## Key decisions
- **log-analyst groups 必須含 read**：`groups: []` 讓 Bob 沒有任何工具，無法讀取 `@file` attachment，改為 `groups: [read]`。
- **_ANALYSIS_PROMPT 不能作為 cmd 引數**：1.46MB 的 prompt 超過 OS ARG_MAX（2MB），改為 `_ANALYSIS_PROMPT` 放在 custom_modes.yaml customInstructions 裡（已存在），`_run_bob` 只傳短 prompt。
- **extractor 排除 Liberty 假 ERROR**：Logstash 把 SystemErr→ERROR/SystemOut→INFO，但 liberty_message 的 SystemErr message 裡大量是正常業務 INFO log（3430/3685 筆），需用 must_not 排除不含錯誤關鍵字的 SystemErr 噪音。
- **Instana endpoint_metrics 改為 service.name 分組**（原 endpoint.name），使 bankdb/postgresql DB call errors 可見。

## Files changed this session
| File | Change |
|------|--------|
| `japp-demo/container-b/logstash/pipeline.conf` | PostgreSQL grok 後補 `add_field message = postgres_detail` |
| `bob-analyser/instana_collector.py` | `collect_endpoint_metrics` 改為 `service.name` 分組 |
| `bob-analyser/analyser/bob_bridge.py` | 移除 `--disable-tool-groups read`；`_ANALYSIS_PROMPT` 不再作為 cmd 引數傳入（改由 customInstructions 承擔） |
| `bob-analyser/analyser/extractor.py` | 新增 `must_not` 排除 was-liberty SystemOut 全部 + SystemErr 無錯誤關鍵字的噪音 |
| `bob-analyser/bob-custom-modes/custom_modes.yaml` | `log-analyst groups: [] → [read]`；補充業務規則違反模式；customInstructions 具體數值改為佔位符 |

## Rules & constraints discovered
- `log-analyst groups: []` 會禁止所有 tool，`@file` 附件無法被 Bob 讀取 → 必須 `groups: [read]`。
- bob_bridge `_ANALYSIS_PROMPT` 作為 cmd 引數 → ARG_MAX 錯誤 → 改放 customInstructions。
- Liberty `liberty_message` index 的 `log.level=ERROR` 有 3685 筆是假 ERROR（SystemErr/SystemOut），佔查詢結果 94%，會讓 Bob 看到雜訊而無法識別真實錯誤。
- custom_modes.yaml 修改需重建 elk-analyser image (`podman-compose build elk-analyser`)。

## Open threads / next steps
- 驗證 PPTX 報告中 Instana DB service error_rate 是否正確出現在 Slide 3.5。
- 確認長時間運行下 Logstash PostgreSQL pipeline 的 message 欄位補全是否穩定。

# Project Architecture Constraints
<!-- Updated: 2026-08-26 17:29:12 +0800 -->

- **Single-process model**: supercronic and FastAPI run in the same container (via `podman-entrypoint.sh`). Concurrent scheduled + on-demand runs are possible and not protected by a lock
- **`/analyse` is synchronous** despite `BackgroundTasks` in signature — it calls `run_analysis()` directly and blocks; `/analyse/async` is the true async endpoint
- **SQLite `_update_job()` uses `ORDER BY rowid DESC LIMIT 1`** — this is a race condition if concurrent jobs exist; architectural constraint, not a bug to silently fix
- **Bob output contract is fragile**: `last_message` must be pure JSON; the `log-analyst` custom mode system prompt enforces this; changing the mode YAML breaks the pipeline
- **ES scroll API**: uses 2-minute scroll TTL, 500 docs/batch, max 5000 hits — increasing max_hits risks OOM; scroll context must be cleared via `clear_scroll`
- **Checkpoint table** enforces `CHECK (id = 1)` — always exactly one row, updated via `ON CONFLICT(id) DO UPDATE`
- **Field mapping is fully configurable** via `config.yaml` `field_mapping` — never hardcode ES field names like `@timestamp` or `log.level` in new code
- **`bob_workspace` volume is shared** with other Podman projects (`ai-sdlc-flow`) — do not change the volume name without checking cross-project impact

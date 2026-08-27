#!/bin/sh
# Updated: 2026-08-26 18:02:32 +0800
# podman-entrypoint.sh
# Podman rootless 相容啟動腳本

set -e

# ── 初始化 SQLite schema ─────────────────────────────────────────────
# volume mount 後 /db 可能為空，重建確保 schema 存在
echo "▶  初始化 SQLite schema..."
python -c "
import sqlite3, pathlib
db_path = '/db/history.db'
schema = pathlib.Path('/app/db/schema.sql').read_text()
conn = sqlite3.connect(db_path)
conn.executescript(schema)
conn.commit()
conn.close()
print('   SQLite 初始化完成：' + db_path)
"

# ── 啟動 FastAPI（前台，接管 PID 1）────────────────────────────────
echo "▶  啟動 FastAPI REST API (port 8080)..."
exec uvicorn api:app \
    --host 0.0.0.0 \
    --port 8080 \
    --workers 1 \
    --log-level info \
    --access-log

#!/bin/sh
# Created: 2026-11-02 22:55:29 +0800
# 授予應用帳號 pg_monitor（Instana 取 DB 指標用）。
# 由 postgres 官方 entrypoint 在「資料目錄首次初始化」時自動執行（/docker-entrypoint-initdb.d/）。
# 已存在的 db_data volume 不會重跑，需手動：
#   podman exec banking-db psql -U bankuser -d bankdb -c "GRANT pg_monitor TO bankuser;"
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -c "GRANT pg_monitor TO \"$POSTGRES_USER\";"

#!/bin/sh
# Created: 2026-08-26 18:54:11 +0800
# web/entrypoint.sh
# 動態讀取 Podman DNS IP 並插入 nginx.conf，再啟動 Nginx
# 解決 elk-analyser container 重啟後 IP 改變導致 Nginx 502 的問題

set -e

# 從 /etc/resolv.conf 取第一個 nameserver IP
RESOLVER_IP=$(grep '^nameserver' /etc/resolv.conf | head -1 | awk '{print $2}')

if [ -z "$RESOLVER_IP" ]; then
    echo "WARNING: 無法取得 DNS resolver IP，使用 fallback 127.0.0.11"
    RESOLVER_IP="127.0.0.11"
fi

echo "▶  DNS resolver: $RESOLVER_IP"

# 將 nginx.conf 中的 RESOLVER_IP placeholder 替換為實際 IP
sed -i "s/RESOLVER_IP/${RESOLVER_IP}/g" /etc/nginx/conf.d/default.conf

echo "▶  啟動 Nginx..."
exec nginx -g 'daemon off;'

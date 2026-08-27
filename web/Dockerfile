# Updated: 2026-08-26 18:54:11 +0800
# web/Dockerfile — ELK Analyser Web UI
# Nginx Alpine，純靜態前端，透過 /api proxy 至 elk-analyser FastAPI

FROM nginx:1.27-alpine

# 複製靜態前端
COPY index.html /usr/share/nginx/html/index.html

# 複製 Nginx 設定（含 /api proxy）
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

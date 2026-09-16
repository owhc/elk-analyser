# Updated: 2026-08-27 18:08:17 +0800
# ELK Analyser Containerfile
# node:22-slim（Bob CLI 需要 Node.js）+ Python 3
#
# 架構說明：
#   - bob CLI 直接裝在本 container，無需 SSH 橋接外部 bob-sandbox
#   - 分析由 Web UI / 手動 CLI / Elasticsearch Alert → API 觸發
#   - 無排程（已移除 supercronic），FastAPI 處理所有觸發請求

FROM node:22-slim

# 安裝 Python 3、pip、curl、tzdata
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-dev \
    curl \
    tzdata \
    git \
    && rm -rf /var/lib/apt/lists/*

# python3 → python 別名
RUN ln -sf /usr/bin/python3 /usr/bin/python \
    && ln -sf /usr/bin/pip3 /usr/bin/pip

# 安裝 Bob CLI（F-08：固定版本號，確保 build 可重現、與 custom_modes.yaml 相容）
# bob CLI 需要 Node.js，本 image 已基於 node:22-slim
# 版本號：留空表示安裝最新版；或指定 X.Y.Z 格式（例如 2.1.0）
# Updated: 2026-09-15 20:10:11 +0800
ARG BOB_VERSION=
RUN curl -s https://s3.us-south.cloud-object-storage.appdomain.cloud/bobshell/install-bobshell.sh \
    | bash -s -- --pm npm ${BOB_VERSION:+--version ${BOB_VERSION}} \
    && which bob || (echo "ERROR: Bob CLI 安裝失敗" && exit 1) \
    && bob --version

# 建立 Bob 設定目錄
RUN mkdir -p /root/.bob/tmp

# 設定時區（台灣時間）
ENV TZ=Asia/Taipei

# 建立工作目錄
WORKDIR /app

# 先複製 requirements.txt 利用 Podman build cache
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# 複製專案程式碼
COPY analyser/ ./analyser/
COPY config/ ./config/
COPY db/ ./db/
COPY mock/ ./mock/
COPY bob-custom-modes/ ./bob-custom-modes/
COPY cli.py analysis_pipeline.py api.py gen_fake_data.py config_loader.py instana_collector.py ./

# 部署 Bob custom modes（log-analyst + report-builder）
# bob 2.x globalScopeRoot = /root/.bob/settings，custom modes 路徑為：
#   /root/.bob/settings/custom_modes.yaml
RUN mkdir -p /root/.bob/settings
COPY bob-custom-modes/custom_modes.yaml /root/.bob/settings/custom_modes.yaml

# 建立必要目錄（rootless：目錄屬於執行使用者，無需 root）
RUN mkdir -p /reports /db /workspace/logs /var/log

# 初始化 SQLite schema（build 時預建，volume mount 後仍需在 entrypoint 重建）
RUN python -c "import sqlite3; conn=sqlite3.connect('/db/history.db'); \
    conn.executescript(open('/app/db/schema.sql').read()); conn.commit(); conn.close()"

# 環境變數（由 podman-compose.yml 或 .env 注入）
ENV BOBSHELL_API_KEY=""
ENV BOB_HOME=/root/.bob
ENV NODE_ENV=production
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# 啟動腳本：啟動 FastAPI
COPY podman-entrypoint.sh /app/podman-entrypoint.sh
RUN chmod +x /app/podman-entrypoint.sh

EXPOSE 8080

CMD ["/app/podman-entrypoint.sh"]

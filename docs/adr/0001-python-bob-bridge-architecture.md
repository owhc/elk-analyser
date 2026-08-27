<!-- Updated: 2026-08-26 22:27:49 +0800 -->
# Python 作資料管道、Bob CLI 作 AI 推理引擎

Python 負責 ELK 提取、預處理、事件序列建構與 PPTX 生成；Bob CLI（`bob run --mode log-analyst`）只負責接收事件序列並輸出根因分析 JSON。分工明確讓兩層可以獨立測試與替換——若未來不使用 Bob，只需替換 Bob Bridge 模組即可。

Bob CLI 直接安裝在 `elk-analyser` container 內（基底 image：`node:22-slim`），`bob_bridge.py` 透過 `subprocess.run` 直接呼叫，無需 SSH 或外部 sandbox。

## Considered Options

- **Bob Shell 作主控**：Bob 調度 Python 腳本。被拒絕，因為 Bob 不擅長資料管道與批次處理，且輸出格式難以被下游 PPTX 生成穩定消費。
- **Python 純規則引擎**：不使用 Bob，只用正規表達式與規則匹配。被拒絕，因為無法做跨事件的因果鏈推理。
- **SSH 橋接外部 bob-sandbox**：初始設計，後改為 bob CLI 直接安裝在本 container，消除 SSH 依賴與 container 間耦合。

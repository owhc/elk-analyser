# ELK Analyser — Slides Plan
<!-- Updated: 2026-10-27 10:28:18 +0800 -->

## Overview

Build an interactive HTML slides deck for ELK Analyser.

**Format:** Single HTML file，手寫 CSS（不用 Reveal.js），每次只顯示一張，鍵盤 ←/→ 換頁
**Language:** 中英混用 — section titles in English, body content in Traditional Chinese
**Audience:** 主管 + 跨部門 — value-first, technical details minimized
**Output path:** `docs/elk-analyser-slides.html`
**Writing style:** dumbify + anti-ai-writing — plain words, concrete examples, no jargon walls
**Structure:** SCQA (Situation → Complication → Question → Answer)

## Design System — Style 5 Minimalist Monochrome

Source: `/Users/ochen/dev/bobexective/style-preview/nordic/style5-minimalist-monochrome.html`

### CSS Variables
```css
:root {
  --bg:    #f9f9f8;   /* off-white background */
  --ink:   #111110;   /* near-black text */
  --mid:   #555552;   /* secondary text */
  --faint: #c8c8c4;   /* ghost text */
  --rule:  #e2e2de;   /* hairline borders */
}
```

### Typography
- **Body font:** `DM Sans` (weights 200, 400, 700; italic 200) — Google Fonts CDN
- **Mono font:** `DM Mono` (weights 400, 500) — for labels, stamps, code, slide numbers
- Giant title: `font-size: clamp(5rem, 11vw, 13rem); font-weight: 200; letter-spacing: -0.04em`
- Stamp labels: `DM Mono, font-size: clamp(0.55rem, 0.72vw, 0.63rem); letter-spacing: 0.14em; text-transform: uppercase`

### Layout Structure (每張 slide)
```
┌─ .strip-top (border-bottom: 1px rule) ──────────────────────┐
│  left stamp (DM Mono uppercase)    right stamp              │
├─ .strip-main (grid: 2.8fr 1fr) ─────────────────────────────┤
│  .hero (content area)             │ .index-col (slide #)    │
│    border-right: 1px rule        │   large ghost number     │
│                                   │   vert-text label        │
├─ .strip-bottom (border-top: 1px rule) ──────────────────────┤
│  nav breadcrumb (DM Mono, active/dim)                       │
└──────────────────────────────────────────────────────────────┘
+ .accent-bar: 4px solid var(--ink) on the left edge
```

### Animations
- `fadeIn`: `opacity: 0 → 1`
- `riseIn`: `opacity: 0, translateY(24px) → opacity: 1, translateY(0)` via `cubic-bezier(0.16,1,0.3,1)`
- `prefers-reduced-motion`: all animations disabled, opacity set to 1

### Fragment Pattern (for step-by-step reveals)
Use JS to toggle `.hidden` class on elements with `data-fragment` attribute.
Initial state: `.fragment { opacity: 0; transform: translateY(16px); transition: all 0.4s ease; }`
Active state: `.fragment.visible { opacity: 1; transform: none; }`

### Code Blocks
```css
pre {
  background: var(--ink);
  color: var(--bg);
  font-family: 'DM Mono', monospace;
  font-size: clamp(0.7rem, 1vw, 0.85rem);
  padding: 1.5rem 2rem;
  border-radius: 0;  /* no border-radius — swiss grid feel */
}
```

### Navigation (JS)
- Arrow keys (← →) and click to navigate
- `currentSlide` counter, hide/show `.slide` elements
- Bottom nav `.nav-item` update active state per slide

### GitHub URL
`https://github.com/ochen-ibm/elk-analyser`

---

## Slide Outline

### Slide 1 — Cover
- **Title:** ELK Analyser — AI-Driven Log Diagnostics
- **Sub:** 自動化日誌分析 × 根因推理 × 即時診斷報告
- **Visual:** 簡潔標題頁，暗色背景

---

### Slide 2 — Situation（現況）
**標題：** Every Incident Starts the Same Way

**內容（具體場景）：**
- 凌晨 2 點，Kibana 警報觸發
- 工程師打開 Kibana，面對幾百筆日誌
- 手動找關鍵字、關聯 TraceID、猜根因
- 30~60 分鐘後才能確認是哪個服務出問題

**視覺：** 時間軸 or 場景插圖

---

### Slide 3 — Complication（問題）
**標題：** 3 Things That Slow You Down

1. **日誌量大** → 人工篩選沒效率
2. **關聯難** → TraceID 跨服務，手動追蹤容易漏
3. **報告慢** → 每次都要重新寫事故報告，格式不一致

**視覺：** 三個痛點卡片

---

### Slide 4 — Question（關鍵問題）
**標題：** Can AI Do This in One Click?

**內容：**
- 自動從 ELK 拉日誌 ✓
- 自動推理根因 ✓
- 自動生成標準診斷報告 ✓

**視覺：** 一行大字 + 三個 checkmark 動畫

---

### Slide 5 — Answer（解答 / 系統介紹）
**標題：** ELK Analyser — 四個步驟，一次閉環

**E2E 流程圖（水平）：**
```
[觸發] → [提取日誌] → [AI 推理] → [PPTX 報告]
Kibana Alert  Elasticsearch  Bob Claude  自動下載
```

**說明：** 從告警觸發到診斷報告，全程自動，無需人工介入

---

### Slide 6 — How It Works（Pipeline Detail）
**標題：** Under the Hood — 4-Stage Pipeline

| 階段 | 元件 | 做什麼 |
|------|------|--------|
| Stage 1 | Extractor | 從 ES 撈日誌，管理斷點 |
| Stage 2 | Preprocessor | 按 TraceID 分組，建事件鏈 |
| Stage 3 | Bob Bridge | 呼叫 Claude AI，推理根因 |
| Stage 4 | Report Builder | 生成 PPTX 診斷報告 |

**簡化說明：** 每個階段只做一件事，失敗不會影響歷史資料

---

### Slide 7 — Trigger Modes（觸發方式）
**標題：** 4 Ways to Start an Analysis

1. **Kibana Alert（自動）** — 告警一觸發，自動開始分析
2. **Grafana Webhook** — 整合 Grafana 告警
3. **REST API** — `POST /analyse`，可掛進任何系統
4. **CLI** — `python cli.py run`，手動指定時間範圍

**視覺：** 四個觸發方式圖示

---

### Slide 8 — Instana APM Integration（進階功能）
**標題：** Optional: Add APM Context with Instana

**內容：**
- 純 ELK 模式：分析日誌中的錯誤與事件鏈
- ELK + Instana 模式：額外加入
  - Service 層的 P95 延遲、錯誤率
  - 跨服務 Trace 分析
  - JVM heap / Artemis 佇列指標
  - PPTX 自動新增 APM 頁（Slide 3.5）

**視覺：** 兩種模式對比

---

### Slide 9 — Output（產出）
**標題：** What You Get — A Ready-to-Share PPTX

**PPTX 頁面結構：**
1. 封面（事故時間、嚴重度）
2. 事件摘要（總錯誤數、受影響服務）
3. 根因分析（每條事件鏈）
4. *[Instana APM 概覽 — 可選]*
5. 建議處理（短期 + 長期）

**視覺：** PPTX 頁面 mockup 截圖 or 示意圖

---

### Slide 10 — Architecture（系統架構）
**標題：** Architecture at a Glance

**元件清單（簡化）：**
- **Frontend:** Nginx（埠 3001）→ Grafana + Web UI
- **Backend:** FastAPI（埠 8080）
- **AI Engine:** Bob CLI（Claude Sonnet）
- **Storage:** Elasticsearch（日誌）+ SQLite（任務歷史）
- **Optional:** Instana Agent（APM）

**容器化：** Podman Compose，全部用同一個 `podman-compose up`

---

### Slide 11 — Design Decisions（設計決策）
**標題：** Key Design Decisions

1. **同步 vs 非同步** — 提供兩個端點：`/analyse`（等結果）+ `/analyse/async`（拿 job_id）
2. **斷點設計** — 每次分析記錄結束時間，下次從這裡開始，不漏不重
3. **無異常設計** — 所有階段返回 dict，失敗只影響當次任務
4. **欄位映射可配置** — ES 欄位名稱全部走 `config.yaml`，換環境不改程式碼

---

### Slide 12 — Quick Start（快速上手）
**標題：** Up and Running in 3 Commands

```bash
# 1. 啟動所有服務
podman-compose up -d

# 2. 觸發一次分析（指定時間範圍）
python cli.py run --from "2026-01-15 08:00" --to "2026-01-15 12:00"

# 3. 下載報告
curl http://localhost:8080/jobs/1/report -O report.pptx
```

---

### Slide 13 — Closing（結尾）
**標題：** From Alert to Report — Automatically

**Key takeaways（三行）：**
1. 一次告警 → 一份完整診斷報告，無需人工介入
2. 支援純 ELK 與 ELK + Instana 兩種模式
3. 配置驅動，換環境不改程式碼

**CTA：** `github.com/your-org/elk-analyser` （或替換成實際連結）

---

## Implementation Notes

- **Slide library:** Reveal.js（CDN 引入，無需 build step）
- **Theme:** 暗色背景（#1a1a2e 或類 Morandi blue），程式碼 highlight 使用 highlight.js
- **Animations:** Slide-in transitions，Pipeline 圖用 fragment 逐步出現
- **Diagrams:** 架構圖用 inline SVG 或 CSS flexbox 排版，不依賴外部圖片
- **Fonts:** IBM Plex Sans（與系統主題一致）or Inter
- **File:** Single self-contained HTML（所有 CSS/JS inline 或 CDN），存至 `docs/elk-analyser-slides.html`

---

## Sub-Tasks

### Sub-task 1 — HTML Slides Scaffold
**Intent:** 建立 Reveal.js 基礎架構，設定主題、字型、顏色
**Expected Outcomes:** 可在瀏覽器開啟，空白 slides 結構正確，主題樣式套用完成
**Todo List:**
- [ ] 建立 `docs/elk-analyser-slides.html`
- [ ] 引入 Reveal.js（CDN），設定暗色主題
- [ ] 定義 CSS 變數（primary color, font, spacing）
- [ ] 建立 13 個空白 section，標題佔位符

**Status:** `[ ] pending`

---

### Sub-task 2 — SCQA Content (Slides 1–5)
**Intent:** 填入 SCQA 故事主線，Situation / Complication / Question / Answer
**Expected Outcomes:** Slides 1~5 內容完整，語言簡潔（dumbify），具體場景描述
**Todo List:**
- [ ] Slide 1 Cover — 標題、subtitle
- [ ] Slide 2 Situation — 凌晨 2 點場景，時間軸視覺
- [ ] Slide 3 Complication — 三大痛點卡片 (CSS grid)
- [ ] Slide 4 Question — 大字 + checkmark 動畫
- [ ] Slide 5 Answer — E2E 流程圖（CSS flexbox arrow chain）

**Status:** `[ ] pending`

---

### Sub-task 3 — Technical Slides (Slides 6–10)
**Intent:** 技術說明層，Pipeline / Trigger / Instana / Output / Architecture
**Expected Outcomes:** 技術細節清楚但不過度，圖表用 CSS 排版
**Todo List:**
- [ ] Slide 6 Pipeline — 四階段表格 + fragment 動畫
- [ ] Slide 7 Trigger Modes — 四種觸發方式卡片
- [ ] Slide 8 Instana — 兩種模式對比 (before/after layout)
- [ ] Slide 9 Output — PPTX 結構示意（mockup 純 CSS）
- [ ] Slide 10 Architecture — 架構圖（inline SVG/CSS box diagram）

**Status:** `[ ] pending`

---

### Sub-task 4 — Decision + Closing Slides (Slides 11–13)
**Intent:** 設計決策說明 + 快速上手 + 結尾
**Expected Outcomes:** Slides 11~13 完成，code block 有 syntax highlight
**Todo List:**
- [ ] Slide 11 Design Decisions — 四個決策卡片
- [ ] Slide 12 Quick Start — code block with highlight
- [ ] Slide 13 Closing — key takeaways + CTA

**Status:** `[ ] pending`

---

### Sub-task 5 — Polish & QA
**Intent:** 確保整體品質：動畫流暢、字型一致、mobile-friendly、無破版
**Expected Outcomes:** 在 Chrome 瀏覽器全屏播放正常，所有 fragment 動畫正確觸發
**Todo List:**
- [ ] 檢查所有 slides transition 動畫
- [ ] 確認 code blocks 在暗色背景下可讀
- [ ] 調整間距 / 字型大小（標題 vs body）
- [ ] 加入 keyboard shortcut hint（? 鍵）
- [ ] 確認 slide 計數器顯示正確

**Status:** `[ ] pending`

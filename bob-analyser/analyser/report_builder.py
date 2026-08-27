# Updated: 2026-08-27 18:08:17 +0800
"""
analyser/report_builder.py
──────────────────────────
使用 python-pptx 產生 PPTX 診斷報告。
設計風格參照華銀資作部IBM月會報告（深藍底 #1A2E4A + 藍色左條 #2B7DE9）。

投影片結構：
  Slide 1  封面             標題、分析視窗、生成時間、任務 ID
  Slide 2  章節分隔         執行摘要
  Slide 3  執行摘要         KPI 指標、受影響模組
  Slide 4  章節分隔         系統異常與根因分析
  Slide 5+  每條 event chain 一頁根因詳情（最多 5 條）
  最後頁   改善建議         短期 + 長期對策
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 色彩常數（華銀設計風格）─────────────────────────────────────────
_BG       = (0x1A, 0x2E, 0x4A)   # 深海軍藍背景
_ACCENT   = (0x2B, 0x7D, 0xE9)   # IBM 藍（左側色條、強調色）
_WHITE    = (0xFF, 0xFF, 0xFF)
_LT_BLUE  = (0xA0, 0xC4, 0xFF)   # 淡藍（副標題、標籤）
_SILVER   = (0xCB, 0xD5, 0xE0)   # 次要文字
_DIM      = (0xA0, 0xAE, 0xC0)   # 較暗文字
_MUTED    = (0x71, 0x80, 0x96)   # 底部小字
_CRITICAL = (0xFC, 0x81, 0x81)   # 玫瑰紅
_HIGH     = (0xFB, 0xBF, 0x24)   # 琥珀
_MEDIUM   = (0x68, 0xD3, 0x91)   # 翠綠
_CARD_BG  = (0x22, 0x3A, 0x5C)   # 卡片背景


def build_report(
    analysis: dict,
    job_id: str,
    query_from: datetime,
    query_to: datetime,
    config=None,
) -> str:
    """使用 python-pptx 直接產生 PPTX 診斷報告（華銀設計風格）。"""
    if config is None:
        from config_loader import AppConfig
        config = AppConfig.load()

    if hasattr(config, "report_output_dir"):
        output_dir = Path(config.report_output_dir)
        prefix     = config.report_filename_prefix
    else:
        output_dir = Path(config["report"]["output_dir"])
        prefix     = config["report"].get("filename_prefix", "elk_analysis")

    output_dir.mkdir(parents=True, exist_ok=True)
    now_str     = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    filename    = f"{prefix}_{now_str}_{job_id[:8]}.pptx"
    output_path = output_dir / filename

    ai_error_msg = None
    if "error" in analysis:
        ai_error_msg = analysis["error"]
        logger.warning("分析結果含錯誤，將使用空白摘要生成報告（F-03）：%s", ai_error_msg)
        analysis.setdefault("summary", {"total_errors":0,"critical_count":0,"warning_count":0,"affected_modules":[]})
        analysis.setdefault("event_chains", [])

    return _build_report(analysis, output_path, job_id, query_from, query_to, ai_error_msg=ai_error_msg)


def _rgb(t):
    from pptx.dml.color import RGBColor
    return RGBColor(*t)


def _build_report(
    analysis: dict,
    output_path: Path,
    job_id: str,
    query_from: datetime,
    query_to: datetime,
    ai_error_msg: str = None,
) -> str:
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt, Emu
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN

        W = Inches(13.33)
        H = Inches(7.5)
        summary = analysis.get("summary", {})
        chains  = analysis.get("event_chains", [])
        now_dt  = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
        from_dt = query_from.strftime("%Y-%m-%d %H:%M")
        to_dt   = query_to.strftime("%Y-%m-%d %H:%M")

        prs = Presentation()
        prs.slide_width  = W
        prs.slide_height = H
        blank = prs.slide_layouts[6]  # blank layout

        def add_slide():
            s = prs.slides.add_slide(blank)
            bg = s.background
            bg.fill.solid()
            bg.fill.fore_color.rgb = _rgb(_BG)
            return s

        def add_rect(slide, x, y, w, h, fill, text="", size=18, bold=False,
                     color=_WHITE, align=PP_ALIGN.LEFT, valign=None):
            from pptx.util import Pt
            from pptx.enum.text import PP_ALIGN
            from pptx.oxml.ns import qn
            from lxml import etree
            shp = slide.shapes.add_shape(1, x, y, w, h)  # MSO_SHAPE_TYPE.RECTANGLE=1
            shp.line.fill.background()
            if fill == "none":
                shp.fill.background()
            else:
                shp.fill.solid()
                shp.fill.fore_color.rgb = _rgb(fill)
            if text:
                tf = shp.text_frame
                tf.word_wrap = True
                p = tf.paragraphs[0]
                p.alignment = align
                run = p.add_run()
                run.text = text
                run.font.size = Pt(size)
                run.font.bold = bold
                run.font.color.rgb = _rgb(color)
                run.font.name = "Calibri"
            return shp

        def add_text(slide, x, y, w, h, text, size=14, bold=False,
                     color=_WHITE, align=PP_ALIGN.LEFT, wrap=True):
            from pptx.util import Pt
            from pptx.enum.text import PP_ALIGN
            txb = slide.shapes.add_textbox(x, y, w, h)
            txb.text_frame.word_wrap = wrap
            p = txb.text_frame.paragraphs[0]
            p.alignment = align
            run = p.add_run()
            run.text = text
            run.font.size = Pt(size)
            run.font.bold = bold
            run.font.color.rgb = _rgb(color)
            run.font.name = "Calibri"
            return txb

        def add_left_bar(slide):
            """左側 IBM 藍色條（模擬華銀風格）"""
            add_rect(slide, Inches(0), Inches(0), Inches(0.12), H, _ACCENT)

        def sev_color(sev):
            s = (sev or "").upper()
            if "CRITICAL" in s: return _CRITICAL
            if "HIGH" in s:     return _HIGH
            return _MEDIUM

        # ── Slide 1：封面 ───────────────────────────────────────────
        s1 = add_slide()
        add_left_bar(s1)
        # 頂部淡藍線
        add_rect(s1, Inches(0.12), Inches(0), W - Inches(0.12), Inches(0.04), _ACCENT)
        # 副標籤
        add_text(s1, Inches(0.4), Inches(1.5), Inches(12), Inches(0.5),
                 "系統日誌分析診斷報告", size=13, color=_LT_BLUE)
        # 主標題
        add_text(s1, Inches(0.4), Inches(2.1), Inches(12), Inches(1.2),
                 "系統異常根因分析報告", size=40, bold=True, color=_WHITE)
        # 分隔線
        add_rect(s1, Inches(0.4), Inches(3.4), Inches(7), Inches(0.025), _ACCENT)
        # 報告資訊
        add_text(s1, Inches(0.4), Inches(3.6), Inches(12), Inches(0.4),
                 f"分析執行者：Bob Shell · log-analyst mode", size=13, color=_SILVER)
        add_text(s1, Inches(0.4), Inches(4.0), Inches(12), Inches(0.4),
                 f"分析時間範圍：{from_dt}  →  {to_dt}", size=13, color=_SILVER)
        add_text(s1, Inches(0.4), Inches(4.4), Inches(12), Inches(0.4),
                 f"報告生成時間：{now_dt}", size=13, color=_SILVER)
        add_text(s1, Inches(0.4), Inches(4.8), Inches(12), Inches(0.4),
                 f"任務 ID：{job_id}", size=11, color=_DIM)
        # AI 分析失敗警示（F-03）
        if ai_error_msg:
            add_rect(s1, Inches(0.4), Inches(5.4), Inches(12.5), Inches(0.55),
                     _CRITICAL,
                     text=f"⚠ AI 分析失敗，本報告為空白摘要：{ai_error_msg[:120]}",
                     size=11, bold=True, color=_WHITE)
        # 底部機密標示
        add_text(s1, Inches(0.4), Inches(7.1), Inches(12), Inches(0.3),
                 "機密 · 僅供內部使用", size=10, color=_MUTED)

        # ── Slide 2：章節分隔 — 執行摘要 ───────────────────────────
        s2 = add_slide()
        add_left_bar(s2)
        add_text(s2, Inches(1.0), Inches(2.8), Inches(11), Inches(1.2),
                 "一、執行摘要", size=36, bold=True, color=_WHITE)
        add_text(s2, Inches(1.0), Inches(4.0), Inches(11), Inches(0.6),
                 "Executive Summary", size=18, color=_LT_BLUE)

        # ── Slide 3：執行摘要 KPI ───────────────────────────────────
        s3 = add_slide()
        add_left_bar(s3)
        add_text(s3, Inches(0.25), Inches(0.15), Inches(10), Inches(0.55),
                 "執行摘要  Executive Summary", size=20, bold=True, color=_WHITE)
        add_rect(s3, Inches(0.25), Inches(0.72), Inches(12.8), Inches(0.03), _ACCENT)

        modules = summary.get("affected_modules", [])
        kpis = [
            ("異常事件總數", str(summary.get("total_errors", 0)),  _LT_BLUE),
            ("CRITICAL",     str(summary.get("critical_count", 0)), _CRITICAL),
            ("WARNING",      str(summary.get("warning_count", 0)),  _HIGH),
            ("受影響模組",   str(len(modules)),                      _MEDIUM),
        ]
        card_w = Inches(2.9)
        card_h = Inches(1.8)
        for i, (label, val, col) in enumerate(kpis):
            cx = Inches(0.35 + i * 3.25)
            cy = Inches(1.0)
            add_rect(s3, cx, cy, card_w, card_h, _CARD_BG)
            add_rect(s3, cx, cy, card_w, Inches(0.06), col)
            add_text(s3, cx + Inches(0.15), cy + Inches(0.15), card_w - Inches(0.3), Inches(0.4),
                     label, size=12, color=_DIM)
            add_text(s3, cx + Inches(0.1), cy + Inches(0.6), card_w - Inches(0.2), Inches(0.9),
                     val, size=36, bold=True, color=col)

        # 受影響模組列表
        add_text(s3, Inches(0.35), Inches(3.0), Inches(12.6), Inches(0.35),
                 "受影響模組", size=13, bold=True, color=_LT_BLUE)
        mod_text = "、".join(modules) if modules else "（無）"
        add_text(s3, Inches(0.35), Inches(3.35), Inches(12.6), Inches(0.5),
                 mod_text, size=14, color=_SILVER)

        # 因果鏈摘要
        add_text(s3, Inches(0.35), Inches(4.0), Inches(12.6), Inches(0.35),
                 f"識別因果鏈：{len(chains)} 條", size=13, bold=True, color=_LT_BLUE)
        for i, c in enumerate(chains[:5]):
            sc = sev_color(c.get("severity"))
            add_rect(s3, Inches(0.35), Inches(4.45 + i * 0.5), Inches(0.06), Inches(0.35), sc)
            add_text(s3, Inches(0.5), Inches(4.45 + i * 0.5), Inches(12.4), Inches(0.4),
                     f"[{c.get('severity','?')}] {c.get('title','未命名')}", size=12, color=_SILVER)

        add_text(s3, Inches(0.35), Inches(7.1), Inches(12.6), Inches(0.3),
                 f"分析範圍：{from_dt} → {to_dt}  ·  任務 ID：{job_id[:8]}", size=9, color=_MUTED)

        # ── Slide 4：章節分隔 — 根因分析 ───────────────────────────
        s4 = add_slide()
        add_left_bar(s4)
        add_text(s4, Inches(1.0), Inches(2.8), Inches(11), Inches(1.2),
                 "二、系統異常根本原因分析", size=32, bold=True, color=_WHITE)
        add_text(s4, Inches(1.0), Inches(4.0), Inches(11), Inches(0.6),
                 "Root Cause Analysis", size=18, color=_LT_BLUE)

        # ── Slide 5+：每條 event chain 一頁 ────────────────────────
        for idx, c in enumerate(chains[:5]):
            sc   = add_slide()
            sev  = c.get("severity", "MEDIUM").upper()
            col  = sev_color(sev)
            add_left_bar(sc)
            # 嚴重等級色條
            add_rect(sc, Inches(0.12), Inches(0), Inches(0.06), H, col)
            # 頁標籤
            add_text(sc, Inches(0.3), Inches(0.15), Inches(12), Inches(0.4),
                     f"根因分析  {idx+1}/{min(len(chains),5)}", size=11, color=_DIM)
            add_rect(sc, Inches(0.25), Inches(0.55), Inches(12.8), Inches(0.025), _ACCENT)
            # 嚴重等級徽章
            add_rect(sc, Inches(0.3), Inches(0.72), Inches(1.3), Inches(0.38), col,
                     text=sev, size=12, bold=True, color=_BG, align=PP_ALIGN.CENTER)
            # 事件標題
            add_text(sc, Inches(1.75), Inches(0.72), Inches(11), Inches(0.45),
                     c.get("title", "未命名"), size=18, bold=True, color=_WHITE)
            # 根因
            add_text(sc, Inches(0.3), Inches(1.35), Inches(2.5), Inches(0.35),
                     "根本原因", size=12, bold=True, color=_LT_BLUE)
            add_rect(sc, Inches(0.3), Inches(1.7), Inches(12.7), Inches(1.15), _CARD_BG,
                     text=c.get("root_cause", "—"), size=13, color=_SILVER)
            # 時序
            add_text(sc, Inches(0.3), Inches(3.0), Inches(2.5), Inches(0.35),
                     "事件時序", size=12, bold=True, color=_LT_BLUE)
            timeline = c.get("timeline", [])
            tl_text = "  →  ".join(ev.get("event", "")[:50] for ev in timeline[:4]) or "（無時序資料）"
            add_text(sc, Inches(0.3), Inches(3.35), Inches(12.7), Inches(0.6),
                     tl_text, size=12, color=_SILVER)
            # 短期處置
            add_text(sc, Inches(0.3), Inches(4.1), Inches(6.0), Inches(0.35),
                     "短期應急處置", size=12, bold=True, color=_CRITICAL)
            add_rect(sc, Inches(0.3), Inches(4.45), Inches(6.0), Inches(1.5), _CARD_BG,
                     text=c.get("short_term_fix", "—"), size=12, color=_SILVER)
            # 長期改善
            add_text(sc, Inches(6.6), Inches(4.1), Inches(6.4), Inches(0.35),
                     "長期架構改善", size=12, bold=True, color=_LT_BLUE)
            add_rect(sc, Inches(6.6), Inches(4.45), Inches(6.4), Inches(1.5), _CARD_BG,
                     text=c.get("long_term_fix", "—"), size=12, color=_SILVER)
            add_text(sc, Inches(0.35), Inches(7.1), Inches(12.6), Inches(0.3),
                     f"任務 ID：{job_id[:8]}  ·  {now_dt}", size=9, color=_MUTED)

        # ── 最後頁：改善建議總表 ────────────────────────────────────
        sf = add_slide()
        add_left_bar(sf)
        add_text(sf, Inches(0.25), Inches(0.15), Inches(10), Inches(0.55),
                 "改善建議  Action Plan", size=20, bold=True, color=_WHITE)
        add_rect(sf, Inches(0.25), Inches(0.72), Inches(12.8), Inches(0.03), _ACCENT)

        y = 1.0
        for i, c in enumerate(chains[:5]):
            if y > 6.5: break
            sc2 = sev_color(c.get("severity"))
            add_rect(sf, Inches(0.3), Inches(y), Inches(0.06), Inches(0.35), sc2)
            add_text(sf, Inches(0.45), Inches(y), Inches(12.3), Inches(0.38),
                     c.get("title", f"問題 {i+1}")[:60], size=13, bold=True, color=_WHITE)
            y += 0.42
            stf = c.get("short_term_fix", "")[:90]
            ltf = c.get("long_term_fix", "")[:90]
            if stf:
                add_text(sf, Inches(0.55), Inches(y), Inches(5.8), Inches(0.35),
                         f"▸ 短期：{stf}", size=11, color=_CRITICAL)
                y += 0.38
            if ltf:
                add_text(sf, Inches(0.55), Inches(y), Inches(5.8), Inches(0.35),
                         f"▸ 長期：{ltf}", size=11, color=_LT_BLUE)
                y += 0.38
            y += 0.1

        add_text(sf, Inches(0.35), Inches(7.1), Inches(12.6), Inches(0.3),
                 f"分析範圍：{from_dt} → {to_dt}  ·  任務 ID：{job_id[:8]}  ·  機密 · 僅供內部使用",
                 size=9, color=_MUTED)

        prs.save(str(output_path))
        logger.info("PPTX 已儲存：%s", output_path)
        return str(output_path)

    except Exception as exc:
        logger.error("報告生成失敗：%s", exc)
        raise

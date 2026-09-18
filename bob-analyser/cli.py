# Updated: 2026-09-17 12:06:42 +0800
"""
cli.py
──────
elk-analyser CLI 入口（click 子指令風格）。

指令：
  run      執行一次分析（主動查詢，需要 --from / --to）
  history  列出分析任務歷史紀錄
  show     顯示特定任務的詳細結果
"""

import json
import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import click

from config_loader import AppConfig
from db.job_repository import JobRepository

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_DEFAULT_CONFIG = "/app/config/config.yaml"


def _repo(config_path: str = _DEFAULT_CONFIG) -> JobRepository:
    cfg = AppConfig.load(config_path)
    return JobRepository(cfg.db_path)


@click.group()
def cli():
    """ELK Analyser — 自動化日誌分析與報告生成工具"""
    pass


@cli.command()
@click.option("--from", "from_time", required=True,
              help="查詢開始時間，格式：'YYYY-MM-DD HH:MM'")
@click.option("--to", "to_time", required=True,
              help="查詢結束時間，格式：'YYYY-MM-DD HH:MM'")
@click.option("--with-instana", "with_instana", is_flag=True, default=False,
              help="同時收集 Instana APM 歷史資料（需 config.yaml instana.enabled=true）")
@click.option("--config", default=_DEFAULT_CONFIG, show_default=True,
              help="設定檔路徑")
def run(from_time, to_time, with_instana, config):
    """執行一次分析任務（需指定 --from 與 --to）。"""
    from analysis_pipeline import run_analysis

    _tz_name = os.environ.get("TZ") or "UTC"
    try:
        _local_tz = ZoneInfo(_tz_name)
    except Exception:
        _local_tz = datetime.now().astimezone().tzinfo
    try:
        fmt = "%Y-%m-%d %H:%M"
        qf = datetime.strptime(from_time, fmt).replace(tzinfo=_local_tz)
        qt = datetime.strptime(to_time, fmt).replace(tzinfo=_local_tz)
    except ValueError:
        click.echo("錯誤：時間格式應為 'YYYY-MM-DD HH:MM'", err=True)
        sys.exit(1)

    instana_ctx = None
    if with_instana:
        click.echo("⚡  收集 Instana APM 資料...")
        try:
            import instana_collector
            instana_ctx = instana_collector.collect(qf, qt, config)
            avail = instana_ctx.get("available", False)
            if avail:
                click.echo(f"   ✅ Instana 收集成功（events={len(instana_ctx.get('events', []))}）")
            else:
                click.echo(f"   ⚠  Instana 收集失敗（{instana_ctx.get('error', '')}），降級為純 ELK", err=True)
        except Exception as exc:
            click.echo(f"   ⚠  Instana collector 錯誤：{exc}，降級為純 ELK", err=True)
            instana_ctx = {"available": False, "error": str(exc)}

    click.echo(f"▶  主動查詢：{from_time} → {to_time}")
    trigger = "cli_instana" if with_instana else "on_demand"
    result = run_analysis(qf, qt, trigger, config, instana_context=instana_ctx)

    if result["status"] == "completed":
        click.echo(f"✅  分析完成")
        click.echo(f"   任務 ID    : {result['job_id']}")
        click.echo(f"   報告路徑  : {result['report_path']}")
        s = result.get("summary", {})
        click.echo(f"   異常總數  : {s.get('total_errors', 0)}")
        click.echo(f"   CRITICAL  : {s.get('critical_count', 0)}")
    else:
        click.echo(f"❌  分析失敗：{result.get('error')}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--limit", default=10, show_default=True, help="顯示最近 N 筆紀錄")
@click.option("--json-output", "json_output", is_flag=True, default=False,
              help="以 JSON 格式輸出（供機器解析）")
@click.option("--config", default=_DEFAULT_CONFIG, show_default=True,
              help="設定檔路徑")
def history(limit, json_output, config):
    """列出分析任務歷史紀錄。"""
    rows = _repo(config).list_jobs(limit)

    if not rows:
        click.echo("尚無分析紀錄。")
        return

    if json_output:
        click.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    # 人工可讀表格
    click.echo(f"\n{'ID':>4}  {'模式':10}  {'狀態':10}  {'開始時間':20}  {'報告路徑'}")
    click.echo("─" * 80)
    for row in rows:
        status = row["status"]
        status_sym = "✅" if status == "completed" else ("❌" if status == "failed" else "⏳")
        report_short = (row.get("report_path") or "—")[-40:]
        started = (row.get("started_at") or "")[:19]
        click.echo(f"{row['id']:>4}  {row['trigger_mode']:10}  {status_sym} {status:8}  {started:20}  {report_short}")


@cli.command()
@click.argument("job_id", type=int)
@click.option("--config", default=_DEFAULT_CONFIG, show_default=True,
              help="設定檔路徑")
def show(job_id, config):
    """顯示特定任務（依 ID）的詳細分析結果。"""
    row = _repo(config).get_job(job_id)

    if not row:
        click.echo(f"找不到任務 ID：{job_id}", err=True)
        sys.exit(1)

    click.echo(f"\n{'─'*50}")
    click.echo(f"任務 ID    : {row['id']}")
    click.echo(f"觸發模式  : {row['trigger_mode']}")
    click.echo(f"查詢視窗  : {row['query_from']} → {row['query_to']}")
    click.echo(f"狀態      : {row['status']}")
    click.echo(f"開始時間  : {row['started_at']}")
    click.echo(f"完成時間  : {row.get('completed_at') or '—'}")
    click.echo(f"報告路徑  : {row.get('report_path') or '—'}")

    if row.get("error_message"):
        click.echo(f"錯誤訊息  : {row['error_message']}")

    if row.get("summary_json"):
        try:
            summary = json.loads(row["summary_json"])
            click.echo(f"\n分析摘要：")
            click.echo(f"  異常總數   : {summary.get('total_errors', 0)}")
            click.echo(f"  CRITICAL   : {summary.get('critical_count', 0)}")
            click.echo(f"  WARNING    : {summary.get('warning_count', 0)}")
            modules = summary.get("affected_modules", [])
            click.echo(f"  受影響模組 : {', '.join(modules) or '無'}")
        except Exception:
            pass


if __name__ == "__main__":
    cli()

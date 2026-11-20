"""命令行入口。

典型流程：
    python -m aqanalysis ingest  --data-dir data
    python -m aqanalysis analyze --all
    python -m aqanalysis report  --period 2026-04
    python -m aqanalysis ingest  --data-dir data/backfill   # 补录/更正
    python -m aqanalysis status                            # 查看再评估标记
    python -m aqanalysis analyze --all && python -m aqanalysis report --period 2026-04
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .review import (
    analysis_staleness,
    close_review,
    flag_reevaluation,
    register_reviews,
    required_reviews,
)
from .reports import publish_report, render_markdown
from .store import Store, utcnow_iso
from .trends import analyze_period


def _store(args) -> Store:
    return Store(args.state_dir)


def cmd_ingest(args) -> int:
    store = _store(args)
    summary = store.ingest(args.data_dir)
    print(f"导入完成（观测版本 v{summary['obs_version']}）：")
    for name, count in summary["files"].items():
        print(f"  {name}: {count} 条")
    if summary["changed_keys"]:
        print(f"  有效值变更 {len(summary['changed_keys'])} 条：")
        for key in summary["changed_keys"][:20]:
            print(f"    {key}")
        if len(summary["changed_keys"]) > 20:
            print(f"    … 其余 {len(summary['changed_keys']) - 20} 条略")
    else:
        print("  无有效值变更")
    # 补录/更正后，未关闭复核行动标记重新评估
    reviews = flag_reevaluation(store.load_reviews(), store.change_log())
    store.save_reviews(reviews)
    flagged = [r for r in reviews if r.status == "open" and r.needs_reevaluation]
    if flagged:
        print(f"  {len(flagged)} 项未关闭复核行动需重新评估（见 status）")
    return 0


def _periods_to_analyze(store: Store, args) -> list[str]:
    if args.all:
        periods = sorted({
            obs.day.strftime("%Y-%m") for obs in store.load_observations()
        })
        if not periods:
            raise SystemExit("没有观测数据，请先导入")
        return periods
    return [args.period]


def cmd_analyze(args) -> int:
    store = _store(args)
    dataset = store.dataset()
    if not dataset.stations:
        raise SystemExit("没有站点配置，请先导入")
    now = utcnow_iso()
    for period in _periods_to_analyze(store, args):
        analysis = analyze_period(dataset, period, now)
        store.save_analysis(period, analysis)
        reviews = store.load_reviews()
        merged, added = register_reviews(reviews, required_reviews(analysis, now))
        store.save_reviews(merged)
        print(f"{period}: 分析完成（观测版本 v{analysis['obs_version']}），"
              f"新增复核行动 {len(added)} 项")
    return 0


def cmd_report(args) -> int:
    store = _store(args)
    analysis = store.load_analysis(args.period)
    if analysis is None:
        raise SystemExit(f"没有 {args.period} 的分析，请先运行 analyze")
    revision = publish_report(store, args.period, analysis, store.load_reviews())
    print(render_markdown(args.period, revision))
    return 0


def cmd_status(args) -> int:
    store = _store(args)
    change_log = store.change_log()
    print(f"当前观测版本：v{store.meta().get('obs_version', 0)}")
    print()
    print("分析快照：")
    analyses = store.list_analyses()
    if not analyses:
        print("  （无）")
    for period in analyses:
        analysis = store.load_analysis(period)
        stale, reasons = analysis_staleness(analysis, change_log)
        mark = "需重新评估" if stale else "最新"
        print(f"  {period}: 基于 v{analysis['obs_version']}，{mark}")
        for reason in reasons:
            print(f"    - {reason}")
    print()
    print("复核行动：")
    reviews = store.load_reviews()
    open_reviews = [r for r in reviews if r.status == "open"]
    if not open_reviews:
        print("  （无未关闭项）")
    for review in open_reviews:
        flag = "，需重新评估" if review.needs_reevaluation else ""
        print(f"  {review.review_id} [{review.area_id}/{review.station_id}] "
              f"{review.period} {review.reason}（期限 {review.due_date}{flag}）")
    return 0


def cmd_close(args) -> int:
    store = _store(args)
    reviews = store.load_reviews()
    try:
        review = close_review(reviews, args.review_id, utcnow_iso())
    except KeyError as exc:
        raise SystemExit(str(exc))
    store.save_reviews(reviews)
    print(f"已关闭 {review.review_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aqanalysis",
        description="空气质量监测趋势分析：区分原始变化与气象条件差异",
    )
    parser.add_argument("--state-dir", default=".aqstate",
                        help="状态目录（默认 .aqstate）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="导入数据目录（可重复执行，支持补录/更正）")
    p.add_argument("--data-dir", required=True)
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("analyze", help="执行月度分析")
    p.add_argument("--period", help="YYYY-MM")
    p.add_argument("--all", action="store_true", help="分析观测覆盖的所有月份")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("report", help="发布一期报告修订并输出 Markdown")
    p.add_argument("--period", required=True, help="YYYY-MM")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("status", help="查看分析时效与复核行动")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("close", help="关闭复核行动")
    p.add_argument("review_id")
    p.set_defaults(func=cmd_close)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "command", None) == "analyze" and not args.all and not args.period:
        raise SystemExit("analyze 需要 --period 或 --all")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

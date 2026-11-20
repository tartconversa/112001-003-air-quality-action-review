"""月度报告：不可变修订序列、修订说明、Markdown 呈现。

每份报告修订保留发布时的观测版本、方法口径与行动状态；
再次发布时自动生成修订说明，解释既有判断为何改变或维持。
"""

from __future__ import annotations

from .coverage import COMPLIANCE_LABELS, COVERAGE_LABELS
from .models import ReviewAction
from .store import Store, utcnow_iso
from .trends import ATTRIBUTION_LABELS


def _conclusions(analysis: dict) -> dict[str, dict]:
    """从分析快照提取可比较的结论项。"""
    out: dict[str, dict] = {}
    for station_id, result in analysis["stations"].items():
        out[f"station:{station_id}"] = {
            "coverage": result["coverage"]["level"],
            "compliance": result["compliance"],
            "attribution": result["change"]["attribution"],
        }
    for area_id, result in analysis["areas"].items():
        out[f"area:{area_id}"] = {
            "compliance": result["compliance"],
            "attribution": result["change"]["attribution"],
        }
    return out


def _changes_between(store: Store, from_version: int, to_version: int) -> list[dict]:
    entries = []
    for entry in store.change_log():
        if from_version < entry["obs_version"] <= to_version:
            entries.append(entry)
    return entries


def _describe_changes(entries: list[dict]) -> str:
    total = sum(len(e["changed"]) for e in entries)
    versions = "、".join(f"v{e['obs_version']}" for e in entries)
    return f"观测版本 {versions} 共补录/更正 {total} 条记录"


def build_revision_note(store: Store, period: str,
                        prev_revision: dict | None,
                        analysis: dict) -> list[str]:
    """对比上一修订与本次分析，说明判断改变或维持的原因。"""
    if prev_revision is None:
        return ["首次发布。"]

    notes: list[str] = []
    old_version = prev_revision["obs_version"]
    new_version = analysis["obs_version"]
    entries = _changes_between(store, old_version, new_version)
    if new_version != old_version:
        notes.append(
            f"观测版本 v{old_version} -> v{new_version}：{_describe_changes(entries)}。"
            if entries else f"观测版本 v{old_version} -> v{new_version}。"
        )
    if prev_revision["method"]["id"] != analysis["method"]["id"]:
        notes.append(
            f"方法口径变更：{prev_revision['method']['id']} -> {analysis['method']['id']}。"
        )

    old = prev_revision["conclusions"]
    new = _conclusions(analysis)
    changed_lines = []
    for key in sorted(new):
        if key not in old:
            continue
        diffs = [field for field in new[key] if new[key][field] != old[key][field]]
        if not diffs:
            continue
        name = key.replace("station:", "站点 ").replace("area:", "区域 ")
        parts = []
        for field_name in diffs:
            labels = {
                "coverage": ("覆盖", COVERAGE_LABELS),
                "compliance": ("达标判定", COMPLIANCE_LABELS),
                "attribution": ("归因", ATTRIBUTION_LABELS),
            }
            label, vocab = labels[field_name]
            parts.append(
                f"{label}由「{vocab.get(old[key][field_name], old[key][field_name])}」"
                f"变为「{vocab.get(new[key][field_name], new[key][field_name])}」"
            )
        changed_lines.append(f"{name}：" + "；".join(parts))

    if changed_lines:
        notes.append("结论变化：" + "；".join(changed_lines) + "。")
        if entries:
            notes.append("变化原因：上述判断差异由观测补录/更正引起，已按当前有效版本重新计算。")
        elif prev_revision["method"]["id"] != analysis["method"]["id"]:
            notes.append("变化原因：判断差异由方法口径变更引起。")
    else:
        if entries:
            notes.append(
                "结论未变：补录/更正涉及的记录未改变任何站点或区域的覆盖等级、"
                "达标判定与归因结论，既有判断维持。"
            )
        else:
            notes.append("结论未变：观测数据与方法口径均未变化。")
    return notes


def publish_report(store: Store, period: str, analysis: dict,
                   reviews: list[ReviewAction], now: str | None = None) -> dict:
    """基于分析快照发布一期报告修订（追加，不改写历史修订）。"""
    now = now or utcnow_iso()
    report = store.load_report(period)
    revisions = report["revisions"]
    prev = revisions[-1] if revisions else None

    open_reviews = [r for r in reviews if r.status == "open"]
    revision = {
        "seq": len(revisions) + 1,
        "published_at": now,
        "obs_version": analysis["obs_version"],
        "method": analysis["method"],
        "analysis_generated_at": analysis["generated_at"],
        "conclusions": _conclusions(analysis),
        "open_reviews": [r.to_dict() for r in open_reviews],
        "revision_note": build_revision_note(store, period, prev, analysis),
        "analysis": analysis,
    }
    revisions.append(revision)
    store.save_report(period, report)
    return revision


# ---------------------------------------------------------------- Markdown

def _fmt(value, unit=""):
    return f"{value:.1f}{unit}" if isinstance(value, (int, float)) else "—"


def render_markdown(period: str, revision: dict) -> str:
    analysis = revision["analysis"]
    lines = [
        f"# {period} 空气质量趋势解释（第 {revision['seq']} 版）",
        "",
        f"- 发布时间：{revision['published_at']}",
        f"- 观测版本：v{revision['obs_version']}",
        f"- 方法口径：{revision['method']['id']}"
        f"（有效日 ≥{revision['method']['valid_day_min_hours']}h；"
        f"覆盖充足 ≥{revision['method']['coverage_sufficient_ratio']:.0%}；"
        f"显著变化 ≥{revision['method']['significant_change_ug']} µg/m³）",
        "",
        "## 修订说明",
        "",
    ]
    lines += [f"- {note}" for note in revision["revision_note"]]

    lines += ["", "## 站点结果", "",
              "| 站点 | 区域 | 覆盖 | 实测均值 | 气象校正均值 | 气象效应 | 达标判定 | 环比归因 |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for station_id, result in analysis["stations"].items():
        adj = result["adjustment"] or {}
        lines.append(
            f"| {station_id} | {result['area_id']} | "
            f"{COVERAGE_LABELS[result['coverage']['level']]}"
            f"（{result['coverage']['valid_days']}/{result['coverage']['expected_days']}天） | "
            f"{_fmt(result['raw_mean'])} | "
            f"{_fmt(adj.get('adjusted_mean'))} | "
            f"{_fmt(adj.get('weather_effect'))} | "
            f"{COMPLIANCE_LABELS[result['compliance']]} | "
            f"{result['change']['attribution_label']} |"
        )
        if result["change"]["detail"]:
            lines.append(f"|  |  |  |  |  |  |  | {result['change']['detail']} |")

    lines += ["", "## 区域结果", ""]
    for area_id, result in analysis["areas"].items():
        lines.append(f"### {area_id}")
        lines.append("")
        lines.append(
            f"- 覆盖 {result['coverage_ratio']:.0%}，"
            f"实测均值 {_fmt(result['raw_mean'])} µg/m³，"
            f"气象校正均值 {_fmt(result['adjusted_mean'])} µg/m³，"
            f"{COMPLIANCE_LABELS[result['compliance']]}"
        )
        if result["excluded"]:
            lines.append(f"- 覆盖不足被排除的站点：{'、'.join(result['excluded'])}")
        lines.append(f"- 环比归因：{result['change']['attribution_label']}"
                     f"（{result['change']['detail']}）")
        if result["matched_actions"]:
            lines.append("- 窗口内治理行动：")
            for action in result["matched_actions"]:
                note = f"；{action['scope_note']}" if action["scope_note"] else ""
                lines.append(
                    f"  - {action['action_id']} {action['action_type']}"
                    f"（{action['start']}~{action['end']}，"
                    f"适用范围：{'、'.join(action['areas'])}）{note}"
                )
        lines.append("")

    shared = [a for a in analysis["city_actions"] if a["shared"]]
    if shared:
        lines += ["## 跨区域行动记功说明", ""]
        for action in shared:
            lines.append(
                f"- {action['action_id']} 覆盖 {'、'.join(action['areas'])}："
                "全市汇总只计一次，不在各区域重复记功。"
            )
        lines.append("")

    if revision["open_reviews"]:
        lines += ["## 未关闭复核行动", "",
                  "| 编号 | 站点 | 责任区域 | 原因 | 跟进期限 | 需重新评估 |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for review in revision["open_reviews"]:
            lines.append(
                f"| {review['review_id']} | {review['station_id']} | "
                f"{review['area_id']} | {review['reason']} | {review['due_date']} | "
                f"{'是' if review['needs_reevaluation'] else '否'} |"
            )
    return "\n".join(lines) + "\n"

from __future__ import annotations

import re
from pathlib import Path

from .models import AXIS_LABELS, AssessmentReport


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z一-龥ぁ-んァ-ヶー_-]+", "-", value).strip("-")
    return slug[:60] or "region"


def render_markdown(report: AssessmentReport) -> str:
    mock_banner = ""
    if report.plan.data_mode == "mock":
        mock_banner = (
            "> [!WARNING]\n"
            "> **デモ用モックデータです。実在地域の意思決定には使用できません。**\n\n"
        )

    axis_rows = "\n".join(
        f"| {AXIS_LABELS[result.axis]} | {result.score:.1f} | "
        f"{report.plan.weights[result.axis]:.1f}% | {result.confidence:.0%} | "
        f"{result.narrative.summary} |"
        for result in report.axis_results
    )
    weight_text = " / ".join(
        f"{AXIS_LABELS[axis]} {weight:.1f}%" for axis, weight in report.plan.weights.items()
    )
    enabled_agents = " / ".join(
        AXIS_LABELS[axis] for axis in report.plan.enabled_axes
    )
    excluded_agents = (
        " / ".join(AXIS_LABELS[axis] for axis in report.plan.excluded_axes)
        if report.plan.excluded_axes
        else "なし"
    )
    exclusion_note = ""
    if report.plan.excluded_axes:
        exclusion_note = (
            "> 除外した軸はAPI取得・専門Agent実行・採点を行わず、"
            "残りのウェイトを100%へ再配分しています。\n\n"
        )
    strengths = "\n".join(f"- {item}" for item in report.narrative.strengths)
    cautions = "\n".join(f"- {item}" for item in report.narrative.cautions)
    followups = "\n".join(f"- {item}" for item in report.narrative.suggested_followups)

    evidence_sections: list[str] = []
    for result in report.axis_results:
        rows = "\n".join(
            (
                f"| {metric.label} | {metric.value:g} {metric.unit} | "
                f"{metric.normalized_score:.1f} | {metric.source.source_name} | "
                f"{metric.source.reference_date} |"
            )
            for metric in result.metrics
        )
        evidence_sections.append(
            f"### {AXIS_LABELS[result.axis]}\n\n"
            "| 指標 | 値 | 相対スコア | 出典 | 基準時点 |\n"
            "|---|---:|---:|---|---|\n"
            f"{rows}\n\n"
            f"強み: {' / '.join(result.narrative.strengths)}\n\n"
            f"注意: {' / '.join(result.narrative.cautions)}"
        )

    source_map = {
        metric.source.source_id: metric.source
        for result in report.axis_results
        for metric in result.metrics
    }
    sources = "\n".join(
        f"- [{source.source_name} / {source.endpoint}]({source.url}) — {source.reference_date}"
        for source in source_map.values()
    )
    execution_rows = "\n".join(
        f"| {step.name} | {step.status} | {step.elapsed_ms} ms | {step.detail} |"
        for step in report.execution_steps
    )
    disclaimers = "\n".join(f"- {item}" for item in report.disclaimers)

    return (
        f"# {report.plan.region.name} 住みやすさ総合評価\n\n"
        f"{mock_banner}"
        f"**総合スコア: {report.overall_score:.1f} / 100**  "
        f"（データ信頼度: {report.overall_confidence:.0%}）\n\n"
        f"{report.narrative.executive_summary}\n\n"
        f"比較基準: {report.plan.region.comparison_group}\n\n"
        "## Agent構成\n\n"
        f"- 使用（{len(report.plan.enabled_axes)}）: {enabled_agents}\n"
        f"- 除外（{len(report.plan.excluded_axes)}）: {excluded_agents}\n\n"
        f"{exclusion_note}"
        f"適用ウェイト: {weight_text}\n\n"
        f"## {len(report.plan.enabled_axes)}軸評価\n\n"
        "| 評価軸 | 点数 | ウェイト | 信頼度 | 要約 |\n"
        "|---|---:|---:|---:|---|\n"
        f"{axis_rows}\n\n"
        "## 主な強み\n\n"
        f"{strengths}\n\n"
        "## 注意点\n\n"
        f"{cautions}\n\n"
        "## 次に試せる質問\n\n"
        f"{followups}\n\n"
        "## 根拠データ\n\n"
        f"{'\n\n'.join(evidence_sections)}\n\n"
        "## 並列実行トレース\n\n"
        "| 処理 | 状態 | 所要時間 | 詳細 |\n"
        "|---|---|---:|---|\n"
        f"{execution_rows}\n\n"
        f"全体所要時間: {report.total_elapsed_ms} ms\n\n"
        "## データソース\n\n"
        f"{sources}\n\n"
        "## 免責・評価条件\n\n"
        f"{disclaimers}\n"
    )


class ReportWriter:
    def __init__(self, outputs_dir: Path) -> None:
        self._outputs_dir = outputs_dir

    def write(self, report: AssessmentReport) -> tuple[Path, Path, str]:
        self._outputs_dir.mkdir(parents=True, exist_ok=True)
        timestamp = report.generated_at.strftime("%Y%m%d-%H%M%S")
        stem = f"{timestamp}-{_safe_slug(report.plan.region.name)}-{report.report_id[:8]}"
        markdown_path = (self._outputs_dir / f"{stem}.md").resolve()
        json_path = (self._outputs_dir / f"{stem}.json").resolve()

        report.artifacts.markdown_path = str(markdown_path)
        report.artifacts.json_path = str(json_path)
        markdown = render_markdown(report)
        markdown_path.write_text(markdown, encoding="utf-8")
        json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return markdown_path, json_path, markdown

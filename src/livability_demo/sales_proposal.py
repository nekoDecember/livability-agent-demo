"""Validate the evidence references and scope of a commander's sales proposal."""

import re

from .models import AssessmentReport, CommanderNarrative


def comparable_metric_codes(reports: list[AssessmentReport]) -> set[str]:
    if not reports:
        return set()
    metric_maps = [
        {
            metric.metric_code: metric
            for result in report.axis_results
            for metric in result.metrics
            if metric.quality > 0 and metric.direction != "context_only"
        }
        for report in reports
    ]
    comparable = set.intersection(*(set(metrics) for metrics in metric_maps))
    comparable = {
        code
        for code in comparable
        if len(
            {
                (
                    metrics[code].unit,
                    metrics[code].source.reference_date,
                    metrics[code].direction,
                    metrics[code].source.source_id,
                )
                for metrics in metric_maps
            }
        )
        == 1
    }
    return comparable


def validate_sales_proposal(
    narrative: CommanderNarrative,
    reports: list[AssessmentReport],
) -> CommanderNarrative:
    codes = {report.plan.region.municipality_code for report in reports}
    positions = [item.region_code for item in narrative.candidate_positions]
    if positions and (set(positions) != codes or len(positions) != len(codes)):
        raise ValueError("Proposal must describe every candidate exactly once")
    if not set(narrative.supporting_metric_codes) <= comparable_metric_codes(reports):
        raise ValueError("Proposal refers to unavailable or incomparable evidence")
    real_data = all(report.plan.data_mode in {"open_data", "government_api"} for report in reports)
    if real_data and any(
        metric.is_mock
        for report in reports
        for result in report.axis_results
        for metric in result.metrics
        if metric.metric_code in narrative.supporting_metric_codes
    ):
        raise ValueError("Measured proposal cannot use mock evidence")
    strength = narrative.recommendation_strength
    if narrative.recommended_region_code is None:
        strength = "undecided"
    elif not real_data:
        strength = "hypothesis"
    elif strength == "recommended" and not narrative.supporting_metric_codes:
        raise ValueError("A strong recommendation requires comparable evidence references")
    metric_codes = {
        metric.metric_code
        for report in reports
        for result in report.axis_results
        for metric in result.metrics
    }
    if not metric_codes:
        return narrative.model_copy(update={"recommendation_strength": strength})
    pattern = re.compile(
        r"(?<![A-Za-z0-9_])(?:"
        + "|".join(re.escape(code) for code in sorted(metric_codes))
        + r")(?![A-Za-z0-9_])"
    )

    def customer_text(text: str) -> str:
        # Keep dates, figures and evidence references; omit internal codes in prose notes.
        def clean_note(match: re.Match[str]) -> str:
            content = pattern.sub("", match[2]).strip(" 、,・/;；")
            content = re.sub(r"[、,](?:\s*[、,])+", "、", content)
            return f"{match[1]}{content}{match[3]}" if content else ""

        text = re.sub(r"(（)([^（）]*)(）)", clean_note, text)
        return re.sub(r"(\()([^()]*)(\))", clean_note, text)

    data = narrative.model_dump()
    data["recommendation_strength"] = strength
    for field in ("summary", "proposal_title"):
        data[field] = customer_text(data[field])
    for field in ("reasons", "tradeoffs", "next_checks"):
        data[field] = [customer_text(text) for text in data[field]]
    for position in data["candidate_positions"]:
        for field in ("fit_summary", "selection_condition"):
            position[field] = customer_text(position[field])
    return CommanderNarrative.model_validate(data)

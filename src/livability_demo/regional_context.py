"""Small, source-backed place facts that do not alter statistical scores."""

from .models import Axis, MetricEvidence, RegionInfo, SourceReference

TERTIARY_EDUCATION_TERMS = (
    "大学進学",
    "大学への進学",
    "大学に進学",
    "大学へ通学",
    "大学に通う",
    "大学生",
    "大学が多い",
    "短大",
    "高専",
    "高等専門学校",
    "高等教育",
    "進学先",
)


def requests_tertiary_education_context(user_request: str) -> bool:
    """Show tertiary-campus context only when the person explicitly cares about it."""

    return any(term in user_request for term in TERTIARY_EDUCATION_TERMS)


def context_metrics(region: RegionInfo, axis: Axis) -> list[MetricEvidence]:
    if (
        axis != Axis.CONVENIENCE
        or region.name != "高崎市"
        or region.prefecture not in (None, "群馬県")
    ):
        return []
    return [
        MetricEvidence(
            metric_code="takasaki_shinkansen_connections",
            label="高崎駅の新幹線接続",
            value=2,
            unit="路線（上越・北陸新幹線）",
            normalized_score=0,
            direction="context_only",
            weight=1,
            source=SourceReference(
                source_id="JR-EAST-TAKASAKI-TIMETABLE",
                source_name="JR東日本 高崎駅時刻表",
                endpoint="station-timetable",
                url="https://timetables.jreast.co.jp/timetable/list0934.html",
                reference_date="2026-09-23閲覧",
                commercial_use_note="駅・路線の事実確認に使用。運賃、本数、駅までの所要時間は未評価。",
            ),
            quality=1,
            note="高崎駅に上越・北陸新幹線の発着がある。市内全域の駅アクセスは表さない。",
        )
    ]

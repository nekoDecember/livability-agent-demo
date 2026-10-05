from livability_demo.deterministic_analysis import build_axis_summary
from livability_demo.models import Axis, RegionInfo
from livability_demo.regional_context import (
    context_metrics,
    requests_tertiary_education_context,
)


def test_takasaki_rail_fact_is_sourced_and_not_scored():
    region = RegionInfo(
        query="高崎市", name="高崎市", municipality_code="10202", prefecture="群馬県"
    )
    facts = context_metrics(region, Axis.CONVENIENCE)
    assert len(facts) == 1
    assert facts[0].direction == "context_only"
    assert "jreast.co.jp" in facts[0].source.url
    assert not context_metrics(region, Axis.HOUSING)
    assert not context_metrics(region.model_copy(update={"name": "前橋市"}), Axis.CONVENIENCE)


def test_summary_uses_specific_available_metric_and_conditions():
    from livability_demo.models import AxisEvidence, MetricEvidence, SourceReference

    region = RegionInfo(query="高崎市", name="高崎市", municipality_code="10202")
    source = SourceReference(
        source_id="TEST",
        source_name="出典",
        endpoint="test",
        url="https://example.com",
        reference_date="2025",
        commercial_use_note="test",
    )
    evidence = AxisEvidence(
        axis=Axis.CONVENIENCE,
        region=region,
        data_mode="open_data",
        api_calls=[],
        elapsed_ms=0,
        metrics=[
            MetricEvidence(
                metric_code="retail_density",
                label="小売・サービス事業所密度",
                value=11.4,
                unit="事業所/千人",
                normalized_score=65,
                direction="higher_is_better",
                weight=0.2,
                source=source,
            ),
            *context_metrics(region, Axis.CONVENIENCE),
        ],
    )
    summary = build_axis_summary(evidence, "高崎市。条件: 新幹線で通勤")
    assert "上越・北陸新幹線" in summary
    assert "11.4" in summary
    assert "駅" in summary

    car_summary = build_axis_summary(evidence, "高崎市。条件: 高崎駅周辺勤務・車通勤")
    assert "上越・北陸新幹線" not in car_summary
    assert "日々の買い物や用事" in car_summary
    assert "評価していません" not in car_summary


def test_tertiary_education_context_is_only_used_for_relevant_conditions():
    from livability_demo.deterministic_analysis import build_axis_summary
    from livability_demo.models import AxisEvidence, MetricEvidence, SourceReference

    region = RegionInfo(query="前橋市", name="前橋市", municipality_code="10201")
    source = SourceReference(
        source_id="P29-TEST",
        source_name="学校データ",
        endpoint="P29-2023",
        url="https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-P29-2023.html",
        reference_date="2023",
        commercial_use_note="CC BY 4.0",
    )
    evidence = AxisEvidence(
        axis=Axis.FAMILY,
        region=region,
        data_mode="open_data",
        api_calls=[],
        elapsed_ms=0,
        metrics=[
            MetricEvidence(
                metric_code="clinics_per_100k",
                label="診療所数",
                value=100,
                unit="施設/10万人",
                normalized_score=50,
                direction="higher_is_better",
                weight=0.3,
                source=source,
            ),
            MetricEvidence(
                metric_code="tertiary_education_campuses",
                label="大学・短大・高専の掲載キャンパス数",
                value=9,
                unit="キャンパス",
                normalized_score=50,
                direction="context_only",
                weight=0.05,
                source=source,
                quality=0.45,
                note="状態コード0（調査なし）を含み現況未確認。採点対象外。",
            ),
        ],
    )

    relevant = "前橋市。条件: 大学進学を重視"
    irrelevant = "前橋市。条件: 大学病院の近くに住みたい"
    assert requests_tertiary_education_context(relevant)
    assert not requests_tertiary_education_context(irrelevant)
    assert "9キャンパス" in build_axis_summary(evidence, relevant)
    assert "9キャンパス" not in build_axis_summary(evidence, irrelevant)

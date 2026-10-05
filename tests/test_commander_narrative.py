import pytest
from pydantic import ValidationError

from livability_demo.deterministic_analysis import build_commander_narrative
from livability_demo.models import CommanderNarrative


@pytest.mark.parametrize(
    "contaminant",
    ["แ้าว?", "\ufffc[... EOL]"],
)
def test_commander_rejects_malformed_text_in_narrative(contaminant: str) -> None:
    with pytest.raises(ValidationError):
        CommanderNarrative(
            recommended_region_code="10201",
            summary="子育て世帯には前橋市が候補です。",
            reasons=["保育関連の公開データを確認しました。"],
            tradeoffs=["通勤時間は別途確認が必要です。"],
            next_checks=[f"園の空き状況を確認してください。{contaminant}"],
            confidence=0.7,
        )


def test_offline_commander_recommends_from_priorities_and_translates_the_evidence() -> None:
    narrative = build_commander_narrative(
        {
            "user_request": "25歳・子育て世帯・車通勤",
            "comparable_axes": ["convenience", "housing", "family"],
            "priority_weights": {"convenience": 10, "housing": 30, "family": 60},
            "candidates": [
                {
                    "region_code": "10202",
                    "region_name": "高崎市",
                    "specialist_findings": [
                        {
                            "axis": "family",
                            "label": "医療・子育て",
                            "score": 70,
                            "confidence": 0.8,
                            "metrics": [
                                {
                                    "metric_code": "nursery_per_1000_children",
                                    "value": 3.2,
                                    "unit": "施設/千人",
                                    "weight": 1,
                                    "normalized_score": 65,
                                    "direction": "higher_is_better",
                                    "quality": 1,
                                    "reference_date": "2024年",
                                }
                            ],
                        },
                        {
                            "axis": "housing",
                            "label": "住まいコスト",
                            "score": 78,
                            "confidence": 0.8,
                            "metrics": [
                                {
                                    "metric_code": "official_land_price",
                                    "value": 60000,
                                    "unit": "円/㎡",
                                    "weight": 1,
                                    "normalized_score": 70,
                                    "direction": "lower_is_better",
                                    "quality": 1,
                                    "reference_date": "2024年",
                                }
                            ],
                        },
                    ],
                },
                {
                    "region_code": "10201",
                    "region_name": "前橋市",
                    "specialist_findings": [
                        {
                            "axis": "family",
                            "label": "医療・子育て",
                            "score": 76,
                            "confidence": 0.85,
                            "metrics": [
                                {
                                    "metric_code": "nursery_per_1000_children",
                                    "value": 4.1,
                                    "unit": "施設/千人",
                                    "weight": 1,
                                    "normalized_score": 80,
                                    "direction": "higher_is_better",
                                    "quality": 1,
                                    "reference_date": "2024年",
                                }
                            ],
                        },
                        {
                            "axis": "housing",
                            "label": "住まいコスト",
                            "score": 65,
                            "confidence": 0.8,
                            "metrics": [
                                {
                                    "metric_code": "official_land_price",
                                    "value": 80000,
                                    "unit": "円/㎡",
                                    "weight": 1,
                                    "normalized_score": 45,
                                    "direction": "lower_is_better",
                                    "quality": 1,
                                    "reference_date": "2024年",
                                }
                            ],
                        },
                    ],
                },
            ],
            "scores": {"10202": 91.0, "10201": 40.0},
            "selected_region_code": "10202",
        }
    )

    assert narrative.recommended_region_code == "10201"
    assert "暫定提案" in narrative.summary
    assert any("医療・子育て" in item and "60%" in item for item in narrative.reasons)
    assert any("保育・幼児教育の受け皿は、前橋市が4.1" in item for item in narrative.reasons)
    assert any("土地を買って家を建てる場合の土地代" in item for item in narrative.tradeoffs)
    assert any("勤務地" in item for item in narrative.next_checks)
    assert narrative.confidence > 0


def test_offline_commander_does_not_recommend_without_shared_evidence() -> None:
    narrative = build_commander_narrative(
        {
            "user_request": "防災重視",
            "comparable_axes": [],
            "candidates": [],
        }
    )

    assert narrative.recommended_region_code is None
    assert narrative.confidence == 0
    assert "まだ第一候補を決める根拠がありません" in narrative.summary
    assert "同じ条件で比べられる取得済みデータ" in narrative.reasons[0]

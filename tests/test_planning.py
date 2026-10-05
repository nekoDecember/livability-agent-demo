import pytest

from livability_demo.models import Axis, RegionInfo
from livability_demo.planning import build_plan, parse_excluded_axes

REGION = RegionInfo(
    query="流山市",
    name="流山市",
    municipality_code="12220",
)


def test_default_weights_are_equal() -> None:
    plan = build_plan("流山市を評価して", REGION, data_mode="mock")
    assert plan.weights == {axis: 20.0 for axis in Axis}
    assert plan.enabled_axes == list(Axis)
    assert plan.excluded_axes == []
    assert sum(plan.weights.values()) == 100


def test_preferences_change_weights_and_still_sum_to_100() -> None:
    plan = build_plan("流山市を子育て重視、車なしで評価して", REGION, data_mode="mock")
    assert plan.excluded_axes == []
    assert plan.weights[Axis.FAMILY] > 20
    assert plan.weights[Axis.CONVENIENCE] > 20
    assert sum(plan.weights.values()) == 100


def test_plain_living_conditions_change_priorities() -> None:
    plan = build_plan(
        "流山市の住みやすさを評価して。条件: 車なし・子育て・都内へ週3通勤",
        REGION,
        data_mode="open_data",
    )
    assert abs(plan.weights[Axis.CONVENIENCE] - plan.weights[Axis.FAMILY]) <= 0.11
    assert plan.weights[Axis.CONVENIENCE] > plan.weights[Axis.HOUSING]
    assert "車に頼らない移動" in plan.preferences
    assert "鉄道・公共交通" not in plan.preferences


def test_car_commute_does_not_increase_rail_or_convenience_priority() -> None:
    plan = build_plan(
        "高崎市の住みやすさを評価して。条件: 高崎駅周辺勤務・独身25歳・車通勤",
        REGION,
        data_mode="open_data",
    )
    assert plan.weights[Axis.CONVENIENCE] == 20
    assert "鉄道・公共交通" not in plan.preferences


def test_rail_commute_is_explicitly_weighted() -> None:
    plan = build_plan(
        "高崎市の住みやすさを評価して。条件: 新幹線通勤",
        REGION,
        data_mode="open_data",
    )
    assert plan.weights[Axis.CONVENIENCE] > 20
    assert "鉄道・公共交通" in plan.preferences


def test_reviewed_ui_weights_override_inferred_weights() -> None:
    plan = build_plan(
        "流山市を車なし・子育てで評価して",
        REGION,
        data_mode="mock",
        requested_weights={Axis.CONVENIENCE: 10, Axis.HOUSING: 60, Axis.FAMILY: 30},
    )
    assert plan.weights[Axis.HOUSING] == 60
    assert plan.weights[Axis.CONVENIENCE] == 10
    assert "画面で確認した重み" in plan.weight_reason


def test_explicit_weight_is_respected() -> None:
    plan = build_plan("流山市を子育て40%で評価して", REGION, data_mode="mock")
    assert plan.weights[Axis.FAMILY] == 40
    assert sum(plan.weights.values()) == 100


def test_named_agent_can_be_excluded_and_weights_are_redistributed() -> None:
    plan = build_plan(
        "流山市を評価して。安心・防災Agentは使わない",
        REGION,
        data_mode="mock",
    )

    assert plan.excluded_axes == [Axis.SAFETY]
    assert Axis.SAFETY not in plan.enabled_axes
    assert Axis.SAFETY not in plan.weights
    assert plan.weights == {
        Axis.CONVENIENCE: 25.0,
        Axis.HOUSING: 25.0,
        Axis.FAMILY: 25.0,
        Axis.FUTURE: 25.0,
    }


def test_explicit_exclusion_list_supports_multiple_agents() -> None:
    excluded = parse_excluded_axes("流山市を評価して。除外Agent: 住まいコスト、防災")
    assert excluded == [Axis.HOUSING, Axis.SAFETY]


def test_programmatic_enabled_axes_are_the_upper_bound() -> None:
    plan = build_plan(
        "流山市を評価して",
        REGION,
        data_mode="mock",
        enabled_axes=[Axis.CONVENIENCE, Axis.FAMILY],
    )
    assert plan.enabled_axes == [Axis.CONVENIENCE, Axis.FAMILY]
    assert plan.excluded_axes == [Axis.HOUSING, Axis.SAFETY, Axis.FUTURE]
    assert plan.weights == {Axis.CONVENIENCE: 50.0, Axis.FAMILY: 50.0}


def test_all_agents_cannot_be_excluded() -> None:
    request = (
        "移動Agentを除外、住まいAgentを除外、子育てAgentを除外、防災Agentを除外、将来性Agentを除外"
    )
    with pytest.raises(ValueError, match="少なくとも1つ"):
        build_plan(request, REGION, data_mode="mock")

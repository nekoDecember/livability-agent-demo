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
        "移動Agentを除外、住まいAgentを除外、子育てAgentを除外、"
        "防災Agentを除外、将来性Agentを除外"
    )
    with pytest.raises(ValueError, match="少なくとも1つ"):
        build_plan(request, REGION, data_mode="mock")

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from math import isfinite

from .catalog import AXIS_AGENT_NAMES
from .models import AXIS_LABELS, AssessmentPlan, Axis, RegionInfo

AXIS_ALIASES: dict[Axis, tuple[str, ...]] = {
    Axis.CONVENIENCE: ("移動", "買い物", "交通", "利便性", "車なし"),
    Axis.HOUSING: ("住まい", "住宅", "住居", "家賃", "コスト"),
    Axis.FAMILY: ("医療", "子育て", "保育", "教育"),
    Axis.SAFETY: ("安心", "安全", "防災", "災害"),
    Axis.FUTURE: ("将来", "将来性", "人口", "持続性"),
}


PREFERENCE_BONUSES: tuple[tuple[tuple[str, ...], Axis, float, str], ...] = (
    (("子育て", "保育園", "幼稚園", "小学生", "子ども", "こども"), Axis.FAMILY, 20, "子育て"),
    (
        ("車なし", "車を使わない", "車を持たない", "電車移動"),
        Axis.CONVENIENCE,
        20,
        "車に頼らない移動",
    ),
    (
        ("電車通勤", "鉄道通勤", "新幹線通勤", "新幹線", "鉄道", "駅近", "公共交通"),
        Axis.CONVENIENCE,
        15,
        "鉄道・公共交通",
    ),
    (("家賃", "住宅費", "住居費", "予算", "コスト重視", "安さ重視"), Axis.HOUSING, 20, "住居費"),
    (("高齢", "介護", "通院", "病院", "医療重視"), Axis.FAMILY, 15, "医療へのアクセス"),
    (("防災", "災害", "洪水", "浸水", "安全重視"), Axis.SAFETY, 20, "災害・安全"),
    (("長く住", "永住", "将来性", "将来重視", "人口減少"), Axis.FUTURE, 20, "長期居住"),
)

EXCLUSION_ACTION_PATTERN = re.compile(
    r"使わ(?:ない|ず)|使用しない|実行しない|"
    r"除外(?:する|して|した|してほしい)?|"
    r"外(?:す|して|した)|抜き|なし|省(?:く|いて)|無効(?:化)?"
)
EXCLUSION_CLAUSE_BOUNDARY = re.compile(
    r"[。！？!?、,，;；\n]|ただし|一方で|けれども|けれど|けど|ですが|だが"
)


def _axis_terms(axis: Axis) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*AXIS_ALIASES[axis], AXIS_LABELS[axis], AXIS_AGENT_NAMES[axis])))


def parse_excluded_axes(user_request: str) -> list[Axis]:
    """Parse deliberately explicit Agent/axis exclusion phrases from a chat request."""

    excluded: set[Axis] = set()
    explicit_lists = re.finditer(
        r"(?:除外|無効)(?:する)?(?:Agent|agent|エージェント|軸)?\s*[:：]\s*"
        r"(?P<targets>[^。！？!?\n]+)",
        user_request,
    )
    for match in explicit_lists:
        targets = match.group("targets")
        for axis in Axis:
            if any(term in targets for term in _axis_terms(axis)):
                excluded.add(axis)

    for action in EXCLUSION_ACTION_PATTERN.finditer(user_request):
        prefix = user_request[max(0, action.start() - 80) : action.start()]
        target_clause = EXCLUSION_CLAUSE_BOUNDARY.split(prefix)[-1]
        target_clause = re.sub(
            r"(?:エージェント|Agent|agent|軸)?\s*(?:は|を|も)?\s*$",
            "",
            target_clause,
        )
        for axis in Axis:
            if any(term in target_clause for term in _axis_terms(axis)):
                excluded.add(axis)
    return [axis for axis in Axis if axis in excluded]


def _resolve_enabled_axes(
    user_request: str,
    configured_axes: Iterable[Axis] | None,
) -> tuple[list[Axis], list[Axis]]:
    configured = (
        list(Axis)
        if configured_axes is None
        else list(dict.fromkeys(Axis(axis) for axis in configured_axes))
    )
    requested_exclusions = set(parse_excluded_axes(user_request))
    enabled = [axis for axis in Axis if axis in configured and axis not in requested_exclusions]
    if not enabled:
        raise ValueError("少なくとも1つの専門Agentを有効にしてください。")
    excluded = [axis for axis in Axis if axis not in enabled]
    return enabled, excluded


def _normalize(
    weights: dict[Axis, float],
    enabled_axes: Sequence[Axis],
) -> dict[Axis, float]:
    total = sum(max(weights[axis], 0) for axis in enabled_axes)
    if total <= 0:
        equal = round(100 / len(enabled_axes), 1)
        normalized = {axis: equal for axis in enabled_axes}
    else:
        normalized = {axis: round(max(weights[axis], 0) / total * 100, 1) for axis in enabled_axes}
    delta = round(100 - sum(normalized.values()), 1)
    first_axis = enabled_axes[0]
    normalized[first_axis] = round(normalized[first_axis] + delta, 1)
    return normalized


def _explicit_weights(user_request: str) -> dict[Axis, float]:
    found: dict[Axis, float] = {}
    for axis, aliases in AXIS_ALIASES.items():
        alias_pattern = "|".join(map(re.escape, aliases))
        patterns = (
            rf"(?:{alias_pattern})\s*(?:は|を|:|：)?\s*(\d{{1,3}}(?:\.\d+)?)\s*%",
            rf"(\d{{1,3}}(?:\.\d+)?)\s*%\s*(?:を)?\s*(?:{alias_pattern})",
        )
        for pattern in patterns:
            match = re.search(pattern, user_request)
            if match:
                found[axis] = min(100.0, float(match.group(1)))
                break
    return found


def build_plan(
    user_request: str,
    region: RegionInfo,
    *,
    data_mode: str,
    enabled_axes: Iterable[Axis] | None = None,
    requested_weights: dict[Axis, float] | None = None,
) -> AssessmentPlan:
    selected_axes, excluded_axes = _resolve_enabled_axes(user_request, enabled_axes)
    living_conditions = (
        user_request.split("。条件: ", 1)[-1] if "。条件: " in user_request else user_request
    )
    weights = {axis: 20.0 for axis in selected_axes}
    preferences: list[str] = []

    for triggers, axis, bonus, label in PREFERENCE_BONUSES:
        if axis in selected_axes and any(trigger in living_conditions for trigger in triggers):
            weights[axis] += bonus
            preferences.append(label)

    if requested_weights is not None:
        if not requested_weights or any(
            not isfinite(weight) or weight < 0 or weight > 100
            for weight in requested_weights.values()
        ):
            raise ValueError("重みは0〜100の範囲で指定してください。")
        if set(requested_weights) - set(selected_axes):
            raise ValueError("重みは有効な評価軸だけに指定してください。")
        if sum(requested_weights.values()) <= 0:
            raise ValueError("有効な評価軸の重みを1つ以上指定してください。")
    explicit = {
        axis: weight
        for axis, weight in (
            requested_weights
            if requested_weights is not None
            else _explicit_weights(living_conditions)
        ).items()
        if axis in selected_axes
    }
    if explicit:
        remaining_axes = [axis for axis in selected_axes if axis not in explicit]
        explicit_total = sum(explicit.values())
        if explicit_total >= 100 or not remaining_axes:
            weights = {axis: explicit.get(axis, 0.0) for axis in selected_axes}
        else:
            base_remaining = sum(weights[axis] for axis in remaining_axes)
            weights = {
                axis: (
                    explicit[axis]
                    if axis in explicit
                    else weights[axis] / base_remaining * (100 - explicit_total)
                )
                for axis in selected_axes
            }
        preferences.append(
            "画面で確認した重み" if requested_weights is not None else "明示ウェイト"
        )

    weights = _normalize(weights, selected_axes)
    reason = f"{len(selected_axes)}軸を均等評価"
    if preferences:
        reason = "ユーザー指定を反映: " + "、".join(dict.fromkeys(preferences))
    if excluded_axes:
        reason += "（使用する軸へウェイトを再配分）"

    excluded_labels = "、".join(AXIS_LABELS[axis] for axis in excluded_axes)
    selection_reason = f"{len(selected_axes)}専門Agentを使用"
    if excluded_axes:
        selection_reason += f"、{len(excluded_axes)}専門Agentを除外: {excluded_labels}"
    else:
        selection_reason += "（除外なし）"

    return AssessmentPlan(
        user_request=user_request,
        region=region,
        weights=weights,
        enabled_axes=selected_axes,
        excluded_axes=excluded_axes,
        agent_selection_reason=selection_reason,
        preferences=list(dict.fromkeys(preferences)),
        weight_reason=reason,
        data_mode=data_mode,  # type: ignore[arg-type]
    )

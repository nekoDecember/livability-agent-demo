import type { AxisKey } from "../types";
import { AXIS_ORDER, normalizeWeights } from "./report";

const RULES: { terms: string[]; axis: AxisKey; bonus: number; label: string }[] = [
  { terms: ["子育て世帯", "子育て", "保育園", "幼稚園", "小学生", "子ども", "こども"], axis: "family", bonus: 32, label: "子ども・子育て" },
  { terms: ["独身", "一人暮らし", "単身"], axis: "housing", bonus: 16, label: "単身の住まい" },
  { terms: ["夫婦", "二人暮らし", "二人世帯"], axis: "housing", bonus: 10, label: "二人世帯の住まい" },
  { terms: ["車なし", "車を使わない", "車を持たない", "電車移動", "鉄道通勤", "電車通勤", "新幹線通勤", "公共交通", "駅近"], axis: "convenience", bonus: 22, label: "車に頼らない移動" },
  { terms: ["新幹線", "鉄道", "電車"], axis: "convenience", bonus: 15, label: "鉄道・公共交通" },
  { terms: ["家賃", "住宅費", "住居費", "予算", "コスト重視", "安さ重視", "安く"], axis: "housing", bonus: 24, label: "住居費" },
  { terms: ["高齢", "介護", "通院", "病院", "医療重視"], axis: "family", bonus: 18, label: "医療への備え" },
  { terms: ["防災", "災害", "洪水", "浸水", "安全重視"], axis: "safety", bonus: 22, label: "災害・安全" },
  { terms: ["長く住", "永住", "将来性", "将来重視", "人口減少"], axis: "future", bonus: 20, label: "長期居住" },
];

export function suggestedWeights(preference: string): { weights: Record<AxisKey, number>; reasons: string[] } {
  const weights = Object.fromEntries(AXIS_ORDER.map(axis => [axis, 20])) as Record<AxisKey, number>;
  const reasons: string[] = [];
  for (const rule of RULES) {
    if (rule.terms.some(term => preference.includes(term))) {
      weights[rule.axis] += rule.bonus;
      reasons.push(rule.label);
    }
  }
  const age = preference.match(/(?:^|\D)(\d{1,3})歳/)?.[1];
  if (age && Number(age) >= 65) {
    weights.family += 16;
    weights.convenience += 8;
    reasons.push("65歳以上の医療・日常移動");
  } else if (age && Number(age) < 30) {
    weights.housing += 8;
    reasons.push("30歳未満の住居負担");
  } else if (age && Number(age) < 50) {
    weights.family += 6;
    reasons.push("30〜49歳の医療・家族環境");
  } else if (age) {
    weights.future += 8;
    reasons.push("50〜64歳の長期居住");
  }
  return { weights: normalizeWeights(weights), reasons };
}

export function prioritizeAxis(weights: Record<AxisKey, number>, axis: AxisKey): Record<AxisKey, number> {
  return normalizeWeights({ ...weights, [axis]: weights[axis] + 60 });
}

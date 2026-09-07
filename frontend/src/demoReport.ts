import type {
  ApiCallTrace,
  AssessmentReport,
  AxisKey,
  AxisResult,
  MetricEvidence,
  SourceReference,
} from "./types";
import { AXIS_LABELS, AXIS_ORDER } from "./lib/report";

const SCORE_SETS: Record<string, Record<AxisKey, number>> = {
  流山市: {
    convenience: 75.5,
    housing: 52.2,
    family: 75.6,
    safety: 62.4,
    future: 82.0,
  },
  柏市: {
    convenience: 83.4,
    housing: 57.3,
    family: 72.4,
    safety: 58.1,
    future: 70.6,
  },
  武蔵野市: {
    convenience: 92.3,
    housing: 32.4,
    family: 79.5,
    safety: 68.2,
    future: 73.7,
  },
};

const REGIONS: Record<string, { prefecture: string; code: string; group: string }> = {
  流山市: { prefecture: "千葉県", code: "12220", group: "人口5万～20万人の市" },
  柏市: { prefecture: "千葉県", code: "12217", group: "人口20万～50万人の市" },
  武蔵野市: { prefecture: "東京都", code: "13203", group: "人口5万～20万人の市" },
};

const METRIC_LABELS: Record<AxisKey, [string, string]> = {
  convenience: ["駅・交通拠点の密度", "小売・サービス事業所密度"],
  housing: ["住宅取引単価中央値", "住宅地の公示地価中央値"],
  family: ["人口10万人あたり診療所数", "子ども千人あたり学校数"],
  safety: ["避難所への到達性", "災害リスクの相対値"],
  future: ["将来人口の維持率", "生産年齢人口の変化"],
};

const SOURCE_NAMES: Record<AxisKey, string> = {
  convenience: "国土交通省 不動産情報ライブラリ",
  housing: "国土交通省 不動産情報ライブラリ",
  family: "e-Stat 社会・人口統計体系",
  safety: "国土交通省 国土数値情報",
  future: "e-Stat 社会・人口統計体系",
};

function sourceFor(axis: AxisKey): SourceReference {
  return {
    source_id: `DEMO-${axis.toUpperCase()}`,
    source_name: SOURCE_NAMES[axis],
    endpoint: axis === "housing" ? "XIT001 / XPT002" : "getStatsData",
    url: "https://www.e-stat.go.jp/regional-statistics/ssdsview/",
    reference_date: axis === "housing" ? "2025" : "2023",
    commercial_use_note: "デモデータ。利用条件は本番化前に確認",
  };
}
function metricFor(
  axis: AxisKey,
  label: string,
  index: number,
  axisScore: number,
): MetricEvidence {
  const metricScore = Math.min(96, Math.max(18, axisScore + (index === 0 ? 10 : -8)));
  const source = sourceFor(axis);
  return {
    metric_code: `${axis}_${index + 1}`,
    label,
    value: Math.round((metricScore * (index === 0 ? 1.2 : 0.72)) * 10) / 10,
    unit: index === 0 ? "相対指標" : "比較対象あたり",
    normalized_score: Math.round(metricScore * 10) / 10,
    direction: axis === "housing" && index === 0 ? "lower_is_better" : "higher_is_better",
    weight: index === 0 ? 0.6 : 0.4,
    source,
    sample_count: index === 0 ? 24 + Math.round(axisScore) : null,
    quality: axis === "safety" ? 0.68 : 0.82,
    note: index === 1 ? "同規模の自治体内で相対化したデモ値" : null,
    is_mock: true,
  };
}

function axisResultFor(name: string, axis: AxisKey, score: number, index: number): AxisResult {
  const [firstLabel, secondLabel] = METRIC_LABELS[axis];
  const source = sourceFor(axis);
  const calls: ApiCallTrace[] = [
    {
      tool_name: `${axis}.get_metrics`,
      endpoint: source.endpoint,
      source_id: source.source_id,
      status: "mocked",
      elapsed_ms: 120 + index * 17,
    },
  ];
  return {
    axis,
    label: AXIS_LABELS[axis],
    score,
    confidence: axis === "safety" ? 0.42 : 0.5,
    narrative: {
      axis,
      summary: `${AXIS_LABELS[axis]}は比較基準に対して概ね${Math.round(score)}点相当です。`,
      strengths: [
        `${firstLabel}は比較対象内で相対的に${score >= 65 ? "良好" : "要確認"}`,
      ],
      cautions: [
        `${secondLabel}は生活条件によって体感差が出ます`,
        "現在はデモ値。実データ接続後に同じ計算契約で差し替えます",
      ],
    },
    metrics: [
      metricFor(axis, firstLabel, 0, score),
      metricFor(axis, secondLabel, 1, score),
    ],
    api_calls: calls,
    elapsed_ms: calls[0].elapsed_ms,
  };
}

export function createDemoReport(name: string): AssessmentReport {
  const scores = SCORE_SETS[name] ?? {
    convenience: 68,
    housing: 61,
    family: 66,
    safety: 59,
    future: 64,
  };
  const region = REGIONS[name] ?? {
    prefecture: "—",
    code: "M-DEMO",
    group: "同程度の人口規模の市区町村（デモ設定）",
  };
  const axisResults = AXIS_ORDER.map((axis, index) => axisResultFor(name, axis, scores[axis], index));
  const overallScore = Math.round(
    (axisResults.reduce((sum, result) => sum + result.score, 0) / axisResults.length) * 10,
  ) / 10;

  return {
    report_id: `demo-${name}`,
    generated_at: "2026-09-08T09:00:00+09:00",
    plan: {
      user_request: `${name}を、車なし・子育てを想定して比較`,
      region: {
        query: name,
        name,
        municipality_code: region.code,
        prefecture: region.prefecture,
        comparison_group: region.group,
        is_mock_resolution: !REGIONS[name],
      },
      weights: {
        convenience: 20,
        housing: 20,
        family: 20,
        safety: 20,
        future: 20,
      },
      enabled_axes: [...AXIS_ORDER],
      excluded_axes: [],
      agent_selection_reason: "5つの評価軸を使用",
      preferences: ["車なし", "子育て"],
      weight_reason: "5軸を均等評価",
      data_mode: "mock",
    },
    overall_score: overallScore,
    overall_confidence: 0.5,
    axis_results: axisResults,
    narrative: {
      executive_summary:
        name === "武蔵野市"
          ? "移動と生活サービスは強い一方、住まいコストが比較上の大きな制約になります。"
          : name === "柏市"
            ? "移動の選択肢と生活サービスのバランスがよく、条件次第で住まいの選択肢も残ります。"
            : "医療・子育てと将来性に強みがあり、住まいコストと交通の優先順位が判断の分かれ目です。",
      strengths: ["評価軸を分解して比較できる", "出典と基準日を追跡できる"],
      cautions: ["すべてデモ値", "市区町村平均のため町丁目差は表さない"],
      suggested_followups: ["住宅費を優先した場合の重みを試す", "候補地域を追加する"],
    },
    execution_steps: [
      {
        name: "受付・計画エージェント",
        status: "completed",
        elapsed_ms: 18,
        detail: `${name} / 5軸を使用 / 5軸を均等評価`,
      },
      {
        name: "地域データAPI群",
        status: "completed",
        elapsed_ms: 176,
        detail: "5領域・5 APIツールを並列取得",
      },
      {
        name: "5専門エージェント（並列）",
        status: "completed",
        elapsed_ms: 334,
        detail: "WorkflowBuilderのfan-out/fan-inで5専門エージェントを並列実行",
      },
      {
        name: "総合評価エージェント",
        status: "completed",
        elapsed_ms: 74,
        detail: "総合評価エージェントが要約",
      },
    ],
    total_elapsed_ms: 602,
    artifacts: { markdown_path: "", json_path: "" },
    disclaimers: ["これは開発用のデモ値です。公式評価ではありません。"],
  };
}

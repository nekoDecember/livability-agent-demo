# アーキテクチャとAPI接続境界

## 設計原則

エージェントと外部APIは1対1にしません。エージェントは利用者に説明しやすい判断軸、MCP/APIツールは判断材料です。外部APIのレスポンスを直接プロンプトへ渡さず、共通の `MetricEvidence` へ変換します。

```text
外部API → 取得・キャッシュ → 正規化指標 → MCPツール → 専門Agent
```

この境界により、将来、公開APIを社内APIへ差し替えてもエージェントプロンプトとDevUIを変更せずに済みます。

## UIとAgent APIの分離

専用フロントは同じ `LivabilityOrchestrator` を包むFastAPIへ、同一originの`/api/`プロキシ経由で接続します。APIキーはNginxのserver-side proxyで付与し、ブラウザへ渡しません。DevUIは従来どおりプロセス内の `LivabilityCoordinatorAgent` を直接表示します。
OpenWebUI Pipeは旧構成との互換用に残していますが、本番公開の入口ではありません。

```text
専用フロント ─→ Nginx /api proxy ─→ Livability Agent HTTP API ─┐
Agent Framework DevUI ─→ LivabilityCoordinatorAgent ──────────┤
旧共有OpenWebUI ─→ 互換Pipe ───────────────────────────────────┘
                                                               ├─→ LivabilityOrchestrator
                                                               ├─ WorkflowBuilder
                                                               └─ Markdown / JSON
```

配置上の不変条件:

- 専用フロントは候補地比較、評価軸、根拠、進捗を所有する。
- Livability APIは個別の認証付きHTTP APIと成果物ストレージを所有する。
- 本番ComposeではフロントとAPIだけがLivability内部networkを共有し、公開networkにはフロントだけを接続する。
- OpenWebUIとの接続は互換用のPipeだけに限定し、Livabilityは共有OpenWebUI networkへ依存しない。
- APIを外部公開する場合はBearer認証を必須とする。
- Pipe登録に使うOpenWebUI管理者キーはイメージやComposeへ埋め込まない。
- DevUIとOpenWebUIは同一のオーケストレーション・採点ロジックを使う。

APIはPipe向けの `/v1/agent/assessments` に加え、接続検証や将来の別クライアント向けに `/v1/models` と `/v1/chat/completions` も提供します。

## 動的Agent選択

`AssessmentPlan.enabled_axes` を1回の評価における構成の正本とします。構成は次の順序で決まります。

1. 指定がなければ全5軸を候補にする。
2. コードから `enabled_axes` が渡された場合は、それを候補の上限にする。
3. 会話中の「使わない」「除外」「抜き」または `除外Agent:` 指定を候補から外す。
4. 残った軸だけへウェイトを再配分し、合計を100%にする。

`AgentTeam.build_specialist_workflow()` は評価ごとに、選択された専門Agentだけを含む `WorkflowBuilder` グラフを生成します。除外軸はAPI取得、fan-out、fan-in、採点のいずれにも参加しません。

不変条件:

- 1つ以上の専門Agentが有効である。
- 使用軸と除外軸は重複せず、合わせて全5軸になる。
- ウェイトは使用軸だけに存在し、合計100%になる。
- 除外軸を実行済み・評価済みとしてレポートしない。

## エージェントと予定API

| エージェント | 内部ツール名 | 外部データ候補 |
|---|---|---|
| 移動・買い物 | `convenience.get_metrics` | e-Stat A/C、XKT015 駅別乗降客数 |
| 住まいコスト | `housing.get_metrics` | e-Stat H、XIT001 取引価格、XPT002 地価 |
| 医療・子育て | `family.get_metrics` | e-Stat E/I/J、XKT006/XKT007/XKT010 |
| 安心・防災 | `safety.get_metrics` | e-Stat K、XKT026/XKT029、XGT001 |
| まちの将来性 | `future.get_metrics` | e-Stat A、XKT013 将来人口メッシュ |

## 現在の実装レベル

| 層 | 状態 | 内容 |
|---|---|---|
| Agent Framework | 実装済み | coreのWorkflowBuilderによる明示的fan-out/fan-in、5専門Agent、総合評価Agent、DevUI用BaseAgent |
| OpenAI | 実装済み | `OpenAIChatClient`、Pydantic structured output、環境変数切替 |
| モック地域API | 実装済み | 決定論的モック、疑似並列レイテンシ、API呼び出しトレース |
| レポート | 実装済み | Markdown/JSON、根拠、出典、基準年、信頼度、実行時間 |
| Agent HTTP API | 実装済み | 認証付き専用API、OpenAI Chat Completions互換、SSE |
| OpenWebUI Pipe | 実装済み | URL/キーをValve化、手動または冪等スクリプトで登録 |
| e-Stat HTTP | 実装済み | `getMetaInfo`、`getStatsData` |
| 国交省 HTTP | 実装済み | XIT001、XIT002、任意JSONエンドポイント |
| 指標マッピング | APIキー取得後 | `cat01`、年次、欠損、自治体コードの確定 |
| GIS事前集計 | APIキー取得後 | タイル取得、市区町村界との重ね合わせ、曝露率計算 |
| MCPサーバー分離 | 次段階 | 現在のproviderメソッドをMCPツールとして公開する |

## 実データ化の順番

1. e-Stat A/C/E/H/I/J/Kを市区町村コードで取得し、共通指標テーブルを作る。
2. XIT001を追加し、住宅タイプ別の中央値と件数を作る。
3. 駅・保育施設など点データを市区町村へ集計する。
4. 洪水・土砂災害など面データを事前GIS集計する。
5. providerをMCPサーバー化し、社内APIと同じ認証・監査方式へ寄せる。

## 正規化後の想定テーブル

```text
region_code
metric_code
value
unit
reference_date
source_id
source_url
sample_count
quality
normalized_score
```

正規化スコアは同程度の人口規模の自治体内パーセンタイルを基本とし、欠損値は推測せず、軸内の残存ウェイトを再配分します。信頼度は点数とは別に保持します。

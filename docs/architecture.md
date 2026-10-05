# アーキテクチャとAPI接続境界

## 設計原則

エージェントと外部APIは1対1にしません。エージェントは利用者に説明しやすい判断軸、MCP/APIツールは判断材料です。外部APIのレスポンスを直接プロンプトへ渡さず、共通の `MetricEvidence` へ変換します。

```text
登録不要API / 公式配布ファイル / 契約API → 取得・検証 → 正規化指標 → provider → 専門Agent
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

`AgentTeam.research_axes()` は選択軸ごとにデータ取得を並列実行し、どれかが失敗した場合は残りの取得をキャンセルします。全軸の取得・検証が終わってから `build_specialist_workflow()` が選択された専門Agentだけを含む `WorkflowBuilder` のfan-out/fan-inを実行し、各Agentには自分の担当軸の証拠だけを渡します。部分的なデータのまま専門Agentを起動しません。除外軸はデータ取得、fan-out、fan-in、採点のいずれにも参加しません。

不変条件:

- 1つ以上の専門Agentが有効である。
- 使用軸と除外軸は重複せず、合わせて全5軸になる。
- ウェイトは使用軸だけに存在し、合計100%になる。
- 除外軸を実行済み・評価済みとしてレポートしない。

## 候補地比較とコマンダー

各候補地のレポートを揃えた後、`/v1/agent/comparisons` が全候補で同じ採点指標を持つ軸を比較可能な根拠として識別します。未取得値を0点に置き換えません。生活条件から作った優先度はコマンダーへ渡し、比較不能な軸の割合も含め、未測定であることが分かる形にします。

コマンダーは利用者条件、優先度、各候補の全専門Agentの要約・強み・注意点、採点対象の数値と出典時点、未取得・未検証の状態を受け取ります。コマンダー自身が最初に検討する候補を選び、理由、譲る点、次に確認する行動を説明します。候補コードは入力候補内のものに限定し、十分な根拠がなければ推薦先を返しません。LLMが利用できない場合は軸スコアの合計から推薦せず、定性的な統合判断ができないことを明示します。

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
| Agent Framework | 実装済み | coreのWorkflowBuilderによる明示的fan-out/fan-in、5専門Agent、候補比較コマンダー、DevUI用BaseAgent |
| OpenAI | 実装済み | `OpenAIChatClient`、Pydantic structured output、環境変数切替 |
| モック地域API | 実装済み | 決定論的モック、疑似並列レイテンシ、API呼び出しトレース |
| 公式公開データ | 実装済み | 登録不要API/警察庁CSVの自動同期、既存行とのマージ、公式host・ライセンスallowlist、来歴、元ファイル/正規化CSVのSHA-256、欠損除外 |
| レポート | 実装済み | Markdown/JSON、根拠、出典、基準年、信頼度、実行時間 |
| Agent HTTP API | 実装済み | 認証付き専用API、OpenAI Chat Completions互換、SSE |
| OpenWebUI Pipe | 実装済み | URL/キーをValve化、手動または冪等スクリプトで登録 |
| e-Stat HTTP | 実装済み | `getMetaInfo`、`getStatsData` |
| 国交省 HTTP | 実装済み | XIT001、XIT002、任意JSONエンドポイント |
| 契約APIの指標マッピング | APIキー取得後 | `cat01`、年次、欠損、自治体コードの確定 |
| GIS事前集計 | APIキー取得後 | タイル取得、市区町村界との重ね合わせ、曝露率計算 |
| MCPサーバー分離 | 次段階 | 現在のproviderメソッドをMCPツールとして公開する |

## 契約APIなしで実データ化する順番

1. `livability-open-data-sync` が統計ダッシュボードの登録不要APIを全地域指定で計9回呼び、10万件上限内で全国市区町村の8指標を正規化する。
2. 警察庁の固定済み公式CSV URLから交通事故本票を取得し、市区町村コードで集計する。
3. 統計ダッシュボードの指標、L02地価、P29高等教育の参考情報、照合可能な交通事故率を既存スナップショットへキー単位でマージし、元ファイル・正規化CSV・manifestのハッシュを固定する。
4. providerは評価リクエスト中にネットワークへ接続せず、manifest更新時だけ新しいローカル版へ切り替える。
5. 国土数値情報の駅・医療・防災GISなどは版と利用条件を確認して事前集計し、既存の `livability-open-data-build` で追加する。

不動産取引価格と現在の市区町村別犯罪率など、登録不要の公式配布物だけでは安全に継続取得できない指標は未取得のままにします。providerは欠損指標を `quality=0` で明示し、点数から除外しつつ信頼度へ反映します。地域について1軸全体が未収録の場合は計画時にその専門Agentを除外し、残りの軸へウェイトを再配分します。

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

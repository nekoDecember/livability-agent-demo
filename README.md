# 地域住みやすさ評価Agent

同じ市区町村・暮らしの条件で、2つの方法による回答を比較します。データ収集型は専門Agentが公式API・配布データの構造化コンテキストを使い、Web検索型は単一Agentが3〜4回の検索から得た情報を使います。回答とともに、固定できる前提、出典、未確認事項を確認できます。

検索方法と商用利用の範囲は[比較の設計](docs/context-comparison.md)、専門Agentの分担は[ワークフロー設計](docs/workflows.md)を参照してください。

## 起動

```sh
cp .env.backend.example .env.backend
openssl rand -hex 32
# 出力を.env.backendのLIVABILITY_API_KEYへ設定します。
docker compose --env-file .env.backend up --build -d --wait
```

APIは8091番で待ち受けます。

## LANアクセスとHTTPプロキシ

Composeの専用フロントはホストの`0.0.0.0`で待ち受けます。同じLAN上の端末からは、MacのLAN IPと`.env.backend`の`LIVABILITY_FRONTEND_PORT`を使い、`http://<MacのLAN IP>:<ポート>`を開きます。APIのホスト側ポートは`127.0.0.1`に限定され、ブラウザからはフロントの同一オリジンAPI経由で接続します。内部APIキーはブラウザへ渡りません。

Cloudflare経由の公開には、既存のgateway構成を引き続き使います。`compose.public.yaml`はAPIのホスト側ポート公開を無効にし、フロントのLANポート公開はbase構成から引き継ぎます。これにより同じフロントをLANと既存のCloudflare経路で利用できます。gatewayのルーティングや監視設定はこのCompose overlayでは変更しません。

外向きHTTP通信にプロキシが必要な場合は、`.env.backend`または`docker compose`を実行するシェルで`http_proxy`/`HTTP_PROXY`、`https_proxy`/`HTTPS_PROXY`を設定します。どちらの大文字小文字も使え、両方ある場合は小文字側を優先します。HTTPS用を省略するとHTTP用の値を使い、HTTPS用を明示した場合はそちらを優先します。`ALL_PROXY`/`all_proxy`も指定できます。空欄は未設定として扱います。

`NO_PROXY`と`no_proxy`は追加分を結合し、`localhost`、loopbackアドレス、`livability-agent-api`、`openwebui`を常に除外します。Composeはこの設定をAPI・公式データ同期・登録用コンテナの実行時環境と、Dockerfile内のパッケージ取得へ渡します。Docker Desktopが`FROM`のベースイメージを取得する通信はDocker daemon側の通信なので、build引数とは別にDocker Desktopのdaemon proxyを設定してください。詳しくは[Dockerのproxy build arguments](https://docs.docker.com/build/building/variables/#proxy-arguments)を参照してください。

## 専用の比較フロント

専用フロントでは、条件入力から2方式の回答比較へ進みます。回答の理由と、データの対象年・単位・欠損、検索回数・出典を並べて確認できます。

```sh
cd frontend
npm install
npm run dev
```

ブラウザで`http://localhost:5173`を開きます。APIが起動していない場合も、開発用モックで画面を確認できます。

APIを起動した状態では、UIは`/v1/agent/assessments/stream`のSSEから実際の進捗を受け取ります。

APIとフロントをComposeでまとめて起動する場合は、プロジェクトルートで実行します。

```sh
docker compose --env-file .env.backend up --build -d --wait
```

本番では、専用フロントをCloudflareへ公開します。フロントの`/api/`プロキシから
内部APIへ接続するため、Livability API自体は公開networkへ接続しません。

## OpenWebUI互換接続

旧構成との互換性のため、OpenWebUI Pipeも残しています。
通常の本番公開経路は専用フロントであり、Livabilityを共有OpenWebUIへ接続しません。
互換構成を使う場合は、先にOpenWebUI基盤を起動します。
このAgentは外部ネットワーク`openwebui-agent-network`に接続します。
APIの名前は`livability-agent-api`です。

```sh
# .env.backendのOPENWEBUI_ADMIN_ENV_FILEへ、OpenWebUI基盤の
# .env.adminの絶対パスを設定してから実行します。
docker compose --env-file .env.backend \
  -f compose.yaml -f compose.openwebui.override.yaml \
  up --build -d
```

起動時に登録用コンテナがOpenWebUIへ管理者としてサインインし、
`livability_agent` Pipeを作成または更新して有効化します。
管理者情報はOpenWebUI基盤のGit管理外ファイル`.env.admin`から読みます。
これは共有基盤側の資格情報で、このAgentの設定ファイルではありません。
パスに既定値はありません。別の場所へcloneしても動くよう、
`OPENWEBUI_ADMIN_ENV_FILE`で明示します。
Compose内の`/registration`は登録用コンテナのパスです。
ホストに`registration`ディレクトリを作る必要はありません。
`openwebui/livability_agent_pipe.py`と`scripts/register_openwebui_pipe.py`をそこへmountします。

APIキーとOpenWebUI登録を含む、このAgentの設定はすべて`.env.backend`に書きます。
このAgentの実ファイルは`.env.backend`の1つだけで、Git管理しません。
Docker Composeでは必ず`--env-file .env.backend`を付けます。
これにより、ポートなどのCompose変数とコンテナ内の設定が同じファイルから読み込まれます。
`uv run livability-*`も同じ`.env.backend`を読みます。

## 回答比較と保存

既定の「2方式を比較」は、同じ候補と暮らしの条件をデータ収集型とWeb検索型へ渡します。データ収集型は構造化された地域データを専門Agentが分析し、コマンダーが統合します。Web検索型はOpenAIのWeb検索を候補全体で3回（設定で4回）実行し、その検索記録から単一Agentが回答します。データ側の指標や回答はWeb検索型へ渡しません。

検索対象は商用利用条件を確認した政府統計・国土交通省・警察庁の公式ドメインです。出典リンクを表示し、本文の大量転載は行いません。第三者の権利や追加利用条件まで一律に許諾されることを意味しません。詳細は[比較の設計](docs/context-comparison.md)を参照してください。

```sh
# .env.backend
LLM_MODE=openai
OPENAI_API_KEY=<APIキー>
WEB_SEARCH_ROUNDS=3
WEB_SEARCH_TIMEOUT_SECONDS=180
# 必要に応じて検索に対応したモデルを指定
# WEB_SEARCH_MODEL=<モデル名>
```

検索に失敗した場合は失敗理由を表示し、内在知識で代替しません。`LLM_MODE=mock`では検索を実行せず、画面にも未実行と表示します。データ収集型だけを実行するモードも残しています。旧APIの`knowledge_only`と以前の保存履歴は互換性のため残しますが、Web検索型とは区別します。

比較後は実行時の条件と優先度を固定して表示します。条件を変える場合は入力へ戻り、両方式を再実行してください。対象地域は一覧から2〜4件、分析の視点は最低1軸を選びます。通勤時間、物件の空き、保育の空きなど、取得していない条件は次に確認する事項として示します。

比較結果はブラウザへ最新10件を自動保存し、保存履歴から再表示できます。HTML、Markdown、JSON、プレゼンHTMLには両方式の回答と前提・出典を含めます。サーバー側の既存成果物保存も継続します。別端末へ自動同期しないため、長期保管にはダウンロードを使ってください。

## 契約APIなしの公式公開データモード

`DATA_MODE=open_data`では、契約やAPIキーを必要とするe-Stat API・不動産情報ライブラリAPIを使いません。起動前の同期コンテナが、利用登録不要と明記された統計ダッシュボードAPIと警察庁の公式交通事故CSVを取得し、検証済みローカルスナップショットへ変換します。評価リクエスト中は外部通信せず、既存の5軸providerと採点ロジックをそのまま使います。HTMLスクレイピング、ブラウザ自動操作、非公開エンドポイントは使いません。

自動同期は全国市区町村が既定です。統計ダッシュボードAPIの全地域取得機能を使い、自治体ごとの連続リクエストは行いません。小売密度、昼夜間人口比、住宅ストック、診療所数、保育・学校密度、2040/2050年人口維持率に加え、国土数値情報L02から住宅地の基準地点価格中央値を取得します。P29から大学・短大・高専の掲載キャンパス数も同期しますが、状態未確認のため高等教育を明示的に重視する条件のときだけ背景情報として表示し、採点しません。警察庁CSVは自治体コードに照合できた地域だけを採用します。高崎市と前橋市は採点可能な22指標中9指標が実測済みで、安心・防災は0/5です。L02値は家賃や売買成約価格ではありません。既存スナップショットにある駅・防災などの公式データ行は競合しない限り保持します。不動産取引価格と現在の市区町村別犯罪率は、登録不要の公式な継続取得経路を確認できていないため自動補完しません。

受け入れるライセンスは `CC-BY-4.0`、`PDL1.0`、または利用規約で商用利用可を個別確認した `COMMERCIAL-USE-ALLOWED` に限定します。配布ページは原則として `.go.jp` / `.lg.jp` のHTTPS URL、利用条件URL・出典・第三者権利の確認結果・取得日・加工内容・元ファイルSHA-256を必須にしています。正規化CSVと保存した元レスポンスも読み込み時にSHA-256を再照合します。

自動同期の最小設定は次のとおりです。Compose起動時に同期は1回だけ動き、7日以内の有効な全国スナップショットがあれば再取得しません。更新に失敗しても前回版が検証できれば継続し、初回取得にも失敗した場合はAPIを起動しません。

```sh
# .env.backend
DATA_MODE=open_data
OPEN_DATA_SYNC_SCOPE=nationwide

docker compose --env-file .env.backend up --build -d --wait
```

ローカルで全国同期だけを実行する場合は `uv run livability-open-data-sync --all-regions`、現在版を無視して再取得する場合は `uv run livability-open-data-sync --all-regions --force-refresh` を使います。検証や小規模運用だけに絞る場合は `--region 流山市` を指定できます。警察庁CSVは約60MBあるため、通常はキャッシュ有効期限内の再取得を避けてください。追加の公式配布物を手動正規化して併用する `livability-open-data-build` も残しています。詳しい設定とCSV契約は [open_data/README.md](open_data/README.md) を参照してください。

スナップショットにない指標は0点や推測値で埋めず「未取得」と表示し、軸内の残存指標へウェイトを再配分します。欠損分は信頼度を下げます。公開元に1軸分のデータがまったくない地域では、その軸を明示して除外し、残りの軸へ総合ウェイトを再配分します。

未取得の状態は「指標自体が今回のデータ集に未収録」「指標は収録されているが、この自治体の値がない」「照合できず採点から除外」に分けます。これは**今回のスナップショット内**で分かる範囲の区別であり、元データに指標が存在しないことの証明ではありません。実測の0は未取得と区別します。画面と保存レポートの両方に状態を示します。

公式ページ:

- [統計ダッシュボード API](https://dashboard.e-stat.go.jp/static/api)
- [統計ダッシュボード コピーライトポリシー](https://dashboard.e-stat.go.jp/static/terms)
- [警察庁 交通事故統計情報のオープンデータ](https://www.npa.go.jp/publications/statistics/koutsuu/opendata/index_opendata.html)
- [国土数値情報・都道府県地価調査（2026年）](https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-L02-2026.html)
- [国土数値情報・学校（2023年）](https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-P29-2023.html)
- [国土数値情報 利用約款](https://nlftp.mlit.go.jp/ksj/other/agreement.html)
- [国土数値情報ダウンロードサイト](https://nlftp.mlit.go.jp/ksj/gml/gml_datalist.html)

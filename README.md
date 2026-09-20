# 地域住みやすさ評価Agent

入力した市区町村を、交通、買い物、住まい、医療、防災などの視点で評価します。
APIキーを使わないモックモードがあります。

専門agentの分担・検証・失敗時の扱いは[ワークフロー設計](docs/workflows.md)を参照してください。

## 起動

```sh
cp .env.backend.example .env.backend
openssl rand -hex 32
# 出力を.env.backendのLIVABILITY_API_KEYへ設定します。
docker compose --env-file .env.backend up --build -d --wait
```

APIは8091番で待ち受けます。

## 専用の比較フロント

OpenWebUIの会話画面とは別に、候補地・評価軸・根拠・実行記録を横並びで確認する専用フロントがあります。

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

## 調査結果の保存・出力

専用フロントで比較が完了すると、「この条件ならどの候補を第一候補にするか」を先頭に、
候補ランキング・5軸比較・根拠・出典・注意点を一つの意思決定レポートにまとめ、
このブラウザへ最新10件を自動保存します。
画面下の「意思決定レポート」から、色付きで印刷しやすい単体HTML、Markdown、JSONをダウンロードし、
「保存履歴」から再表示できます。
APIの元MarkdownとJSONレポートも保存データに含み、サーバー側の既存成果物保存も継続します。
重みを変更した場合は「現在の重みで保存」で新しい版を保存してください。
ダウンロード対象は直前に保存した版です。
API接続失敗時のデモ代替結果やモックデータには、その旨を明記します。
ブラウザの保存容量不足時は通知を表示し、その場でダウンロードできます。
別端末への同期はなく、ブラウザデータの消去で履歴は失われるため、長期保管にはダウンロードを使います。

## 評価モード

画面の「評価モード」で、次の2つをリクエスト単位に切り替えられます。

- 「外部データあり」: サーバーの`DATA_MODE`設定に従い、地域データプロバイダーを使います。
- 「LLM知識だけ」: 地域データAPI・検索をスキップし、LLMの一般知識だけで粗い比較用スコアを作ります。

後者はLLM自体を呼ぶため、OpenAI設定時はサーバーからLLM APIへ通信します。
APIキーがない`LLM_MODE=mock`では、同じ表示契約のオフライン代替を使い、「LLM知識のみ」とは区別して実行記録に残します。
最新統計や町丁目差を保証するモードではないため、方向性を決めた後は一次情報で確認してください。

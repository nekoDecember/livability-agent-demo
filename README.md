# 地域住みやすさ評価Agent

入力した市区町村を、交通、買い物、住まい、医療、防災などの視点で評価します。
APIキーを使わないモックモードがあります。

## 起動

```sh
cp .env.backend.example .env.backend
docker compose up --build -d
```

APIは8091番で待ち受けます。

## OpenWebUIとの接続

共有OpenWebUIを使う場合は、先にOpenWebUI基盤を起動します。
このAgentは外部ネットワーク`openwebui-agent-network`に接続します。
APIの名前は`livability-agent-api`です。

```sh
docker compose -f compose.yaml -f compose.openwebui.override.yaml up --build -d
```

起動時に登録用コンテナがOpenWebUIへ管理者としてサインインし、
`livability_agent` Pipeを作成または更新して有効化します。
管理者情報はOpenWebUI基盤のGit管理外ファイル`.env.admin`から読みます。
これは共有基盤側の資格情報で、このAgentの設定ファイルではありません。

APIキーとOpenWebUI登録を含む、このAgentの設定はすべて`.env.backend`に書きます。
このAgentの実ファイルは`.env.backend`の1つだけで、Git管理しません。
Docker Composeと`uv run livability-*`のどちらも同じ`.env.backend`を読みます。

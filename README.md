# 地域住みやすさ評価Agent

入力した市区町村を、交通、買い物、住まい、医療、防災などの視点で評価します。
APIキーを使わないモックモードがあります。

## 起動

```sh
cp .env.backend.example .env.backend
openssl rand -hex 32
# 出力を.env.backendのLIVABILITY_API_KEYへ設定します。
docker compose --env-file .env.backend up --build -d --wait
```

APIは8091番で待ち受けます。

## OpenWebUIとの接続

共有OpenWebUIを使う場合は、先にOpenWebUI基盤を起動します。
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

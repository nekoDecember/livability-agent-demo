# 公式公開データ・スナップショット

`DATA_MODE=open_data` は、このディレクトリの `manifest.json` と `metrics.csv` を読みます。評価リクエスト中に外部通信は行いません。公式データの取得は、APIの起動前に別の同期コマンドで行います。

## 自動同期

次の3ソースを自動取得します。どれも公式に公開され、利用登録は不要です。

- 統計ダッシュボードの公開Web API: 自治体解決と8指標。PDL1.0
- 警察庁「交通事故統計情報のオープンデータ」本票CSV: 交通事故率。PDL1.0
- 国土交通省 国土数値情報「都道府県地価調査」2026年全国GeoJSON ZIP: 住宅地の基準地点価格中央値。CC BY 4.0

HTML解析、検索結果からのURL抽出、ブラウザ自動操作、非公開エンドポイントは使いません。警察庁CSVのURLと基準年は、公式年次ページで確認した値を設定に固定します。翌年版へ切り替える場合は、公式ページで公開を確認してから `OPEN_DATA_TRAFFIC_CSV_URL` と `OPEN_DATA_TRAFFIC_REFERENCE_YEAR` を更新してください。

既定の `nationwide` スコープでは、統計ダッシュボードAPIの `RegionCode` を省略した公式の全地域取得機能を使います。APIの10万件上限に合わせ、地域一覧1回と指標グループ8回へ分割し、警察庁CSVと国土数値情報L02全国ZIPを各1回取得して全国スナップショットを作ります。自治体数分のリクエストは行いません。

```sh
cp .env.backend.example .env.backend

# .env.backend
DATA_MODE=open_data
OPEN_DATA_SYNC_SCOPE=nationwide

# 全国同期だけを実行
uv run livability-open-data-sync --all-regions

# 有効期限内のスナップショットも再取得
uv run livability-open-data-sync --all-regions --force-refresh
```

動作確認などで対象を限定する場合だけ、`OPEN_DATA_SYNC_SCOPE=selected` と `OPEN_DATA_SYNC_REGIONS=流山市,柏市`、またはコマンドの `--region 流山市` を使います。

Composeでは `livability-open-data-sync` が起動前に1回実行され、APIコンテナは完了後に読み取り専用でスナップショットをマウントします。同期コンテナだけが `./open_data` へ書き込みます。Linuxでは通常 `LIVABILITY_HOST_UID=1000` / `LIVABILITY_HOST_GID=1000`、macOSでは `id -u` と `id -g` の結果を `.env.backend` に設定してください。

7日以内の有効な全国スナップショットと必要ソースが揃っていれば、ネットワーク取得を省略します。更新に失敗した場合、`--allow-stale` は前回版のスキーマとSHA-256が検証できたときだけ成功扱いにします。初回取得に失敗した場合は空データや推測値で起動しません。

自動同期は既存データを全削除しません。同じ自治体・同じ指標だけを新しい値で置換し、それ以外の駅・地価・防災などの行と出典を保持します。実行中のproviderも `manifest.json` の更新を検知して、次の評価から新しいスナップショットを読みます。

## 自動作成する指標

| 軸 | 指標 | 加工 |
|---|---|---|
| 移動・買い物 | `retail_density` | 小売事業所数 ÷ 最も近い基準年の人口 |
| 移動・買い物 | `daytime_population_ratio` | 公開値をそのまま利用 |
| 住まい | `housing_stock` | 住宅数 ÷ 一般世帯数。住宅数が非公表の地域は住宅に住む一般世帯数による低品質の代理指標 |
| 住まい | `residential_land_price` | L02の用途区分000（住宅地）を自治体コードで集計した基準地価格中央値。2026年7月1日時点、単位円/㎡。家賃・売買成約価格ではない |
| 医療・子育て | `clinics_per_100k` | 公開済み人口10万人当たり値 |
| 医療・子育て | `nursery_per_1000_children` | 保育所等数 ÷ 0〜14歳人口 |
| 医療・子育て | `schools_per_1000_children` | 小・中・高等学校数 ÷ 0〜14歳人口 |
| 安心・防災 | `traffic_accidents` | 警察庁本票の事故件数 ÷ 2020年人口 |
| 将来性 | `population_retention_2040` | 2040年推計人口 ÷ 2020年実績人口 |
| 将来性 | `population_retention_2050` | 2050年推計人口 ÷ 2020年実績人口 |

異なる基準年を組み合わせた指標と代理指標は `metrics.csv` の `note` に明記します。取得値は0〜100点へ直接変換せず、既存の `METRIC_CATALOG` と採点処理へ生値を渡します。

## 受け入れる追加データ

自動同期の対象外でも、次の条件を満たす公式配布物は既存の手動ビルダーで追加できます。

- 国・自治体の公式ダウンロード機能から取得したファイル
- 配布元が `.go.jp` または `.lg.jp` のHTTPSページ
- `CC-BY-4.0`、`PDL1.0`、または商用利用可を個別確認した `COMMERCIAL-USE-ALLOWED`
- 出典、利用条件URL、第三者権利の確認結果、取得日、基準時点、加工内容、元ファイルのSHA-256を記録できるもの

非商用限定、一部用途制限、第三者権利が未確認、利用条件が不明なデータは入れません。

```sh
uv run livability-open-data-build \
  --metrics /path/to/work/metrics.csv \
  --sources /path/to/work/sources.json \
  --output open_data
```

`metrics.example.csv` と `sources.example.json` は入力形式の例です。記載された数値・自治体・取得日は架空であり、実行用データではありません。

## `metrics.csv` の契約

列は例ファイルと完全一致させます。`metric_code` は既存の `METRIC_CATALOG` のコード、`axis` はその所属軸を指定します。CSVには生値だけを書きます。

収録しない指標は行自体を作りません。実行時に「未取得（採点対象外）」として補われ、同じ軸の残存ウェイトだけで点数を計算し、欠損分は信頼度を下げます。1軸に利用可能な指標が1件もない場合は、その軸を推測せず除外し、残りの評価軸へ総合ウェイトを再配分します。

許可ホストやライセンス種別を追加する場合は、利用条件を確認したうえでコードレビューを伴う変更にします。設定値だけで検証を迂回する仕組みはありません。

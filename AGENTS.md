# 仕組み

オルカン（eMAXIS Slim 全世界株式）の構成銘柄と組入比率を推計して、静的ページとして GitHub Pages に出す。
公開ページは https://kazunari-shibata.github.io/orukan-tracker/ 。

推計なので、iShares が公表する実際の組入比率とは一致しない。ここに書いてあるのは
「どう推計しているか」と「なぜそうしてあるか」で、作業の前に読む。

## 構成

| パス | 何 |
|---|---|
| `.github/workflows/update.yml` | 毎日の更新。GitHub がこのパスしか見ないので動かせない |
| `tools/cron/` | 更新を時刻どおりに始める Cloudflare Worker |
| `tools/build.py` | 組入比率の推計。`history/<日付>.csv` と `.meta.json` を書く |
| `tools/nav.py` | 基準価額の推移と、最新の終値での推計。`nav.json` を書く |
| `tools/render.py` | `index.template.html` を埋めて `index.html` と `rows/` を書く |
| `tools/symbols.py` | iShares のティッカー/取引所 → Yahoo のシンボル、取引所 → 通貨と除数 |
| `tools/labels.py` | 国・業種の日本語名と国旗（表示用） |
| `tools/logos.py` | 企業ロゴを `logos/` に取得する（手で実行してコミットする） |
| `index.template.html` | ページのひな形。`<!--ssg:名前-->` が差し込み口 |
| `logos/` | 取得済みのロゴ。git に入る唯一の生成物 |

生成物（`history/`、`nav.json`、`index.html`、`rows/`、`_site/`）は git に入れない。
Actions が毎回作り直す。

## 毎日の流れ

1. 06:20 JST に Cloudflare Worker `orukan-cron` が `workflow_dispatch` を叩く（下記）
2. 前回の `history/` を Actions のキャッシュから復元する（`history-` プレフィックスで最新を拾う）
3. `build.py` が当日の CSV を書く
4. 最新7日ぶんだけ残して古い CSV を捨てる（週末や連休をまたいで前日比を出すため。下の「前日比と NEW」）
5. `nav.py`（`--history history` で推計と答え合わせも）が `nav.json` と `history/nav-estimates.json` を書く
6. `history/` を `history-<run_id>` として保存し直す
7. `render.py` でページを作り、`logos/` を `_site/` に足す
8. `upload-pages-artifact` → `deploy-pages` で GitHub Pages に出す

前日比（順位の上げ下げ）に前回の CSV が要るが、毎日のデータは git にもファイルにも残さず
Actions のキャッシュにだけ置く。キャッシュが無い日は前日比なしで出す。

`concurrency: { group: update, cancel-in-progress: true }` を付けてあるので、
手動実行と重なったら古いほうが打ち切られる。

## 組入比率の推計

iShares の保有銘柄ファイル（数量・為替・評価額）を土台に、株価だけ Yahoo の直近値に差し替える。

1. 保有ファイルの全行の `Market Value` を合計して `fund_total`（差し替え前のファンド総額）にする
2. `Asset Class == "Equity"` かつ `Weight (%) > 0` の行を組入比率の大きい順に並べる
3. 各行の現地通貨建て株価を `Price × FX Rate` で出す（`Currency` 列は全銘柄 USD 換算済みなので使えない）
4. 株価を Yahoo の値に差し替えて `mv = Quantity × price / fx` を出す
5. 差額の合計を `fund_total` に足して `total` にし、`weight = mv / total × 100` とする
6. `weight` の大きい順に並べ直して順位を振る

社名の日本語化や、同じ会社の複数クラス株の名寄せはしない。保有ファイルの1行を1銘柄として載せる。
会社単位にまとめる手がかりがファイルに無く、名前だけでまとめると別会社の同名銘柄まで巻き込むため。

`.meta.json` の `universe.securities` は評価額が残っている銘柄数、`regions` はその `Location` の数。
評価額ゼロで残っているだけの銘柄（ロシア株など）は数えない。

## 表示モード（速報値 / 公表値）

ページの上のボタンで切り替える。選んだモードはブラウザの `localStorage` に残す。
どちらのモードでも、表示する数字が同じ相場の日の値になるように分けてある。

| | 速報値（既定） | 公表値 |
|---|---|---|
| 株価・前日比 | Yahoo の最新の終値と前日終値 | 保有ファイルの株価と、前の日付の保有ファイルの株価 |
| 組入比率・順位 | 推計（上の節） | 保有ファイルの `Weight (%)` の大きい順 |
| 時価総額 | Yahoo の値 | Yahoo の値を保有ファイルの株価と為替で引き直した値 |
| 基準価額 | 推計（下の節）。出せなければ公表値 | 公表値 |
| グラフ | 公表値の推移の後ろに推計値を1点足す | 公表値の推移 |

オルカンの基準価額は前日の海外の終値で計算されるので、平日なら「公表された基準価額」と
「前営業日の保有ファイル」が同じ相場の日を指す。06:20 JST の時点で iShares の保有ファイルは
前営業日の分（その日の終値の分はまだ出ていない）なので、公表値モードはそろう。
祝日をまたぐとずれるので、どちらのモードも各数字がいつの値かを切り替えボタンの横に出す。

ファイルは増やさない。両方のモードの中身を同じ `index.html` と `rows/*.json` に入れ、
`html` の `data-mode` で片方を隠す（`.m-live` / `.m-pub`）。表の並び順がモードで違うので、
`rows/<ページ>.json` は `{"rows": 速報値の行 HTML, "pub": 公表値の行 HTML}` を持つ。

## 基準価額の推計

```
推計 ＝ 公表済みの基準価額 × 評価額の変化 × 今のドル円 ÷ 公表日の TTM
```

- 評価額の変化は、公表済みの基準価額が使った相場の日（公表日より前の最後の米国の取引日）から
  最新の終値の日まで。`build.py` が米国の日付ごとの水準を毎回作り直し、`meta.json` の `levels` に
  入れる（`acwi_levels`）。保有ファイルの日付までは、同じ保有ファイルの Historical シートにある
  ACWI の `Non-FV NAV`（各市場の終値で計算した NAV。`NAV per Share` は米国の引けに合わせて
  海外株を補正しているのでオルカンと合わない）の直近30取引日（`LEVELS_KEEP`）、
  そこから最新の終値までは `total / fund_total`。
  ACWI は年2回分配してその日の NAV が下がるが、オルカンは分配しないので、権利落ち日の行の
  `Ex-Dividends` を足し戻す（オルカンの受け取る配当は源泉税のぶん少ないので、わずかに上振れる）。
  過去の水準をためないので、キャッシュが消えた日や連休明けでも推計を出せる。
  Historical シートに保有ファイルの日付の行が無ければ `::warning::` を出して推計を出さない
- 今のドル円は、株価と同じまとめ取得の `JPY=X`。Yahoo への取得は増えない
- TTM は三菱UFJ銀行の公表相場（`nav.py` の `MUFG_TTM_URL`）の米ドルの (TTS + TTB) / 2。
  オルカンは当日の TTM で円に直すので、公表日の TTM で割って戻す
- 推計できない（TTM が取れない、水準が足りない、公表値から15%以上離れた）日は `::warning::` を出し、
  速報値モードも公表値を出す

2023-12〜2026-09 の556営業日で、ACWI の Non-FV NAV を評価額の代わりに使ったバックテストでは、
公表値との差の中央値が 0.13%、9割の日が 0.39% 以内、前日比の向きの一致が 91.5%。
差の大半は、06:20 から TTM が決まる 10時ごろまでのドル円の動きと、日本株（約5%）だけが
当日の終値で評価されることによる。公表値とは一致しないので、画面には「推計」と出す。

分配金の足し戻しは、2021-06〜2026-06 の ACWI の権利落ち日12回をまたぐ公表日で確かめた
（ドル円の動きを外すため、今のドル円の代わりに公表日の TTM を使った）。足し戻さないと
推計が 0.2〜1.0% 下振れ（多くは 0.7〜1.0%）、足し戻すと 0.3% 以内（中央値 0.07%）に収まる。前後の公表日の誤差の中央値は 0.06%。

## 推計の答え合わせ

直前の推計を、同じ相場の日の公表データと1回ずつ比べる。長く貯めはしない（キャッシュの2日ぶんで足りる）。
結果は Actions の実行画面に `::notice::` で出る。

- 組入比率（`build.py` の `check_weights`）: 前回の CSV の `weight` と順位を、今回の保有ファイルの
  `Weight (%)` と順位で比べ、`meta.json` の `check` に入れる。前回の `pricesAsOf` が今回の
  `holdingsAsOf` と同じ日だけ。全銘柄と上位100銘柄（`CHECK_TOP`、実際の順位で数える）のそれぞれで、
  比率の誤差の絶対値の合計と最大、順位の一致率、順位のずれの中央値。比率の誤差から `stale` の行は外す
- 基準価額（`nav.py` の `check_estimates`）: 出した推計を `history/nav-estimates.json` に10件残し、
  公表日の相場の日（`estimate()` の `base_day` と同じ引き方）と `pricesAsOf` が一致する推計を探して
  `nav.json` の `check` に入れる。土日の朝の推計は3回とも月曜分と比べる。
  日本の平日の祝日の朝の推計は、その日の終値を使う基準価額が無いので答え合わせされない

答え合わせの相手は iShares の保有ファイルとオルカンの公表値。組入比率はオルカンの実際の比率ではない。

## 株価の取得

`query2.finance.yahoo.com/v7/finance/quote` に 150 件ずつ投げる（`QUOTE_CHUNK`）。
認証（crumb）は `yfinance.data.YfData` に任せている。全銘柄（約2,200件）でも十数回で済む。

- 為替も同じまとめ取得で拾う（`JPY=X` など）。JPY は基準価額の推計に使うので日本株が無くても要る
- Yahoo はたまに応答から数件落とすので、欠けた分だけもう一度まとめて取る
- それでも欠けた銘柄は、組入比率の上位 100 件（`EACH_LIMIT`）だけ `fast_info` で1銘柄ずつ取り直す
- 前日終値は `regularMarketPreviousClose` を使う。日足から拾うと、当日分の行が NaN のまま
  先に生えている銘柄で前々日を掴む
- 株価と時価総額の単位が違う。まとめ取得はロンドンでも時価総額がポンド建てで返るので
  除数で割らないが、`fast_info` はペンス建てなので割る

### 止まる条件と、止めない条件

取得の仕組みそのものが壊れたときは止める。1銘柄ずつの取得に黙って切り替えるとアクセスが
100倍になり、気づかないままアクセス制限にかかるため。

- `yfinance.data.YfData` が無い、まとめ取得が例外、応答が空 → 止める
- 半分以上の銘柄で株価が欠けた → 止める（応答の項目名が変わった可能性）
- 基準価額が取れない → **止めない**。`::warning::` を出して基準価額の欄を隠して出す

止まった日は CSV が書かれないので、ページは前日のまま残る。

### 使わない値

おかしい値でページを汚さないよう、次は捨てて保有ファイルの株価に落とす（`stale` が立つ）。

- Yahoo の建値の通貨が取引所から決めた想定と違う銘柄（米ドル建てで上場している海外株など）
- 保有ファイルの株価と 25% 以上離れている銘柄（エジプト株は4割ほど違う）
- 為替が保有ファイルの `FX Rate` と 25% 以上離れていたら、ファイルの値を使う

## 通貨と単位

保有ファイルの `Currency` 列は全銘柄 USD なので、現地通貨は取引所から決める
（`symbols.py` の `EXCHANGE_META`）。Yahoo が補助単位で返す市場は除数を持つ。

| 市場 | 通貨 | 除数 |
|---|---|---|
| ロンドン | GBP | 100（ペンス） |
| ヨハネスブルグ | ZAR | 100（セント） |
| テルアビブ | ILS | 100（アゴラ） |
| クウェート | KWD | 1000（フィルス） |

時価総額は USD で持ち、ドルのまま出す。円に直すと為替の動きが前日比に混ざるため。

## シンボル変換（`symbols.py`）

取引所名から Yahoo のサフィックスを引く。変換できない取引所・ティッカーは `None` を返し、
呼び出し側がファイルの株価を使う。

- 米国は無サフィックス。空白をハイフンに（`BRK B` → `BRK-B`）
- 香港は4桁、韓国は6桁にゼロ埋め（`700` → `0700.HK`）
- `Nasdaq Omx Nordic` は取引所名が同じで国ごとにサフィックスが違う（`NORDIC`）
- 取引所ごとの付記を落とす。ロンドン `RR.`、メキシコ `WALMEX*`、タイの無議決権預託証券 `PTT.R`、
  トルコ `ASELS.E`。残った `.` はハイフンに（`BT.A` → `BT-A.L`）
- ティッカーが Yahoo と食い違う数銘柄だけ `OVERRIDE` で手当てする
- Yahoo で引けないのでサフィックスを持たない市場: マレーシア（Yahoo は数字コード）、
  アブダビ / ドバイ、フィリピン（データが無い）

## 出力

### `history/<日付>.csv`

日付は日本時間。同じ日に再実行すると上書きする。列は `build.py` の `CSV_FIELDS` の順で、
`render.py` もこの順で読む。

```
rank, symbol, ticker, name, sector, country, currency,
price, prev_close, market_cap_usd, base_weight, weight, stale,
base_price, base_prev_price, base_market_cap_usd
```

`symbol` は Yahoo のシンボル、引けない銘柄は `<ティッカー>@<取引所>`。ロゴのファイル名にも使う。
`base_weight` は保有ファイルの `Weight (%)`、`weight` が推計値。`stale` は株価を差し替えられなかった行。
`base_price` 以降は公表値モード用。`base_price` は保有ファイルの現地通貨建て株価、`base_prev_price` は
前の日付の保有ファイルの株価（保有ファイルが前回と同じ日付なら、前回の比較相手を引き継ぐ）、
`base_market_cap_usd` は時価総額を保有ファイルの株価と為替で引き直した値。

### `history/<日付>.meta.json`

`date`、`generatedAt`、`holdingsAsOf`（保有ファイルの日付）、`count`、`universe`、
`coveredWeight`、`fx`、`levels`（基準価額の推計用。上の節）、`check`（前回の推計の答え合わせ）、
それに `pricesAsOf` と `usMarketState`。

`pricesAsOf` は米国株の株価がいつの値かを多数決で決めた米国東部時間の日付。
`usMarketState` が `REGULAR` なら取引時間中の株価（終値ではない）なので答え合わせには使えない。

### `nav.json`

投資信託協会の投信総合検索ライブラリーの CSV（Shift-JIS、`Content-Type` は utf-8 を名乗る）から作る。
三菱UFJアセットマネジメントの CSV と API は Actions の IP を 403 で弾くので使わない
（値は 2018-10-31 以降の全営業日で一致を確認済み。投資信託協会のほうが反映は遅いことがある）。

分配金再投資ベースの列は無いが、オルカンは無分配なので基準価額と一致する。

`--history` を渡すと `estimate`（推計値、公表値との差、どの日の終値で推計したか、使った TTM とドル円）と
`check`（前の推計の答え合わせ。上の節）も入れる。

## ページ生成（`render.py`）

`index.template.html` の `<!--ssg:名前--> 〜 <!--/ssg:名前-->` を埋める。
マーカーが無ければ止まる。差し込み口は `modebar`、`navhead`、`rows`、`rowspub`、`countries`、`mix`。

- 表は100銘柄ずつ（`PAGE_SIZE`）。1ページ目だけ `index.html` に直接書き、
  全ページぶんの行 HTML を `rows/<ページ>.json` に書く（2ページ目以降はブラウザが読む）
- 国・地域で絞ったページは `rows/c/<国コード>/<ページ>.json`。順位は全体の順位のまま。
  国が減ったときに古いファイルが残らないよう、毎回作り直す
- 銘柄検索の索引は `rows/search.json`（`[順位, ティッカー, 名前, 組入比率, 公表値モードの順位, 公表値モードの組入比率]`）。
  検索欄を使ったときだけ読む
- 国コードは国旗の絵文字から作る（🇯🇵 → JP）
- 積み上げ棒は上位7項目（`MIX_TOP`）に色を付け、残りは「その他」にまとめる（色が8色まで）
- ロゴは `logos/` にファイルがある銘柄だけ `img` を出す。無い銘柄（大半）で 404 を量産しないため
- ロゴは企業名とは別の列にして、横スクロールしても左端に残す（`position:sticky`）。
  右の列を見ているときも、どの行の会社か分かるようにするため
- 構成銘柄の表で `title`（ホバー、スマホはタップで吹き出し）を付けるのはロゴと企業名だけ
  （社名全体とティッカー）。ほかのセルには付けない

### 前日比と NEW

前の日の CSV があれば、`symbol` を突き合わせて順位の差を出す。正なら順位が上がった。
「前の日」は、株価の日付（`pricesAsOf`、公表値モードは `holdingsAsOf`）が当日と違う直近の CSV。
週末や祝日の実行は株価が前回と同じ日付のままなので、すぐ前の CSV と比べると全部据え置きになる。
前の日の銘柄数が当日の9割を下回るときは NEW を判定しない（掲載数を増やした直後に全部が
新顔になるのを避ける。日々の入れ替えで数銘柄増減するのは普通なので1割まで許す）。

### 見た目を変えるとき

ページの見た目は `index.template.html`、データ部分の HTML は `render.py` を触る。
どちらを変えたときも、Actions の画面から Update を手で実行して出し直す
（毎日の実行を待たないと反映されない）。

## 毎日の起動（`tools/cron/`）

GitHub の `schedule` は高負荷時に遅れるだけでなく丸ごと捨てられる。実際、予定4回のうち
発火したのは1回だけで、それも2時間半遅れだった（分をずらしても改善しなかった）。
そこで Cloudflare Workers の Cron Triggers から `workflow_dispatch` を叩いている。
ワークフロー側の `schedule` は外してあるので、起動はこの Worker だけ。
止まれば毎日の更新も止まる（ページは前日のデータのまま残る）。

`schedule` を外したことで、60日間動きが無いとワークフローが止められる制限も外れた
（scheduled なワークフローだけの制限）。以前あった「有効化し直す」ステップは要らない。

Workers の無料枠に収まる（Cron Triggers はアカウントで5個まで、1日1回なので
10万リクエスト/日にも当たらない。`fetch` の待ち時間は CPU 時間に数えない）。
Actions 側は1回40秒ほどだが、GitHub はジョブ単位で分に切り上げるので1回1分。

デプロイ:

```
cd tools/cron
npx wrangler secret put GH_TOKEN   # このリポジトリだけの fine-grained PAT（Actions: write）
npx wrangler deploy
npx wrangler tail                  # 発火の確認
```

cron を変えた直後は、トリガーの登録に少し時間がかかる。作成から4分後の時刻では発火しなかった。
検証用の時刻を入れるなら、デプロイから5分以上あとにする。登録が済めば予定時刻から十数秒で発火する。

デプロイを待たずにコードと PAT を確かめるなら、リモートで `scheduled` を直接叩ける:

```
npx wrangler dev --remote --test-scheduled
curl http://localhost:8787/__scheduled
```

PAT の期限が切れると静かに止まるので、`scheduled` は失敗を throw している
（Workers のダッシュボードにエラーとして出る）。期限を決めたらカレンダーに入れておく。

`tools/cron/` を消しても、デプロイ済みの Worker `orukan-cron` は消えない。
`npx wrangler delete` するか Cloudflare のダッシュボードで消す（残っても空振りするだけ）。

## 時刻

06:20 JST なのは、米国市場のクローズ（夏時間 05:00 / 冬時間 06:00 JST）後で、
アジアのオープン（09:00 JST）前だから。全銘柄が確定した終値で揃う唯一の時間帯で、
冬時間でもクローズから20分空けて終値の確定を待てる。

毎時0分は避けている（GitHub の `schedule` を使っていた頃、07:00 にすると1時間半以上遅れた）。
組入比率の大半を占める米国株は日本の日中に動かないので、日本の日中に実行しても値はほとんど動かない。

## ロゴ（`logos.py`）

各社の公式サイトのアイコンを取る。企業を識別する目的で当該企業のロゴを使う形なので、
有料のロゴAPIは使わない。滅多に変わらないため、毎日のワークフローには入れず手で実行してコミットする。

```
pip install -r tools/requirements-logos.txt
python tools/logos.py --out logos            # 上位100銘柄
python tools/logos.py --out logos --top 0    # 全銘柄
```

全銘柄だと Yahoo へ約2,000回アクセスするので、並行数を絞って間隔を空ける。
調べた会社サイトとアイコンの大きさは `logo_sources.json` に残し、再実行ではそこから続ける。
`yfinance` の `website` が持株会社を指している会社は `DOMAIN_OVERRIDE` で手当てする。

## 手元で動かす

```
pip install -r tools/requirements.txt
python tools/build.py --out-dir history
python tools/nav.py --out nav.json --history history
python tools/render.py
python -m http.server
```

`build.py --holdings` にローカルの `.xls` / `.xls.gz` を渡せば、iShares に取りに行かずに試せる。
`--top` で銘柄数を絞れる（既定は全株式）。

## データ元

- 保有銘柄ファイル: iShares MSCI ACWI ETF（`build.py` の `HOLDINGS_URL`）。
  `userType=individual` を付けると投資家区分のゲートを通り、UA も Cookie も無しで
  SpreadsheetML が返る。最新の1日分しか取れない。
  ファイル全体は免責文の生 HTML のせいで XML として壊れているので、Holdings シートだけ切り出してパースする
- 株価・時価総額・為替: Yahoo Finance の非公式 API（yfinance 経由）
- 基準価額: 投資信託協会の CSV（`nav.py` の `TOUSHIN_URL`）
- TTM: 三菱UFJ銀行の公表相場（三菱UFJリサーチ&コンサルティングのサイト、`nav.py` の `MUFG_TTM_URL`）。
  過去の日付のページは当日分がまだ無いとトップページへ転送されるので、ページの日付を確かめる

データ元の規約が再配布や公開目的の利用を認めていないため、個人利用に留める。

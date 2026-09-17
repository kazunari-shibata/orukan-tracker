# orukan-tracker（オルカンの構成銘柄一覧）

全世界株式インデックス（オルカン / ACWI）の全構成銘柄と、その日の組入比率の推計を
1ページにまとめて GitHub Pages に出す。自分用で、検索エンジンには載せない（`noindex`）。

- `tools/build.py` — iShares の保有ファイル + Yahoo の株価から、その日の推計を
  `history/<日付>.csv`（全銘柄）と `<日付>.meta.json`（保有ファイルの日付・為替など）に書き出す
- `tools/nav.py` — オルカンの基準価額の推移から `nav.json` を作る
- `tools/render.py` — 履歴 CSV と `nav.json` から `index.html` と `rows/<ページ>.json` を作る（静的生成）
- `tools/symbols.py` — iShares のティッカー/取引所 → Yahoo Finance のシンボル変換
- `tools/labels.py` — 業種・国の日本語名と国旗（表示用）
- `tools/logos.py` — 銘柄のロゴを `logos/` に取得する（手で実行。`--top 0` で全銘柄。調べた結果は `logo_sources.json`）
- `index.template.html` — ページのひな形
- `.github/workflows/update.yml` — 毎日の更新とデプロイ

## 全体の流れ

毎日 06:20 JST に GitHub Actions が、推計 → 基準価額の取得 → ページの生成 → GitHub Pages への
デプロイまでを1回で行う。

**生成したものは保存しない。** 推計の CSV も保有銘柄ファイルも基準価額も、git にも Releases にも
入れない。ページは実行のたびに作り直して Pages に出すだけ。
例外は前日比（順位の上げ下げ）に要る前回の CSV で、これだけ Actions のキャッシュに
直近2日分を置く。キャッシュが無い日（初回や、7日以上実行が空いたとき）は前日比なしで出る。

- GitHub の schedule は遅れたり飛んだりするが、飛んだ日はページが前日のまま残るだけなので許容している。
- 公開リポジトリの schedule は、60日間リポジトリに動きが無いと止められる。毎日のコミットが無いので、
  ワークフローが実行のたびに自分を有効化し直している。
- 見た目やスクリプトを変えたら、Actions の画面から Update を手で実行する（push では走らない）。
- 株価の取得が失敗したら実行ごと止まり、ページは前回のまま残る。

## 生成（手元）

```
pip install -r tools/requirements.txt
python tools/build.py --out-dir history
python tools/nav.py --out nav.json
python tools/render.py
python -m http.server
```

生成物（`history/`・`nav.json`・`index.html`・`rows/`）は `.gitignore` 済み。

`--holdings <.xls または .xls.gz>` を付けると、保有ファイルを取得せずにローカルのファイルで計算する。

`render.py` は `index.template.html` の `<!--ssg:名前-->` 〜 `<!--/ssg:名前-->` を埋めて
`index.html` を書く。見た目はひな形を、データ部分の HTML は `render.py` を直す。
JS が描くのはチャート（`nav.json` を後から読む）とページ送りだけ（国・業種の比率は render.py が書き込む）。

前日比の順位の上げ下げは、最新の日付の CSV とその前の日付の CSV を比べて出す。

6時20分なのは、米国市場のクローズ（夏時間 05:00 / 冬時間 06:00 JST）後・アジアのオープン（09:00 JST）前で、
全銘柄が確定した終値で揃う唯一の時間帯だから。冬時間でもクローズから20分空けて終値の確定を待つ。
毎時0分は GitHub の schedule が混んで遅れやすい（07:00 にしていたときは1時間半以上遅れた）ので避けている。
それでも発火しない日はある（上記）。
組入比率の大半を占める米国株は日本の日中に動かないので、日本の日中に実行しても値はほとんど動かない。

## 基準価額

ページ上部の折れ線は、オルカン（eMAXIS Slim 全世界株式）の実際の基準価額。
投資信託協会の投信総合検索ライブラリーの CSV を読んでいる。

```
https://toushin-lib.fwg.ne.jp/FdsWeb/FDST030000/csv-file-download?isinCd=JP90C000H1T1&associFundCd=0331418A
```

Shift-JIS（Content-Type は utf-8 を名乗る）、日付は「2026年09月11日」、2列目が基準価額。
分配金再投資ベースの列は無いが、オルカンは無分配なので基準価額と一致する。

三菱UFJアセットマネジメントの設定来 CSV（`www.am.mufg.jp/fund_file/setteirai/253425.csv`）と
ファンド情報 API（`developer.am.mufg.jp`）は、GitHub Actions のデータセンター IP を 403 で弾くので使わない。
値は投資信託協会と同じ（2018-10-31〜2026-09-11 の全営業日で一致を確認）。ただし投資信託協会のほうが
反映が遅く、三菱UFJアセットマネジメントに当日分が出ている夜でも前営業日までしか無いことがある。
取得に失敗したときは警告だけ出して正常終了し、その日は基準価額の欄を出さない（株価側の更新は止めない）。

基準価額の更新は1営業日に1回で、前営業日の海外市場の終値をもとに算出されるため、
表示は常に1営業日遅れになる。

## 保有銘柄ファイル

iShares から実行のたびに取得する。`userType=individual` を付けると投資家区分の
ゲートを通り、UA も Cookie も無しで SpreadsheetML がそのまま返る。

```
https://www.blackrock.com/varnish-api/blk-one01-product-data/product-data/api/v1/
get-fund-document?appType=PRODUCT_PAGE&appSubType=ISHARES&targetSite=us-ishares
&locale=en_US&portfolioId=239600&component=fundDownload&userType=individual
```

商品ページ上の `.../1467271812596.ajax?fileType=xls&...` は、200 と
`Content-Type: application/vnd.ms-excel` を返しながら中身がファンド紹介ページの
HTML なので使わない。

`--holdings` にローカルの `.xls` / `.xls.gz` を渡せば、オフラインでも生成できる。
保有ファイルは iShares が最新の1日分しか出さず、ここでも保存しないので、過去の分は取り直せない。

## 株価の取得

現在値・前日終値・時価総額・為替は、Yahoo のクオート
（`https://query2.finance.yahoo.com/v7/finance/quote?symbols=...`）から**まとめて**取っている。
全約2,200銘柄＋為替でも、認証1回と150件ずつ15回ほどで済む。1銘柄ずつ取ると数千回になり、
共有 IP の GitHub Actions からはアクセス制限にかかりやすい。
Yahoo はたまに応答から数件落とすので、欠けた分だけもう一度まとめて取り直す。

まとめ取得は yfinance の内部 API（`yfinance.data.YfData`）と Yahoo の非公式エンドポイントに
頼っている。仕組みそのものが動かないとき（`YfData` が無い、通信や応答の解析に失敗、
応答が空、半数以上の銘柄の株価が欠ける）は**エラーで止める**。1銘柄ずつの取得に黙って
切り替えるとアクセス数が100倍になり、気づかないまま制限にかかりやすくなるため。
止まればデプロイされないので、ページは前回のデータのまま残る。

応答の中で一部の銘柄だけ欠けたときは、組入比率の上位100銘柄だけ `fast_info` で
1銘柄ずつ（並行2本で）取り直す。為替が欠けたら日足の終値で補う。
それでも取れない銘柄は保有ファイルの株価のまま計算し、`stale` を立てる。

次の銘柄も保有ファイルの株価のまま計算する（`stale`）。
- Yahoo で引けない市場: マレーシア（Yahoo は数字コードで、ファイルの略称から引けない）、
  アラブ首長国連邦、フィリピン（Yahoo にデータが無い）。組入比率の合計で 0.3% ほど
- 建値の通貨が取引所から決めた通貨と違う銘柄（ロンドン上場で米ドル建ての銘柄など）
- 株価が保有ファイルの値から25%以上離れている銘柄（エジプト株は iShares の評価額と
  Yahoo の株価が4割ほど違う）

単位に注意。ロンドン株はまとめ取得でも `fast_info` でも株価がペンス建てで返るが、
時価総額はまとめ取得だとポンド建て、`fast_info` だとペンス建てで返る。
どちらも取得関数の中で通貨単位にそろえてから返している。

## 前日比

現在値・前日終値はどちらも同じクオート（`regularMarketPrice` / `lastPrice` と
`regularMarketPreviousClose`）から**対で**取っている。日足から前日終値を拾うと、
当日分の行が NaN のまま先に生える銘柄（米国株・英国株で頻発）で `dropna()` 後の
`iloc[-2]` が前々日を指してしまい、2日分の変化率になる。

## 計算のしかた

保有ファイルには銘柄ごとの数量・現地通貨建て株価・対USD為替・USD建て時価がある。
全銘柄（--top で銘柄数を絞れる）の株価と為替を最新に差し替えて時価を計算し直し、
ファンド全体の時価（現金・先物などは基準日のまま）で割ってウェイトを出している。
つまりウェイトは概算で、iShares の公表値とは小数点以下でずれる。

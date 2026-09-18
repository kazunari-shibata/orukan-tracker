# 仕組みのメモ

このページの作り方と、そうしてある理由。作業の前に読む。

## 流れ

毎日 06:20 JST に Cloudflare Worker（`tools/cron/`）が GitHub Actions（`.github/workflows/update.yml`）を起動し、Actions が次を行って GitHub Pages に出す。

1. `build.py` — iShares の保有銘柄ファイルを、Yahoo の最新の株価・為替で計算し直して組入比率を推計する
2. `nav.py` — オルカンの基準価額の推移を取得する
3. `render.py` — `index.template.html` にデータを埋めてページを作る

生成したデータは保存しない。前日比に要る前回の推計だけ Actions のキャッシュに置く。
見た目やスクリプトを変えたら、Actions の画面から Update を手で実行する。

## データ元

- 保有銘柄ファイル: iShares MSCI ACWI ETF（`build.py` の `HOLDINGS_URL`）。最新の1日分しか取れない
- 株価・時価総額・為替: Yahoo Finance の非公式 API（yfinance 経由）。数千回のアクセスを避けるため、まとめて取る
- 基準価額: 投資信託協会の CSV（`nav.py` の `TOUSHIN_URL`）。三菱UFJアセットマネジメントは Actions の IP を弾く

## 注意点

- 株価のまとめ取得が失敗したら止める。ページは前回のまま残る
- Yahoo で取れない銘柄（マレーシア株など）や値がおかしい銘柄は、保有ファイルの株価のまま計算する
- ロンドン株の株価はペンス建てで返るので、ポンドに直している
- 06:20 なのは、米国市場が閉まってから、アジア市場が開く前だから
- 毎時0分は混んで遅れやすいので避けている（07:00 にしていたときは1時間半以上遅れた）

## 毎日の起動（`tools/cron/`）

GitHub の `schedule` は高負荷時に遅れるだけでなく丸ごと捨てられる。実際、予定4回のうち
発火したのは1回だけで、それも2時間半遅れだった（分をずらしても改善しなかった）。
そこで Cloudflare Workers の Cron Triggers から `workflow_dispatch` を叩いている。
ワークフロー側の `schedule` は外してあるので、起動はこの Worker だけ。
止まれば毎日の更新も止まる（ページは前日のデータのまま残る）。

Workers の無料枠に収まる（Cron Triggers はアカウントで5個まで、1日1回なので
10万リクエスト/日にも当たらない。`fetch` の待ち時間は CPU 時間に数えない）。

デプロイ:

```
cd tools/cron
npx wrangler secret put GH_TOKEN   # このリポジトリだけの fine-grained PAT（Actions: write）
npx wrangler deploy
npx wrangler tail                  # 発火の確認
```

cron を変えた直後は、トリガーの登録に少し時間がかかる。作成から4分後の時刻では発火しなかった。
検証用の時刻を入れるなら、デプロイから5分以上あとにする。

PAT の期限が切れると静かに止まるので、`scheduled` は失敗を throw している
（Workers のダッシュボードにエラーとして出る）。期限を決めたらカレンダーに入れておく。

`tools/cron/` を消しても、デプロイ済みの Worker `orukan-cron` は消えない。
`npx wrangler delete` するか Cloudflare のダッシュボードで消す（残っても空振りするだけ）。

## 手元で動かす

```
pip install -r tools/requirements.txt
python tools/build.py --out-dir history
python tools/nav.py --out nav.json
python tools/render.py
python -m http.server
```

ロゴは `tools/logos.py` で `logos/` に取得する（手で実行）。

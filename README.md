## オルカンの構成銘柄一覧
オルカン（またはACWIをベンチマークしている全世界株式インデックスファンド）の全構成銘柄と組入比率を見ることができるツールです。<br>
組入比率は iShares の保有銘柄ファイルと Yahoo Finance の株価から推計したものです。<br>
データ元の規約が再配布や公開目的の利用を認めていないため、個人利用に留めてください。<br>

### 流れ

毎日 06:20 JST に GitHub Actions（`.github/workflows/update.yml`）が次を行い、GitHub Pages に出す。

1. `build.py` — iShares の保有銘柄ファイルを、Yahoo の最新の株価・為替で計算し直して組入比率を推計する
2. `nav.py` — オルカンの基準価額の推移を取得する
3. `render.py` — `index.template.html` にデータを埋めてページを作る

生成したデータは保存しない。前日比に要る前回の推計だけ Actions のキャッシュに置く。
見た目やスクリプトを変えたら、Actions の画面から Update を手で実行する。

### データ元

- 保有銘柄ファイル: iShares MSCI ACWI ETF（`build.py` の `HOLDINGS_URL`）。最新の1日分しか取れない
- 株価・時価総額・為替: Yahoo Finance の非公式 API（yfinance 経由）。数千回のアクセスを避けるため、まとめて取る
- 基準価額: 投資信託協会の CSV（`nav.py` の `TOUSHIN_URL`）。三菱UFJアセットマネジメントは Actions の IP を弾く

### 注意点

- 株価のまとめ取得が失敗したら止める。ページは前回のまま残る
- Yahoo で取れない銘柄（マレーシア株など）や値がおかしい銘柄は、保有ファイルの株価のまま計算する
- ロンドン株の株価はペンス建てで返るので、ポンドに直している
- 06:20 なのは、米国市場が閉まってから、アジア市場が開く前だから
- Actions は大幅に遅れたり、スキップされることもある

### 手元で動かす

```
pip install -r tools/requirements.txt
python tools/build.py --out-dir history
python tools/nav.py --out nav.json
python tools/render.py
python -m http.server
```

ロゴは `tools/logos.py` で `logos/` に取得する（手で実行）。

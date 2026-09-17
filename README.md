# orukan-tracker

オルカン（eMAXIS Slim 全世界株式）の全構成銘柄と組入比率の推計を、毎日更新して1ページで見るための個人用ツール。

https://kazunari-shibata.github.io/orukan-tracker/

- 毎朝 GitHub Actions がデータを取得してページを作り、GitHub Pages に出す
- 組入比率は iShares の保有銘柄ファイルと Yahoo Finance の株価から計算した推計（公式の値ではない）
- 仕組みの詳細は [tools/README.md](tools/README.md)

## 注意

- **個人利用に留める。** データ元の規約が再配布や公開目的の利用を認めていないため。URL を広めない、広告を載せない、データをリポジトリに保存しない。
- **noindex にしている。** 検索には出ないが、アクセス制限ではないので URL を知っていれば開ける。

#!/usr/bin/env python3
"""オルカン（eMAXIS Slim 全世界株式）の基準価額の推移を JSON にする。

投資信託協会の投信総合検索ライブラリーの CSV を読む。

三菱UFJアセットマネジメントの CSV と API（developer.am.mufg.jp）は GitHub Actions の
IP を 403 で弾くので使わない。値は同じ（2018-10-31〜の全営業日で一致を確認）だが、
投資信託協会のほうが反映が遅いことがある。

  python tools/nav.py --out nav.json
"""
import argparse
import csv
import json
import sys
import urllib.request
from pathlib import Path

TOUSHIN_URL = ("https://toushin-lib.fwg.ne.jp/FdsWeb/FDST030000/csv-file-download"
               "?isinCd=JP90C000H1T1&associFundCd=0331418A")
FUND = "eMAXIS Slim 全世界株式（オール・カントリー）"


def load(src, encoding="shift_jis"):
    if str(src).startswith("http"):
        with urllib.request.urlopen(src, timeout=30) as r:
            raw = r.read()
    else:
        raw = Path(src).read_bytes()
    return raw.decode(encoding)


def parse_toushin(text):
    """投資信託協会の CSV。Shift-JIS、日付は「2026年09月11日」、2列目が基準価額。

    分配金再投資ベースの列は無いが、オルカンは無分配なので基準価額と一致する。
    """
    import re
    rows = list(csv.reader(text.splitlines()))
    dates, values = [], []
    for r in rows:
        m = re.match(r"(\d{4})年(\d{2})月(\d{2})日", r[0].strip()) if r else None
        if not m or len(r) < 2:
            continue
        try:
            values.append(round(float(r[1].replace(",", ""))))
        except ValueError:
            continue
        dates.append(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    return dates, values


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=TOUSHIN_URL, help="投資信託協会の CSV の URL かローカルパス")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    try:
        # Content-Type は utf-8 を名乗るが本文は Shift-JIS
        dates, values = parse_toushin(load(args.src, "cp932"))
        if len(values) < 2:
            raise ValueError("基準価額を読み取れませんでした")
    except Exception as e:
        # ここで落ちると株価側の更新まで巻き添えで止まるので、警告だけ出して正常終了する。
        # nav.json が無ければ、ページは基準価額の欄を隠して出す。
        print(f"::warning::[nav] 基準価額を取得できなかったので、今日は基準価額の欄を出しません: {e}",
              file=sys.stderr)
        return

    prev, last = values[-2], values[-1]
    out = {
        "fund": FUND,
        "asOf": dates[-1],
        "latest": last,
        "chgAmount": last - prev,
        "chg1d": round((last / prev - 1) * 100, 2),
        "dates": dates,
        "values": values,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"[nav] {args.out} を書き出しました "
          f"({dates[0]}〜{dates[-1]} / {len(values)}営業日 / 最新 {last:,}円 {out['chg1d']:+}%)",
          file=sys.stderr)


if __name__ == "__main__":
    main()

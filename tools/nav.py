#!/usr/bin/env python3
"""オルカン（eMAXIS Slim 全世界株式）の基準価額の推移を JSON にする。

投資信託協会の投信総合検索ライブラリーの CSV を読む。

三菱UFJアセットマネジメントの CSV と API（developer.am.mufg.jp）は GitHub Actions の
IP を 403 で弾くので使わない。値は同じ（2018-10-31〜の全営業日で一致を確認）だが、
投資信託協会のほうが反映が遅いことがある。

--history に build.py の出力先を渡すと、最新の終値で計算した基準価額の推計も入れる
（ページの速報値モード用。estimate() 参照）。

  python tools/nav.py --out nav.json --history history
"""
import argparse
import csv
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

TOUSHIN_URL = ("https://toushin-lib.fwg.ne.jp/FdsWeb/FDST030000/csv-file-download"
               "?isinCd=JP90C000H1T1&associFundCd=0331418A")
# 三菱UFJ銀行の公表相場（三菱UFJリサーチ&コンサルティングのサイト）。Shift-JIS。
MUFG_TTM_URL = "https://www.murc-kawasesouba.jp/fx/past/index.php?id={:%y%m%d}"
MONTHS = {m: i for i, m in enumerate(
    "January February March April May June July August September October November December".split(), 1)}
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


def fetch_ttm(day):
    """三菱UFJ銀行が day（YYYY-MM-DD）に公表した米ドルの TTM（(TTS + TTB) / 2）。

    過去の日付のページは当日分がまだ無いとトップページ（最新の相場）へ転送されるので、
    ページに書かれた日付が day と一致するかを確かめる。
    """
    d = date.fromisoformat(day)
    html = load(MUFG_TTM_URL.format(d), "cp932")
    m = re.search(r"As of (\w+) (\d{1,2}), (\d{4})", html)
    # strptime の %B はロケール依存なので、月名は自前の表で引く（build.py の to_iso と同じ）
    if not m or (MONTHS.get(m.group(1)), int(m.group(2)), int(m.group(3))) != (d.month, d.day, d.year):
        raise ValueError(f"{day} の TTM のページがありません")
    m = re.search(r">\s*USD\s*</td>\s*<td[^>]*>\s*([\d.]+)\s*</td>\s*<td[^>]*>\s*([\d.]+)", html)
    if not m:
        raise ValueError("TTM のページから米ドルの行を読み取れませんでした")
    return (float(m.group(1)) + float(m.group(2))) / 2


def estimate(history, nav_day, nav_value):
    """最新の終値で計算したら基準価額がいくらになるかを推計する。

    推計 ＝ 公表済みの基準価額 × 評価額の変化 × 今のドル円 ÷ 公表日の TTM

    評価額の変化は、公表済みの基準価額が使った相場の日（nav_day より前の最後の米国の取引日）
    から最新の終値の日まで。build.py が meta.json に残す levels（米国の日付ごとの水準）で引く。
    オルカンは海外株を前日の終値、円換算を当日の TTM で計算するので、それに合わせている。
    """
    # TTM は推計できない日も取ってログに出す（取れているかを毎日確かめられるように）
    ttm = fetch_ttm(nav_day)
    print(f"[nav] {nav_day} の TTM（米ドル）: {ttm}", file=sys.stderr)
    metas = sorted(Path(history).glob("????-??-??.meta.json"))
    if not metas:
        raise ValueError(f"{history} に meta.json がありません")
    meta = json.loads(metas[-1].read_text())
    levels, prices_day = meta.get("levels") or {}, meta.get("pricesAsOf")
    jpy = (meta.get("fx") or {}).get("JPY")
    base_days = [d for d in levels if d < nav_day]
    if not base_days or prices_day not in levels or not jpy:
        raise ValueError("評価額の水準がまだたまっていません")
    base_day = max(base_days)
    # 公表済みの基準価額のほうが新しい相場を使っている（株価の取得が止まっている）
    if prices_day < base_day:
        raise ValueError(f"株価（{prices_day}）が基準価額の相場の日（{base_day}）より古い")
    value = round(nav_value * levels[prices_day] / levels[base_day] * jpy / ttm)
    if abs(value / nav_value - 1) > 0.15:
        raise ValueError(f"推計値 {value:,}円 が公表値から離れすぎています")
    return {
        "value": value,
        "chgAmount": value - nav_value,
        "chg": round((value / nav_value - 1) * 100, 2),
        "pricesAsOf": prices_day,   # どの日の終値（米国東部時間）で推計したか
        "baseAsOf": base_day,       # 公表済みの基準価額が使った相場の日
        "ttm": ttm,
        "usdjpy": jpy,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=TOUSHIN_URL, help="投資信託協会の CSV の URL かローカルパス")
    ap.add_argument("--out", required=True)
    ap.add_argument("--history", type=Path,
                    help="build.py の出力先。渡すと最新の終値で基準価額を推計する（速報値モード用）")
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
    if args.history:
        try:
            out["estimate"] = estimate(args.history, dates[-1], last)
        except Exception as e:
            # 推計が出せなくても公表値は出す。ページの速報値モードは公表値に落とす
            print(f"::warning::[nav] 基準価額を推計できなかったので、速報値モードも公表値を出します: {e}",
                  file=sys.stderr)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"[nav] {args.out} を書き出しました "
          f"({dates[0]}〜{dates[-1]} / {len(values)}営業日 / 最新 {last:,}円 {out['chg1d']:+}%)",
          file=sys.stderr)


if __name__ == "__main__":
    main()

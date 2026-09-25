#!/usr/bin/env python3
"""毎日の更新の結果（推計の答え合わせと、手当てが要りそうなこと）を Slack に送る。

build.py が meta.json に、nav.py が nav.json に入れた check と issues、実行ログ（--log）の
::warning:: を読んで、Incoming Webhook に投げる。
Webhook の URL は環境変数 SLACK_WEBHOOK_URL（Actions の Secret）。無ければ送らずに文面だけ出す。
更新が途中で失敗した日も、失敗したことと理由、実行画面へのリンクを送る（JOB_STATUS、RUN_URL）。

  python tools/notify.py --history history --nav _site/nav.json --log logs/run.log
"""
import argparse
import csv
import json
import os
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path

from build import CHECK_TOP
from logos import MIN_PX
from nav import JST

ROOT = Path(__file__).resolve().parent.parent
PAGE_URL = "https://kazunari-shibata.github.io/orukan-tracker/"
LOGOS_TOP = 100          # この順位までの銘柄は、ロゴを調べていなければ知らせる
# データの日付がこれより古ければ、取得元の更新が止まっているとみなす（日数。連休をまたげるだけ空ける）
STALE_DAYS = {"holdings": 5, "prices": 4, "nav": 6}


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def md(day):
    """2026-09-24 → 9/24（木）"""
    d = date.fromisoformat(day)
    return f"{d.month}/{d.day}（{'月火水木金土日'[d.weekday()]}）"


def age(day, today):
    return (date.fromisoformat(today) - date.fromisoformat(day)).days


def nav_lines(nav):
    lines = ["*基準価額*"]
    if not nav:
        return lines + ["• 取得できませんでした"]
    est = nav.get("estimate")
    if est:
        lines.append(f"• 今日の推計: *{est['value']:,}円* （{md(est['pricesAsOf'])}の終値で計算）")
    else:
        lines.append("• 今日の推計: 出せませんでした")
    check = nav.get("check")
    if not check:
        return lines + ["• 答え合わせ: 今日はなし"]
    published = check["published"]
    lines.append(f"• 答え合わせ: {md(check['navDate'])}の公表値 {published:,}円 に対して")
    for e in check["estimates"]:
        lines.append(f"　　{md(e['madeOn'])}の推計 {e['value']:,}円 → "
                     f"{e['value'] - published:+,}円（{e['diff']:+.2f}%）のずれ")
    return lines


def weight_lines(meta):
    lines = ["*組入比率*"]
    if not meta:
        return lines + ["• 今日の推計を出せませんでした"]
    check = meta.get("check")
    if not check:
        return lines + ["• 答え合わせ: 今日はなし"]
    top, whole = check[f"top{CHECK_TOP}"], check["all"]
    return lines + [
        f"{md(check['estimatedOn'])}の推計を、iShares の {md(check['pricesAsOf'])}の実際の比率と比べると",
        f"• 上位{CHECK_TOP}銘柄: 比率のずれ 合計 {top['absSum']:.3f}% ／ 順位がぴったり {top['rankMatch']:.0f}%",
        f"• 全{whole['n']:,}銘柄: 比率のずれ 合計 {whole['absSum']:.3f}% ／ 順位のずれ 中央値 {whole['rankGapMedian']}位",
        f"• 一番ずれた銘柄: {whole['absMaxSymbol']}（{whole['absMax']:.4f}%）",
    ]


def some(items, n=5):
    items = list(items)
    return ", ".join(items[:n]) + (f" ほか{len(items) - n}件" if len(items) > n else "")


def issue_lines(meta, prev_meta, nav, history, today, log):
    """手当てが要りそうなことを1行ずつ。無ければ空。"""
    out = [line.split("::warning::", 1)[1].strip() for line in log if "::warning::" in line]
    if meta:
        if meta.get("usMarketState") == "REGULAR":
            out.append("米国株の株価が取引時間中の値です（終値ではない）。今日の推計は答え合わせに使えません")
        for key, label, where in (("holdingsAsOf", "holdings", "iShares の保有ファイル"),
                                  ("pricesAsOf", "prices", "Yahoo の株価")):
            day = meta.get(key)
            if day and age(day, today) > STALE_DAYS[label]:
                out.append(f"{where}が {md(day)}のままです。取得元の更新が止まっていないか確認を")
        if len(list(history.glob("????-??-??.csv"))) < 2:
            out.append("前回の推計がキャッシュに無いので、前日比と組入比率の答え合わせを出せていません")
        # 銘柄ごとの問題は毎日同じものが多い（通貨の違い、エジプト株など）ので、前回から増えたものだけ
        cur, prev = meta.get("issues") or {}, (prev_meta or {}).get("issues") or {}
        new = {k: [s for s in cur.get(k) or [] if s not in (prev.get(k) or [])] for k in cur}
        if missing := new.get("priceMissing"):
            weights = cur["priceMissing"]
            out.append(f"株価を取れなくなった銘柄 {len(missing)}件（比率 計{sum(weights[s] for s in missing):.2f}%。"
                       f"保有ファイルの株価で代用）: {some(missing)}")
        if suspect := new.get("suspect"):
            ratio = cur["suspect"]
            out.append(f"保有ファイルと株価が離れすぎていて使わなかった銘柄 {len(suspect)}件: "
                       f"{some(f'{s}（×{ratio[s]}）' for s in suspect)}")
        if mismatched := new.get("mismatched"):
            ccy = cur["mismatched"]
            out.append(f"建値の通貨が想定と違った銘柄 {len(mismatched)}件（symbols.py の確認を）: "
                       f"{some(f'{s}（{ccy[s]}）' for s in mismatched)}")
        if nocap := new.get("noMarketCap"):
            out.append(f"時価総額を取れなくなった銘柄 {len(nocap)}件: {some(nocap)}")
        out += logo_lines(history / f"{meta['date']}.csv")
    if nav and nav.get("asOf") and age(nav["asOf"], today) > STALE_DAYS["nav"]:
        out.append(f"基準価額の公表値が {md(nav['asOf'])}のままです。投資信託協会の CSV を確認を")
    return out


def logo_lines(csv_path):
    """上位の銘柄で、logos.py がまだ手を付けていないもの（新しく上位に入った銘柄など）。

    logos.py の done() と同じく、png がある・見送りにした（rejected）・小さいアイコンしか無かったものは済み。
    Yahoo で引けない銘柄（"@" を含む）はロゴも取らない。
    """
    sources = load_json(ROOT / "tools" / "logo_sources.json") or {}

    def done(sym):
        info = sources.get(sym, {})
        return ((ROOT / "logos" / f"{sym}.png").exists() or "rejected" in info
                or ("px" in info and info["px"] < MIN_PX))
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            top = [r["symbol"] for r in csv.DictReader(f) if int(r["rank"]) <= LOGOS_TOP]
    except (OSError, ValueError, KeyError):
        return []
    new = [s for s in top if "@" not in s and not done(s)]
    if not new:
        return []
    return [f"上位{LOGOS_TOP}銘柄にロゴが無い銘柄 {len(new)}件（tools/logos.py を実行）: {some(new)}"]


def failure_lines(log):
    """失敗した理由。ログの最後のほうのエラーらしい行（無ければ最後の行）。"""
    lines = [line.strip() for line in log if line.strip()]
    errors = [line for line in lines if any(k in line for k in
                                            ("Error", "Exception", "SystemExit", "失敗", "ありません"))]
    tail = (errors or lines)[-3:]
    return [f"`{line[:300]}`" for line in tail] or ["（ログがありません。実行画面で確認を）"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", required=True, type=Path)
    ap.add_argument("--nav", required=True, type=Path)
    ap.add_argument("--log", type=Path)
    args = ap.parse_args()

    today = datetime.now(JST).date().isoformat()
    status = os.environ.get("JOB_STATUS", "success")
    run_url = os.environ.get("RUN_URL")
    try:
        log = args.log.read_text().splitlines() if args.log else []
    except OSError:
        log = []
    metas = sorted(args.history.glob("????-??-??.meta.json"))
    meta = load_json(metas[-1]) if metas else None
    if meta and meta.get("date") != today:
        meta = None             # 今日の build.py が書く前に失敗した
    prev_meta = load_json(metas[-2]) if meta and len(metas) > 1 else None
    nav = load_json(args.nav)

    issues = issue_lines(meta, prev_meta, nav, args.history, today, log)
    if status != "success":
        lines = [f":rotating_light: *オルカン推計 {md(today)}　更新が途中で失敗しました*",
                 "ページは更新されていません。", "", "*ログの最後のほう（失敗の手がかり）*", *failure_lines(log)]
    else:
        lines = [f":bar_chart: *オルカン推計 {md(today)}*"]
    if issues:
        lines += ["", f":warning: *要確認（{len(issues)}件）*", *(f"• {s}" for s in issues)]
    if status == "success":
        lines += ["", *nav_lines(nav), "", *weight_lines(meta)]
    lines += ["", f"<{PAGE_URL}|ページを見る>" + (f" ／ <{run_url}|実行画面>" if run_url else "")]
    text = "\n".join(lines)
    print(text, file=sys.stderr)

    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        print("[notify] SLACK_WEBHOOK_URL が無いので送りません", file=sys.stderr)
        return
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:
        # 通知の失敗でページの公開を止めない
        print(f"::warning::[notify] Slack に送れませんでした: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()

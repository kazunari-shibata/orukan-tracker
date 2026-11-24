#!/usr/bin/env python3
"""知らせることがあった日だけ、Slack に送る。毎日の答え合わせの結果は送らない（Actions の実行画面に出る）。

送るのは次のどれかがあった日:
  - 更新が途中で失敗した（失敗の理由と実行画面へのリンク。JOB_STATUS、RUN_URL）
  - 手当てが要りそうなこと（実行ログ --log の ::warning::、データの日付が古い、meta.json の issues の
    うち前回から増えたもの、上位の銘柄でロゴが無いもの）
  - 掲載している銘柄の前日比が ±MOVE_PCT% 以上
  - 基準価額の推計の前日比が ±NAV_PCT% 以上
値動きの2つは、株価の日付（pricesAsOf）が前回から変わった日だけ見る。週末や祝日の実行は前回と
同じ終値なので、同じ知らせを何度も送らないため。
Webhook の URL は環境変数 SLACK_WEBHOOK_URL（Actions の Secret）。無ければ送らずに文面だけ出す。

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

from logos import MIN_PX
from nav import JST

ROOT = Path(__file__).resolve().parent.parent
PAGE_URL = "https://kazunari-shibata.github.io/orukan-tracker/"
LOGOS_TOP = 100          # この順位までの銘柄は、ロゴを調べていなければ知らせる
# データの日付がこれより古ければ、取得元の更新が止まっているとみなす（日数。連休をまたげるだけ空ける）
STALE_DAYS = {"holdings": 5, "prices": 4, "nav": 6}
MOVE_PCT = 10            # 掲載している銘柄の前日比がこれ以上（絶対値、%）なら知らせる
NAV_PCT = 3              # 基準価額の推計の前日比がこれ以上（絶対値、%）なら知らせる


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


def mover_lines(csv_path):
    """前日比が ±MOVE_PCT% 以上の銘柄。動いた大きさの順。株価を差し替えられなかった行は除く。"""
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return []
    moves = []
    for r in rows:
        try:
            chg = (float(r["price"]) / float(r["prev_close"]) - 1) * 100
        except (KeyError, ValueError, ZeroDivisionError):
            continue
        if r.get("stale") != "1" and abs(chg) >= MOVE_PCT:
            moves.append((chg, r))
    moves.sort(key=lambda m: -abs(m[0]))
    return [f"{r['name']}（{r['ticker']}、{r['rank']}位）: *{chg:+.2f}%*" for chg, r in moves]


def nav_move_line(nav):
    """基準価額の推計の前日比が ±NAV_PCT% 以上なら、その1行。"""
    est = (nav or {}).get("estimate")
    if not est or abs(est["chg"]) < NAV_PCT:
        return None
    return (f"基準価額の推計 *{est['value']:,}円*（前日比 {est['chgAmount']:+,}円、*{est['chg']:+.2f}%*。"
            f"{md(est['pricesAsOf'])}の終値で計算）")


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
    # 値動きは、株価の日付が前回から変わった日だけ見る（週末や祝日に同じ知らせを繰り返さない）
    moves, nav_move = [], None
    if status == "success" and meta and meta.get("pricesAsOf") != (prev_meta or {}).get("pricesAsOf"):
        moves = mover_lines(args.history / f"{meta['date']}.csv")
        nav_move = nav_move_line(nav)

    if status != "success":
        lines = [f":rotating_light: *オルカン推計 {md(today)}　更新が途中で失敗しました*",
                 "ページは更新されていません。", "", "*ログの最後のほう（失敗の手がかり）*", *failure_lines(log)]
    elif issues or moves or nav_move:
        lines = [f":bar_chart: *オルカン推計 {md(today)}*"]
    else:
        print("[notify] 知らせることが無いので送りません", file=sys.stderr)
        return
    if nav_move or moves:
        lines += ["", ":chart_with_upwards_trend: *大きく動きました*"]
        lines += [f"• {nav_move}"] if nav_move else []
        if moves:
            lines += [f"前日比が ±{MOVE_PCT}% 以上の銘柄（{len(moves)}件）", *(f"• {m}" for m in moves)]
    if issues:
        lines += ["", f":warning: *要確認（{len(issues)}件）*", *(f"• {s}" for s in issues)]
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

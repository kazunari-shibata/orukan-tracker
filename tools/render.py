"""履歴 CSV と nav.json から、ページを静的生成する。

GitHub Actions で実行して GitHub Pages に出す（生成物は git に入れない）。
  - index.template.html の <!--ssg:名前--> 〜 <!--/ssg:名前--> を埋めて index.html を書く
  - 表は100銘柄ずつのページ送り。1ページ目は index.html に直接書き込み、
    全ページぶんの行 HTML を rows/<ページ>.json に書き出す（2ページ目以降はブラウザが読む）
  - 前日比（順位の上げ下げ）は、最新の CSV と、株価の日付が違う直近の CSV を比べて出す
  - 出すのは速報値（Yahoo の最新の終値と推計）だけ。保有ファイルの公表値は公式サイトで見られる

ページの見た目を変えるときは index.template.html を、データ部分の HTML を変えるときは
このファイルを編集する。手元で確かめるときもこれを実行してから配信する。

  python tools/render.py
"""

import argparse
import csv
import json
import shutil
import sys
import re
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))
PAGE_SIZE = 100   # 1ページの銘柄数

sys.path.insert(0, str(Path(__file__).resolve().parent))
from labels import COUNTRY, SECTOR_JA  # noqa: E402


def fmt_price(v):
    # 4桁以上は小数を落とす（1,812,000 KRW のような桁でも読める幅に収める）
    if v is None:
        return "—"
    return f"{v:,.0f}" if v >= 1000 else f"{v:,.2f}"


def pct(v):
    return ("+" if v > 0 else "") + f"{v:.2f}%"


def delta(v):
    return "—" if v is None else pct(v)


def cls(v):
    return "up" if v > 0.005 else "down" if v < -0.005 else "flat"


def fmt_cap(usd):
    # 時価総額はドル建てで出す（円に直すと為替の動きが混ざる）。
    # 1兆ドル = 1万億ドルなので、兆ドルは小数2桁まで出して億ドルとの桁の差を埋める
    if usd is None:
        return "—"
    return f"{usd / 1e12:.2f}兆ドル" if usd >= 1e12 else f"{round(usd / 1e8):,}億ドル"


def hue(sym):
    # ロゴが無いときのバッジ用。シンボルから安定した色相を作る
    h = 7
    for c in sym:
        h = (h * 31 + ord(c)) % 360
    return h


def rank_mark(r):
    # 順位の上げ下げ（前日の最終スナップショット比）。据え置きは何も出さない。
    if r.get("rankNew"):
        return '<span class="rankdelta new">NEW</span>'
    d = r.get("rankDelta")
    if not d:
        return ""
    return f'<span class="rankdelta {"up" if d > 0 else "down"}">{"▲" if d > 0 else "▼"}{abs(d)}</span>'


def render_rows(data, rows, logos):
    e = lambda v: escape(str(v if v is not None else ""))
    out = []
    for r in rows:
        initial = r["name"].strip()[:1].upper()
        name_title = f'{e(r["name"])}（{e(r["ticker"])}）'
        out.append(
            '<tr>'
            f'<td class="num-rank">{r["rank"]}{rank_mark(r)}</td>'
            # ロゴは横スクロールしても左端に残す列なので、企業名とは別のセルにする。
            # 企業名は … で切れることがあり、ロゴだけ見えている状態でも社名が分かるよう、
            # どちらもマウスを乗せる（スマホはタップする）と全体とティッカーが見えるようにする
            f'<td class="lg" title="{name_title}">'
            f'<span class="logo" style="--seed:{hue(r["symbol"])}" data-letter="{e(initial)}">'
            # ロゴがある会社だけ img を出す。無い会社（大半）で 404 を大量に出さないため。
            + (f'<img src="logos/{e(r["symbol"])}.png" alt="" loading="lazy" decoding="async"'
               ' onerror="this.remove()">' if r["symbol"] in logos else '')
            + '</span></td>'
            f'<td class="co" title="{name_title}">'
            f'<span class="nm">{e(r["name"])}</span></td>'
            f'<td>{r["weight"]:.3f}%</td>'
            f'<td>{fmt_cap(r.get("marketCapUsd"))}</td>'
            f'<td>{fmt_price(r.get("price"))}'
            f'<span class="ccy">{e(r["currency"])}</span></td>'
            f'<td class="{cls(r.get("chg1d") or 0)}">{delta(r.get("chg1d"))}</td>'
            f'<td class="sectorcell">{e(r["sector"])}</td>'
            f'<td class="flagcell">{r["flag"]}'
            f'<span class="cname">{e(r["countryJa"])}</span></td>'
            "</tr>"
        )
    return "\n".join(out)


def md(iso):
    """"2026-09-18" -> "9/18"。"""
    try:
        _, m, d = iso.split("-")
        return f"{int(m)}/{int(d)}"
    except (AttributeError, ValueError):
        return iso or "—"


def render_navhead(nav):
    """基準価額の数字。推計値を出し、推計できなかった日は公表値を出す。"""
    if not nav:
        return ""
    est = nav.get("estimate")
    # 推計の前日比は、公表済みの最新の基準価額との差
    value, amt, chg = ((est["value"], est["chgAmount"], est["chg"]) if est
                       else (nav["latest"], nav["chgAmount"], nav["chg1d"]))
    return (f'<div class="fundname" id="fundname">{escape(nav["fund"])}</div>'
            f'<div class="navnow"><b>{value:,}</b><span class="navunit">円</span>'
            ' <span class="chglabel">前日比</span>'
            f'<span class="navchg {cls(chg)}">{"+" if amt > 0 else ""}{amt:,}円 ({pct(chg)})</span></div>')


def render_asof(data, nav):
    """基準価額の枠の下に出す、数字がいつの値か・どう推計したかの説明。"""
    t = datetime.fromisoformat(data["generatedAt"]).astimezone(JST)
    text = f'{t.month}/{t.day} {t.hour}:{t.minute:02d} JST 時点の推計'
    if data.get("pricesAsOf"):
        text += f'（米国株は {md(data["pricesAsOf"])} の終値）'
    text += '。'
    count = len(data["rows"])
    if data.get("universe", {}).get("securities", 0) > count:
        text += (f'株価は上位{count:,}銘柄だけ最新の値に差し替え、残りは'
                 f' {md(data.get("holdingsAsOf"))} 時点の保有銘柄の値のまま。')
    if nav and not nav.get("estimate"):
        text += f'基準価額は推計できなかったため {md(nav["asOf"])} の公表値。'
    return escape(text)


def fill(html, name, content):
    pat = re.compile(rf"(<!--ssg:{name}-->).*?(<!--/ssg:{name}-->)", re.S)
    if not pat.search(html):
        raise SystemExit(f"[render] index.html にマーカー ssg:{name} がありません")
    return pat.sub(lambda m: m.group(1) + content + m.group(2), html)


def write_pages(data, logos, out_dir):
    """全ページぶんの行 HTML を rows/<ページ>.json（{"rows": 行 HTML}）に書き出す。"""
    rows = data["rows"]
    pages = max(1, -(-len(rows) // PAGE_SIZE))
    out_dir.mkdir(parents=True, exist_ok=True)
    for n in range(1, pages + 1):
        part = render_rows(data, rows[(n - 1) * PAGE_SIZE:n * PAGE_SIZE], logos)
        (out_dir / f"{n}.json").write_text(json.dumps({"rows": part}, ensure_ascii=False, separators=(",", ":")))
    # 以前の国・地域別ページを取り除く
    shutil.rmtree(out_dir / "c", ignore_errors=True)

    # 銘柄数が減って余ったページや、以前の検索索引を消す
    for p in out_dir.glob("*.json"):
        if not (p.stem.isdigit() and 1 <= int(p.stem) <= pages):
            p.unlink()
    return pages


def num(v, cast=float):
    return cast(v) if v not in ("", None) else None


def read_day(history, day):
    """history/<日付>.csv と .meta.json を読む。"""
    meta = json.loads((history / f"{day}.meta.json").read_text())
    with open(history / f"{day}.csv", newline="", encoding="utf-8") as f:
        rows = [{
            "rank": int(r["rank"]), "symbol": r["symbol"], "ticker": r["ticker"],
            "name": r["name"], "sector": r["sector"], "country": r["country"],
            "currency": r["currency"], "price": num(r["price"]),
            "prevClose": num(r["prev_close"]), "marketCapUsd": num(r["market_cap_usd"], int),
            "weight": num(r["weight"]), "stale": r["stale"] == "1",
        } for r in csv.DictReader(f)]
    return meta, rows


def mark_changes(rows, prev):
    """株価の前日比と、順位の上げ下げを付ける（prev は前の日の {symbol: 順位}）。"""
    for r in rows:
        r["chg1d"] = (round((r["price"] / r["prevClose"] - 1) * 100, 2)
                      if r["price"] and r["prevClose"] else None)
        was = prev.get(r["symbol"])
        # 正なら順位が上がった
        r["rankDelta"] = was - r["rank"] if was else None
        # 前の日の銘柄数が大きく少なければ（掲載数を増やした直後は）新顔を判定しない。
        # 日々の入れ替えで数銘柄増減するのは普通なので、1割までの差は許す。
        r["rankNew"] = bool(prev) and len(prev) >= len(rows) * 0.9 and not was


def load_data(history):
    """最新の日付の CSV を表示用のデータにする。前の日の CSV があれば順位の上げ下げを付ける。"""
    days = sorted(p.name[:-len(".csv")] for p in history.glob("????-??-??.csv"))
    if not days:
        raise SystemExit(f"[render] {history} に履歴 CSV がありません")
    meta, rows = read_day(history, days[-1])

    def before():
        """株価の日付（pricesAsOf）が今回と違う、直近の CSV の行。

        週末や祝日の実行は株価が前回と同じ日付のままなので、すぐ前の CSV と比べると
        全銘柄が据え置きになる。日付が変わったところまでさかのぼる。
        """
        key = "pricesAsOf"
        for day in reversed(days[:-1]):
            m, prev = read_day(history, day)
            if not m.get(key) or not meta.get(key) or m[key] != meta[key]:
                return prev
        return []

    for r in rows:
        loc = r["country"]
        r["flag"], r["countryJa"] = COUNTRY.get(loc, ("🏳️", loc))
        r["sector"] = SECTOR_JA.get(r["sector"], r["sector"])
    mark_changes(rows, {r["symbol"]: r["rank"] for r in before()})
    return {**meta, "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", default=ROOT / "index.template.html", type=Path)
    ap.add_argument("--page", default=ROOT / "index.html", type=Path)
    ap.add_argument("--history", default=ROOT / "history", type=Path)
    ap.add_argument("--nav", default=ROOT / "nav.json", type=Path)
    ap.add_argument("--rows", default=ROOT / "rows", type=Path, help="ページごとの行 HTML の出力先")
    args = ap.parse_args()

    data = load_data(args.history)
    nav = json.loads(args.nav.read_text()) if args.nav.exists() else None

    html = args.template.read_text()
    count = len(data["rows"])
    scope = f"（上位{count:,}銘柄）" if data.get("universe", {}).get("securities", 0) > count else ""
    html = fill(html, "scope", scope)
    html = fill(html, "asof", render_asof(data, nav))
    html = fill(html, "navhead", render_navhead(nav))
    logos = {p.stem for p in (ROOT / "logos").glob("*.png")}
    html = fill(html, "rows", render_rows(data, data["rows"][:PAGE_SIZE], logos))
    pages = write_pages(data, logos, args.rows)
    html = re.sub(r'(<nav\b[^>]*\bid="pager"[^>]*\bdata-pages=")\d+',
                  lambda m: f"{m[1]}{pages}", html, count=1)
    # 基準価額が無いときは欄ごと隠す（チャートは JS が nav.json から描く）
    html = re.sub(r'<section class="nav" id="nav"(?: hidden)?>',
                  '<section class="nav" id="nav">' if nav else '<section class="nav" id="nav" hidden>',
                  html, count=1)
    html = re.sub(r'<body data-generated="[^"]*">',
                  f'<body data-generated="{escape(data["generatedAt"])}">', html, count=1)
    args.page.write_text(html)
    print(f"[render] {data['date']} の {len(data['rows'])} 銘柄を {args.page} と {args.rows}/"
          f"（{pages} ページ）に書き込みました")


if __name__ == "__main__":
    main()

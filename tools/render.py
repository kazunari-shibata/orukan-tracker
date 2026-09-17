"""履歴 CSV と nav.json から、ページを静的生成する。

GitHub Actions で実行して GitHub Pages に出す（生成物は git に入れない）。
  - index.template.html の <!--ssg:名前--> 〜 <!--/ssg:名前--> を埋めて index.html を書く
  - 表は100銘柄ずつのページ送り。1ページ目は index.html に直接書き込み、
    全ページぶんの行 HTML を rows/<ページ>.json に書き出す（2ページ目以降はブラウザが読む）
  - 前日比（順位の上げ下げ）は、最新の CSV とその前の日付の CSV を比べて出す

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


def fmt_cap(usd, jpy):
    # 時価総額は USD 建てで持っておき、表示時に円へ直す。
    # 為替が取れていなければドルのまま出す（単位を偽らない）。
    if usd is None:
        return "—"
    v, tril, bil = (usd * jpy, "兆円", "億円") if jpy else (usd, "兆ドル", "億ドル")
    return f"{v / 1e12:.1f}{tril}" if v >= 1e12 else f"{round(v / 1e8):,}{bil}"


def cap_title(usd, jpy):
    # 円で出しているときは、マウスを乗せると元のドル建てが見えるようにする
    if usd is None or not jpy:
        return ""
    return fmt_cap(usd, None)


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
    jpy = (data.get("fx") or {}).get("JPY")
    out = []
    for r in rows:
        chg_title = ("" if r.get("prevClose") is None
                     else f'前日終値 {fmt_price(r["prevClose"])} {r["currency"]}')
        initial = r["name"].strip()[:1].upper()
        out.append(
            f'<tr data-rank="{r["rank"]}">'
            f'<td class="num-rank">{r["rank"]}{rank_mark(r)}</td>'
            # 企業名は … で切れることがあるので、マウスを乗せると全体とティッカーが見えるようにする
            f'<td class="co" title="{e(r["name"])}（{e(r["ticker"])}）">'
            f'<span class="logo" style="--seed:{hue(r["symbol"])}" data-letter="{e(initial)}">'
            # ロゴがある会社だけ img を出す。無い会社（大半）で 404 を大量に出さないため。
            + (f'<img src="logos/{e(r["symbol"])}.png" alt="" loading="lazy" decoding="async"'
               ' onerror="this.remove()">' if r["symbol"] in logos else '')
            + '</span>'
            f'<span class="nm">{e(r["name"])}</span></td>'
            f'<td>{r["weight"]:.3f}%</td>'
            f'<td title="{e(cap_title(r.get("marketCapUsd"), jpy))}">{fmt_cap(r.get("marketCapUsd"), jpy)}</td>'
            f'<td>{fmt_price(r.get("price"))}'
            f'<span class="ccy">{e(r["currency"])}</span></td>'
            f'<td class="{cls(r.get("chg1d") or 0)}" title="{e(chg_title)}">{delta(r.get("chg1d"))}</td>'
            f'<td class="sectorcell">{e(r["sector"])}</td>'
            f'<td class="flagcell" title="{e(r["country"])}">{r["flag"]}'
            f'<span class="cname">{e(r["countryJa"])}</span></td>'
            "</tr>"
        )
    return "\n".join(out)


def render_navhead(nav):
    if not nav:
        return ""
    amt = nav["chgAmount"]
    return (
        f'<div class="fundname" id="fundname">{escape(nav["fund"])}</div>'
        f'<div class="navnow"><b id="navprice">{nav["latest"]:,}</b><span class="navunit">円</span>'
        ' <span class="chglabel">前日比</span>'
        f'<span id="navchg" class="{cls(nav["chg1d"])}">'
        f'{"+" if amt > 0 else ""}{amt:,}円 ({pct(nav["chg1d"])})</span></div>'
    )


def fill(html, name, content):
    pat = re.compile(rf"(<!--ssg:{name}-->).*?(<!--/ssg:{name}-->)", re.S)
    if not pat.search(html):
        raise SystemExit(f"[render] index.html にマーカー ssg:{name} がありません")
    return pat.sub(lambda m: m.group(1) + content + m.group(2), html)


def country_code(r):
    """国旗の絵文字から ISO の2文字コードを取り出す（🇯🇵 -> JP）。URL とファイル名に使う。"""
    code = "".join(chr(ord(c) - 0x1F1E6 + ord("A")) for c in r["flag"] if 0x1F1E6 <= ord(c) <= 0x1F1FF)
    return code or re.sub(r"[^A-Za-z]", "", r["country"]).upper()[:12] or "XX"


def countries(rows):
    """[(コード, 国旗, 日本語名, 銘柄数)] を銘柄数の多い順に。"""
    seen = {}
    for r in rows:
        code = country_code(r)
        if code not in seen:
            seen[code] = [code, r["flag"], r["countryJa"], 0]
        seen[code][3] += 1
    return sorted(seen.values(), key=lambda c: (-c[3], c[2]))


def render_countries(rows):
    """国・地域の絞り込み欄の選択肢。data-pages と data-count はページ送りが使う。"""
    total = len(rows)
    out = [f'<option value="" data-pages="{-(-total // PAGE_SIZE)}" data-count="{total}">'
           f'すべての国・地域（{total:,}）</option>']
    for code, flag, name, n in countries(rows):
        out.append(f'<option value="{code}" data-pages="{-(-n // PAGE_SIZE)}" data-count="{n}"'
                   f' data-name="{escape(name)}">{flag} {escape(name)}（{n:,}）</option>')
    return "".join(out)


MIX_TOP = 7   # 積み上げ棒は上位7項目に色を付け、残りは「その他」にまとめる（色は8色まで）


def render_mix(rows):
    """国・地域と業種の比率。1本の横積み上げ棒と、その下の凡例（名前・比率）で出す。"""
    def stack(items, title):
        head, rest = items[:MIX_TOP], items[MIX_TOP:]
        if rest:
            names = "、".join(f"{label} {w:.1f}%" for label, _, w in rest)
            head.append((f"その他（{len(rest)}）", None, sum(w for *_, w in rest), names))
        segs, legend = [], []
        for i, (label, href, w, *extra) in enumerate(head):
            slot = "other" if extra else i + 1
            tip = extra[0] if extra else f"{label} {w:.1f}%"
            segs.append(f'<i class="s{slot}" style="flex-grow:{w:.3f}" title="{escape(tip)}"></i>')
            inner = (f'<span class="sw s{slot}"></span>{escape(label)}'
                     f' <b>{w:.1f}%</b>')
            # 国は押すと下の表をその国で絞り込む
            legend.append('<li'
                          + (f' title="{escape(extra[0])}"' if extra else '') + '>'
                          + (f'<a href="{href}">{inner}</a>' if href else inner) + '</li>')
        return (f'<div class="mixrow"><h3>{title}</h3><div class="stack">{"".join(segs)}</div>'
                f'<ul class="mixlegend">{"".join(legend)}</ul></div>')

    by_country, by_sector = {}, {}
    for r in rows:
        code = country_code(r)
        c = by_country.setdefault(code, [r["countryJa"], 0.0])
        c[1] += r["weight"] or 0
        by_sector[r["sector"]] = by_sector.get(r["sector"], 0.0) + (r["weight"] or 0)

    countries_ = [(name, f"#country={code}&page=1", w)
                  for code, (name, w) in sorted(by_country.items(), key=lambda kv: -kv[1][1])]
    sectors = [(k, None, w) for k, w in sorted(by_sector.items(), key=lambda kv: -kv[1])]
    return stack(countries_, "国・地域") + stack(sectors, "業種")


def write_pages(data, logos, out_dir):
    """全ページぶんの行 HTML を rows/<ページ>.json に書き出す。{"rows": 行 HTML}。

    国・地域で絞り込んだページも rows/c/<国コード>/<ページ>.json に書く（順位は全体の順位のまま）。

    あわせて、銘柄検索用の索引 rows/search.json（[順位, ティッカー, 名前, 組入比率]）も書く。
    検索欄を使ったときにだけブラウザが読む。
    """
    rows = data["rows"]
    pages = max(1, -(-len(rows) // PAGE_SIZE))
    out_dir.mkdir(parents=True, exist_ok=True)
    for n in range(1, pages + 1):
        chunk = rows[(n - 1) * PAGE_SIZE:n * PAGE_SIZE]
        body = {"rows": render_rows(data, chunk, logos)}
        (out_dir / f"{n}.json").write_text(json.dumps(body, ensure_ascii=False, separators=(",", ":")))
    # 国・地域ごとのページ。国が減ったときに古いファイルが残らないよう、毎回作り直す
    shutil.rmtree(out_dir / "c", ignore_errors=True)
    for code, *_ in countries(rows):
        mine = [r for r in rows if country_code(r) == code]
        (out_dir / "c" / code).mkdir(parents=True)
        for n in range(1, -(-len(mine) // PAGE_SIZE) + 1):
            body = {"rows": render_rows(data, mine[(n - 1) * PAGE_SIZE:n * PAGE_SIZE], logos)}
            (out_dir / "c" / code / f"{n}.json").write_text(
                json.dumps(body, ensure_ascii=False, separators=(",", ":")))

    index = [[r["rank"], r["ticker"], r["name"], round(r["weight"], 3)] for r in rows]
    (out_dir / "search.json").write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")))
    # 銘柄数が減ってページが減ったら、余ったページを消す
    for p in out_dir.glob("*.json"):
        if p.stem != "search" and not (p.stem.isdigit() and 1 <= int(p.stem) <= pages):
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
            "baseWeight": num(r["base_weight"]), "weight": num(r["weight"]),
            "stale": r["stale"] == "1",
        } for r in csv.DictReader(f)]
    return meta, rows


def load_data(history):
    """最新の日付の CSV を表示用のデータにする。前の日付の CSV があれば順位の上げ下げを付ける。"""
    days = sorted(p.name[:-len(".csv")] for p in history.glob("????-??-??.csv"))
    if not days:
        raise SystemExit(f"[render] {history} に履歴 CSV がありません")
    meta, rows = read_day(history, days[-1])
    prev = {r["symbol"]: r["rank"] for r in read_day(history, days[-2])[1]} if len(days) > 1 else {}
    for r in rows:
        loc = r["country"]
        r["flag"], r["countryJa"] = COUNTRY.get(loc, ("🏳️", loc))
        r["sector"] = SECTOR_JA.get(r["sector"], r["sector"])
        r["chg1d"] = (round((r["price"] / r["prevClose"] - 1) * 100, 2)
                      if r["prevClose"] else None)
        was = prev.get(r["symbol"])
        # 正なら順位が上がった
        r["rankDelta"] = was - r["rank"] if was else None
        # 前の日の銘柄数が大きく少なければ（掲載数を増やした直後は）新顔を判定しない。
        # 日々の入れ替えで数銘柄増減するのは普通なので、1割までの差は許す。
        r["rankNew"] = bool(prev) and len(prev) >= len(rows) * 0.9 and not was
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
    html = fill(html, "navhead", render_navhead(nav))
    logos = {p.stem for p in (ROOT / "logos").glob("*.png")}
    html = fill(html, "rows", render_rows(data, data["rows"][:PAGE_SIZE], logos))
    pages = write_pages(data, logos, args.rows)
    html = fill(html, "countries", render_countries(data["rows"]))
    html = fill(html, "mix", render_mix(data["rows"]))
    html = re.sub(r'<nav class="pager" id="pager"[^>]*>',
                  f'<nav class="pager" id="pager" aria-label="ページ" data-size="{PAGE_SIZE}">', html, count=1)
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

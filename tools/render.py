"""履歴 CSV と nav.json から、ページを静的生成する。

GitHub Actions で実行して GitHub Pages に出す（生成物は git に入れない）。
  - index.template.html の <!--ssg:名前--> 〜 <!--/ssg:名前--> を埋めて index.html を書く
  - 表は100銘柄ずつのページ送り。1ページ目は index.html に直接書き込み、
    全ページぶんの行 HTML を rows/<ページ>.json に書き出す（2ページ目以降はブラウザが読む）
  - 前日比（順位の上げ下げ）は、最新の CSV と、株価（公表値モードは保有ファイル）の
    日付が違う直近の CSV を比べて出す
  - 表示は速報値モード（Yahoo の最新の終値と推計）と公表値モード（保有ファイルと公表された
    基準価額）の2通り。ファイルを増やさないよう、両方を同じ HTML と rows/*.json に入れ、
    どちらを見せるかはブラウザが切り替える

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
            f'<tr data-rank="{r["rank"]}">'
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
    """基準価額の数字。速報値モードは推計値（無ければ公表値）、公表値モードは公表値。"""
    if not nav:
        return ""

    def block(mode, value, amt, chg):
        return (f'<div class="navnow m-{mode}"><b>{value:,}</b><span class="navunit">円</span>'
                ' <span class="chglabel">前日比</span>'
                f'<span class="navchg {cls(chg)}">{"+" if amt > 0 else ""}{amt:,}円 ({pct(chg)})</span></div>')

    official = (nav["latest"], nav["chgAmount"], nav["chg1d"])
    est = nav.get("estimate")
    # 推計の前日比は、公表済みの最新の基準価額との差
    live = (est["value"], est["chgAmount"], est["chg"]) if est else official
    return (f'<div class="fundname" id="fundname">{escape(nav["fund"])}</div>'
            + block("live", *live) + block("pub", *official))


def render_modebar(data, nav):
    """切り替えボタンの横に出す、両モードの数字がいつの値かの説明。"""
    t = datetime.fromisoformat(data["generatedAt"]).astimezone(JST)
    text = (f'速報値は {t.month}/{t.day} {t.hour}:{t.minute:02d} JST 時点の推計、'
            f'公表値は {md(data.get("holdingsAsOf"))} 時点の保有銘柄'
            + (f'と {md(nav["asOf"])} の基準価額' if nav else '') + 'の公表データ。')
    return escape(text)


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
            names = "、".join(f"{label} {w:.1f}%" for label, w in rest)
            head.append((f"その他（{len(rest)}）", sum(w for _, w in rest), names))
        segs, legend = [], []
        for i, (label, w, *extra) in enumerate(head):
            slot = "other" if extra else i + 1
            tip = extra[0] if extra else f"{label} {w:.1f}%"
            segs.append(f'<i class="s{slot}" style="flex-grow:{w:.3f}" title="{escape(tip)}"></i>')
            legend.append('<li'
                          + (f' title="{escape(extra[0])}"' if extra else '') + '>'
                          f'<span class="sw s{slot}"></span>{escape(label)} <b>{w:.1f}%</b></li>')
        return (f'<div class="mixrow"><h3>{title}</h3><div class="stack">{"".join(segs)}</div>'
                f'<ul class="mixlegend">{"".join(legend)}</ul></div>')

    by_country, by_sector = {}, {}
    for r in rows:
        code = country_code(r)
        c = by_country.setdefault(code, [r["countryJa"], 0.0])
        c[1] += r["weight"] or 0
        by_sector[r["sector"]] = by_sector.get(r["sector"], 0.0) + (r["weight"] or 0)

    countries_ = [(name, w) for name, w in sorted(by_country.values(), key=lambda c: -c[1])]
    sectors = sorted(by_sector.items(), key=lambda kv: -kv[1])
    return stack(countries_, "国・地域") + stack(sectors, "業種")


def render_page(data, live, pub, n, logos):
    """n ページ目の行 HTML。{"rows": 速報値モード, "pub": 公表値モード}。並び順はモードごとに違う。"""
    part = lambda rows: render_rows(data, rows[(n - 1) * PAGE_SIZE:n * PAGE_SIZE], logos)
    return json.dumps({"rows": part(live), "pub": part(pub)}, ensure_ascii=False, separators=(",", ":"))


def write_pages(data, logos, out_dir):
    """全ページぶんの行 HTML を rows/<ページ>.json に書き出す（中身は render_page）。

    国・地域で絞り込んだページも rows/c/<国コード>/<ページ>.json に書く（順位は全体の順位のまま）。

    あわせて、銘柄検索用の索引 rows/search.json
    （[順位, ティッカー, 名前, 組入比率, 公表値モードの順位, 公表値モードの組入比率]）も書く。
    検索欄を使ったときにだけブラウザが読む。
    """
    rows, pub = data["rows"], data["pubRows"]
    pages = max(1, -(-len(rows) // PAGE_SIZE))
    out_dir.mkdir(parents=True, exist_ok=True)
    for n in range(1, pages + 1):
        (out_dir / f"{n}.json").write_text(render_page(data, rows, pub, n, logos))
    # 国・地域ごとのページ。国が減ったときに古いファイルが残らないよう、毎回作り直す
    shutil.rmtree(out_dir / "c", ignore_errors=True)
    for code, *_ in countries(rows):
        mine = [r for r in rows if country_code(r) == code]
        mine_pub = [r for r in pub if country_code(r) == code]
        (out_dir / "c" / code).mkdir(parents=True)
        for n in range(1, -(-len(mine) // PAGE_SIZE) + 1):
            (out_dir / "c" / code / f"{n}.json").write_text(render_page(data, mine, mine_pub, n, logos))

    index = [[r["rank"], r["ticker"], r["name"], round(r["weight"], 3),
              r["pubRank"], round(r["baseWeight"] or 0, 3)] for r in rows]
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
            # 公表値モード用（この列ができる前の CSV には無い）
            "basePrice": num(r.get("base_price")), "basePrevPrice": num(r.get("base_prev_price")),
            "baseMarketCapUsd": num(r.get("base_market_cap_usd"), int),
        } for r in csv.DictReader(f)]
    return meta, rows


def pub_order(rows):
    """公表値モードの並び。保有ファイルの組入比率の大きい順。"""
    return sorted(rows, key=lambda r: (-(r["baseWeight"] or 0), r["rank"]))


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
    """最新の日付の CSV を表示用のデータにする。前の日の CSV があれば順位の上げ下げを付ける。

    rows が速報値モード（Yahoo の株価と推計の組入比率）、pubRows が公表値モード
    （保有ファイルの株価と組入比率）。公表値モードの前日比は、前の日付の保有ファイルの株価と比べる。
    """
    days = sorted(p.name[:-len(".csv")] for p in history.glob("????-??-??.csv"))
    if not days:
        raise SystemExit(f"[render] {history} に履歴 CSV がありません")
    meta, rows = read_day(history, days[-1])

    def before(key):
        """key（株価か保有ファイルの日付）が今回と違う、直近の CSV の行。

        週末や祝日の実行は株価（公表値モードは保有ファイル）が前回と同じ日付のままなので、
        すぐ前の CSV と比べると全銘柄が据え置きになる。日付が変わったところまでさかのぼる。
        """
        for day in reversed(days[:-1]):
            m, prev = read_day(history, day)
            if not m.get(key) or not meta.get(key) or m[key] != meta[key]:
                return prev
        return []

    live_prev, pub_prev = before("pricesAsOf"), before("holdingsAsOf")
    for r in rows:
        loc = r["country"]
        r["flag"], r["countryJa"] = COUNTRY.get(loc, ("🏳️", loc))
        r["sector"] = SECTOR_JA.get(r["sector"], r["sector"])
    mark_changes(rows, {r["symbol"]: r["rank"] for r in live_prev})

    pub = []
    for i, r in enumerate(pub_order(rows), 1):
        r["pubRank"] = i
        pub.append({**r, "rank": i, "weight": r["baseWeight"] or 0.0, "price": r["basePrice"],
                    "prevClose": r["basePrevPrice"], "marketCapUsd": r["baseMarketCapUsd"],
                    "stale": False})
    mark_changes(pub, {r["symbol"]: i for i, r in enumerate(pub_order(pub_prev), 1)})
    return {**meta, "rows": rows, "pubRows": pub}


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
    html = fill(html, "modebar", render_modebar(data, nav))
    html = fill(html, "navhead", render_navhead(nav))
    logos = {p.stem for p in (ROOT / "logos").glob("*.png")}
    html = fill(html, "rows", render_rows(data, data["rows"][:PAGE_SIZE], logos))
    html = fill(html, "rowspub", render_rows(data, data["pubRows"][:PAGE_SIZE], logos))
    pages = write_pages(data, logos, args.rows)
    html = fill(html, "countries", render_countries(data["rows"]))
    html = fill(html, "mix", f'<div class="m-live">{render_mix(data["rows"])}</div>'
                             f'<div class="m-pub">{render_mix(data["pubRows"])}</div>')
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

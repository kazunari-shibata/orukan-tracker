#!/usr/bin/env python3
"""企業ロゴを取得して logos/ に置く（サイトから自前で配信する）。

取得元は各社の公式サイトのアイコン。企業を識別する目的で当該企業のロゴを
使う形なので、有料のロゴAPIは使わない。ロゴは滅多に変わらないため、
毎日のワークフローではなく手で実行してコミットする。

  pip install -r tools/requirements-logos.txt
  python tools/logos.py --out logos            # 上位100銘柄
  python tools/logos.py --out logos --top 0    # 全銘柄

全銘柄だと Yahoo へ約2,000回アクセスするので、並行数を絞って間隔を空ける。
調べた会社サイトとアイコンの大きさは logo_sources.json に残し、再実行ではそこから続ける
（途中でアクセス制限にかかっても、もう一度実行すれば残りだけ取る）。
"""
import argparse
import csv
import io
import json
import time
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SIZE = 64          # 表示は28px前後。高DPI向けに2倍強で持つ
MIN_PX = 1         # 画像が取れれば、小さくても保存する。ぼやけても頭文字のバッジよりは会社がわかる
SOURCES = Path(__file__).resolve().parent / "logo_sources.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; orukan-nakami/1.0)"}

# yfinance の website が持ち株会社などを指していて、ロゴが合わないもの
DOMAIN_OVERRIDE = {
    "GOOGL": "google.com",      # abc.xyz は Alphabet の持株会社サイト
    "GOOG": "google.com",
    "BRK-B": "berkshirehathaway.com",
    "COP": "conocophillips.com",
    "8306.T": "mufg.jp",
    "ROP.SW": "roche.com",
    "0700.HK": "tencent.com",
    "9988.HK": "alibaba.com",
    "005930.KS": "samsung.com",
    "000660.KS": "skhynix.com",
    "2330.TW": "tsmc.com",
    "2454.TW": "mediatek.com",
}


class RateLimited(Exception):
    pass


def domain_for(sym, yf):
    """Yahoo の会社情報から公式サイトのドメインを引く。無ければ ""。"""
    if sym in DOMAIN_OVERRIDE:
        return DOMAIN_OVERRIDE[sym]
    for attempt in range(3):
        try:
            site = yf.Ticker(sym).info.get("website") or ""
            break
        except Exception as e:
            if "Rate" not in type(e).__name__ and "Too Many" not in str(e):
                return ""
            time.sleep(60 * (attempt + 1))      # 制限は一時的なことが多いので待って取り直す
    else:
        raise RateLimited(sym)
    host = site.split("//")[-1].split("/")[0]
    return host[4:] if host.startswith("www.") else host


def fetch(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()
    except Exception:
        return None


def best_icon(domain, Image):
    """複数の取得元から拾って、いちばん解像度の高いものを返す。"""
    best, best_px = None, 0
    # www 付き / 無しはサイトによってどちらかしか生きていないので両方あたる
    hosts = [domain] if domain.startswith("www.") else [domain, f"www.{domain}"]
    urls = []
    for h in hosts:
        # apple-touch-icon は各社が用意している高解像度版で、いちばん綺麗なことが多い
        urls += [f"https://{h}/apple-touch-icon.png",
                 f"https://{h}/apple-touch-icon-precomposed.png",
                 f"https://www.google.com/s2/favicons?sz=128&domain={h}",
                 f"https://icons.duckduckgo.com/ip3/{h}.ico",
                 f"https://{h}/favicon.ico"]
    for url in urls:
        raw = fetch(url)
        # SPA が画像URLにも HTML を返すことがあるので弾く
        if not raw or raw[:1] == b"<":
            continue
        try:
            im = Image.open(io.BytesIO(raw))
            # ICO は複数解像度を含むので最大のものを選ぶ
            if getattr(im, "n_frames", 1) > 1 or im.format == "ICO":
                im = Image.open(io.BytesIO(raw))
                sizes = getattr(im, "ico", None)
                if sizes:
                    im = sizes.getimage(max(im.ico.sizes()))
            im = im.convert("RGBA")
        except Exception:
            continue
        px = min(im.size)
        if px > best_px:
            best, best_px = im, px
    return best, best_px


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="logos")
    ap.add_argument("--history", default="history",
                    help="最新の履歴 CSV から掲載中の銘柄を読む。組入比率の上位 --top 銘柄だけ取得する")
    ap.add_argument("--top", type=int, default=100,
                    help="組入比率の上位から取得する銘柄数。0 で全銘柄")
    ap.add_argument("--force", action="store_true", help="取得済みも取り直す")
    ap.add_argument("--symbols", default="",
                    help="この銘柄だけ取る（カンマ区切り、@ファイル名でも中身を読む）。"
                         "--top は見ない。前に何も取れなかった（px が 0）ものも対象にするので、"
                         "ロゴが無い銘柄だけ挙げて取り直すのに使う")
    args = ap.parse_args()

    from PIL import Image
    import yfinance as yf

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    latest = sorted(Path(args.history).glob("????-??-??.csv"))
    if not latest:
        raise SystemExit(f"[logos] {args.history} に履歴 CSV がありません")
    with open(latest[-1], newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows = rows[:args.top] if args.top else rows
    # Yahoo で引けない銘柄（"MAYBANK@Bursa Malaysia" など）はサイトも引けないので除く
    symbols = [r["symbol"] for r in rows if "@" not in r["symbol"]]
    if args.symbols:
        listed = (Path(args.symbols[1:]).read_text().split()
                  if args.symbols.startswith("@") else args.symbols.split(","))
        symbols = [s for s in (t.strip() for t in listed) if s and "@" not in s]

    # {シンボル: {"domain": ドメイン or "", "px": 取れたアイコンの大きさ（未取得は無し）}}
    sources = json.loads(SOURCES.read_text()) if SOURCES.exists() else {}

    def save_sources():
        SOURCES.write_text(json.dumps(dict(sorted(sources.items())), ensure_ascii=False, indent=1) + "\n")

    # 取得済み（png がある）、見送りを決めたもの（rejected）、MIN_PX 未満しか無かったものは飛ばす。
    # 前に小さすぎて保存しなかったもの（px が MIN_PX 以上で png が無い）はもう一度取る。
    def done(sym):
        info = sources.get(sym, {})
        # --symbols で名指ししたものは png 済みや px=0 でも取り直すが、
        # 人が見送りを決めた rejected だけは尊重する（--force なら無視して取る）
        if args.symbols:
            return "rejected" in info
        return ((out / f"{sym}.png").exists() or "rejected" in info
                or ("px" in info and info["px"] < MIN_PX))
    todo = [s for s in symbols if args.force or not done(s)]
    print(f"[logos] {len(todo)} 件を取得します（全 {len(symbols)} 件）", file=sys.stderr)

    # 1) 会社サイトを Yahoo で引く。約2,000件になるので並行2本でゆっくり。
    need = [s for s in todo if args.force or "domain" not in sources.get(s, {})]
    print(f"[logos] 会社サイトを Yahoo で調べます: {len(need)} 件", file=sys.stderr)

    def lookup(sym):
        time.sleep(0.3)
        return sym, domain_for(sym, yf)

    try:
        with ThreadPoolExecutor(2) as pool:
            for n, (sym, domain) in enumerate(pool.map(lookup, need), 1):
                sources.setdefault(sym, {})["domain"] = domain
                if n % 50 == 0:
                    save_sources()
                    print(f"[logos]   {n}/{len(need)}", file=sys.stderr)
    except RateLimited as e:
        save_sources()
        raise SystemExit(f"[logos] Yahoo のアクセス制限が続いたので止めました（{e}）。"
                         "時間を空けて再実行すると続きから取ります")
    save_sources()

    # 2) 会社サイトからアイコンを取る。相手は各社のサイトなので並行8本。
    def one(sym):
        domain = sources[sym].get("domain")
        if not domain:
            return sym, domain, 0
        im, px = best_icon(domain, Image)
        if im is not None and px >= MIN_PX:
            im.resize((SIZE, SIZE), Image.LANCZOS).save(out / f"{sym}.png", "PNG", optimize=True)
        return sym, domain, px

    got = small = none = 0
    with ThreadPoolExecutor(8) as pool:
        for n, (sym, domain, px) in enumerate(pool.map(one, todo), 1):
            sources[sym]["px"] = px
            if not domain:
                none += 1
            elif px >= MIN_PX:
                got += 1
            else:
                small += 1
            print(f"  {sym:<14}{str(domain):<32}{px}px", file=sys.stderr)
            if n % 100 == 0:
                save_sources()
    save_sources()

    have = len(list(out.glob("*.png")))
    print(f"[logos] 今回保存 {got} 件 / 小さすぎて見送り {small} 件 / サイト不明 {none} 件"
          f"（保存済みは全部で {have} 件）", file=sys.stderr)


if __name__ == "__main__":
    main()

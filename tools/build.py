#!/usr/bin/env python3
"""ACWI（iShares MSCI ACWI ETF）の上位銘柄スナップショットを生成する。

iShares の保有銘柄ファイル（数量・為替・基準時価総額）を土台に、
Yahoo Finance の直近株価で時価総額とウェイトを引き直して JSON に書き出す。
1 日 1 回（06:20 JST）実行する想定。

  python tools/build.py --out-dir history

結果は日付（日本時間）ごとの CSV（全銘柄の推計）と、同名の .meta.json
（保有ファイルの日付・為替など）に書き出す。同じ日に再実行すると上書きする。
この CSV が履歴として git に残り、表示用の HTML は render.py がそこから作る。
"""
import argparse
import csv
import gzip
import urllib.request
import json
import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from symbols import exchange_meta, to_yahoo  # noqa: E402

# 保有銘柄ファイル。userType=individual を付けると投資家区分のゲートを通り、
# UA も Cookie も無しで SpreadsheetML がそのまま返る。
# 複数銘柄のクオートを1回で返す Yahoo のエンドポイント。yfinance の正式な API ではない。
QUOTE_URL = "https://query2.finance.yahoo.com/v7/finance/quote"
QUOTE_CHUNK = 150   # 150件ずつで全銘柄（約2,200件）を取れることは確認済み

# 1銘柄ずつの取り直しは、組入比率の大きいこの銘柄数までに限る。全銘柄で取り直すと、
# Yahoo に無い銘柄のために毎日数百回アクセスすることになる。
EACH_LIMIT = 100

# Yahoo が補助単位で返す通貨 -> (通貨, 除数)。株価はこの単位で返る。
SUBUNIT = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100),
           "ILA": ("ILS", 100), "KWF": ("KWD", 1000)}


def yahoo_unit(currency):
    """Yahoo の通貨表記を (通貨, 除数) にする。"GBp" -> ("GBP", 100)。"""
    return SUBUNIT.get(currency, (currency, 1))

HOLDINGS_URL = (
    "https://www.blackrock.com/varnish-api/blk-one01-product-data/product-data"
    "/api/v1/get-fund-document?appType=PRODUCT_PAGE&appSubType=ISHARES"
    "&targetSite=us-ishares&locale=en_US&portfolioId=239600"
    "&component=fundDownload&userType=individual"
)

NS = {"ss": "urn:schemas-microsoft-com:office:spreadsheet"}
ROW = "{urn:schemas-microsoft-com:office:spreadsheet}Row"

MONTHS = {m: i for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}


def to_iso(s):
    """iShares の "Sep 10, 2026" を "2026-09-10" にする。

    strptime の %b はロケール依存なので、月名は自前の表で引く。
    """
    try:
        mon, day, year = s.replace(",", "").split()
        return f"{int(year):04d}-{MONTHS[mon]:02d}-{int(day):02d}"
    except (ValueError, KeyError):
        return s          # 形式が変わったら素のまま通す


def num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def load_holdings(path):
    """保有銘柄ファイルの中身（テキスト）を返す。URL / .xls / .xls.gz を受け付ける。"""
    if str(path).startswith("http"):
        with urllib.request.urlopen(path, timeout=60) as r:
            return r.read().decode("utf-8")
    path = Path(path)
    if path.suffix == ".gz":
        return gzip.decompress(path.read_bytes()).decode("utf-8")
    return path.read_text(encoding="utf-8")


def sheet_rows(raw, name):
    """SpreadsheetML から1シートだけ切り出して、行ごとのセルの文字列のリストにする。

    ファイル全体は免責文の生 HTML のせいで XML として壊れているので、シートごとに切り出してパースする。
    シートが無ければ None。
    """
    m = re.search(rf'<ss:Worksheet ss:Name="{name}">.*?</ss:Worksheet>', raw, re.S)
    if not m:
        return None
    root = ET.fromstring(
        '<?xml version="1.0"?><r xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
        + m.group(0)
        + "</r>"
    )
    rows = []
    for r in root.iter(ROW):
        cells = []
        for c in r.findall("ss:Cell", NS):
            d = c.find("ss:Data", NS)
            cells.append((d.text or "") if d is not None else "")
        rows.append(cells)
    return rows


def parse_holdings(path, raw=None):
    """iShares の SpreadsheetML の Holdings シートを読む。"""
    if raw is None:
        raw = load_holdings(path)
    rows = sheet_rows(raw, "Holdings")
    if rows is None:
        raise SystemExit(f"Holdings シートが見つかりません: {path}")

    raw_as_of = next((r[1] for r in rows if r and r[0] == "Fund Holdings as of"), "")
    as_of = to_iso(raw_as_of)
    hi = next(i for i, r in enumerate(rows) if r and r[0] == "Ticker")
    header = rows[hi]
    recs = [dict(zip(header, r)) for r in rows[hi + 1:] if len(r) >= len(header)]
    return as_of, recs


def parse_historical(raw):
    """Historical シート（ACWI の日々の NAV）を読む。[(日付, Non-FV NAV, 分配金)] の古い順。

    Non-FV NAV は各市場の終値そのままで計算した NAV（NAV per Share は米国の引けに合わせて
    海外株を補正している）。オルカンも各市場の終値で評価するので Non-FV を使う。
    分配金は権利落ち日の行に入っていて、その行の NAV はもう分配金のぶん下がっている。
    """
    rows = sheet_rows(raw, "Historical") or []
    if not rows:
        return []
    col = {name: i for i, name in enumerate(rows[0])}
    if not {"As Of", "Non-FV NAV", "Ex-Dividends"} <= col.keys():
        return []
    out = []
    for r in rows[1:]:
        if len(r) < len(col):
            continue
        nav = num(r[col["Non-FV NAV"]])
        if nav:
            out.append((to_iso(r[col["As Of"]]), nav, num(r[col["Ex-Dividends"]]) or 0.0))
    return sorted(out)


JST = timezone(timedelta(hours=9))

# 履歴 CSV の列。render.py もこの順で読む。
# base_ で始まる列は保有ファイルの値（公表値モード用）。base_prev_price はその前の日付の
# 保有ファイルの株価、base_market_cap_usd は時価総額を保有ファイルの株価と為替で引き直した値。
CSV_FIELDS = ["rank", "symbol", "ticker", "name", "sector", "country", "currency",
              "price", "prev_close", "market_cap_usd", "base_weight", "weight", "stale",
              "base_price", "base_prev_price", "base_market_cap_usd"]

# 評価額の水準（meta の levels）に入れる米国の取引日数。基準価額の推計が、公表済みの基準価額が
# 使った相場の日までさかのぼるのに使う。年末年始の連休をまたげる長さにしてある。
LEVELS_KEEP = 30

# 組入比率の答え合わせで、全銘柄とは別に見る上位の銘柄数（実際の順位で数える）
CHECK_TOP = 100


def fetch_quotes_batch(units, mismatched, retry=False, stamps=None, nocap=None):
    """現在値・前日終値・時価総額を、Yahoo のクオートからまとめて取る。

    units は {シンボル: (通貨, 除数)}。全銘柄でも認証1回＋150件ずつ十数回で済むので、
    1銘柄ずつ取るよりアクセス制限にかかりにくい。応答の中で欠けた銘柄は返さない
    （呼び出し側が1銘柄ずつの取得で補う）。

    Yahoo の建値の通貨が想定（取引所から決めた通貨と除数）と違う銘柄は、単位を
    取り違えるので使わず mismatched に入れる（米ドル建てで上場している海外株など）。

    取得の仕組みそのものが動かないとき（yfinance の内部 API が変わった、Yahoo の
    応答の形が変わった、1件も返らない）は、1銘柄ずつの取得に黙って切り替えず
    エラーで止める。切り替えるとアクセス数が100倍になり、気づかないままアクセス制限に
    かかりやすくなるため。止まればその日の CSV は書かれず、ページは前日のまま残る。

    株価はロンドンならペンス建てで返るので除数で割る。時価総額はロンドンでも
    ポンド建てで返る（fast_info はペンス建て）ので割らない。

    日足から前日終値を拾うと、当日分の行が NaN のまま先に生えている銘柄で
    前々日を掴んでしまう。regularMarketPreviousClose は取引所の立会日基準で
    確定しているので、株価サイトの前日比とも一致する。

    nocap を渡すと、株価はあるのに時価総額が無い銘柄の応答の項目名を入れる（原因調査用）。
    """
    try:
        from yfinance.data import YfData   # 認証（crumb）の面倒を見てもらう
    except ImportError as e:
        raise SystemExit(f"[orukan] yfinance の内部 API（yfinance.data.YfData）が見つかりません: {e}")

    out, syms = {}, list(units)
    for i in range(0, len(syms), QUOTE_CHUNK):
        chunk = syms[i:i + QUOTE_CHUNK]
        try:
            res = YfData().get(QUOTE_URL, params={"symbols": ",".join(chunk)}, timeout=30)
            items = res.json()["quoteResponse"]["result"]
        except Exception as e:
            raise SystemExit(f"[orukan] まとめ取得に失敗しました（{len(chunk)} 件）: {e!r}")
        # 取り直しでは Yahoo に無い銘柄だけを頼むこともあるので、空でも止めない
        if not items and not retry:
            raise SystemExit(f"[orukan] まとめ取得の応答が空でした（{len(chunk)} 件を要求）")
        for q in items:
            sym = q.get("symbol")
            if sym not in units:
                continue
            if q.get("currency") and yahoo_unit(q["currency"]) != units[sym]:
                mismatched[sym] = q["currency"]
                continue
            div = units[sym][1]
            price, prev = q.get("regularMarketPrice"), q.get("regularMarketPreviousClose")
            out[sym] = (price / div if price else None, prev / div if prev else None,
                        q.get("marketCap"))
            if nocap is not None:
                if price and not q.get("marketCap"):
                    nocap[sym] = sorted(q)
                else:
                    nocap.pop(sym, None)   # 取り直しで取れたら外す
            if stamps is not None and q.get("regularMarketTime"):
                stamps[sym] = (q["regularMarketTime"], q.get("marketState"))
    return out


def us_price_stamp(stamps, us_symbols):
    """米国株の株価がいつの値かを、多数決で決める。

    返すのは (米国東部時間の日付, 市場の状態)。推計と、後日 iShares が公表する実績
    （その日付時点の保有ファイル）を突き合わせるのに使う。市場の状態が REGULAR なら
    取引時間中の株価（終値ではない）なので、答え合わせには使えない。
    """
    from collections import Counter
    from zoneinfo import ZoneInfo

    et = ZoneInfo("America/New_York")
    days = Counter(datetime.fromtimestamp(stamps[s][0], et).date().isoformat()
                   for s in us_symbols if s in stamps)
    states = Counter(stamps[s][1] for s in us_symbols if s in stamps and stamps[s][1])
    return (days.most_common(1)[0][0] if days else None,
            states.most_common(1)[0][0] if states else None)


def fetch_quotes_each(units, yf, mismatched, workers=2):
    """まとめ取得で欠けた銘柄を1銘柄ずつ取る（fast_info。1銘柄2回アクセスする）。

    fast_info は時価総額も株価と同じ単位（ロンドンはペンス）で返すので、どちらも除数で割る。
    並行数を絞って、アクセス制限にかかりにくくしている。
    """
    def one(sym):
        try:
            fi = yf.Ticker(sym).fast_info
            if fi.get("currency") and yahoo_unit(fi["currency"]) != units[sym]:
                mismatched[sym] = fi["currency"]
                return sym, (None, None, None)
            vals = (fi.get("lastPrice"), fi.get("regularMarketPreviousClose"),
                    fi.get("marketCap"))
        except Exception:
            return sym, (None, None, None)
        return sym, tuple(v / units[sym][1] if v else None for v in vals)

    with ThreadPoolExecutor(workers) as pool:
        return dict(pool.map(one, units))


def fetch_fx(currencies, yf):
    """USD 1 単位あたりの現地通貨（保有ファイルの FX Rate と同じ向き）。

    まとめ取得で為替が欠けたときの予備。日足の直近の終値を使う。
    """
    # JPY は保有銘柄に日本株がなくても要る（時価総額を円で表示するため）。
    pairs = {c: f"{c}=X" for c in set(currencies) | {"JPY"} if c != "USD"}
    out = {"USD": 1.0}
    if not pairs:
        return out
    data = yf.download(list(pairs.values()), period="5d", interval="1d",
                       progress=False, auto_adjust=False)["Close"]
    for ccy, sym in pairs.items():
        try:
            series = data[sym].dropna() if hasattr(data, "columns") else data.dropna()
            if len(series):
                out[ccy] = float(series.iloc[-1])
        except Exception:
            pass
    return out


def previous_day(out_dir, day):
    """day より前の日付で最新の (meta, {symbol: CSV の行})。無ければ (None, {})。"""
    days = sorted(p.name[:-len(".csv")] for p in out_dir.glob("????-??-??.csv")
                  if p.name[:-len(".csv")] < day)
    if not days:
        return None, {}
    try:
        meta = json.loads((out_dir / f"{days[-1]}.meta.json").read_text())
        with open(out_dir / f"{days[-1]}.csv", newline="", encoding="utf-8") as f:
            return meta, {r["symbol"]: r for r in csv.DictReader(f)}
    except (OSError, ValueError):
        return None, {}


def check_weights(prev_meta, prev_rows, holdings_day, rows):
    """前回の推計（組入比率・順位）を、その株価の日付の保有ファイルと答え合わせする。

    前回の株価の日付が今回の保有ファイルの日付と同じときだけ比べる。前回が推計していない
    （保有ファイルがもう株価と同じ日付だった）ときや、日付が合わないときは None。
    比率の誤差からは、株価を差し替えられなかった行（stale）を外す。ファイルの値のままなので
    誤差が小さく見えるため。どちらかにしか無い銘柄は突き合わせられないので数えない。
    """
    if (not prev_meta or prev_meta.get("pricesAsOf") != holdings_day
            or prev_meta.get("holdingsAsOf") == holdings_day):
        return None
    actual = sorted(rows, key=lambda r: -r["base_weight"])
    pairs = []
    for rank, r in enumerate(actual, 1):
        p = prev_rows.get(r["symbol"])
        if p:
            pairs.append({"symbol": r["symbol"], "rank": rank, "est_rank": int(p["rank"]),
                          "weight": r["base_weight"], "est_weight": float(p["weight"]),
                          "stale": p["stale"] == "1"})

    def summary(ps):
        if not ps:
            return None
        errs = sorted((abs(p["est_weight"] - p["weight"]), p["symbol"]) for p in ps if not p["stale"])
        gaps = sorted(abs(p["est_rank"] - p["rank"]) for p in ps)
        return {
            "n": len(ps),
            "absSum": round(sum(e for e, _ in errs), 4),     # 比率の誤差の絶対値の合計（%）
            "absMax": round(errs[-1][0], 4) if errs else None,
            "absMaxSymbol": errs[-1][1] if errs else None,
            "rankMatch": round(sum(g == 0 for g in gaps) / len(gaps) * 100, 1),   # 順位の一致率（%）
            "rankGapMedian": gaps[len(gaps) // 2],
        }

    return {
        "estimatedOn": prev_meta.get("date"),
        "pricesAsOf": holdings_day,
        "all": summary(pairs),
        f"top{CHECK_TOP}": summary([p for p in pairs if p["rank"] <= CHECK_TOP]),
    }


def acwi_levels(hist, holdings_day, prices_day, ratio):
    """評価額の水準を米国の日付ごとに出す。{日付: 水準}。nav.py が基準価額の推計に使う。

    保有ファイルの日付までは ACWI の Non-FV NAV の推移（parse_historical）、そこから最新の終値までは
    ratio（total / fund_total）でつなぐ。水準は日付どうしの比にだけ意味があり、保有ファイルの日付を 1 とする。
    オルカンは分配しないので、ACWI の権利落ち日は分配金を足し戻して、落ちたぶんを戻す
    （オルカンが受け取る配当は源泉税が引かれるので、足し戻すぶんだけわずかに上振れる）。
    保有ファイルの日付の NAV が無ければ（シートが無い、形が変わった）空にして、推計を出さない。
    """
    days = [d for d, _, _ in hist]
    if holdings_day not in days:
        return {}
    end = days.index(holdings_day)
    kept = hist[max(0, end - LEVELS_KEEP + 1):end + 1]
    levels = {holdings_day: 1.0}
    # 新しい日から古い日へ。前の日の水準 ＝ その日の水準 × 前の日の NAV ÷（その日の NAV ＋ 分配金）
    for (day, nav, div), (prev_day, prev_nav, _) in zip(reversed(kept), reversed(kept[:-1])):
        levels[prev_day] = levels[day] * prev_nav / (nav + div)
    if prices_day and prices_day > holdings_day:
        levels[prices_day] = ratio
    return {d: round(v, 8) for d, v in sorted(levels.items())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdings", default=HOLDINGS_URL,
                    help="保有銘柄ファイル。既定は iShares から直接取得。"
                         "ローカルの .xls / .xls.gz も指定できる")
    ap.add_argument("--out-dir", required=True, type=Path,
                    help="日付ごとの CSV と .meta.json を書き出すディレクトリ")
    ap.add_argument("--top", type=int, default=None,
                    help="載せる銘柄数。既定は保有ファイルの全株式")
    args = ap.parse_args()

    import yfinance as yf

    raw = load_holdings(args.holdings)
    as_of, recs = parse_holdings(args.holdings, raw)
    fund_total = sum(num(r.get("Market Value")) or 0.0 for r in recs)
    equities = [r for r in recs if r.get("Asset Class") == "Equity"]
    equities.sort(key=lambda r: -(num(r.get("Weight (%)")) or 0.0))
    # 投資先の広がり。評価額ゼロで残っているだけの銘柄（ロシア株など）は数えない。
    # 社数ではなく銘柄数なのは、議決権の違うクラス株を会社単位に確実にまとめる手がかりが
    # ファイルに無いため（名前だけでまとめると、別会社の同名銘柄までまとめてしまう）。
    held = [r for r in equities if (num(r.get("Weight (%)")) or 0.0) > 0]
    universe = {
        "securities": len(held),
        "regions": len({r.get("Location") for r in held} - {"", "--", None}),
    }

    # 社名の日本語化や、同じ会社の複数クラス株の名寄せはしない。保有ファイルの1行を1銘柄として載せる。
    usable = []
    for r in held:
        qty, price_usd, fx = (num(r.get("Quantity")), num(r.get("Price")),
                              num(r.get("FX Rate")))
        if not qty or not price_usd or not fx:
            continue
        exchange, loc = r.get("Exchange", ""), r.get("Location", "")
        # Yahoo で引けない銘柄（マレーシア株など）も載せる。株価は保有ファイルの値のまま。
        sym = to_yahoo(r["Ticker"], exchange, loc)
        ccy, divisor = exchange_meta(exchange, loc)
        r["_sym"] = sym
        r["_key"] = sym or f"{r['Ticker']}@{exchange}"
        r["_ccy"] = ccy
        r["_divisor"] = divisor
        r["_local_price"] = price_usd * fx      # ファイル上の現地通貨建て株価
        usable.append(r)

    # held は組入比率の大きい順に並んでいる
    picks = usable[:args.top]
    units = {r["_sym"]: (r["_ccy"], r["_divisor"]) for r in picks if r["_sym"]}

    print(f"[orukan] {len(picks)} 銘柄（うち Yahoo で取得 {len(units)}）…", file=sys.stderr)
    # 為替も同じまとめ取得で取る。JPY は基準価額の推計に使うので日本株がなくても要る。
    fx_pairs = {f"{c}=X": c for c in ({p["_ccy"] for p in picks} | {"JPY"}) - {"USD"}}
    mismatched = {}
    wanted = {**units, **{s: (c, 1) for s, c in fx_pairs.items()}}
    stamps, nocap = {}, {}
    quotes = fetch_quotes_batch(wanted, mismatched, stamps=stamps, nocap=nocap)
    # Yahoo はたまに応答から数件落とす（毎回違う銘柄・為替）。欠けた分だけもう一度まとめて取る。
    gaps = {s: u for s, u in wanted.items()
            if s not in mismatched and None in (quotes.get(s) or (None, None))[:2]}
    if gaps:
        quotes.update(fetch_quotes_batch(gaps, mismatched, retry=True, stamps=stamps, nocap=nocap))

    fx_live = {"USD": 1.0}
    for pair, ccy in fx_pairs.items():
        rate = quotes.pop(pair, (None,))[0]
        if rate:
            fx_live[ccy] = rate
    # 現在値と前日終値はどちらも同じクオートから対で取る。
    missing = [s for s in units if None in (quotes.get(s) or (None, None))[:2]]
    # 大半が欠けるのは個々の銘柄の問題ではなく、応答の項目名が変わったなど仕組みの故障。
    # 1銘柄ずつの取得で黙って埋めず止める（fetch_quotes_batch の docstring 参照）。
    if len(missing) * 2 > len(units):
        raise SystemExit(f"[orukan] まとめ取得で {len(missing)}/{len(units)} 銘柄の株価が"
                         "欠けました。Yahoo の応答の形が変わった可能性があります")

    if missing_fx := {c for c in fx_pairs.values() if c not in fx_live}:
        fx_live = {**fetch_fx(missing_fx, yf), **fx_live}
    # 欠けた銘柄のうち、組入比率の上位のものだけ1銘柄ずつ取り直す
    upper = {r["_sym"] for r in picks[:EACH_LIMIT]}
    retry = {s: units[s] for s in missing if s in upper}
    if retry:
        quotes.update(fetch_quotes_each(retry, yf, mismatched))
    print(f"[orukan] まとめ取得 {len(units) - len(missing)} 銘柄（取り直し {len(gaps)} 件）/ "
          f"個別取得 {len(retry)} 銘柄", file=sys.stderr)

    generated = datetime.now(timezone.utc)
    day = generated.astimezone(JST).date().isoformat()
    # 公表値モードの前日比は、前の日付の保有ファイルの株価と比べる。iShares の更新が
    # 止まって保有ファイルが前回と同じ日付なら、前回の比較相手をそのまま引き継ぐ。
    prev_meta, prev_rows = previous_day(args.out_dir, day)
    same_file = bool(prev_meta) and prev_meta.get("holdingsAsOf") == as_of

    rows, mv_delta, stale, suspect, failed = [], 0.0, [], {}, {}
    for r in picks:
        sym = r["_sym"]
        base_mv = num(r.get("Market Value")) or 0.0
        qty = num(r["Quantity"])
        fx_file = num(r["FX Rate"])
        fx = fx_live.get(r["_ccy"], fx_file)
        # 為替が保有ファイルと大きくずれていたらデータ不良とみなす
        if not fx or abs(fx / fx_file - 1) > 0.25:
            fx = fx_file

        # 値はどれも取得時点で通貨単位（ペンスではなくポンド）に揃えてある
        price, prev_close, mcap = quotes.get(sym, (None, None, None)) if sym else (None,) * 3
        price = float(price) if price else None
        prev_close = float(prev_close) if prev_close else None
        mcap = float(mcap) if mcap else None
        # 保有ファイルの株価と大きく離れていたら、単位の違いや Yahoo 側の不整合とみなす
        # （エジプト株は iShares の評価額と Yahoo の株価が4割ほど違う）。為替と同じ基準。
        if price is not None and abs(price / r["_local_price"] - 1) > 0.25:
            suspect[sym] = round(price / r["_local_price"], 2)
            price = prev_close = mcap = None
        is_stale = price is None
        if is_stale and sym and sym not in suspect:
            failed[sym] = num(r.get("Weight (%)")) or 0.0
        if is_stale:
            price = r["_local_price"]
            stale.append(r["_key"])

        mv = qty * price / fx
        mv_delta += mv - base_mv
        loc = r.get("Location", "")
        prev = prev_rows.get(r["_key"], {})
        base_prev = num(prev.get("base_prev_price" if same_file else "base_price"))
        # 発行済株式数は変わらないとみなし、時価総額を保有ファイルの株価と為替に引き直す
        base_mcap = mcap * r["_local_price"] / price / fx_file if mcap and not is_stale else None
        rows.append({
            "ticker": r["Ticker"],
            "symbol": r["_key"],
            "name": r["Name"],                  # 保有ファイルの表記のまま
            "sector": r.get("Sector", ""),
            "country": loc,
            "currency": r["_ccy"],
            "price": round(price, 4),
            "prev_close": round(prev_close, 4) if prev_close else None,
            "market_cap_usd": round(mcap / fx) if mcap else None,
            "base_weight": num(r.get("Weight (%)")) or 0.0,
            "stale": is_stale,
            "base_price": round(r["_local_price"], 4),
            "base_prev_price": round(base_prev, 4) if base_prev else None,
            "base_market_cap_usd": round(base_mcap) if base_mcap else None,
            "_mv": mv,
        })

    total = fund_total + mv_delta
    for row in rows:
        row["weight"] = round(row.pop("_mv") / total * 100, 5)

    rows.sort(key=lambda x: -x["weight"])
    for i, row in enumerate(rows, 1):
        row["rank"] = i

    prices_as_of, us_market_state = us_price_stamp(
        stamps, [r["_sym"] for r in picks if r["_sym"] and r.get("Location") == "United States"])
    levels = acwi_levels(parse_historical(raw), as_of, prices_as_of, total / fund_total)
    if not levels:
        print(f"::warning::[orukan] Historical シートに {as_of} の Non-FV NAV が無いので、"
              "基準価額の推計に使う水準を出しません", file=sys.stderr)
    meta = {
        "date": day,
        "generatedAt": generated.isoformat(timespec="seconds"),
        "holdingsAsOf": as_of,
        "fundName": "iShares MSCI ACWI ETF",
        "count": len(rows),
        "universe": universe,
        "coveredWeight": round(sum(r["weight"] for r in rows), 2),
        # 米国株の株価の日付（米国東部時間）と、取得時の市場の状態（REGULAR なら取引時間中）。
        # 推計の答え合わせをするなら、holdingsAsOf がこの日付の保有ファイルと比べる。
        "pricesAsOf": prices_as_of,
        "usMarketState": us_market_state,
        "fx": {k: round(v, 4) for k, v in sorted(fx_live.items())},
        # 米国の日付ごとの評価額の水準。nav.py が基準価額の推計に使う（acwi_levels 参照）
        "levels": levels,
        # 前回の推計の答え合わせ（check_weights 参照）
        "check": check_weights(prev_meta, prev_rows, as_of, rows),
        # 手当てが要るかもしれないもの。notify.py が前回から増えたものを Slack に出す。
        # Yahoo で引けない取引所（マレーシア株など）の銘柄は毎日同じなので入れない
        "issues": {
            # Yahoo のシンボルはあるのに株価が取れなかった銘柄 {シンボル: 保有ファイルの比率}
            # （通貨が違って使わなかった銘柄は mismatched に出るので除く）
            "priceMissing": dict(sorted(((s, w) for s, w in failed.items() if s not in mismatched),
                                        key=lambda kv: -kv[1])),
            "suspect": suspect,                 # 保有ファイルの株価と離れすぎて使わなかった（倍率）
            "mismatched": mismatched,           # 建値の通貨が想定と違った
            "noMarketCap": [s for s in nocap if s in units],   # 株価はあるが時価総額が無い
        },
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    with open(args.out_dir / f"{day}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow({**row, "stale": int(row["stale"])})
    (args.out_dir / f"{day}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1) + "\n")
    print(f"[orukan] {args.out_dir}/{day}.csv を書き出しました "
          f"({len(rows)}銘柄 / 合計ウェイト {meta['coveredWeight']}%)", file=sys.stderr)
    check = meta["check"]
    if check:
        top, whole = check[f"top{CHECK_TOP}"], check["all"]
        print(f"::notice::[orukan] 組入比率の答え合わせ（{check['estimatedOn']} の推計 vs "
              f"{check['pricesAsOf']} の保有ファイル）: 上位{CHECK_TOP} 誤差合計 {top['absSum']}% / "
              f"順位一致 {top['rankMatch']}%、全{whole['n']}銘柄 誤差合計 {whole['absSum']}% / "
              f"最大 {whole['absMax']}%（{whole['absMaxSymbol']}）/ 順位のずれの中央値 {whole['rankGapMedian']}",
              file=sys.stderr)
    if mismatched:
        print(f"[orukan] 建値の通貨が想定と違うため使わなかった: "
              f"{', '.join(f'{k}({v})' for k, v in mismatched.items())}", file=sys.stderr)
    if suspect:
        print(f"[orukan] 保有ファイルの株価と離れすぎているため使わなかった（倍率）: "
              f"{', '.join(f'{k}({v})' for k, v in suspect.items())}", file=sys.stderr)
    # 株価は取れたのに時価総額だけ無い銘柄。GitHub Actions でだけ起きていて手元では再現しないので、
    # 応答にどの項目が入っていたかを出して原因を探る（sharesOutstanding があれば株価から計算できる）
    nocap = {s: keys for s, keys in nocap.items() if s in units}
    if nocap:
        with_shares = [s for s, keys in nocap.items() if "sharesOutstanding" in keys]
        print(f"[orukan] 株価はあるが時価総額が無い（{len(nocap)} 銘柄、うち sharesOutstanding あり "
              f"{len(with_shares)}）: {', '.join(list(nocap)[:30])}{' …' if len(nocap) > 30 else ''}",
              file=sys.stderr)
        first = next(iter(nocap))
        print(f"[orukan] {first} の応答の項目: {', '.join(nocap[first])}", file=sys.stderr)
    if stale:
        print(f"[orukan] 株価を取得できずファイル値を使用（{len(stale)} 銘柄）: "
              f"{', '.join(stale[:30])}{' …' if len(stale) > 30 else ''}", file=sys.stderr)


if __name__ == "__main__":
    main()

"""iShares の Holdings ティッカー/取引所を Yahoo Finance のシンボルへ変換する。"""

SUFFIX = {
    # 米国（サフィックスなし）
    "NASDAQ": "", "NYSE": "", "NYSE Arca": "", "Nyse Mkt Llc": "",
    "Cboe BZX formerly known as BATS": "", "Cboe BZX": "",
    # 海外
    "Taiwan Stock Exchange": ".TW",
    "Gretai Securities Market": ".TWO",
    "Korea Exchange (Stock Market)": ".KS",
    "Korea Exchange (Kosdaq)": ".KQ",
    "Tokyo Stock Exchange": ".T",
    "Hong Kong Exchanges And Clearing Ltd": ".HK",
    "London Stock Exchange": ".L",
    "SIX Swiss Exchange": ".SW",
    "Xetra": ".DE", "Deutsche Boerse Xetra": ".DE",
    "Euronext Amsterdam": ".AS",
    "Nyse Euronext - Euronext Paris": ".PA",
    "Nyse Euronext - Euronext Brussels": ".BR",
    "Nyse Euronext - Euronext Lisbon": ".LS",
    "Bolsa De Madrid": ".MC",
    "Borsa Italiana": ".MI",
    "Toronto Stock Exchange": ".TO",
    "Asx - All Markets": ".AX",
    "Nasdaq Omx Helsinki Ltd.": ".HE",
    "Nasdaq Omx Stockholm": ".ST",
    "Nasdaq Omx Copenhagen A/S": ".CO",
    "Omx Nordic Exchange Copenhagen A/S": ".CO",
    "Nasdaq Omx Nordic": None,                  # 国で決まる（NORDIC 参照）
    "Oslo Stock Exchange": ".OL", "Oslo Bors Asa": ".OL",
    "Irish Stock Exchange": ".IR", "Irish Stock Exchange - All Market": ".IR",
    "Wiener Boerse Ag": ".VI",
    "Athens Exchange S.A. Cash Market": ".AT",
    "Warsaw Stock Exchange/Equities/Main Market": ".WA",
    "Budapest Stock Exchange": ".BD",
    "Prague Stock Exchange": ".PR",
    "Istanbul Stock Exchange": ".IS",
    "Singapore Exchange": ".SI",
    "Indonesia Stock Exchange": ".JK",
    "Stock Exchange Of Thailand": ".BK",
    "National Stock Exchange Of India": ".NS",
    "Bombay Stock Exchange": ".BO", "Bse Ltd": ".BO",
    "Shanghai Stock Exchange": ".SS",
    "Shenzhen Stock Exchange": ".SZ",
    "New Zealand Exchange Ltd": ".NZ",
    "Tel Aviv Stock Exchange": ".TA",
    "Saudi Stock Exchange": ".SR",
    "Qatar Exchange": ".QA",
    "Kuwait Stock Exchange": ".KW",
    "Egyptian Exchange": ".CA",
    "Johannesburg Stock Exchange": ".JO",
    "Bolsa Mexicana De Valores": ".MX",
    "Bm&Fbovespa Sa": ".SA", "XBSP": ".SA",
    "Santiago Stock Exchange": ".SN",
    "Bolsa De Valores De Colombia": ".CL",
    # Yahoo で引けないので載せない（保有ファイルの株価のまま表示する）:
    # Bursa Malaysia（Yahoo は数字コードで、ファイルの略称から引けない）、
    # Abu Dhabi / Dubai、Philippine Stock Exchange（Yahoo にデータが無い）
}

# 北欧の統合市場は、ファイル上は同じ取引所名で国ごとに Yahoo のサフィックスが違う
NORDIC = {"Sweden": (".ST", "SEK"), "Finland": (".HE", "EUR"),
          "Denmark": (".CO", "DKK"), "Iceland": (".IC", "ISK")}

# iShares のティッカーが Yahoo と食い違う銘柄だけ手当てする。
OVERRIDE = {
    "CICT.SI": "C38U.SI",          # CapitaLand Integrated Commercial Trust
    "CLAR.SI": "A17U.SI",          # CapitaLand Ascendas REIT
    "SHFL.NS": "SHRIRAMFIN.NS",    # Shriram Finance
    "BAAKOMB.PR": "KOMB.PR",       # Komercni banka
}


def to_yahoo(ticker, exchange, location=""):
    """変換できない取引所・ティッカーは None を返す（呼び出し側でファイルの株価を使う）。"""
    suffix = SUFFIX.get(exchange)
    if exchange == "Nasdaq Omx Nordic":
        suffix = NORDIC.get(location, (None,))[0]
    if suffix is None:
        return None
    t = ticker.strip()
    if not t or t == "--":
        return None
    if suffix == "":
        return OVERRIDE.get(t, t.replace(" ", "-"))  # "BRK B" -> "BRK-B"
    if suffix == ".HK":
        sym = t.zfill(4) + suffix                     # "700" -> "0700.HK"
    elif suffix in (".KS", ".KQ"):
        sym = t.zfill(6) + suffix
    else:
        # 取引所ごとの付記を落とす: ロンドン "RR." / メキシコ "WALMEX*" /
        # タイの無議決権預託証券 "PTT.R" / トルコ "ASELS.E"
        if suffix == ".BK" and t.endswith(".R"):
            t = t[:-2]
        elif suffix == ".IS" and t.endswith(".E"):
            t = t[:-2]
        t = t.rstrip(".*")
        sym = t.replace(" ", "-").replace(".", "-") + suffix   # "BT.A" -> "BT-A.L"
    return OVERRIDE.get(sym, sym)


# 取引所 -> (現地通貨, Yahoo の建値を現地通貨にする除数)
# 保有ファイルの Currency 列は全銘柄 USD（換算済み）なので通貨はここで決める。
# ロンドン/ヨハネスブルグ/テルアビブ（1/100）とクウェート（フィルス、1/1000）は
# Yahoo が補助単位で株価を返すため除数を持つ。
EXCHANGE_META = {
    "NASDAQ": ("USD", 1), "NYSE": ("USD", 1), "NYSE Arca": ("USD", 1),
    "Nyse Mkt Llc": ("USD", 1), "Cboe BZX formerly known as BATS": ("USD", 1),
    "Cboe BZX": ("USD", 1),
    "Taiwan Stock Exchange": ("TWD", 1), "Gretai Securities Market": ("TWD", 1),
    "Korea Exchange (Stock Market)": ("KRW", 1), "Korea Exchange (Kosdaq)": ("KRW", 1),
    "Tokyo Stock Exchange": ("JPY", 1),
    "Hong Kong Exchanges And Clearing Ltd": ("HKD", 1),
    "London Stock Exchange": ("GBP", 100),
    "SIX Swiss Exchange": ("CHF", 1),
    "Xetra": ("EUR", 1), "Deutsche Boerse Xetra": ("EUR", 1),
    "Euronext Amsterdam": ("EUR", 1),
    "Nyse Euronext - Euronext Paris": ("EUR", 1),
    "Nyse Euronext - Euronext Brussels": ("EUR", 1),
    "Nyse Euronext - Euronext Lisbon": ("EUR", 1),
    "Bolsa De Madrid": ("EUR", 1),
    "Borsa Italiana": ("EUR", 1),
    "Toronto Stock Exchange": ("CAD", 1),
    "Asx - All Markets": ("AUD", 1),
    "Nasdaq Omx Helsinki Ltd.": ("EUR", 1),
    "Nasdaq Omx Stockholm": ("SEK", 1),
    "Nasdaq Omx Copenhagen A/S": ("DKK", 1),
    "Omx Nordic Exchange Copenhagen A/S": ("DKK", 1),
    "Oslo Stock Exchange": ("NOK", 1), "Oslo Bors Asa": ("NOK", 1),
    "Irish Stock Exchange": ("EUR", 1), "Irish Stock Exchange - All Market": ("EUR", 1),
    "Wiener Boerse Ag": ("EUR", 1),
    "Athens Exchange S.A. Cash Market": ("EUR", 1),
    "Warsaw Stock Exchange/Equities/Main Market": ("PLN", 1),
    "Budapest Stock Exchange": ("HUF", 1),
    "Prague Stock Exchange": ("CZK", 1),
    "Istanbul Stock Exchange": ("TRY", 1),
    "Singapore Exchange": ("SGD", 1),
    "Indonesia Stock Exchange": ("IDR", 1),
    "Stock Exchange Of Thailand": ("THB", 1),
    "Bursa Malaysia": ("MYR", 1),
    "Philippine Stock Exchange Inc.": ("PHP", 1),
    "National Stock Exchange Of India": ("INR", 1),
    "Bombay Stock Exchange": ("INR", 1), "Bse Ltd": ("INR", 1),
    "Shanghai Stock Exchange": ("CNY", 1),
    "Shenzhen Stock Exchange": ("CNY", 1),
    "New Zealand Exchange Ltd": ("NZD", 1),
    "Tel Aviv Stock Exchange": ("ILS", 100),
    "Saudi Stock Exchange": ("SAR", 1),
    "Qatar Exchange": ("QAR", 1),
    "Kuwait Stock Exchange": ("KWD", 1000),
    "Abu Dhabi Securities Exchange": ("AED", 1),
    "Dubai Financial Market": ("AED", 1),
    "Egyptian Exchange": ("EGP", 1),
    "Johannesburg Stock Exchange": ("ZAR", 100),
    "Bolsa Mexicana De Valores": ("MXN", 1),
    "Bm&Fbovespa Sa": ("BRL", 1), "XBSP": ("BRL", 1),
    "Santiago Stock Exchange": ("CLP", 1),
    "Bolsa De Valores De Colombia": ("COP", 1),
}


def exchange_meta(exchange, location=""):
    if exchange == "Nasdaq Omx Nordic":
        return (NORDIC.get(location, (None, "USD"))[1], 1)
    return EXCHANGE_META.get(exchange, ("USD", 1))

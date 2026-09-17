"""表示用の対応表。保有ファイルの英語表記 -> 日本語・国旗（render.py が使う）。"""

# iShares の Location -> (国旗, 日本語名)。台湾・香港が入るので「国・地域」。
COUNTRY = {
    "United States": ("🇺🇸", "アメリカ"), "Japan": ("🇯🇵", "日本"),
    "United Kingdom": ("🇬🇧", "イギリス"), "China": ("🇨🇳", "中国"),
    "Taiwan": ("🇹🇼", "台湾"), "Korea (South)": ("🇰🇷", "韓国"),
    "Canada": ("🇨🇦", "カナダ"), "France": ("🇫🇷", "フランス"),
    "Germany": ("🇩🇪", "ドイツ"), "Switzerland": ("🇨🇭", "スイス"),
    "Netherlands": ("🇳🇱", "オランダ"), "Australia": ("🇦🇺", "オーストラリア"),
    "India": ("🇮🇳", "インド"), "Spain": ("🇪🇸", "スペイン"),
    "Italy": ("🇮🇹", "イタリア"), "Sweden": ("🇸🇪", "スウェーデン"),
    "Denmark": ("🇩🇰", "デンマーク"), "Ireland": ("🇮🇪", "アイルランド"),
    "Hong Kong": ("🇭🇰", "香港"), "Brazil": ("🇧🇷", "ブラジル"),
    "Singapore": ("🇸🇬", "シンガポール"), "Finland": ("🇫🇮", "フィンランド"),
    "Belgium": ("🇧🇪", "ベルギー"), "Norway": ("🇳🇴", "ノルウェー"),
    "Israel": ("🇮🇱", "イスラエル"), "Mexico": ("🇲🇽", "メキシコ"),
    "South Africa": ("🇿🇦", "南アフリカ"), "Austria": ("🇦🇹", "オーストリア"),
    "Portugal": ("🇵🇹", "ポルトガル"), "New Zealand": ("🇳🇿", "ニュージーランド"),
    "Thailand": ("🇹🇭", "タイ"), "Indonesia": ("🇮🇩", "インドネシア"),
    "Malaysia": ("🇲🇾", "マレーシア"), "Saudi Arabia": ("🇸🇦", "サウジアラビア"),
    "United Arab Emirates": ("🇦🇪", "アラブ首長国連邦"), "Poland": ("🇵🇱", "ポーランド"),
    "Turkey": ("🇹🇷", "トルコ"), "Greece": ("🇬🇷", "ギリシャ"),
    "Chile": ("🇨🇱", "チリ"), "Philippines": ("🇵🇭", "フィリピン"),
    "Colombia": ("🇨🇴", "コロンビア"), "Czech Republic": ("🇨🇿", "チェコ"),
    "Egypt": ("🇪🇬", "エジプト"), "Hungary": ("🇭🇺", "ハンガリー"),
    "Kuwait": ("🇰🇼", "クウェート"), "Peru": ("🇵🇪", "ペルー"),
    "Qatar": ("🇶🇦", "カタール"), "Russian Federation": ("🇷🇺", "ロシア"),
}



SECTOR_JA = {
    "Information Technology": "情報技術", "Financials": "金融",
    "Industrials": "資本財", "Consumer Discretionary": "一般消費財",
    "Health Care": "ヘルスケア", "Communication": "通信サービス",
    "Consumer Staples": "生活必需品", "Energy": "エネルギー",
    "Materials": "素材", "Utilities": "公益", "Real Estate": "不動産",
}

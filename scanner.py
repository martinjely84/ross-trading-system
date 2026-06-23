# ============================================================
# scanner.py — Pre-market gap scanner + real-time momentum
# Uses yfinance for free data (no API key required)
# ============================================================
import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
import time
import config
from session import et_now

# Known catalyst keywords for news detection
CATALYST_KEYWORDS = [
    "earnings", "beat", "revenue", "fda", "approval", "trial", "contract",
    "merger", "acquisition", "acquired", "upgrade", "price target",
    "short squeeze", "defense", "government", "partnership", "license",
    "breakthrough", "positive", "granted", "wins", "awarded"
]


def get_finviz_gap_scanner():
    """
    Use finvizfinance library to get pre-market gappers.
    Returns list of tickers to investigate further.
    """
    try:
        from finvizfinance.screener.overview import Overview
        foverview = Overview()
        foverview.set_filter(filters_dict={
            'Gap': 'Up 2%',
            'Country': 'USA',
        })
        df = foverview.screener_view()
        if df is None or df.empty:
            print("[SCANNER] No results from finvizfinance")
            return []
        # Sort by Change descending (biggest gappers first).
        # finvizfinance may return Change as a numeric fraction OR a percent
        # string like "5.20%"; coerce robustly so a string format doesn't blow
        # up the whole scan and silently return zero tickers.
        change = df['Change'].astype(str).str.replace('%', '', regex=False).str.strip()
        df['Change'] = pd.to_numeric(change, errors='coerce')
        df = df.dropna(subset=['Change']).sort_values('Change', ascending=False)
        tickers = df['Ticker'].tolist()[:30]
        print(f"[SCANNER] Found {len(tickers)} tickers from Finviz")
        return tickers
    except Exception as e:
        print(f"[SCANNER] Finviz error: {e}")
        return []


def get_stock_data(ticker: str):
    """
    Pull key data for a ticker using yfinance.
    Returns dict with all fields needed for watchlist evaluation.
    """
    try:
        tk = yf.Ticker(ticker)
        info = tk.info
        fast_info = tk.fast_info

        prev_close = fast_info.get("previousClose") or info.get("previousClose", 0)
        current_price = fast_info.get("lastPrice") or info.get("currentPrice", 0)

        if not prev_close or not current_price:
            return None

        gap_pct = ((current_price - prev_close) / prev_close) * 100

        # Pre-market volume — yfinance doesn't give pre-market vol directly
        # Fall back to regular market volume if pre-market not available
        premarket_price = info.get("preMarketPrice") or current_price
        premarket_vol = info.get("preMarketVolume") or info.get("regularMarketVolume") or 0

        # Relative volume — compare to 30-day average
        avg_vol = info.get("averageVolume", 0) or info.get("averageDailyVolume10Day", 0)
        rel_vol = (premarket_vol / avg_vol) if avg_vol > 0 else 0

        # Float and short interest
        float_shares = info.get("floatShares", 0) or 0
        shares_short = info.get("sharesShort", 0) or 0
        short_pct = (shares_short / float_shares * 100) if float_shares > 0 else 0

        # 52-week high / previous day high
        prev_high = info.get("regularMarketDayHigh", 0) or 0
        week52_high = info.get("fiftyTwoWeekHigh", 0) or 0

        return {
            "ticker": ticker,
            "current_price": round(current_price, 2),
            "premarket_price": round(premarket_price, 2),
            "prev_close": round(prev_close, 2),
            "gap_pct": round(gap_pct, 2),
            "premarket_vol": int(premarket_vol),
            "avg_vol": int(avg_vol),
            "rel_vol": round(rel_vol, 2),
            "float": int(float_shares),
            "short_pct": round(short_pct, 2),
            "prev_day_high": round(prev_high, 2),
            "week52_high": round(week52_high, 2),
            "market_cap": info.get("marketCap", 0),
            "exchange": info.get("exchange", ""),
            "name": info.get("shortName", ticker),
        }
    except Exception as e:
        print(f"[SCANNER] Error fetching {ticker}: {e}")
        return None


def check_catalyst(ticker: str, stock_info: dict):
    """
    Check for news catalyst via Finviz news tab.
    Returns (has_catalyst, catalyst_summary).
    """
    url = f"https://finviz.com/quote.ashx?t={ticker}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        news_table = soup.find("table", {"id": "news-table"})
        if not news_table:
            return False, "No catalyst found"

        headlines = []
        for row in news_table.find_all("tr")[:10]:
            td = row.find_all("td")
            if len(td) >= 2:
                headlines.append(td[1].get_text(strip=True).lower())

        for headline in headlines:
            for kw in CATALYST_KEYWORDS:
                if kw in headline:
                    # Return the first matching headline (capitalized)
                    return True, td[1].get_text(strip=True)[:120]

        return False, "No confirmed catalyst"
    except Exception as e:
        print(f"[CATALYST] Error for {ticker}: {e}")
        return False, "Catalyst check failed"


def evaluate_stock(ticker: str):
    """
    Full pre-market evaluation. Returns watchlist entry or None.
    Applies all Module 1 filters.
    """
    data = get_stock_data(ticker)
    if not data:
        return None

    failures = []

    # Condition 1 — Price
    if not (config.MIN_PRICE <= data["current_price"] <= config.MAX_PRICE):
        failures.append(f"Price ${data['current_price']} out of range")
        # Only hard-exclude if truly extreme (penny stock or mega-cap)
        if data["current_price"] < 0.10 or data["current_price"] > 1000:
            return None

    # Condition 2 — Gap (soft filter only)
    if data["gap_pct"] < config.MIN_GAP_PCT:
        failures.append(f"Gap {data['gap_pct']}% < {config.MIN_GAP_PCT}%")
    if data["gap_pct"] < 0:
        return None  # Hard exclude gapping down

    # Condition 3 — Pre-market volume (soft filter)
    if data["premarket_vol"] < config.MIN_PREMARKET_VOL:
        failures.append(f"PM vol {data['premarket_vol']:,} < {config.MIN_PREMARKET_VOL:,}")

    # Condition 4 — Relative volume
    if data["rel_vol"] < config.MIN_RELATIVE_VOL:
        failures.append(f"RelVol {data['rel_vol']}x < 5x")

    # Condition 5 — Float (soft filter only in training mode)
    float_shares = data["float"]
    if float_shares > config.MAX_FLOAT_HARD:
        failures.append(f"Float {float_shares/1e6:.0f}M — large float LOWER CONVICTION")
    elif float_shares > config.MAX_FLOAT_ACCEPTABLE:
        failures.append(f"Float {float_shares/1e6:.1f}M — moderate float")

    # Condition 6 — Catalyst (soft filter — include with warning if no catalyst found)
    has_catalyst, catalyst_summary = check_catalyst(ticker, data)
    if not has_catalyst:
        catalyst_summary = "No confirmed catalyst — trade with caution"
        failures.append("No catalyst confirmed")

    data["catalyst"] = catalyst_summary
    data["has_catalyst"] = has_catalyst

    # Condition 7 — Short interest flags
    squeeze_flag = ""
    if data["short_pct"] >= config.HIGH_SQUEEZE_SHORT_INT:
        squeeze_flag = "HIGH PRIORITY SQUEEZE ⚠️"
    elif data["short_pct"] >= config.MIN_SQUEEZE_SHORT_INT:
        squeeze_flag = "SQUEEZE CANDIDATE"
    data["squeeze_flag"] = squeeze_flag

    # Assign conviction rank
    rank = assign_rank(data, failures)
    data["conviction"] = rank
    data["weak_conditions"] = failures

    # Pre-market high/low (approximated — real values need live feed)
    data["pm_high"] = data["premarket_price"]
    data["pm_low"] = data["prev_close"]

    return data


def assign_rank(data: dict, failures: list):
    """Module 1 conviction ranking."""
    gap = data["gap_pct"]
    float_s = data["float"]
    short_pct = data["short_pct"]
    rel_vol = data["rel_vol"]

    if (
        len(failures) == 0
        and float_s < config.MAX_FLOAT_PREFERRED
        and gap >= 20
        and short_pct >= config.MIN_SQUEEZE_SHORT_INT
        and rel_vol >= config.MIN_RELATIVE_VOL
    ):
        return "A+"

    if (
        len(failures) == 0
        and float_s <= config.MAX_FLOAT_ACCEPTABLE
        and gap >= config.MIN_GAP_PCT
    ):
        return "A"

    return "B"


def run_premarket_scan():
    """
    Full pre-market scan. Returns sorted watchlist.
    """
    print("[SCANNER] Running pre-market gap scan...")
    tickers = get_finviz_gap_scanner()
    print(f"[SCANNER] {len(tickers)} candidates from Finviz")

    watchlist = []
    for ticker in tickers:
        time.sleep(0.5)  # Rate limit courtesy
        result = evaluate_stock(ticker)
        if result:
            watchlist.append(result)
            print(f"[SCANNER] ✅ {ticker} — {result['conviction']} — Gap {result['gap_pct']}%")
        else:
            print(f"[SCANNER] ❌ {ticker} — excluded")

    # Sort: A+ first, then by gap%
    rank_order = {"A+": 0, "A": 1, "B": 2}
    watchlist.sort(key=lambda x: (rank_order.get(x["conviction"], 3), -x["gap_pct"]))
    return watchlist


def format_watchlist_message(watchlist: list):
    """Format watchlist for Telegram."""
    if not watchlist:
        return "📋 PRE-MARKET SCAN COMPLETE\n\nNo stocks meeting all criteria today. Sit on hands. 🤚"

    lines = ["📋 <b>PRE-MARKET WATCHLIST</b>\n"]
    for i, s in enumerate(watchlist, 1):
        lines.append(
            f"<b>{i}. {s['ticker']}</b> [{s['conviction']}] {s.get('squeeze_flag', '')}\n"
            f"   Price: ${s['current_price']} | Gap: +{s['gap_pct']}%\n"
            f"   PM Vol: {s['premarket_vol']:,} | RelVol: {s['rel_vol']}x\n"
            f"   Float: {s['float']/1e6:.1f}M | SI: {s['short_pct']}%\n"
            f"   Catalyst: {s['catalyst']}\n"
            f"   PM High: ${s['pm_high']} | PM Low: ${s['pm_low']}\n"
            f"   Prev Close: ${s['prev_close']}\n"
        )
        if s.get("weak_conditions"):
            lines.append(f"   ⚠️ Weak: {', '.join(s['weak_conditions'])}\n")
        lines.append("")

    return "\n".join(lines)

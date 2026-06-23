# ============================================================
# opt_scanner.py — Gap scanner + options chain selector
# Finds gap stocks that are options-eligible with liquid contracts
# ============================================================
import yfinance as yf
import pandas as pd
import requests
from bs4 import BeautifulSoup
import time
from datetime import date, timedelta
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import options_bot.opt_config as cfg

CATALYST_KEYWORDS = [
    "earnings", "beat", "revenue", "fda", "approval", "trial", "contract",
    "merger", "acquisition", "acquired", "upgrade", "price target",
    "short squeeze", "defense", "government", "partnership", "license",
    "breakthrough", "positive", "granted", "wins", "awarded"
]


# ── Stock screener ──────────────────────────────────────────

def get_gap_candidates():
    """Use Finviz to find pre-market gappers."""
    try:
        from finvizfinance.screener.overview import Overview
        foverview = Overview()
        foverview.set_filter(filters_dict={"Gap": "Up 5%", "Country": "USA"})
        df = foverview.screener_view()
        if df is None or df.empty:
            return []
        df["Change"] = pd.to_numeric(df["Change"], errors="coerce")
        df = df.dropna(subset=["Change"]).sort_values("Change", ascending=False)
        return df["Ticker"].tolist()[:30]
    except Exception as e:
        print(f"[OPT SCANNER] Finviz error: {e}")
        return []


def get_stock_data(ticker: str):
    """Pull underlying stock data via yfinance."""
    try:
        tk = yf.Ticker(ticker)
        info = tk.info
        fast = tk.fast_info

        prev_close = fast.get("previousClose") or info.get("previousClose", 0)
        current_price = fast.get("lastPrice") or info.get("currentPrice", 0)
        if not prev_close or not current_price:
            return None

        gap_pct = ((current_price - prev_close) / prev_close) * 100
        premarket_price = info.get("preMarketPrice") or current_price
        premarket_vol = info.get("preMarketVolume") or info.get("regularMarketVolume") or 0
        avg_vol = info.get("averageVolume") or info.get("averageDailyVolume10Day") or 0
        rel_vol = (premarket_vol / avg_vol) if avg_vol > 0 else 0
        float_shares = info.get("floatShares") or 0
        shares_short = info.get("sharesShort") or 0
        short_pct = (shares_short / float_shares * 100) if float_shares > 0 else 0

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
            "market_cap": info.get("marketCap") or 0,
            "name": info.get("shortName") or ticker,
        }
    except Exception as e:
        print(f"[OPT SCANNER] Stock data error {ticker}: {e}")
        return None


def check_catalyst(ticker: str):
    """Scrape Finviz news for catalyst keywords."""
    try:
        resp = requests.get(
            f"https://finviz.com/quote.ashx?t={ticker}",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        news_table = soup.find("table", {"id": "news-table"})
        if not news_table:
            return False, "No catalyst"
        for row in news_table.find_all("tr")[:10]:
            tds = row.find_all("td")
            if len(tds) >= 2:
                headline = tds[1].get_text(strip=True).lower()
                for kw in CATALYST_KEYWORDS:
                    if kw in headline:
                        return True, tds[1].get_text(strip=True)[:120]
        return False, "No confirmed catalyst"
    except Exception as e:
        print(f"[OPT SCANNER] Catalyst error {ticker}: {e}")
        return False, "Catalyst check failed"


# ── Options chain selection ──────────────────────────────────

def select_contract(ticker: str, direction: str, current_price: float):
    """
    Pick the best options contract from yfinance chain.
    direction: "CALL" for bullish gap-up, "PUT" for bearish
    Returns contract dict or None.
    """
    try:
        tk = yf.Ticker(ticker)
        expirations = tk.options
        if not expirations:
            return None

        today = date.today()
        # Find the first expiration within our DTE window
        target_exp = None
        for exp_str in expirations:
            exp_date = date.fromisoformat(exp_str)
            dte = (exp_date - today).days
            if cfg.SCALP_MIN_DTE <= dte <= cfg.DEFAULT_MAX_DTE:
                target_exp = exp_str
                break
        # If no weekly expiration found within window, take the nearest one
        if not target_exp and expirations:
            for exp_str in expirations:
                exp_date = date.fromisoformat(exp_str)
                dte = (exp_date - today).days
                if dte <= cfg.SWING_MAX_DTE and dte >= 1:
                    target_exp = exp_str
                    break

        if not target_exp:
            print(f"[OPT SCANNER] {ticker}: No suitable expiration found")
            return None

        chain = tk.option_chain(target_exp)
        contracts_df = chain.calls if direction == "CALL" else chain.puts

        if contracts_df.empty:
            return None

        # Filter for liquid contracts with reasonable spread
        contracts_df = contracts_df.copy()
        contracts_df = contracts_df[contracts_df["ask"] > 0]
        contracts_df = contracts_df[contracts_df["bid"] > 0]

        # Spread filter: (ask - bid) / ask <= MAX_SPREAD_PCT
        contracts_df["spread_pct"] = (contracts_df["ask"] - contracts_df["bid"]) / contracts_df["ask"]
        contracts_df = contracts_df[contracts_df["spread_pct"] <= cfg.MAX_SPREAD_PCT]

        # Volume and OI filters
        contracts_df = contracts_df[
            (contracts_df["volume"].fillna(0) >= cfg.MIN_OPTION_VOLUME) |
            (contracts_df["openInterest"].fillna(0) >= cfg.MIN_OPEN_INTEREST)
        ]

        if contracts_df.empty:
            print(f"[OPT SCANNER] {ticker}: No liquid contracts found for {target_exp}")
            return None

        # Pick the contract closest to ATM (minimize |strike - current_price|)
        contracts_df["dist_from_atm"] = abs(contracts_df["strike"] - current_price)
        best = contracts_df.sort_values("dist_from_atm").iloc[0]

        dte = (date.fromisoformat(target_exp) - today).days
        mid_price = round((best["bid"] + best["ask"]) / 2, 2)
        spread_pct = round(best["spread_pct"] * 100, 1)
        iv = round(best.get("impliedVolatility", 0) * 100, 1)

        return {
            "contract_symbol": best["contractSymbol"],
            "strike": best["strike"],
            "expiry": target_exp,
            "dte": dte,
            "direction": direction,
            "bid": round(best["bid"], 2),
            "ask": round(best["ask"], 2),
            "mid": mid_price,
            "volume": int(best.get("volume") or 0),
            "open_interest": int(best.get("openInterest") or 0),
            "iv_pct": iv,
            "spread_pct": spread_pct,
            "in_the_money": bool(best.get("inTheMoney", False)),
        }
    except Exception as e:
        print(f"[OPT SCANNER] Chain error {ticker}: {e}")
        return None


# ── Conviction ranking ───────────────────────────────────────

def assign_rank(data: dict, failures: list):
    gap = data["gap_pct"]
    float_s = data["float"]
    rel_vol = data["rel_vol"]

    if (len(failures) == 0 and float_s < cfg.MAX_FLOAT_PREFERRED
            and gap >= 20 and rel_vol >= 5.0):
        return "A+"
    if (len(failures) == 0 and gap >= cfg.MIN_GAP_PCT and rel_vol >= cfg.MIN_RELATIVE_VOL):
        return "A"
    return "B"


def evaluate_stock(ticker: str):
    """Full pre-market evaluation for options watchlist."""
    data = get_stock_data(ticker)
    if not data:
        return None

    failures = []

    if data["current_price"] < cfg.MIN_PRICE or data["current_price"] > cfg.MAX_PRICE:
        return None   # hard exclude

    if data["gap_pct"] < cfg.MIN_GAP_PCT:
        failures.append(f"Gap {data['gap_pct']}% < {cfg.MIN_GAP_PCT}%")
    if data["gap_pct"] <= 0:
        return None   # only trade bullish gaps for calls

    if data["premarket_vol"] < cfg.MIN_PREMARKET_VOL:
        failures.append(f"PM vol {data['premarket_vol']:,} low")
    if data["rel_vol"] < cfg.MIN_RELATIVE_VOL:
        failures.append(f"RelVol {data['rel_vol']}x low")
    if data["float"] > cfg.MAX_FLOAT_HARD:
        return None   # too illiquid/big for momentum

    has_catalyst, catalyst_summary = check_catalyst(ticker)
    if not has_catalyst:
        catalyst_summary = "No confirmed catalyst"
        failures.append("No catalyst")

    data["catalyst"] = catalyst_summary
    data["has_catalyst"] = has_catalyst
    data["conviction"] = assign_rank(data, failures)
    data["weak_conditions"] = failures

    # Find the best call contract (gap-up = bullish = CALL)
    contract = select_contract(ticker, "CALL", data["current_price"])
    if not contract:
        print(f"[OPT SCANNER] {ticker}: No liquid options contract found — skipping")
        return None

    data["contract"] = contract
    data["pm_high"] = data["premarket_price"]
    return data


# ── Main scan ────────────────────────────────────────────────

def run_options_scan():
    """Run pre-market scan and return options watchlist."""
    print("[OPT SCANNER] Running pre-market options scan...")
    tickers = get_gap_candidates()
    print(f"[OPT SCANNER] {len(tickers)} gap candidates from Finviz")

    watchlist = []
    for ticker in tickers:
        time.sleep(0.5)
        result = evaluate_stock(ticker)
        if result:
            watchlist.append(result)
            c = result["contract"]
            print(f"[OPT SCANNER] ✅ {ticker} [{result['conviction']}] Gap {result['gap_pct']}% | "
                  f"CALL {c['expiry']} ${c['strike']} ask ${c['ask']}")
        else:
            print(f"[OPT SCANNER] ❌ {ticker} — excluded")

    rank_order = {"A+": 0, "A": 1, "B": 2}
    watchlist.sort(key=lambda x: (rank_order.get(x["conviction"], 3), -x["gap_pct"]))
    return watchlist


def format_watchlist_message(watchlist: list):
    """Format options watchlist for Telegram."""
    if not watchlist:
        return "📋 OPTIONS SCAN COMPLETE\n\nNo eligible plays today. Sit on hands. 🤚"

    lines = ["📋 <b>OPTIONS WATCHLIST</b>\n"]
    for i, s in enumerate(watchlist, 1):
        c = s["contract"]
        lines.append(
            f"<b>{i}. {s['ticker']}</b> [{s['conviction']}]\n"
            f"   Stock: ${s['current_price']} | Gap: +{s['gap_pct']}%\n"
            f"   PM Vol: {s['premarket_vol']:,} | RelVol: {s['rel_vol']}x\n"
            f"   Float: {s['float']/1e6:.1f}M\n"
            f"   Catalyst: {s['catalyst']}\n"
            f"   ▶ CALL {c['expiry']} ${c['strike']} strike | {c['dte']} DTE\n"
            f"   Ask: ${c['ask']} | Mid: ${c['mid']} | IV: {c['iv_pct']}% | Spread: {c['spread_pct']}%\n"
            f"   Vol: {c['volume']:,} | OI: {c['open_interest']:,}\n"
        )
        if s.get("weak_conditions"):
            lines.append(f"   ⚠️ Weak: {', '.join(s['weak_conditions'])}\n")
        lines.append("")

    return "\n".join(lines)

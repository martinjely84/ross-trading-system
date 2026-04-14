# ============================================================
# signals.py — Entry signal evaluation (Modules 3, 4, 5)
# ============================================================
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
import config
from session import Session, et_now

ET = pytz.timezone("America/New_York")


def get_1min_candles(ticker: str, period="1d", interval="1m"):
    try:
        tk = yf.Ticker(ticker)
        df = tk.history(period=period, interval=interval)
        if df.empty:
            return None
        df.index = df.index.tz_convert(ET)
        return df
    except Exception as e:
        print(f"[SIGNALS] Candle error {ticker}: {e}")
        return None


def calc_vwap(df: pd.DataFrame):
    """Calculate VWAP from 9:30am candles."""
    market_open = df.index[0].replace(hour=9, minute=30, second=0, microsecond=0)
    df_session = df[df.index >= market_open].copy()
    if df_session.empty:
        return None
    typical_price = (df_session["High"] + df_session["Low"] + df_session["Close"]) / 3
    cum_tp_vol = (typical_price * df_session["Volume"]).cumsum()
    cum_vol = df_session["Volume"].cumsum()
    vwap = cum_tp_vol / cum_vol
    return vwap


def calc_ema(series: pd.Series, period=9):
    return series.ewm(span=period, adjust=False).mean()


def check_invalid_conditions(ticker: str, session: Session):
    """Module 4 — hard disqualifiers. Returns (valid, reason)."""
    now = et_now()

    # Time check
    cutoff = now.replace(hour=11, minute=0, second=0, microsecond=0)
    if now >= cutoff:
        return False, "After 11:00am Eastern"

    # Session armed
    if not session.armed:
        return False, "Session not armed — /start required"

    # Daily loss limit
    if session.trading_suspended or session.check_limit_hit():
        return False, "Daily loss limit reached — trading suspended"

    # Watchlist check
    watchlist_tickers = [s["ticker"] for s in session.watchlist]
    if ticker not in watchlist_tickers:
        return False, f"{ticker} not on pre-market watchlist"

    # Previously stopped out check
    stopped_tickers = [
        t["ticker"] for t in session.trades_today
        if t["exit_reason"] in ("Stop Loss", "VWAP Break")
    ]
    if ticker in stopped_tickers:
        return False, f"{ticker} already stopped out today — human approval required"

    return True, "OK"


def evaluate_gap_and_go(ticker: str, session: Session, watchlist_entry: dict):
    """
    Setup A — Gap and Go (9:30–10:00am)
    Returns signal dict or None.
    """
    valid, reason = check_invalid_conditions(ticker, session)
    if not valid:
        return None, reason

    now = et_now()
    open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
    cutoff_time = now.replace(hour=10, minute=0, second=0, microsecond=0)

    if not (open_time <= now <= cutoff_time):
        return None, "Outside Gap and Go window (9:30-10:00am)"

    # Conviction check for time window
    conviction = watchlist_entry.get("conviction", "B")
    if conviction not in ("A+", "A"):
        return None, "Setup B grade — not valid for Gap and Go"

    df = get_1min_candles(ticker)
    if df is None or len(df) < 6:
        return None, "Insufficient candle data"

    vwap = calc_vwap(df)
    if vwap is None:
        return None, "VWAP calculation failed"

    ema9 = calc_ema(df["Close"])
    current_candle = df.iloc[-1]
    current_price = current_candle["Close"]
    current_vwap = vwap.iloc[-1]
    current_ema = ema9.iloc[-1]
    pm_high = watchlist_entry.get("pm_high", 0)

    # Condition 2 — Price above VWAP
    if current_price <= current_vwap:
        return None, f"Price ${current_price:.2f} below VWAP ${current_vwap:.2f}"

    # Condition 3 — Breaking above pre-market high
    if current_candle["Close"] <= pm_high:
        return None, f"Price not breaking PM high ${pm_high:.2f}"

    # Condition 4 — Breakout candle volume 2x avg of prior 5
    prior_5_avg = df["Volume"].iloc[-6:-1].mean()
    if current_candle["Volume"] < prior_5_avg * config.BREAKOUT_VOL_MULTIPLIER:
        return None, f"Breakout volume insufficient ({current_candle['Volume']:,} < {prior_5_avg*2:,.0f})"

    # Condition 5 — 9 EMA sloping upward
    if ema9.iloc[-1] <= ema9.iloc[-2]:
        return None, "9 EMA not sloping upward"

    # All conditions met — build signal
    entry_price = current_price
    stop_loss = current_candle["Low"]

    # Use VWAP as stop if tighter (within 3%)
    stop_type = "Candle Low"
    vwap_stop_threshold = entry_price * (1 - config.VWAP_STOP_THRESHOLD_PCT)
    if current_vwap >= vwap_stop_threshold and current_vwap < stop_loss:
        stop_loss = current_vwap
        stop_type = "VWAP"

    risk_per_share = round(entry_price - stop_loss, 4)
    if risk_per_share <= 0:
        return None, "Invalid stop loss (risk <= 0)"

    risk_amount = session.halt_risk if "Halt" in watchlist_entry.get("signal_type", "") else session.per_trade_risk
    share_size = int(risk_amount / risk_per_share)
    if share_size < 1:
        return None, f"Share size rounds to 0 (risk ${risk_per_share:.2f}/share)"

    target1 = round(entry_price + risk_per_share, 2)
    target2 = round(entry_price + (risk_per_share * 2), 2)

    # Late session warning
    notes = watchlist_entry.get("catalyst", "")
    window_start = now.replace(hour=10, minute=30, second=0, microsecond=0)
    if now >= window_start:
        notes += " | ⚠️ LATE SESSION — REDUCED CONVICTION — CONSIDER SMALLER SIZE"

    return {
        "signal_type": "Gap and Go",
        "ticker": ticker,
        "conviction": conviction,
        "time": now.strftime("%H:%M:%S"),
        "catalyst": watchlist_entry.get("catalyst", ""),
        "float": watchlist_entry.get("float", 0),
        "short_interest": watchlist_entry.get("short_pct", 0),
        "entry_price": round(entry_price, 2),
        "stop_loss": round(stop_loss, 2),
        "stop_type": stop_type,
        "risk_per_share": round(risk_per_share, 4),
        "share_size": share_size,
        "total_risk": round(risk_per_share * share_size, 2),
        "target1": target1,
        "target2": target2,
        "daily_loss_used": session.daily_loss_used,
        "daily_loss_limit": session.daily_loss_limit,
        "daily_loss_remaining": session.daily_loss_remaining(),
        "notes": notes,
        "vwap": round(current_vwap, 2),
    }, "SIGNAL"


def evaluate_first_candle_new_high(ticker: str, session: Session, watchlist_entry: dict):
    """
    Setup B — First Candle New High (9:30-11:00am)
    """
    valid, reason = check_invalid_conditions(ticker, session)
    if not valid:
        return None, reason

    now = et_now()

    # B setups only in first hour (before 10:30am)
    cutoff_b = now.replace(hour=10, minute=30, second=0, microsecond=0)
    if now > cutoff_b:
        return None, "B setup after 10:30am not valid"

    df = get_1min_candles(ticker)
    if df is None or len(df) < 6:
        return None, "Insufficient candle data"

    vwap = calc_vwap(df)
    ema9 = calc_ema(df["Close"])
    current_candle = df.iloc[-1]
    current_price = current_candle["Close"]
    current_vwap = vwap.iloc[-1]

    # Condition 1 — Up 10%+ on day
    prev_close = watchlist_entry.get("prev_close", 0)
    if prev_close > 0:
        day_change = (current_price - prev_close) / prev_close * 100
        if day_change < 10:
            return None, f"Only up {day_change:.1f}% — needs 10%+"

    # Condition 4 — Price above VWAP throughout pullback
    if current_price <= current_vwap:
        return None, f"Price below VWAP — no entry"

    # Check for 3+ candle pullback with higher lows
    # Look at last 3-8 candles before current
    lookback = df.iloc[-8:-1]
    if len(lookback) < 3:
        return None, "Not enough candles to evaluate pullback"

    # Find pullback sequence (declining highs / lower closes for 3+ candles)
    pullback_candles = []
    for i in range(len(lookback) - 1, 1, -1):
        c = lookback.iloc[i]
        c_prev = lookback.iloc[i-1]
        if c["Low"] > c_prev["Low"]:  # Higher low = valid pullback candle
            pullback_candles.insert(0, c)
        else:
            break

    if len(pullback_candles) < 3:
        return None, f"Only {len(pullback_candles)} pullback candles — need 3+"

    # Condition 3 — Volume declined on pullback candles
    pullback_vols = [c["Volume"] for c in pullback_candles]
    vol_declining = all(pullback_vols[i] < pullback_vols[i-1] for i in range(1, len(pullback_vols)))
    if not vol_declining:
        return None, "Volume not consistently declining during pullback"

    # Condition 5 — Breaking above highest candle in pullback
    pullback_high = max(c["High"] for c in pullback_candles)
    if current_candle["Close"] <= pullback_high:
        return None, f"Not breaking above pullback high ${pullback_high:.2f}"

    # Condition 6 — Breakout volume 2x pullback average
    pullback_avg_vol = sum(pullback_vols) / len(pullback_vols)
    if current_candle["Volume"] < pullback_avg_vol * config.BREAKOUT_VOL_MULTIPLIER:
        return None, "Breakout volume not 2x pullback average"

    # Condition 7 — 9 EMA curling upward
    if ema9.iloc[-1] <= ema9.iloc[-2]:
        return None, "9 EMA not curling upward"

    # Build signal
    conviction = watchlist_entry.get("conviction", "B")
    entry_price = current_price
    stop_loss = min(c["Low"] for c in pullback_candles)
    stop_type = "Candle Low"

    vwap_stop_threshold = entry_price * (1 - config.VWAP_STOP_THRESHOLD_PCT)
    if current_vwap >= vwap_stop_threshold and current_vwap < stop_loss:
        stop_loss = current_vwap
        stop_type = "VWAP"

    risk_per_share = round(entry_price - stop_loss, 4)
    if risk_per_share <= 0:
        return None, "Invalid stop"

    share_size = int(session.per_trade_risk / risk_per_share)
    if share_size < 1:
        return None, "Share size rounds to 0"

    target1 = round(entry_price + risk_per_share, 2)
    target2 = round(entry_price + (risk_per_share * 2), 2)

    notes = watchlist_entry.get("catalyst", "")
    if now >= now.replace(hour=10, minute=30, second=0, microsecond=0):
        notes += " | ⚠️ LATE SESSION — REDUCED CONVICTION"

    return {
        "signal_type": "First Candle New High",
        "ticker": ticker,
        "conviction": conviction,
        "time": now.strftime("%H:%M:%S"),
        "catalyst": watchlist_entry.get("catalyst", ""),
        "float": watchlist_entry.get("float", 0),
        "short_interest": watchlist_entry.get("short_pct", 0),
        "entry_price": round(entry_price, 2),
        "stop_loss": round(stop_loss, 2),
        "stop_type": stop_type,
        "risk_per_share": round(risk_per_share, 4),
        "share_size": share_size,
        "total_risk": round(risk_per_share * share_size, 2),
        "target1": target1,
        "target2": target2,
        "daily_loss_used": session.daily_loss_used,
        "daily_loss_limit": session.daily_loss_limit,
        "daily_loss_remaining": session.daily_loss_remaining(),
        "notes": notes,
        "vwap": round(current_vwap, 2),
    }, "SIGNAL"

# ============================================================
# signals.py — Entry signal evaluation
# TRAINING MODE: fires on first eligible watchlist stock each day
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
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize("America/New_York")
        else:
            df.index = df.index.tz_convert(ET)
        return df
    except Exception as e:
        print(f"[SIGNALS] Candle error {ticker}: {e}")
        return None


def get_price(ticker: str):
    """Get current price — try fast_info first, fall back to history."""
    try:
        tk = yf.Ticker(ticker)
        price = tk.fast_info.get("lastPrice") or tk.fast_info.get("last_price")
        if price and price > 0:
            return round(float(price), 2)
        # Fall back to 1-min candle close
        df = tk.history(period="1d", interval="1m")
        if not df.empty:
            return round(float(df["Close"].iloc[-1]), 2)
    except Exception as e:
        print(f"[SIGNALS] Price error {ticker}: {e}")
    return None


def calc_vwap(df: pd.DataFrame):
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


def is_trading_window():
    """Returns True if we're in the 9:31am–11:00am ET trading window on a weekday."""
    now = et_now()
    if now.weekday() >= 5:
        return False, "Weekend"
    open_time  = now.replace(hour=9,  minute=31, second=0, microsecond=0)
    close_time = now.replace(hour=11, minute=0,  second=0, microsecond=0)
    if now < open_time:
        return False, f"Market not open yet ({now.strftime('%H:%M')} ET)"
    if now >= close_time:
        return False, "Trading window closed (after 11:00 ET)"
    return True, "OK"


def evaluate_gap_and_go(ticker: str, session: Session, watchlist_entry: dict):
    """
    TRAINING MODE — Fire on any watchlist stock that meets minimum criteria:
      1. Session is armed
      2. Within 9:31–11:00am ET trading window
      3. Not already in a position for this ticker
      4. Not already stopped out on this ticker today
      5. Daily loss limit not hit
      6. Can get a current price
    No VWAP, no PM-high, no volume checks.
    Caps at MAX_TRADES_PER_DAY to avoid runaway firing.
    """
    # --- Gate 1: session state ---
    if not session.armed:
        return None, "Session not armed"
    if session.trading_suspended or session.check_limit_hit():
        return None, "Daily loss limit reached"
    if ticker in session.open_positions:
        return None, f"Already in position: {ticker}"

    # --- Gate 2: already stopped out today ---
    stopped = [t["ticker"] for t in session.trades_today
               if t.get("exit_reason") in ("Stop Loss", "VWAP Break")]
    if ticker in stopped:
        return None, f"{ticker} stopped out today"

    # --- Gate 3: trading window ---
    in_window, reason = is_trading_window()
    if not in_window:
        return None, reason

    # --- Gate 4: daily trade cap ---
    MAX_TRADES_PER_DAY = 3
    if len(session.trades_today) >= MAX_TRADES_PER_DAY:
        return None, f"Trade cap reached ({MAX_TRADES_PER_DAY}/day in training mode)"

    # --- Gate 5: get a price ---
    current_price = get_price(ticker)
    if not current_price or current_price <= 0:
        print(f"[SIGNALS] {ticker} — could not get price, skipping")
        return None, "Could not get price"

    # Sanity check price is still within config bounds
    if current_price < config.MIN_PRICE or current_price > config.MAX_PRICE:
        return None, f"Price ${current_price} out of range"

    # --- Build signal ---
    now = et_now()

    # Stop loss: 2% below current price (simple fixed stop for training)
    stop_loss = round(current_price * 0.98, 2)
    risk_per_share = round(current_price - stop_loss, 4)
    if risk_per_share <= 0:
        risk_per_share = round(current_price * 0.02, 4)

    # Size from per-trade risk. Reject (don't floor to 1) when the risk-correct
    # size rounds to zero — a forced 1-share position can risk several times the
    # per-trade limit on a higher-priced stock.
    per_trade_risk = session.per_trade_risk
    share_size = int(per_trade_risk / risk_per_share)
    if share_size < 1:
        return None, f"Share size rounds to 0 (risk ${risk_per_share:.2f}/share > ${per_trade_risk:.2f} budget)"

    target1 = round(current_price + risk_per_share * 1.5, 2)  # 1.5R
    target2 = round(current_price + risk_per_share * 3.0, 2)  # 3R

    conviction = watchlist_entry.get("conviction", "B")
    catalyst   = watchlist_entry.get("catalyst", "No catalyst noted")

    notes = catalyst
    if now.hour >= 10 and now.minute >= 30:
        notes += " | ⚠️ LATE — REDUCED CONVICTION"

    print(f"[SIGNALS] ✅ {ticker} SIGNAL — price ${current_price} | stop ${stop_loss} | size {share_size} | risk ${per_trade_risk:.2f}")

    return {
        "signal_type":         "Gap and Go",
        "ticker":              ticker,
        "conviction":          conviction,
        "time":                now.strftime("%H:%M:%S"),
        "catalyst":            catalyst,
        "float":               watchlist_entry.get("float", 0),
        "short_interest":      watchlist_entry.get("short_pct", 0),
        "entry_price":         current_price,
        "stop_loss":           stop_loss,
        "stop_type":           "2% Fixed Stop",
        "risk_per_share":      risk_per_share,
        "share_size":          share_size,
        "total_risk":          round(risk_per_share * share_size, 2),
        "target1":             target1,
        "target2":             target2,
        "daily_loss_used":     session.daily_loss_used,
        "daily_loss_limit":    session.daily_loss_limit,
        "daily_loss_remaining": session.daily_loss_remaining(),
        "notes":               notes,
        "vwap":                0,
    }, "SIGNAL"


def evaluate_first_candle_new_high(ticker: str, session: Session, watchlist_entry: dict):
    """
    Setup B — disabled in training mode. Always returns None so
    evaluate_gap_and_go handles everything.
    """
    return None, "Setup B disabled in training mode"

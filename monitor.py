# ============================================================
# monitor.py — Real-time position monitor + exit engine
# Module 6 exit triggers + Module 8 alerts
# ============================================================
import yfinance as yf
import pandas as pd
import threading
from datetime import datetime
import pytz
import config
import telegram_bot as tg
from session import Session, et_now
from signals import get_1min_candles, calc_vwap, calc_ema
from executor import sell_market, get_fill_price

ET = pytz.timezone("America/New_York")
_exit_lock = threading.Lock()

# Tracks exit orders that were submitted but whose fill could not be confirmed.
# ticker -> order_id. Prevents the monitor loop from resubmitting a duplicate
# sell every cycle (which could oversell and flip the position to short).
_pending_exit_orders = {}


def get_current_price(ticker: str):
    try:
        tk = yf.Ticker(ticker)
        price = tk.fast_info.get("lastPrice")
        if price:
            return round(float(price), 2)
    except Exception:
        pass
    return None


def _execute_exit(ticker, shares, reason):
    """Submit the broker exit and return the fill price only after a confirmed fill.

    At most ONE exit order per ticker is ever outstanding. If a previous exit
    could not be confirmed, the next cycle resolves that same order instead of
    submitting a new sell — otherwise repeated fill-confirmation timeouts would
    stack duplicate sells and oversell the position.
    """
    if shares <= 0:
        return None

    # If we already have an unconfirmed exit order for this ticker, resolve it
    # rather than submitting another sell.
    existing = _pending_exit_orders.get(ticker)
    if existing:
        status, fill = get_fill_price(existing, max_wait_secs=2)
        if status == "filled":
            _pending_exit_orders.pop(ticker, None)
            return fill
        if status == "timeout":
            tg.send(f"🚨 EXIT STILL UNCONFIRMED — {ticker}\nPrior {reason} sell order is still pending. Not resubmitting. Check Alpaca immediately.")
            return None
        # status == "failed": the prior order definitely did not reduce the
        # position; clear it and submit a fresh exit below.
        _pending_exit_orders.pop(ticker, None)

    order = sell_market(ticker, shares)
    if not order or not order.get("id"):
        tg.send(f"🚨 EXIT FAILED — {ticker}\nCould not submit {reason} sell order. Check Alpaca immediately.")
        return None

    status, fill = get_fill_price(order["id"])
    if status == "filled":
        return fill

    if status == "timeout":
        # Outcome unknown — the order may have filled. Remember it so the next
        # cycle resolves it instead of submitting a duplicate sell.
        _pending_exit_orders[ticker] = order["id"]
        tg.send(f"🚨 EXIT UNCONFIRMED — {ticker}\n{reason} order was submitted but fill was not confirmed. Will resolve before any retry. Check Alpaca immediately.")
    else:
        tg.send(f"🚨 EXIT REJECTED — {ticker}\n{reason} sell order did not fill (status={status}). Check Alpaca immediately.")
    return None


def _close_with_broker(session, ticker, shares, reason):
    pos = session.open_positions.get(ticker)
    if not pos:
        return None
    shares = min(int(shares), int(pos.get("remaining_shares", shares)))
    fill = _execute_exit(ticker, shares, reason)
    if fill is None:
        return None
    return session.close_position(ticker, fill, reason, shares=shares)


def monitor_position(ticker: str, session: Session):
    """
    Check all exit conditions for an open position.
    Returns list of exit actions to take.
    """
    pos = session.open_positions.get(ticker)
    if not pos:
        return

    now = et_now()
    df = get_1min_candles(ticker)
    if df is None or len(df) < 2:
        return

    vwap = calc_vwap(df)
    ema9 = calc_ema(df["Close"])

    if vwap is None:
        return

    current_candle = df.iloc[-1]
    prev_candle = df.iloc[-2]
    current_close = current_candle["Close"]
    current_vwap = vwap.iloc[-1]
    current_ema = ema9.iloc[-1]
    current_stop = pos["current_stop"]
    entry_price = pos["entry_price"]
    remaining = pos["remaining_shares"]

    # --- STOP APPROACHING ALERT ---
    if current_close <= current_stop + 0.10 and current_close > current_stop:
        tg.send(
            f"⚠️ STOP APPROACHING — <b>{ticker}</b>\n"
            f"Price: ${current_close:.2f} | Stop: ${current_stop:.2f}\n"
            f"Within $0.10 of stop"
        )

    # EXIT TRIGGER 1 — STOP LOSS HIT
    if current_candle["Close"] < current_stop:
        trade = _close_with_broker(session, ticker, remaining, "Stop Loss")
        if trade:
            tg.send_exit({
                "trigger": "🛑 STOP LOSS HIT — FULL EXIT",
                "ticker": ticker,
                "action": "SELL ALL",
                "exit_price": trade["exit_price"],
                "pnl": trade["pnl"],
                "r_multiple": trade["r_multiple"],
                "daily_loss_used": session.daily_loss_used,
                "daily_loss_limit": session.daily_loss_limit,
            })
            _check_daily_limit(session)
        return

    # EXIT TRIGGER 2 — VWAP BREAK
    if current_candle["Close"] < current_vwap:
        # Only if VWAP break is not already below stop (stop takes priority)
        if current_vwap > current_stop:
            trade = _close_with_broker(session, ticker, remaining, "VWAP Break")
            if trade:
                tg.send_exit({
                    "trigger": "📉 VWAP BREAK — FULL EXIT",
                    "ticker": ticker,
                    "action": "SELL ALL",
                    "exit_price": trade["exit_price"],
                    "pnl": trade["pnl"],
                    "r_multiple": trade["r_multiple"],
                    "daily_loss_used": session.daily_loss_used,
                    "daily_loss_limit": session.daily_loss_limit,
                })
                _check_daily_limit(session)
            return

    # EXIT TRIGGER 3 — TARGET 1 HIT
    if not pos["t1_hit"] and current_close >= pos["target1"]:
        shares_to_sell = max(1, remaining // 2)
        trade = _close_with_broker(session, ticker, shares_to_sell, "Target 1")
        if not trade:
            return
        pos = session.open_positions.get(ticker)
        if pos:
            # Move stop to breakeven
            session.open_positions[ticker]["current_stop"] = entry_price
            session.open_positions[ticker]["t1_hit"] = True
            session.open_positions[ticker]["breakeven_set"] = True
            session.save()
        tg.send(
            f"🎯 TARGET 1 HIT — SOLD 50%\n"
            f"<b>{ticker}</b> @ ${trade['exit_price']:.2f}\n"
            f"Partial gain: ${trade['pnl'] if trade else '?':.2f}\n"
            f"Stop moved to breakeven: ${entry_price:.2f}\n"
            f"Remaining: {pos['remaining_shares'] if pos else 0} shares"
        )
        return

    # EXIT TRIGGER 4 — TARGET 2 HIT
    if pos.get("t1_hit") and not pos.get("t2_hit") and current_close >= pos["target2"]:
        remaining = pos["remaining_shares"]
        shares_to_sell = max(1, remaining // 2)
        trade = _close_with_broker(session, ticker, shares_to_sell, "Target 2")
        if not trade:
            return
        pos = session.open_positions.get(ticker)
        if pos:
            session.open_positions[ticker]["t2_hit"] = True
            session.save()
        tg.send(
            f"🎯🎯 TARGET 2 HIT — SOLD 25%\n"
            f"<b>{ticker}</b> @ ${trade['exit_price']:.2f}\n"
            f"Partial gain: ${trade['pnl'] if trade else '?':.2f}\n"
            f"🏃 RUNNER ACTIVE: {pos['remaining_shares'] if pos else 0} shares\n"
            f"Stop at breakeven: ${entry_price:.2f}"
        )
        return

    # EXIT TRIGGER 5 — RUNNER EXIT CONDITIONS
    if pos.get("t2_hit") and pos.get("remaining_shares", 0) > 0:
        runner_exit_reason = None

        # a) VWAP break
        if current_close < current_vwap:
            runner_exit_reason = "VWAP break on runner"

        # b) 9 EMA crosses below price by more than 2%
        elif current_ema < current_close * 0.98:
            runner_exit_reason = "9 EMA > 2% below price"

        # c) Lower low AND lower high on two consecutive candles above breakeven
        elif (
            current_candle["Low"] < prev_candle["Low"]
            and current_candle["High"] < prev_candle["High"]
            and current_close > entry_price
        ):
            runner_exit_reason = "Lower low + lower high above breakeven"

        # d) 11am and no new high in last 10 minutes
        elif now.hour == 11 and now.minute == 0:
            recent = df.iloc[-10:]
            if recent["High"].max() <= df.iloc[-11]["High"]:
                runner_exit_reason = "11am — no new high in 10 minutes"

        if runner_exit_reason:
            trade = _close_with_broker(session, ticker, pos.get("remaining_shares", 0), "Runner Exit")
            if trade:
                tg.send_exit({
                    "trigger": f"🏃 RUNNER EXIT — {runner_exit_reason}",
                    "ticker": ticker,
                    "action": "SELL ALL remaining",
                    "exit_price": trade["exit_price"],
                    "pnl": trade["pnl"],
                    "r_multiple": trade["r_multiple"],
                    "daily_loss_used": session.daily_loss_used,
                    "daily_loss_limit": session.daily_loss_limit,
                })
            return

    # EXIT TRIGGER 6 — TIME STOP WARNING (15 min, no T1, no new high)
    entry_time_str = pos.get("entry_time", "")
    try:
        entry_dt = now.replace(
            hour=int(entry_time_str[:2]),
            minute=int(entry_time_str[3:5]),
            second=int(entry_time_str[6:8])
        )
        minutes_open = (now - entry_dt).seconds // 60
        if minutes_open >= 15 and not pos.get("t1_hit"):
            recent_high = df.iloc[-15:]["High"].max() if len(df) >= 15 else df["High"].max()
            tg.send(
                f"⏰ TIME STOP WARNING — <b>{ticker}</b>\n"
                f"15 minutes open — no progress toward T1\n"
                f"Recent high: ${recent_high:.2f}\n"
                f"CONSIDER MANUAL EXIT"
            )
    except Exception:
        pass

    # EXIT TRIGGER 7 — HARD 11AM STOP
    hard_stop_time = now.replace(hour=11, minute=0, second=0, microsecond=0)
    if now >= hard_stop_time and current_close < entry_price:
        trade = _close_with_broker(session, ticker, remaining, "Hard 11am Stop")
        if trade:
            tg.send_exit({
                "trigger": "🕚 HARD TIME STOP — 11AM RULE",
                "ticker": ticker,
                "action": "SELL ALL",
                "exit_price": trade["exit_price"],
                "pnl": trade["pnl"],
                "r_multiple": trade["r_multiple"],
                "daily_loss_used": session.daily_loss_used,
                "daily_loss_limit": session.daily_loss_limit,
            })
        return

    # EXIT TRIGGER 8 — END OF DAY
    eod_time = now.replace(hour=15, minute=55, second=0, microsecond=0)
    if now >= eod_time:
        trade = _close_with_broker(session, ticker, remaining, "End of Day")
        if trade:
            tg.send_exit({
                "trigger": "🔔 END OF DAY CLOSE",
                "ticker": ticker,
                "action": "SELL ALL",
                "exit_price": trade["exit_price"],
                "pnl": trade["pnl"],
                "r_multiple": trade["r_multiple"],
                "daily_loss_used": session.daily_loss_used,
                "daily_loss_limit": session.daily_loss_limit,
            })


def _check_daily_limit(session: Session):
    """Check and alert on daily loss limit milestones."""
    pct = session.loss_pct()

    if session.check_limit_hit():
        session.trading_suspended = True
        session.save()
        tg.send(
            f"🚨🚨 <b>DAILY LOSS LIMIT REACHED</b> 🚨🚨\n"
            f"ALL TRADING SUSPENDED\n"
            f"Loss today: ${session.daily_loss_used:.2f} / ${session.daily_loss_limit:.2f}\n"
            f"Review and reset tomorrow at 8am."
        )
    elif pct >= 0.90:
        tg.send(
            f"🔴 CRITICAL: 90% OF DAILY LOSS LIMIT USED\n"
            f"${session.daily_loss_used:.2f} / ${session.daily_loss_limit:.2f}\n"
            f"ONE MORE LOSS MAY SUSPEND TRADING"
        )
    elif pct >= 0.75:
        tg.send(
            f"🟠 WARNING: 75% of daily loss limit used\n"
            f"${session.daily_loss_used:.2f} / ${session.daily_loss_limit:.2f}\n"
            f"REDUCE SIZE ON NEXT TRADE"
        )
    elif pct >= 0.50:
        tg.send(
            f"🟡 WARNING: 50% of daily loss limit used\n"
            f"${session.daily_loss_used:.2f} / ${session.daily_loss_limit:.2f}"
        )


def monitor_all_positions(session: Session):
    """Run monitor loop for all open positions."""
    for ticker in list(session.open_positions.keys()):
        with _exit_lock:
            if ticker in session.open_positions:
                monitor_position(ticker, session)

# ============================================================
# opt_signals.py — Options signal evaluation
# Fires CALL signals on gap-up momentum stocks
# ============================================================
import yfinance as yf
import pandas as pd
from datetime import datetime, date
import pytz
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import options_bot.opt_config as cfg
from options_bot.opt_session import OptionsSession, et_now
from options_bot.opt_scanner import select_contract
from options_bot.opt_executor import sell_option, get_fill_price

ET = pytz.timezone("America/New_York")


def get_current_option_price(contract_symbol: str):
    """Get current mid-price for an options contract via yfinance."""
    try:
        tk = yf.Ticker(contract_symbol)
        fast = tk.fast_info
        price = fast.get("lastPrice") or fast.get("last_price")
        if price and price > 0:
            return round(float(price), 2)
        # Fall back: get from option chain by parsing OCC symbol
        # OCC: AAPL250117C00150000 -> underlying=AAPL, exp=250117, type=C, strike=150.00
        info = tk.info
        bid = info.get("bid") or 0
        ask = info.get("ask") or 0
        if bid > 0 and ask > 0:
            return round((bid + ask) / 2, 2)
        return None
    except Exception as e:
        print(f"[OPT SIGNALS] Price error {contract_symbol}: {e}")
        return None


def get_stock_price(ticker: str):
    """Get current underlying price."""
    try:
        tk = yf.Ticker(ticker)
        price = tk.fast_info.get("lastPrice") or tk.fast_info.get("last_price")
        if price and price > 0:
            return round(float(price), 2)
        df = tk.history(period="1d", interval="1m")
        if not df.empty:
            return round(float(df["Close"].iloc[-1]), 2)
    except Exception as e:
        print(f"[OPT SIGNALS] Stock price error {ticker}: {e}")
    return None


def is_trading_window():
    now = et_now()
    if now.weekday() >= 5:
        return False, "Weekend"
    open_t  = now.replace(hour=cfg.TRADING_START_HOUR, minute=cfg.TRADING_START_MINUTE, second=0, microsecond=0)
    close_t = now.replace(hour=cfg.TRADING_END_HOUR,   minute=cfg.TRADING_END_MINUTE,   second=0, microsecond=0)
    if now < open_t:
        return False, f"Not yet open ({now.strftime('%H:%M')} ET)"
    if now >= close_t:
        return False, "Trading window closed (after 11:00 ET)"
    return True, "OK"


def evaluate_options_signal(ticker: str, session: OptionsSession, watchlist_entry: dict):
    """
    Evaluate whether to fire a CALL options signal on a gap-up stock.

    Gates (same discipline as stock bot):
      1. Session armed + loss limit not hit
      2. Within 9:31-11:00am ET window
      3. No existing position in this ticker's contracts
      4. Daily trade cap not reached
      5. Can get a live stock price
      6. Contract still liquid (can find valid ask price)

    Returns (signal_dict, reason_str) or (None, reason_str).
    """
    # --- Gate 1: session state ---
    if not session.armed:
        return None, "Session not armed"
    if session.trading_suspended or session.check_limit_hit():
        return None, "Daily loss limit reached or trading suspended"

    # Skip if we already have a position in any contract for this ticker
    underlying_open = [p["underlying"] for p in session.open_positions.values()]
    if ticker in underlying_open:
        return None, f"Already in position for {ticker}"

    # Skip if we stopped out on this ticker today
    stopped = [t["underlying"] for t in session.trades_today
               if t.get("exit_reason") in ("Stop Loss", "Manual Stop")]
    if ticker in stopped:
        return None, f"{ticker} stopped out today"

    # --- Gate 2: trading window ---
    in_window, reason = is_trading_window()
    if not in_window:
        return None, reason

    # --- Gate 3: trade cap ---
    if len(session.trades_today) >= cfg.MAX_TRADES_PER_DAY:
        return None, f"Trade cap reached ({cfg.MAX_TRADES_PER_DAY}/day)"

    # --- Gate 4: get live stock price ---
    current_price = get_stock_price(ticker)
    if not current_price or current_price <= 0:
        return None, "Could not get underlying price"
    if current_price < cfg.MIN_PRICE:
        return None, f"Underlying ${current_price} below min ${cfg.MIN_PRICE}"

    # --- Gate 5: find or refresh the contract ---
    preloaded_contract = watchlist_entry.get("contract")
    contract = preloaded_contract

    # Re-fetch the chain if the preloaded ask was stale (>30% off current price)
    if not contract:
        contract = select_contract(ticker, "CALL", current_price)
    if not contract:
        return None, "No liquid options contract available"

    ask_price = contract["ask"]
    if ask_price <= 0:
        return None, "Contract ask is zero — market not open yet"

    # --- Build the signal ---
    now = et_now()
    max_premium = session.max_premium

    # How many contracts can we afford within our risk budget?
    contracts = int(max_premium / (ask_price * 100))
    if contracts < 1:
        return None, f"Premium ${ask_price*100:.0f} exceeds budget ${max_premium:.0f}"
    total_cost = round(ask_price * contracts * 100, 2)

    # Stop: 50% of premium paid
    stop_premium = round(ask_price * (1 - cfg.STOP_LOSS_PCT), 2)
    # Target: 75% gain on premium
    target_premium = round(ask_price * (1 + cfg.PROFIT_TARGET_PCT), 2)

    risk_dollars = round((ask_price - stop_premium) * contracts * 100, 2)

    conviction = watchlist_entry.get("conviction", "B")
    catalyst = watchlist_entry.get("catalyst", "No catalyst noted")
    notes = catalyst
    if now.hour >= 10 and now.minute >= 30:
        notes += " | ⚠️ LATE — REDUCED CONVICTION"

    print(
        f"[OPT SIGNALS] ✅ {ticker} CALL SIGNAL | "
        f"contract={contract['contract_symbol']} ask=${ask_price} | "
        f"x{contracts} contracts | risk=${risk_dollars:.2f}"
    )

    return {
        "signal_type":      "Options Gap and Go",
        "ticker":           ticker,
        "underlying":       ticker,
        "direction":        "CALL",
        "conviction":       conviction,
        "catalyst":         catalyst,
        "contract_symbol":  contract["contract_symbol"],
        "strike":           contract["strike"],
        "expiry":           contract["expiry"],
        "dte":              contract["dte"],
        "ask_price":        ask_price,
        "mid_price":        contract["mid"],
        "contracts":        contracts,
        "total_cost":       total_cost,
        "stop_premium":     stop_premium,
        "target_premium":   target_premium,
        "risk_dollars":     risk_dollars,
        "daily_loss_used":  session.daily_loss_used,
        "daily_loss_limit": session.daily_loss_limit,
        "notes":            notes,
        "time":             now.strftime("%H:%M:%S"),
    }, "SIGNAL"


def monitor_option_positions(session: OptionsSession, send_fn):
    """
    Check all open option positions and exit at target or stop.
    Called every 30s during market hours.
    """
    if not session.open_positions:
        return

    for contract_symbol, pos in list(session.open_positions.items()):
        try:
            current_premium = get_current_option_price(contract_symbol)
            if not current_premium or current_premium <= 0:
                # Try via underlying's chain
                current_premium = _get_premium_from_chain(pos["underlying"], contract_symbol)
            if not current_premium or current_premium <= 0:
                print(f"[OPT MONITOR] Could not get price for {contract_symbol}")
                continue

            premium_in = pos["premium_paid"]
            pnl_pct = round((current_premium - premium_in) / premium_in * 100, 1)
            pnl_dollars = round((current_premium - premium_in) * pos["contracts"] * 100, 2)

            def close_with_broker(reason):
                order = sell_option(contract_symbol, pos["contracts"])
                if not order or not order.get("id"):
                    send_fn(f"🚨 OPTIONS EXIT FAILED — {pos['underlying']}\nCould not submit sell-to-close for {contract_symbol}. Check Alpaca immediately.")
                    return None
                fill = get_fill_price(order["id"])
                if fill is None:
                    send_fn(f"🚨 OPTIONS EXIT UNCONFIRMED — {pos['underlying']}\nSell-to-close was submitted for {contract_symbol}, but fill was not confirmed. Check Alpaca immediately.")
                    return None
                return session.close_position(contract_symbol, fill, reason)

            # Target hit
            if current_premium >= pos["target_premium"]:
                trade = close_with_broker("Target Hit")
                if trade:
                    sign = "+" if trade["pnl"] >= 0 else ""
                    send_fn(
                        f"🎯 <b>TARGET HIT — {pos['underlying']} CALL</b>\n"
                        f"{contract_symbol}\n"
                        f"Entry: ${premium_in:.2f} → Exit: ${trade['exit_premium']:.2f} ({trade['pnl_pct']:+.1f}%)\n"
                        f"P&L: <b>{sign}${abs(trade['pnl']):.2f}</b> on {pos['contracts']} contract(s)"
                    )
                continue

            # Stop hit
            if current_premium <= pos["stop_premium"]:
                trade = close_with_broker("Stop Loss")
                if trade:
                    send_fn(
                        f"🛑 <b>STOP HIT — {pos['underlying']} CALL</b>\n"
                        f"{contract_symbol}\n"
                        f"Entry: ${premium_in:.2f} → Exit: ${trade['exit_premium']:.2f} ({trade['pnl_pct']:+.1f}%)\n"
                        f"P&L: <b>-${abs(trade['pnl']):.2f}</b> on {pos['contracts']} contract(s)\n"
                        f"Loss limit used: ${session.daily_loss_used:.2f}/${session.daily_loss_limit:.2f}"
                    )
                continue

            print(
                f"[OPT MONITOR] {pos['underlying']} {contract_symbol} | "
                f"${current_premium:.2f} ({pnl_pct:+.1f}%) | "
                f"Stop ${pos['stop_premium']:.2f} | Target ${pos['target_premium']:.2f}"
            )

        except Exception as e:
            print(f"[OPT MONITOR] Error monitoring {contract_symbol}: {e}")


def _get_premium_from_chain(underlying: str, contract_symbol: str):
    """Fallback: scrape the option chain for the contract's current mid-price."""
    try:
        tk = yf.Ticker(underlying)
        for exp in tk.options:
            chain = tk.option_chain(exp)
            for df in [chain.calls, chain.puts]:
                match = df[df["contractSymbol"] == contract_symbol]
                if not match.empty:
                    row = match.iloc[0]
                    bid, ask = row.get("bid", 0), row.get("ask", 0)
                    if bid > 0 and ask > 0:
                        return round((bid + ask) / 2, 2)
                    last = row.get("lastPrice", 0)
                    if last > 0:
                        return round(last, 2)
    except Exception:
        pass
    return None

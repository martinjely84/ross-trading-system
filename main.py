#!/usr/bin/env python3
# Ross Cameron Momentum Trading System
# Run: python main.py

import time
import os
import json
import requests
import yfinance as yf
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
import config
from session import Session, et_now
from scanner import run_premarket_scan, format_watchlist_message
from monitor import monitor_all_positions
from reports import generate_daily_report, generate_weekly_report
from signals import evaluate_gap_and_go, evaluate_first_candle_new_high
from brain import understand, analyze_and_improve

TOKEN = config.TELEGRAM_TOKEN
CHAT_ID = config.TELEGRAM_CHAT_ID
ET = pytz.timezone("America/New_York")

session = Session()
scheduler = BackgroundScheduler(timezone=ET)
pending_signals = {}  # ticker -> signal, waiting for approval
# Tickers whose buy fill could not be confirmed. We refuse to auto-re-enter
# these for the rest of the day so an unconfirmed (but possibly live) fill is
# never duplicated into a second long.
blocked_tickers = set()


def send(text):
    if not TOKEN or not CHAT_ID:
        print(f"[SEND SKIP] Telegram not configured: {text[:80]}")
        return
    import threading
    def _send():
        for attempt in range(3):
            try:
                r = requests.post(
                    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                    json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
                    timeout=15
                )
                if r.status_code == 200:
                    print(f"[SENT] {text[:60]}")
                    return
                else:
                    print(f"[SEND FAIL] {r.status_code} {r.text[:80]}")
            except Exception as e:
                print(f"[SEND ERROR attempt {attempt+1}] {e}")
                time.sleep(2)
    threading.Thread(target=_send, daemon=True).start()


def get_updates(offset):
    if not TOKEN:
        return []
    try:
        params = {"timeout": 0, "limit": 10}
        if offset:
            params["offset"] = offset
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates", params=params, timeout=15)
        return r.json().get("result", [])
    except Exception:
        return []


def _authorized_message(msg):
    chat_id = msg.get("chat", {}).get("id")
    if not CHAT_ID:
        print("[AUTH] TELEGRAM_CHAT_ID is not configured; ignoring commands")
        return False
    if chat_id != CHAT_ID:
        print(f"[AUTH] Ignoring Telegram message from unauthorized chat {chat_id}")
        return False
    return True


def _record_long_position(ticker, signal, fill, share_size):
    risk_per_share = round(fill * 0.02, 4)
    stop_loss = round(fill - risk_per_share, 4)
    target1 = round(fill + risk_per_share * 1.5, 4)
    target2 = round(fill + risk_per_share * 3.0, 4)
    session.add_position(
        ticker, fill, stop_loss,
        share_size, target1, target2,
        signal["signal_type"], signal["conviction"]
    )
    send(
        f"✅ <b>ORDER FILLED</b>\n"
        f"{ticker} — {share_size} shares @ ${fill:.2f}\n"
        f"Stop: ${stop_loss:.2f} | T1: ${target1:.2f} | T2: ${target2:.2f}"
    )
    return True


def _add_filled_long_position(ticker, signal, order):
    from executor import get_fill_price, get_position
    status, fill = get_fill_price(order.get("id"))
    if status == "filled":
        return _record_long_position(ticker, signal, fill, signal["share_size"])

    if status == "timeout":
        # Outcome unknown — the buy may have filled at the broker. Reconcile
        # against the actual broker position before deciding.
        pos = get_position(ticker)
        if pos:
            try:
                fill = round(float(pos.get("avg_entry_price")), 4)
                qty = abs(int(float(pos.get("qty", 0))))
            except (TypeError, ValueError):
                fill, qty = None, 0
            if fill and qty:
                send(f"ℹ️ {ticker} buy reconciled from broker after unconfirmed fill.")
                return _record_long_position(ticker, signal, fill, qty)
        # Could not confirm a position. Block auto re-entry for the day so we
        # never stack a second long on top of a possibly-live order.
        blocked_tickers.add(ticker)
        send(f"🚨 BUY UNCONFIRMED for {ticker}. Order submitted but fill not confirmed and no broker position found. Auto re-entry blocked for today — check Alpaca.")
        return False

    # status == "failed": order definitely did not fill; no position taken.
    send(f"❌ Order for {ticker} did not fill (rejected/expired). Check Alpaca.")
    return False


def _has_buying_power(signal):
    from executor import get_account
    acct = get_account()
    try:
        buying_power = float(acct.get("buying_power", 0))
    except Exception:
        buying_power = 0
    order_cost = float(signal["entry_price"]) * int(signal["share_size"])
    if buying_power < order_cost * 1.05:
        send(
            f"❌ Skipping {signal['ticker']}: insufficient buying power "
            f"(${buying_power:.2f} available, need about ${order_cost:.2f})."
        )
        return False
    return True


def handle(text):
    raw = text.strip()
    text = raw.lower()
    print(f"[CMD] {raw}")

    # Handle trade approvals
    if text.startswith("/approve "):
        ticker = text.split()[1].upper()
        if ticker in pending_signals:
            signal = pending_signals.pop(ticker)
            from executor import buy_market
            if not _has_buying_power(signal):
                return
            send(f"⏳ Placing BUY order: {signal['share_size']} shares of {ticker}...")
            order = buy_market(ticker, signal['share_size'])
            if order and order.get("id"):
                _add_filled_long_position(ticker, signal, order)
            else:
                send(f"❌ Order failed for {ticker}. Check Alpaca manually.")
        else:
            send(f"❌ No pending signal for {ticker}")
        return

    if text.startswith("/reject "):
        ticker = text.split()[1].upper()
        if ticker in pending_signals:
            pending_signals.pop(ticker)
            send(f"🚫 Signal rejected for {ticker}")
        return

    text = text.strip()

    if text == "/startauto":
        val = get_account_value()
        if val is None:
            send("❌ Could not fetch Alpaca account value. Session not armed.")
            return
        session.arm(val)
        send(
            f"✅ <b>SESSION ARMED — AUTO</b>\n"
            f"Account: ${val:,.2f} (live from Alpaca)\n"
            f"Daily loss limit: ${session.daily_loss_limit:.2f}\n"
            f"Per trade risk: ${session.per_trade_risk:.2f}\n"
            f"Good luck today. 🎯"
        )

    elif text.startswith("/start"):
        parts = text.split()
        if len(parts) >= 2:
            try:
                val = float(parts[1].replace("$", "").replace(",", ""))
                session.arm(val)
                send(
                    f"✅ <b>SESSION ARMED</b>\n"
                    f"Account: ${val:,.2f}\n"
                    f"Daily loss limit: ${session.daily_loss_limit:.2f}\n"
                    f"Per trade risk: ${session.per_trade_risk:.2f}\n"
                    f"Good luck today. 🎯"
                )
            except:
                send("❌ Usage: /start 500")
        else:
            send("❌ Usage: /start 500")

    elif text == "/status":
        if not session.armed:
            send("⚠️ Not armed. Send /start 500")
        else:
            pos = list(session.open_positions.keys())
            send(
                f"📊 <b>STATUS</b>\n"
                f"Account: ${session.account_value:.2f}\n"
                f"Loss used: ${session.daily_loss_used:.2f} / ${session.daily_loss_limit:.2f}\n"
                f"Positions: {', '.join(pos) if pos else 'None'}\n"
                f"Trades today: {len(session.trades_today)}\n"
                f"Suspended: {'YES ⛔' if session.trading_suspended else 'NO ✅'}"
            )

    elif text == "/watchlist":
        if session.watchlist:
            send(format_watchlist_message(session.watchlist))
        else:
            send("📋 No watchlist yet. Send /scan or wait for 8am ET.")

    elif text == "/scan":
        send("🔍 Scanning now...")
        wl = run_premarket_scan()
        session.watchlist = wl
        session.save()
        send(format_watchlist_message(wl))

    elif text.startswith("/close"):
        parts = text.split()
        if len(parts) >= 2:
            ticker = parts[1].upper()
            if ticker in session.open_positions:
                from monitor import _close_with_broker
                pos = session.open_positions[ticker]
                shares = pos.get("remaining_shares", pos.get("share_size", 0))
                trade = _close_with_broker(session, ticker, shares, "Manual")
                send(f"✅ Closed {ticker} @ ${trade['exit_price']:.2f} | P&L: ${trade['pnl']:.2f}" if trade else "❌ Close failed or unconfirmed. Check Alpaca immediately.")
            else:
                send(f"❌ No open position in {ticker}")
        else:
            send("❌ Usage: /close TICKER")

    elif text == "/suspend":
        session.trading_suspended = True
        session.save()
        send("⛔ Suspended. Send /resume to re-enable.")

    elif text == "/resume":
        if session.check_limit_hit():
            send("❌ Cannot resume — daily loss limit hit.")
        else:
            session.trading_suspended = False
            session.save()
            send("✅ Resumed.")

    elif text == "/report":
        generate_daily_report(session)

    elif text == "/help":
        send(
            "📖 <b>COMMANDS</b>\n"
            "/start 500 — arm session\n"
            "/status — current status\n"
            "/watchlist — today's watchlist\n"
            "/scan — run scan now\n"
            "/close TICKER — close position\n"
            "/suspend — pause signals\n"
            "/resume — resume signals\n"
            "/report — daily report\n"
            "/help — this menu\n\n"
            "Or just ask me anything naturally!"
        )
    elif text == "/suggestions":
        from brain import _load_insights
        insights = _load_insights()
        suggestions = insights.get("latest_suggestions", [])
        if suggestions:
            send("🔧 <b>IMPROVEMENT SUGGESTIONS</b>\n" + "\n".join(suggestions))
        else:
            send("No suggestions yet — need a few sessions of data first.")

    elif raw.startswith("/"):
        send("❓ Unknown command. Send /help")
    else:
        # Conversational
        response = understand(raw, session)
        print(f"[BRAIN] '{raw}' -> '{response[:50] if response else None}'")
        if response:
            send(response)
        else:
            send("Not sure what you mean. Try 'how are we doing?' or send /help")


# Scheduled jobs
def get_account_value():
    """Get real account value from Alpaca. Returns None if unavailable."""
    try:
        from executor import get_account
        acct = get_account()
        val = float(acct.get('equity', 0) or acct.get('cash', 0))
        if val > 0:
            return val
    except Exception as e:
        print(f"[ACCOUNT] Could not fetch from Alpaca: {e}")
    return None


def job_scan():
    # Get real account value from Alpaca.
    account_val = get_account_value()
    if account_val is None:
        send("❌ Morning scan skipped: could not fetch Alpaca account value.")
        return
    # New trading day — clear yesterday's unconfirmed-fill blocks.
    blocked_tickers.clear()
    session.arm(account_val)
    send(
        f"🌅 <b>GOOD MORNING — SESSION ARMED</b>\n"
        f"Account: ${account_val:,.2f} | Daily limit: ${session.daily_loss_limit:.2f} | Risk/trade: ${session.per_trade_risk:.2f}\n"
        f"Running pre-market scan..."
    )
    wl = run_premarket_scan()
    session.watchlist = wl
    session.save()
    send(format_watchlist_message(wl))


def process_bot_control():
    ctrl_file = "bot_control.json"
    if not os.path.exists(ctrl_file):
        return
    try:
        with open(ctrl_file) as f:
            cmd = json.load(f)
        os.remove(ctrl_file)
        action = cmd.get("action")
        if action == "arm":
            account_val = get_account_value()
            if account_val is None:
                send("❌ Dashboard arm failed: could not fetch Alpaca account value.")
                return
            session.arm(account_val)
            send(f"▶ <b>Armed via dashboard</b> — Account: ${account_val:,.2f}")
        elif action == "disarm":
            session.armed = False
            session.save()
            send("⏹ <b>Disarmed via dashboard</b>")
        elif action == "suspend":
            session.trading_suspended = True
            session.save()
            send("⏸ <b>Suspended via dashboard</b>")
        elif action == "resume":
            if session.check_limit_hit():
                send("❌ Cannot resume — daily loss limit hit.")
            else:
                session.trading_suspended = False
                session.save()
                send("▶ <b>Resumed via dashboard</b>")
        elif action == "close_all":
            from monitor import _close_with_broker
            for ticker in list(session.open_positions.keys()):
                pos = session.open_positions.get(ticker)
                shares = pos.get("remaining_shares", pos.get("share_size", 0)) if pos else 0
                trade = _close_with_broker(session, ticker, shares, "Dashboard Close All")
                if trade:
                    send(f"✕ Closed {ticker} @ ${trade['exit_price']:.2f} | P&L ${trade['pnl']:.2f}")
                else:
                    send(f"🚨 Close All: {ticker} NOT confirmed closed — still tracked. Check Alpaca immediately.")
            session.save()
            send("✕ <b>Close All via dashboard</b> — done")
    except Exception as e:
        print(f"[BOT CONTROL] {e}")

def job_signals():
    now = et_now()

    # Weekend guard
    if now.weekday() >= 5:
        return

    # Only run 9:31am–11:00am ET
    if now.hour < 9 or (now.hour == 9 and now.minute < 31) or now.hour >= 11:
        return

    if not session.armed:
        print("[SIGNALS] Session not armed — skipping signal check")
        return
    if session.trading_suspended:
        print("[SIGNALS] Trading suspended — skipping signal check")
        return

    # Cap total trades per day
    MAX_TRADES_PER_DAY = 3
    if len(session.trades_today) >= MAX_TRADES_PER_DAY:
        print(f"[SIGNALS] Trade cap reached ({MAX_TRADES_PER_DAY}) — done for today")
        return

    if not session.watchlist:
        print("[SIGNALS] Watchlist is empty")
        return

    print(f"[SIGNALS] Checking {len(session.watchlist)} watchlist stocks at {now.strftime('%H:%M:%S')} ET")

    for stock in session.watchlist:
        ticker = stock["ticker"]

        if ticker in session.open_positions:
            print(f"[SIGNALS] {ticker} — already in position, skipping")
            continue
        if ticker in pending_signals:
            print(f"[SIGNALS] {ticker} — already pending, skipping")
            continue
        if ticker in blocked_tickers:
            print(f"[SIGNALS] {ticker} — blocked (unconfirmed prior order), skipping")
            continue

        signal, reason = evaluate_gap_and_go(ticker, session, stock)

        if not signal:
            print(f"[SIGNALS] {ticker} — no signal: {reason}")
            continue

        # Signal fired — place order immediately
        session.signals_today.append(signal)
        session.save()

        from executor import buy_market
        if not _has_buying_power(signal):
            continue
        send(
            f"🚨 <b>SIGNAL: {signal['signal_type']}</b>\n"
            f"<b>{signal['ticker']}</b> [{signal['conviction']}] {et_now().strftime('%H:%M')} ET\n"
            f"📰 {signal['catalyst']}\n\n"
            f"▶️ ENTRY: <b>${signal['entry_price']:.2f}</b>\n"
            f"🛑 STOP: ${signal['stop_loss']:.2f} ({signal['stop_type']})\n"
            f"📦 SIZE: {signal['share_size']} shares | Risk: ${signal['total_risk']:.2f}\n"
            f"🎯 T1: ${signal['target1']:.2f} | T2: ${signal['target2']:.2f}\n"
            f"📊 Loss used: ${signal['daily_loss_used']:.2f} / ${signal['daily_loss_limit']:.2f}\n"
            f"⚡ Placing order automatically..."
        )

        order = buy_market(ticker, signal['share_size'])
        if order and order.get("id") and _add_filled_long_position(ticker, signal, order):
            print(f"[SIGNALS] ✅ Order placed for {ticker}")
        else:
            send(f"❌ Order FAILED for {ticker} — check Alpaca manually")
            print(f"[SIGNALS] ❌ Order failed for {ticker}")

        # Only fire ONE new trade per job cycle to avoid flooding
        break

def job_monitor():
    if session.armed:
        monitor_all_positions(session)

def job_830():
    if et_now().weekday() >= 5:
        return
    wl_count = len(session.watchlist)
    if wl_count > 0:
        send(f"🔔 <b>Market opens in 60 minutes.</b>\nWatchlist has {wl_count} stocks ready.\nSend /start [amount] to arm the session.")
    else:
        send("🔔 <b>Market opens in 60 minutes.</b>\nNo stocks on watchlist today — may be a sit-on-hands day.\nSend /start [amount] if you want to monitor.")

def job_10am():
    send("⏰ 10:00am — <b>A+ SETUPS ONLY</b>")

def job_1045():
    pos = list(session.open_positions.keys())
    send(f"⏰ <b>10:45am — 15 MIN TO CUTOFF</b>\nOpen: {', '.join(pos) if pos else 'None'}")

def job_11am():
    send("🔔 <b>11:00am — TRADING WINDOW CLOSED</b>")

def job_report():
    generate_daily_report(session)
    # Self-improvement analysis
    insight = analyze_and_improve(session)
    if insight:
        send(f"\n🧠 <b>SYSTEM LEARNING</b>\n{insight}")

def job_weekly():
    if et_now().weekday() == 4:
        generate_weekly_report()


def main():
    print("=" * 50)
    print("  Ross Cameron Trading System")
    print("=" * 50)

    scheduler.add_job(job_scan,    "cron",     hour=8,  minute=0)
    scheduler.add_job(job_830,     "cron",     hour=8,  minute=30)
    scheduler.add_job(job_signals, "interval", seconds=30)
    scheduler.add_job(job_monitor, "interval", seconds=30)
    scheduler.add_job(job_10am,    "cron",     hour=10, minute=0)
    scheduler.add_job(job_1045,    "cron",     hour=10, minute=45)
    scheduler.add_job(job_11am,    "cron",     hour=11, minute=0)
    scheduler.add_job(job_report,  "cron",     hour=15, minute=30)
    scheduler.add_job(job_weekly,  "cron",     hour=16, minute=0)
    scheduler.add_job(process_bot_control, "interval", seconds=10)
    scheduler.start()

    print("[MAIN] Running. Send /help to the bot. Ctrl+C to stop.")

    # Load or initialize offset
    offset_file = "/root/trading/.tg_offset"
    try:
        with open(offset_file) as f:
            offset = int(f.read().strip())
        print(f"[MAIN] Resuming from offset {offset}")
    except Exception:
        # First run — skip all pending old messages
        updates = get_updates(None)
        offset = updates[-1]["update_id"] + 1 if updates else None
        print(f"[MAIN] First run, starting at offset {offset}")

    while True:
        try:
            updates = get_updates(offset)
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    with open(offset_file, "w") as f:
                        f.write(str(offset))
                except Exception:
                    pass
                msg = u.get("message", {})
                text = msg.get("text", "")
                if text and _authorized_message(msg):
                    handle(text)
        except Exception as e:
            print(f"[LOOP ERROR] {e}")
        time.sleep(2)


if __name__ == "__main__":
    main()

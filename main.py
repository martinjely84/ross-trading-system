#!/usr/bin/env python3
# Ross Cameron Momentum Trading System
# Run: python main.py

import time
import requests
import yfinance as yf
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from session import Session, et_now
from scanner import run_premarket_scan, format_watchlist_message
from monitor import monitor_all_positions
from reports import generate_daily_report, generate_weekly_report
from signals import evaluate_gap_and_go, evaluate_first_candle_new_high

TOKEN = "8370287942:AAGKQPIbybD3WByLiF29aqg9NxnWXLWrH-Q"
CHAT_ID = 8620447966
ET = pytz.timezone("America/New_York")

session = Session()
scheduler = BackgroundScheduler(timezone=ET)
pending_signals = {}  # ticker -> signal, waiting for approval


def send(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=5
        )
    except Exception as e:
        print(f"[SEND ERROR] {e}")


def get_updates(offset):
    try:
        params = {"timeout": 0, "limit": 10}
        if offset:
            params["offset"] = offset
        r = requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates", params=params, timeout=5)
        return r.json().get("result", [])
    except Exception as e:
        print(f"[POLL ERROR] {e}")
        return []


def handle(text):
    text = text.strip().lower()
    print(f"[CMD] {text}")

    # Handle trade approvals
    if text.startswith("/approve "):
        ticker = text.split()[1].upper()
        if ticker in pending_signals:
            signal = pending_signals.pop(ticker)
            from executor import buy_market
            send(f"⏳ Placing BUY order: {signal['share_size']} shares of {ticker}...")
            order = buy_market(ticker, signal['share_size'])
            if order:
                session.add_position(
                    ticker, signal['entry_price'], signal['stop_loss'],
                    signal['share_size'], signal['target1'], signal['target2'],
                    signal['signal_type'], signal['conviction']
                )
                send(f"✅ <b>ORDER PLACED</b>\n{ticker} — {signal['share_size']} shares\nStop: ${signal['stop_loss']:.2f} | T1: ${signal['target1']:.2f} | T2: ${signal['target2']:.2f}")
            else:
                send(f"❌ Order failed for {ticker}. Check Webull manually.")
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

    if text.startswith("/start"):
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
                price = yf.Ticker(ticker).fast_info.get("lastPrice", 0)
                trade = session.close_position(ticker, price, "Manual")
                send(f"✅ Closed {ticker} @ ${price:.2f} | P&L: ${trade['pnl']:.2f}" if trade else "❌ Error")
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
            "/help — this menu"
        )
    else:
        send("❓ Unknown command. Send /help")


# Scheduled jobs
def job_scan():
    send("🌅 <b>PRE-MARKET SCAN STARTING</b>")
    wl = run_premarket_scan()
    session.watchlist = wl
    session.save()
    send(format_watchlist_message(wl))

def job_signals():
    now = et_now()
    if not session.armed or session.trading_suspended:
        return
    if now.hour < 9 or (now.hour == 9 and now.minute < 30) or now.hour >= 11:
        return
    for stock in session.watchlist:
        ticker = stock["ticker"]
        if ticker in session.open_positions or ticker in pending_signals:
            continue
        signal, _ = evaluate_gap_and_go(ticker, session, stock)
        if not signal:
            signal, _ = evaluate_first_candle_new_high(ticker, session, stock)
        if signal:
            session.signals_today.append(signal)
            session.save()
            pending_signals[ticker] = signal
            send(
                f"🚨 <b>SIGNAL: {signal['signal_type']}</b>\n"
                f"<b>{signal['ticker']}</b> [{signal['conviction']}] {et_now().strftime('%H:%M')} ET\n"
                f"📰 {signal['catalyst']}\n\n"
                f"▶️ ENTRY: <b>${signal['entry_price']:.2f}</b>\n"
                f"🛑 STOP: ${signal['stop_loss']:.2f}\n"
                f"📦 SIZE: {signal['share_size']} shares | Risk: ${signal['total_risk']:.2f}\n"
                f"🎯 T1: ${signal['target1']:.2f} | T2: ${signal['target2']:.2f}\n"
                f"📊 Loss used: ${signal['daily_loss_used']:.2f} / ${signal['daily_loss_limit']:.2f}\n\n"
                f"Reply /approve {ticker} to execute or /reject {ticker} to skip"
            )

def job_monitor():
    if session.armed:
        monitor_all_positions(session)

def job_10am():
    send("⏰ 10:00am — <b>A+ SETUPS ONLY</b>")

def job_1045():
    pos = list(session.open_positions.keys())
    send(f"⏰ <b>10:45am — 15 MIN TO CUTOFF</b>\nOpen: {', '.join(pos) if pos else 'None'}")

def job_11am():
    send("🔔 <b>11:00am — TRADING WINDOW CLOSED</b>")

def job_report():
    generate_daily_report(session)

def job_weekly():
    if et_now().weekday() == 4:
        generate_weekly_report()


def main():
    print("=" * 50)
    print("  Ross Cameron Trading System")
    print("=" * 50)

    scheduler.add_job(job_scan,    "cron",     hour=8,  minute=0)
    scheduler.add_job(job_signals, "interval", seconds=30)
    scheduler.add_job(job_monitor, "interval", seconds=30)
    scheduler.add_job(job_10am,    "cron",     hour=10, minute=0)
    scheduler.add_job(job_1045,    "cron",     hour=10, minute=45)
    scheduler.add_job(job_11am,    "cron",     hour=11, minute=0)
    scheduler.add_job(job_report,  "cron",     hour=15, minute=30)
    scheduler.add_job(job_weekly,  "cron",     hour=16, minute=0)
    scheduler.start()

    send("🤖 <b>Trading System ONLINE</b>\nSend /help for commands.")
    print("[MAIN] Running. Send /help to the bot. Ctrl+C to stop.")

    offset = None
    while True:
        try:
            updates = get_updates(offset)
            for u in updates:
                offset = u["update_id"] + 1
                msg = u.get("message", {})
                text = msg.get("text", "")
                if text:
                    handle(text)
        except Exception as e:
            print(f"[LOOP ERROR] {e}")
        time.sleep(2)


if __name__ == "__main__":
    main()

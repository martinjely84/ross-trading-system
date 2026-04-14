#!/usr/bin/env python3
# ============================================================
# main.py — Ross Cameron Momentum Day Trading System
# ============================================================
import time
import requests
import pytz
from apscheduler.schedulers.background import BackgroundScheduler

import config
from session import Session, et_now
from scanner import run_premarket_scan, format_watchlist_message
from signals import evaluate_gap_and_go, evaluate_first_candle_new_high
from monitor import monitor_all_positions
from reports import generate_daily_report, generate_weekly_report

ET = pytz.timezone("America/New_York")
session = Session()
scheduler = BackgroundScheduler(timezone=ET)

# ── Telegram helpers ───────────────────────────────────────

def tg_send(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": session.chat_id, "text": text, "parse_mode": "HTML"},
            timeout=5
        )
    except Exception as e:
        print(f"[TG SEND ERROR] {e}")

def tg_updates(offset):
    try:
        params = {"timeout": 0, "limit": 10}
        if offset:
            params["offset"] = offset
        resp = requests.get(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/getUpdates",
            params=params, timeout=5
        )
        return resp.json().get("result", [])
    except Exception as e:
        print(f"[TG POLL ERROR] {e}")
        return []

# ── Command handler ────────────────────────────────────────

def handle_update(msg):
    text = msg.get("text", "").strip()
    chat_id = msg.get("chat", {}).get("id")
    if not text or not chat_id:
        return

    # Save chat_id on first contact
    if not session.chat_id:
        session.chat_id = chat_id
        session.save()

    print(f"[CMD] {text} from {chat_id}")

    if text.lower().startswith("/start"):
        parts = text.split()
        if len(parts) >= 2:
            try:
                val = float(parts[1].replace("$","").replace(",",""))
                session.arm(val)
                tg_send(
                    f"✅ <b>SESSION ARMED</b>\n"
                    f"Account: ${val:,.2f}\n"
                    f"Daily loss limit: ${session.daily_loss_limit:.2f}\n"
                    f"Per trade risk: ${session.per_trade_risk:.2f}\n"
                    f"Ready. Good luck today. 🎯"
                )
            except ValueError:
                tg_send("❌ Usage: /start 500")
        else:
            tg_send("❌ Usage: /start 500")

    elif text.lower() == "/status":
        if not session.armed:
            tg_send("⚠️ Not armed. Send /start 500")
        else:
            pos = list(session.open_positions.keys())
            tg_send(
                f"📊 <b>STATUS</b>\n"
                f"Account: ${session.account_value:.2f}\n"
                f"Loss used: ${session.daily_loss_used:.2f} / ${session.daily_loss_limit:.2f}\n"
                f"Open positions: {', '.join(pos) if pos else 'None'}\n"
                f"Trades today: {len(session.trades_today)}\n"
                f"Suspended: {'YES ⛔' if session.trading_suspended else 'NO ✅'}"
            )

    elif text.lower() == "/watchlist":
        if session.watchlist:
            tg_send(format_watchlist_message(session.watchlist))
        else:
            tg_send("📋 No watchlist yet. Scan runs at 8am ET or send /scan")

    elif text.lower() == "/scan":
        tg_send("🔍 Scanning...")
        wl = run_premarket_scan()
        session.watchlist = wl
        session.save()
        tg_send(format_watchlist_message(wl))

    elif text.lower().startswith("/close"):
        parts = text.split()
        if len(parts) >= 2:
            ticker = parts[1].upper()
            if ticker in session.open_positions:
                import yfinance as yf
                price = yf.Ticker(ticker).fast_info.get("lastPrice", 0)
                trade = session.close_position(ticker, price, "Manual")
                tg_send(f"✅ Closed {ticker} @ ${price:.2f} | P&L: ${trade['pnl']:.2f}" if trade else f"❌ Error closing {ticker}")
            else:
                tg_send(f"❌ No open position in {ticker}")
        else:
            tg_send("❌ Usage: /close TICKER")

    elif text.lower() == "/suspend":
        session.trading_suspended = True
        session.save()
        tg_send("⛔ Trading suspended. Send /resume to re-enable.")

    elif text.lower() == "/resume":
        if session.check_limit_hit():
            tg_send("❌ Cannot resume — daily loss limit hit.")
        else:
            session.trading_suspended = False
            session.save()
            tg_send("✅ Trading resumed.")

    elif text.lower() == "/report":
        generate_daily_report(session)

    elif text.lower() == "/help":
        tg_send(
            "📖 <b>COMMANDS</b>\n"
            "/start 500 — arm session\n"
            "/status — current status\n"
            "/watchlist — today's watchlist\n"
            "/scan — run scan now\n"
            "/close TICKER — close a position\n"
            "/suspend — pause signals\n"
            "/resume — resume signals\n"
            "/report — daily report\n"
            "/help — this menu"
        )
    else:
        tg_send(f"❓ Unknown command. Send /help")

# ── Scheduled jobs ─────────────────────────────────────────

def job_premarket_scan():
    tg_send("🌅 <b>PRE-MARKET SCAN STARTING</b>")
    wl = run_premarket_scan()
    session.watchlist = wl
    session.save()
    tg_send(format_watchlist_message(wl))

def job_check_signals():
    now = et_now()
    if not session.armed or session.trading_suspended:
        return
    if now.hour < 9 or (now.hour == 9 and now.minute < 30) or now.hour >= 11:
        return
    for stock in session.watchlist:
        ticker = stock["ticker"]
        if ticker in session.open_positions:
            continue
        if now.hour == 9 or (now.hour == 10 and now.minute <= 30):
            signal, _ = evaluate_gap_and_go(ticker, session, stock)
            if signal:
                session.signals_today.append(signal)
                session.save()
                from telegram_bot import send_signal
                send_signal(signal)
                continue
            signal, _ = evaluate_first_candle_new_high(ticker, session, stock)
            if signal:
                session.signals_today.append(signal)
                session.save()
                from telegram_bot import send_signal
                send_signal(signal)

def job_monitor():
    if session.armed:
        monitor_all_positions(session)

def job_10am():
    tg_send("⏰ 10:00am — <b>A+ SETUPS ONLY</b> from here.")

def job_1045():
    pos = list(session.open_positions.keys())
    tg_send(f"⏰ <b>10:45am — 15 MIN TO CUTOFF</b>\nOpen: {', '.join(pos) if pos else 'None'}")

def job_11am():
    tg_send("🔔 <b>11:00am — TRADING WINDOW CLOSED</b>")
    stats = session.summary_stats()
    if stats:
        tg_send(f"📊 {stats['total_trades']} trades | P&L: ${stats['total_pnl']:.2f} | WR: {stats['win_rate']}%")

def job_daily_report():
    generate_daily_report(session)

def job_weekly_report():
    if et_now().weekday() == 4:
        generate_weekly_report()

# ── Main ───────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Ross Cameron Momentum System — OpenClaw")
    print("=" * 60)
    print("[MAIN] Starting up...")

    # Start from scratch - process all pending messages
    offset = None
    print(f"[MAIN] Ready to receive commands.")

    # Schedule jobs
    scheduler.add_job(job_premarket_scan,  "cron",     hour=8,  minute=0,  id="scan")
    scheduler.add_job(job_check_signals,   "interval", seconds=30,          id="signals")
    scheduler.add_job(job_monitor,         "interval", seconds=30,          id="monitor")
    scheduler.add_job(job_10am,            "cron",     hour=10, minute=0,   id="10am")
    scheduler.add_job(job_1045,            "cron",     hour=10, minute=45,  id="1045")
    scheduler.add_job(job_11am,            "cron",     hour=11, minute=0,   id="11am")
    scheduler.add_job(job_daily_report,    "cron",     hour=15, minute=30,  id="report")
    scheduler.add_job(job_weekly_report,   "cron",     hour=16, minute=0,   id="weekly")
    scheduler.start()

    # Send startup message if we have a chat_id
    if session.chat_id:
        tg_send("🤖 <b>Trading System ONLINE</b>\nSend /help for commands.")
    else:
        print("[MAIN] Waiting for first Telegram message to register chat ID...")

    print("[MAIN] Running. Ctrl+C to stop.")

    try:
        while True:
            updates = tg_updates(offset)
            if updates:
                print(f"[CMD] {len(updates)} new message(s)")
            for u in updates:
                offset = u["update_id"] + 1
                msg = u.get("message", {})
                if msg:
                    handle_update(msg)
            time.sleep(2)
    except (KeyboardInterrupt, SystemExit):
        print("[MAIN] Shutting down...")
        scheduler.shutdown()
        if session.chat_id:
            tg_send("⚠️ Trading system shut down.")

if __name__ == "__main__":
    main()

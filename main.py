#!/usr/bin/env python3
# ============================================================
# main.py — Ross Cameron Momentum Day Trading System
# Run this every morning. Keep it running until market close.
# Usage: python main.py
# ============================================================
import time
import json
import pytz
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler

import config
import telegram_bot as tg
from session import Session, et_now
from scanner import run_premarket_scan, format_watchlist_message
from signals import evaluate_gap_and_go, evaluate_first_candle_new_high
from monitor import monitor_all_positions, _check_daily_limit
from reports import generate_daily_report, generate_weekly_report

ET = pytz.timezone("America/New_York")

# ── Global session object ──────────────────────────────────
session = Session()
session.update_id_offset = None  # Always reset offset on startup
session.save()
scheduler = BackgroundScheduler(timezone=ET)

# ── Telegram offset (in-memory, not from file) ─────────────
_tg_offset = None

# ── Telegram command handler ───────────────────────────────

def handle_commands():
    """Poll Telegram for incoming commands from Martin."""
    global _tg_offset
    import requests as _req
    try:
        params = {"timeout": 0, "limit": 10}
        if _tg_offset:
            params["offset"] = _tg_offset
        resp = _req.get(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/getUpdates",
            params=params, timeout=5
        )
        updates = resp.json().get("result", [])
    except Exception as e:
        print(f"[CMD] Poll error: {e}")
        return

    print(f"[CMD] {len(updates)} updates, offset={_tg_offset}")
    for update in updates:
        _tg_offset = update["update_id"] + 1

        msg = update.get("message", {})
        text = msg.get("text", "").strip()
        chat_id = msg.get("chat", {}).get("id")

        # Auto-register chat_id
        if chat_id and not config.TELEGRAM_CHAT_ID:
            config.TELEGRAM_CHAT_ID = chat_id
            tg._chat_id_cache = chat_id

        if not text:
            continue

        print(f"[CMD] Received: {text}")

        # /start <account_value>
        if text.lower().startswith("/start"):
            parts = text.split()
            if len(parts) >= 2:
                try:
                    account_val = float(parts[1].replace("$", "").replace(",", ""))
                    session.arm(account_val)
                    tg.send(
                        f"✅ <b>SESSION ARMED</b>\n"
                        f"Account: ${account_val:,.2f}\n"
                        f"Daily loss limit: ${session.daily_loss_limit:.2f}\n"
                        f"Per trade risk: ${session.per_trade_risk:.2f}\n"
                        f"Halt trade risk: ${session.halt_risk:.2f}\n"
                        f"Ready to scan. Good luck today. 🎯"
                    )
                except ValueError:
                    tg.send("❌ Usage: /start 500  (just the dollar amount)")
            else:
                tg.send("❌ Usage: /start 500")

        # /status
        elif text.lower() == "/status":
            if not session.armed:
                tg.send("⚠️ Session not armed. Send /start <account_value>")
            else:
                open_pos = list(session.open_positions.keys())
                tg.send(
                    f"📊 <b>SESSION STATUS</b>\n"
                    f"Account: ${session.account_value:.2f}\n"
                    f"Daily P&L: ${-session.daily_loss_used:.2f}\n"
                    f"Daily limit used: ${session.daily_loss_used:.2f} / ${session.daily_loss_limit:.2f}\n"
                    f"Open positions: {', '.join(open_pos) if open_pos else 'None'}\n"
                    f"Trades today: {len(session.trades_today)}\n"
                    f"Trading suspended: {'YES ⛔' if session.trading_suspended else 'NO ✅'}"
                )

        # /watchlist
        elif text.lower() == "/watchlist":
            if session.watchlist:
                tg.send(format_watchlist_message(session.watchlist))
            else:
                tg.send("📋 No watchlist yet. Pre-market scan runs at 8:00am ET.")

        # /scan — manual scan trigger
        elif text.lower() == "/scan":
            tg.send("🔍 Running manual scan...")
            wl = run_premarket_scan()
            session.watchlist = wl
            session.save()
            tg.send(format_watchlist_message(wl))

        # /close <ticker> — manual close
        elif text.lower().startswith("/close"):
            parts = text.split()
            if len(parts) >= 2:
                ticker = parts[1].upper()
                if ticker in session.open_positions:
                    import yfinance as yf
                    price = yf.Ticker(ticker).fast_info.get("lastPrice", 0)
                    trade = session.close_position(ticker, price, "Manual")
                    tg.send(
                        f"✅ Manual close: <b>{ticker}</b> @ ${price:.2f}\n"
                        f"P&L: ${trade['pnl']:.2f}" if trade else f"❌ No position in {ticker}"
                    )
                else:
                    tg.send(f"❌ No open position in {ticker}")

        # /report — manual daily report
        elif text.lower() == "/report":
            generate_daily_report(session)

        # /suspend — manual trading suspend
        elif text.lower() == "/suspend":
            session.trading_suspended = True
            session.save()
            tg.send("⛔ Trading manually suspended. Send /resume to re-enable.")

        # /resume — resume trading (human override)
        elif text.lower() == "/resume":
            if session.check_limit_hit():
                tg.send("❌ Cannot resume — daily loss limit already hit.")
            else:
                session.trading_suspended = False
                session.save()
                tg.send("✅ Trading resumed.")

        # /help
        elif text.lower() == "/help":
            tg.send(
                "📖 <b>COMMANDS</b>\n"
                "/start 500 — arm session with account value\n"
                "/status — show session status\n"
                "/watchlist — show today's watchlist\n"
                "/scan — run manual pre-market scan\n"
                "/close TICKER — manually close a position\n"
                "/suspend — pause all trading signals\n"
                "/resume — resume trading\n"
                "/report — generate daily report now\n"
                "/help — this menu"
            )


# ── Scheduled jobs ─────────────────────────────────────────

def job_premarket_scan():
    """8:00am — run pre-market scan."""
    print("[SCHEDULER] Pre-market scan starting...")
    tg.send("🌅 <b>PRE-MARKET SCAN STARTING</b> — 8:00am ET")
    wl = run_premarket_scan()
    session.watchlist = wl
    session.save()
    tg.send(format_watchlist_message(wl))


def job_scan_watchlist_updates():
    """Every 60s pre-market — check for watchlist updates."""
    now = et_now()
    if now.hour < 8 or now.hour >= 9:
        return
    # Check for new gap candidates briefly
    # (lightweight — just top Finviz tickers)
    from scanner import get_finviz_gap_scanner, evaluate_stock
    import time as t
    tickers = get_finviz_gap_scanner()
    current_tickers = [s["ticker"] for s in session.watchlist]
    for ticker in tickers[:5]:
        if ticker not in current_tickers:
            t.sleep(0.5)
            result = evaluate_stock(ticker)
            if result:
                session.watchlist.append(result)
                session.save()
                tg.send(
                    f"🆕 NEW WATCHLIST ADDITION\n"
                    f"<b>{result['ticker']}</b> [{result['conviction']}]\n"
                    f"Gap: +{result['gap_pct']}% | Vol: {result['premarket_vol']:,}\n"
                    f"Catalyst: {result['catalyst']}"
                )


def job_check_signals():
    """Every 30s during market hours — check for entry signals."""
    now = et_now()
    if not session.armed or session.trading_suspended:
        return
    if now.hour < 9 or (now.hour == 9 and now.minute < 30):
        return
    if now.hour >= 11:
        return

    for stock in session.watchlist:
        ticker = stock["ticker"]
        if ticker in session.open_positions:
            continue  # Already in position

        # Try Gap and Go first (9:30-10:00)
        if now.hour == 9 or (now.hour == 10 and now.minute == 0):
            signal, reason = evaluate_gap_and_go(ticker, session, stock)
            if signal:
                session.signals_today.append(signal)
                session.save()
                tg.send_signal(signal)
                continue

        # Try First Candle New High (9:30-10:30)
        if now.hour == 9 or (now.hour == 10 and now.minute <= 30):
            signal, reason = evaluate_first_candle_new_high(ticker, session, stock)
            if signal:
                session.signals_today.append(signal)
                session.save()
                tg.send_signal(signal)


def job_monitor_positions():
    """Every 30s — monitor open positions for exits."""
    if not session.armed:
        return
    monitor_all_positions(session)


def job_10am_alert():
    """10:00am — A+ setups only from now."""
    tg.send("⏰ 10:00am — <b>A+ SETUPS ONLY</b> from this point.")


def job_1045_alert():
    """10:45am — 15 minutes to cutoff."""
    open_pos = list(session.open_positions.keys())
    msg = "⏰ <b>10:45am — 15 MINUTES TO TRADING CUTOFF</b>\n"
    if open_pos:
        msg += f"Open positions: {', '.join(open_pos)}\nReview all positions now."
    else:
        msg += "No open positions."
    tg.send(msg)


def job_11am_cutoff():
    """11:00am — trading window closed."""
    tg.send("🔔 <b>11:00am — TRADING WINDOW CLOSED</b>\nNo new entries.")
    stats = session.summary_stats()
    if stats:
        tg.send(
            f"📊 <b>Session so far:</b>\n"
            f"Trades: {stats['total_trades']} | W/L: {stats['winners']}/{stats['losers']}\n"
            f"P&L: ${stats['total_pnl']:.2f} | Avg R: {stats['avg_r']:.2f}R"
        )
    else:
        tg.send("No trades taken this session.")


def job_daily_report():
    """3:30pm — send daily report."""
    generate_daily_report(session)


def job_weekly_report():
    """Friday 4:00pm — send weekly report."""
    now = et_now()
    if now.weekday() == 4:  # Friday
        generate_weekly_report()


def job_command_poll():
    """Every 3s — poll Telegram for commands."""
    handle_commands()


# ── Main ───────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Ross Cameron Momentum System — OpenClaw")
    print("=" * 60)

    tg.send(
        "🤖 <b>Ross Cameron Trading System ONLINE</b>\n"
        "Send /start <account_value> to arm the session.\n"
        "Example: /start 500\n\n"
        "Send /help for all commands."
    )

    # Schedule all jobs
    scheduler.add_job(job_premarket_scan,       "cron", hour=8, minute=0, id="premarket_scan")
    scheduler.add_job(job_scan_watchlist_updates,"interval", seconds=60, id="wl_updates")
    scheduler.add_job(job_check_signals,         "interval", seconds=30, id="signals")
    scheduler.add_job(job_monitor_positions,     "interval", seconds=30, id="monitor")
    scheduler.add_job(job_10am_alert,            "cron", hour=10, minute=0, id="10am")
    scheduler.add_job(job_1045_alert,            "cron", hour=10, minute=45, id="1045")
    scheduler.add_job(job_11am_cutoff,           "cron", hour=11, minute=0, id="11am")
    scheduler.add_job(job_daily_report,          "cron", hour=15, minute=30, id="daily_report")
    scheduler.add_job(job_weekly_report,         "cron", hour=16, minute=0, id="weekly_report")
    scheduler.start()
    print("[MAIN] Scheduler running. Ctrl+C to stop.")

    try:
        while True:
            try:
                handle_commands()
            except Exception as e:
                print(f"[CMD ERROR] {e}")
            time.sleep(2)
    except (KeyboardInterrupt, SystemExit):
        print("[MAIN] Shutting down...")
        scheduler.shutdown()
        tg.send("⚠️ Trading system shut down manually.")


if __name__ == "__main__":
    main()

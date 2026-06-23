#!/usr/bin/env python3
# Options Trading Bot — Gap-and-Go Calls
# Run: python -m options_bot.opt_main  (from project root)
#   or: python options_bot/opt_main.py

import time
import threading
import requests
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import options_bot.opt_config as cfg
from options_bot.opt_session import OptionsSession, et_now
from options_bot.opt_scanner import run_options_scan, format_watchlist_message
from options_bot.opt_signals import evaluate_options_signal, monitor_option_positions
from options_bot.opt_executor import buy_option, sell_option, get_account, get_fill_price
from options_bot.opt_brain import understand, analyze_and_improve

ET = pytz.timezone("America/New_York")
session = OptionsSession()
scheduler = BackgroundScheduler(timezone=ET)


# ── Telegram helpers ─────────────────────────────────────────

def send(text: str):
    if not cfg.TOKEN or not cfg.CHAT_ID:
        print(f"[SEND SKIP] Options Telegram not configured: {text[:80]}")
        return
    def _send():
        for attempt in range(3):
            try:
                r = requests.post(
                    f"https://api.telegram.org/bot{cfg.TOKEN}/sendMessage",
                    json={"chat_id": cfg.CHAT_ID, "text": text, "parse_mode": "HTML"},
                    timeout=15,
                )
                if r.status_code == 200:
                    print(f"[SENT] {text[:60]}")
                    return
                print(f"[SEND FAIL] {r.status_code} {r.text[:80]}")
            except Exception as e:
                print(f"[SEND ERROR attempt {attempt+1}] {e}")
                time.sleep(2)
    threading.Thread(target=_send, daemon=True).start()


def get_updates(offset):
    if not cfg.TOKEN:
        return []
    try:
        params = {"timeout": 0, "limit": 10}
        if offset:
            params["offset"] = offset
        r = requests.get(
            f"https://api.telegram.org/bot{cfg.TOKEN}/getUpdates",
            params=params, timeout=15,
        )
        return r.json().get("result", [])
    except Exception:
        return []


# ── Command handler ───────────────────────────────────────────

def _authorized_message(msg):
    chat_id = msg.get("chat", {}).get("id")
    if not cfg.CHAT_ID:
        print("[AUTH] OPTIONS_TELEGRAM_CHAT_ID/TELEGRAM_CHAT_ID is not configured; ignoring commands")
        return False
    if chat_id != cfg.CHAT_ID:
        print(f"[AUTH] Ignoring options Telegram message from unauthorized chat {chat_id}")
        return False
    return True

def handle(text: str):
    raw = text.strip()
    cmd = raw.lower()
    print(f"[CMD] {raw}")

    # /start [amount] or /startauto
    if cmd == "/startauto":
        val = _get_account_value()
        if val is None:
            send("❌ Could not fetch Alpaca account value. Options session not armed.")
            return
        session.arm(val)
        send(
            f"✅ <b>OPTIONS SESSION ARMED — AUTO</b>\n"
            f"Account: ${val:,.2f}\n"
            f"Daily loss limit: ${session.daily_loss_limit:.2f}\n"
            f"Max premium/trade: ${session.max_premium:.2f}\n"
            f"Strategy: Long calls on gap-up momentum\n"
            f"Window: 9:31am–11:00am ET | Max {cfg.MAX_TRADES_PER_DAY} trades\n"
            f"Good luck. 🎯"
        )
        return

    if cmd.startswith("/start"):
        parts = cmd.split()
        if len(parts) >= 2:
            try:
                val = float(parts[1].replace("$", "").replace(",", ""))
                session.arm(val)
                send(
                    f"✅ <b>OPTIONS SESSION ARMED</b>\n"
                    f"Account: ${val:,.2f}\n"
                    f"Daily loss limit: ${session.daily_loss_limit:.2f}\n"
                    f"Max premium/trade: ${session.max_premium:.2f}\n"
                    f"Strategy: Long calls on gap-up momentum\n"
                    f"Window: 9:31am–11:00am ET | Max {cfg.MAX_TRADES_PER_DAY} trades\n"
                    f"Good luck. 🎯"
                )
            except Exception:
                send("❌ Usage: /start 500")
        else:
            send("❌ Usage: /start 500")
        return

    if cmd == "/status":
        if not session.armed:
            send("⚠️ Not armed. Send /start 500")
            return
        pos_lines = []
        for sym, p in session.open_positions.items():
            pos_lines.append(f"  {p['underlying']} {p['direction']} ${p['strike']} | "
                             f"paid ${p['premium_paid']:.2f} | stop ${p['stop_premium']:.2f} | "
                             f"target ${p['target_premium']:.2f}")
        send(
            f"📊 <b>OPTIONS STATUS</b>\n"
            f"Account: ${session.account_value:.2f}\n"
            f"Loss used: ${session.daily_loss_used:.2f} / ${session.daily_loss_limit:.2f}\n"
            f"Trades today: {len(session.trades_today)}\n"
            f"Open positions: {len(session.open_positions)}\n"
            + ("\n".join(pos_lines) if pos_lines else "  None") + "\n"
            f"Suspended: {'YES ⛔' if session.trading_suspended else 'NO ✅'}"
        )
        return

    if cmd == "/watchlist":
        if session.watchlist:
            send(format_watchlist_message(session.watchlist))
        else:
            send("📋 No watchlist yet. Send /scan or wait for 8am ET.")
        return

    if cmd == "/scan":
        send("🔍 Scanning for options plays...")
        wl = run_options_scan()
        session.watchlist = wl
        session.save()
        send(format_watchlist_message(wl))
        return

    if cmd.startswith("/close"):
        parts = cmd.split()
        if len(parts) >= 2:
            ticker = parts[1].upper()
            # Find open position by underlying ticker
            match = None
            for sym, p in session.open_positions.items():
                if p["underlying"] == ticker or sym == ticker:
                    match = (sym, p)
                    break
            if match:
                contract_symbol, pos = match
                from options_bot.opt_signals import get_current_option_price, _get_premium_from_chain
                price = get_current_option_price(contract_symbol) or _get_premium_from_chain(pos["underlying"], contract_symbol) or pos["stop_premium"]
                result = sell_option(contract_symbol, pos["contracts"])
                fill = get_fill_price(result.get("id")) if result and result.get("id") else None
                trade = session.close_position(contract_symbol, fill, "Manual") if fill is not None else None
                if trade:
                    sign = "+" if trade["pnl"] >= 0 else ""
                    send(f"✅ Closed {pos['underlying']} CALL @ ${fill:.2f} | P&L: {sign}${trade['pnl']:.2f} ({trade['pnl_pct']:+.1f}%)")
                else:
                    send("❌ Close failed or unconfirmed. Check Alpaca immediately.")
            else:
                send(f"❌ No open position for {ticker}")
        else:
            send("❌ Usage: /close TICKER")
        return

    if cmd == "/suspend":
        session.trading_suspended = True
        session.save()
        send("⛔ Suspended. Send /resume to re-enable.")
        return

    if cmd == "/resume":
        if session.check_limit_hit():
            send("❌ Cannot resume — daily loss limit hit.")
        else:
            session.trading_suspended = False
            session.save()
            send("✅ Resumed.")
        return

    if cmd == "/report":
        _send_daily_report()
        return

    if cmd == "/suggestions":
        insights = _load_opt_insights()
        suggestions = insights.get("latest_suggestions", [])
        if suggestions:
            send("🔧 <b>IMPROVEMENT SUGGESTIONS</b>\n" + "\n".join(suggestions))
        else:
            send("No suggestions yet — need a few sessions of data first.")
        return

    if cmd == "/help":
        send(
            "📖 <b>OPTIONS BOT COMMANDS</b>\n"
            "/start 500 — arm session with account value\n"
            "/startauto — arm using live Alpaca balance\n"
            "/status — positions and P&L\n"
            "/watchlist — today's options watchlist\n"
            "/scan — run options scan now\n"
            "/close TICKER — close position manually\n"
            "/suspend — pause signals\n"
            "/resume — resume signals\n"
            "/report — daily P&L report\n"
            "/suggestions — improvement suggestions\n"
            "/help — this menu\n\n"
            "Or just ask me anything naturally!\n"
            "Example: 'what's our theta risk?' or 'should we buy AAPL calls?'"
        )
        return

    if raw.startswith("/"):
        send("❓ Unknown command. Send /help")
        return

    # Conversational fallback
    response = understand(raw, session)
    print(f"[BRAIN] '{raw[:40]}' -> '{(response or '')[:50]}'")
    if response:
        send(response)
    else:
        send("Not sure what you mean. Try 'how are we doing?' or send /help")


# ── Account value helper ──────────────────────────────────────

def _get_account_value():
    try:
        acct = get_account()
        val = float(acct.get("equity") or acct.get("cash") or 0)
        if val > 0:
            return val
    except Exception as e:
        print(f"[ACCOUNT] {e}")
    return None


def _load_opt_insights():
    import json
    insights_file = "opt_insights.json"
    if os.path.exists(insights_file):
        try:
            with open(insights_file) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


# ── Scheduled jobs ────────────────────────────────────────────

def job_scan():
    now = et_now()
    if now.weekday() >= 5:
        return
    account_val = _get_account_value()
    if account_val is None:
        send("❌ Options morning scan skipped: could not fetch Alpaca account value.")
        return
    session.arm(account_val)
    send(
        f"🌅 <b>OPTIONS BOT — GOOD MORNING</b>\n"
        f"Account: ${account_val:,.2f} | Limit: ${session.daily_loss_limit:.2f} | Max premium: ${session.max_premium:.2f}\n"
        f"Running options scan..."
    )
    wl = run_options_scan()
    session.watchlist = wl
    session.save()
    send(format_watchlist_message(wl))


def job_830():
    now = et_now()
    if now.weekday() >= 5:
        return
    wl_count = len(session.watchlist)
    if wl_count > 0:
        send(f"🔔 <b>Options market opens in 60 minutes.</b>\n{wl_count} plays on watchlist.\nSend /start [amount] to arm.")
    else:
        send("🔔 <b>Options market opens in 60 minutes.</b>\nNo plays today — sit on hands.")


def job_signals():
    now = et_now()
    if now.weekday() >= 5:
        return
    if now.hour < 9 or (now.hour == 9 and now.minute < 31) or now.hour >= 11:
        return
    if not session.armed:
        print("[OPT SIGNALS JOB] Not armed")
        return
    if session.trading_suspended:
        print("[OPT SIGNALS JOB] Suspended")
        return
    if len(session.trades_today) >= cfg.MAX_TRADES_PER_DAY:
        print(f"[OPT SIGNALS JOB] Trade cap {cfg.MAX_TRADES_PER_DAY} reached")
        return
    if not session.watchlist:
        print("[OPT SIGNALS JOB] Empty watchlist")
        return

    for stock in session.watchlist:
        ticker = stock["ticker"]
        signal, reason = evaluate_options_signal(ticker, session, stock)

        if not signal:
            print(f"[OPT SIGNALS JOB] {ticker} — no signal: {reason}")
            continue

        session.signals_today.append(signal)
        session.save()

        send(
            f"🚨 <b>OPTIONS SIGNAL: {signal['signal_type']}</b>\n"
            f"<b>{signal['underlying']}</b> [{signal['conviction']}] {et_now().strftime('%H:%M')} ET\n"
            f"📰 {signal['catalyst']}\n\n"
            f"▶ BUY CALL: {signal['contract_symbol']}\n"
            f"   Strike ${signal['strike']} | Exp {signal['expiry']} ({signal['dte']} DTE)\n"
            f"   Ask: ${signal['ask_price']:.2f}/share | {signal['contracts']} contract(s)\n"
            f"   Total cost: ${signal['total_cost']:.2f}\n"
            f"   🛑 Stop at: ${signal['stop_premium']:.2f} (50% loss)\n"
            f"   🎯 Target: ${signal['target_premium']:.2f} (75% gain)\n"
            f"   Risk: ${signal['risk_dollars']:.2f}\n"
            f"⚡ Placing order automatically..."
        )

        order = buy_option(signal["contract_symbol"], signal["contracts"])
        fill = get_fill_price(order.get("id")) if order and order.get("id") else None
        if fill is not None:
            session.add_position(
                contract_symbol=signal["contract_symbol"],
                underlying=signal["underlying"],
                direction=signal["direction"],
                strike=signal["strike"],
                expiry=signal["expiry"],
                contracts=signal["contracts"],
                premium_paid=fill,
                stop_premium=round(fill * (1 - cfg.STOP_LOSS_PCT), 2),
                target_premium=round(fill * (1 + cfg.PROFIT_TARGET_PCT), 2),
                signal_type=signal["signal_type"],
                conviction=signal["conviction"],
            )
            send(
                f"✅ <b>ORDER FILLED</b>\n"
                f"{signal['contract_symbol']}\n"
                f"x{signal['contracts']} @ ${fill:.2f}/share (${fill * signal['contracts'] * 100:.2f} total)"
            )
        else:
            send(f"❌ Order failed or unconfirmed for {signal['contract_symbol']} — check Alpaca manually")

        break  # one trade per cycle


def job_monitor():
    if session.armed:
        monitor_option_positions(session, send)


def job_10am():
    send("⏰ 10:00am — <b>A+ SETUPS ONLY</b> — theta decay accelerating on short DTE contracts")


def job_1045():
    open_pos = list(session.open_positions.keys())
    send(f"⏰ <b>10:45am — 15 MIN TO CUTOFF</b>\nOpen: {', '.join(open_pos) if open_pos else 'None'}")


def job_11am():
    send("🔔 <b>11:00am — TRADING WINDOW CLOSED</b>\nMonitoring existing positions only.")


def job_report():
    _send_daily_report()
    insight = analyze_and_improve(session)
    if insight:
        send(f"\n🧠 <b>SYSTEM LEARNING</b>\n{insight}")


def _send_daily_report():
    stats = session.summary_stats()
    if not stats:
        send("📊 <b>DAILY REPORT</b>\nNo trades today.")
        return

    lines = [
        "📊 <b>OPTIONS DAILY REPORT</b>",
        f"Trades: {stats['total_trades']} | Win rate: {stats['win_rate']}%",
        f"P&L: ${stats['total_pnl']:+.2f}",
        f"Avg P&L%: {stats['avg_pnl_pct']:+.1f}% per trade",
        f"Loss limit used: ${session.daily_loss_used:.2f} / ${session.daily_loss_limit:.2f}",
        "",
        "Trades:",
    ]
    for t in session.trades_today:
        sign = "+" if t["pnl"] >= 0 else ""
        lines.append(
            f"  {t['underlying']} {t['direction']} ${t['strike']} ({t.get('dte','?')}DTE) [{t['conviction']}] "
            f"{sign}${t['pnl']:.2f} ({t['pnl_pct']:+.1f}%) — {t['exit_reason']}"
        )
    send("\n".join(lines))


# ── Main loop ─────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  Options Trading Bot — Gap-and-Go Calls")
    print("=" * 50)
    print(f"  Token configured: {'YES' if cfg.TOKEN else 'NO'}")
    print(f"  Chat ID: {cfg.CHAT_ID}")
    print("=" * 50)

    if not cfg.TOKEN or not cfg.CHAT_ID:
        print("\nWARNING: Set OPTIONS_TELEGRAM_TOKEN/OPTIONS_TELEGRAM_CHAT_ID or TELEGRAM_TOKEN/TELEGRAM_CHAT_ID before running.\n")

    scheduler.add_job(job_scan,    "cron",     hour=8,  minute=0)
    scheduler.add_job(job_830,     "cron",     hour=8,  minute=30)
    scheduler.add_job(job_signals, "interval", seconds=30)
    scheduler.add_job(job_monitor, "interval", seconds=30)
    scheduler.add_job(job_10am,    "cron",     hour=10, minute=0)
    scheduler.add_job(job_1045,    "cron",     hour=10, minute=45)
    scheduler.add_job(job_11am,    "cron",     hour=11, minute=0)
    scheduler.add_job(job_report,  "cron",     hour=15, minute=30)
    scheduler.start()

    print("[MAIN] Options bot running. Send /help to the bot. Ctrl+C to stop.")

    offset_file = "opt_tg_offset"
    try:
        with open(offset_file) as f:
            offset = int(f.read().strip())
        print(f"[MAIN] Resuming from Telegram offset {offset}")
    except Exception:
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

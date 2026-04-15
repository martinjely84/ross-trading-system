# ============================================================
# brain.py -- Conversational AI + self-improvement engine
# ============================================================
import json
import os
from datetime import datetime, timedelta
import pytz

ET = pytz.timezone("America/New_York")
INSIGHTS_FILE = "insights.json"


def understand(text: str, session) -> str:
    t = text.lower().strip()
    now = datetime.now(ET)

    # Date / time
    if any(w in t for w in ["what day", "what time", "what date", "whats today", "what's today"]):
        return "It's {} - {} CT.".format(
            now.strftime("%A, %B %d"),
            now.strftime("%I:%M %p")
        )

    # Greetings
    if any(w in t for w in ["hello", "hey", "hi", "morning", "good morning", "you ok", "how are you", "what's up", "whats up", "sup", "alright"]):
        hour = now.hour
        greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
        armed = "Session armed and ready to trade." if session.armed else "Send /start 500 when you're ready."
        return "{} Martin! {}".format(greeting, armed)

    # P&L / how are we doing
    if any(w in t for w in ["how are we", "how we doing", "how did we", "what did we make", "profit", "p&l", "pnl", "make today", "lose today", "results", "did we make"]):
        return _pnl_summary(session)

    # Account / balance
    if any(w in t for w in ["account", "balance", "how much", "total", "worth"]):
        return _account_summary(session)

    # Trades
    if any(w in t for w in ["trades", "how many trades", "what trades", "trade today"]):
        return _trades_summary(session)

    # Watchlist
    if any(w in t for w in ["watchlist", "watching", "what stocks", "any stocks", "looking at"]):
        if session.watchlist:
            names = ["{} ({}, +{}%)".format(s["ticker"], s["conviction"], s["gap_pct"]) for s in session.watchlist[:5]]
            return "Watching {} stocks today:\n{}".format(len(session.watchlist), "\n".join(names))
        return "No watchlist yet. Scan runs at 7am CT or send /scan."

    # Positions
    if any(w in t for w in ["position", "in anything", "holding", "open trade"]):
        pos = session.open_positions
        if pos:
            lines = []
            for ticker, p in pos.items():
                lines.append("{}: {} shares @ ${:.2f}, stop ${:.2f}".format(
                    ticker, p["remaining_shares"], p["entry_price"], p["current_stop"]
                ))
            return "Open positions:\n" + "\n".join(lines)
        return "No open positions right now."

    # Loss limit
    if any(w in t for w in ["limit", "how much left", "budget", "risk left", "loss limit"]):
        if not session.armed:
            return "Session not armed yet. Send /start 500."
        return "Daily limit: ${:.2f} | Used: ${:.2f} | Left: ${:.2f}".format(
            session.daily_loss_limit, session.daily_loss_used, session.daily_loss_remaining()
        )

    # Status
    if any(w in t for w in ["suspended", "running", "active", "status", "working"]):
        if session.trading_suspended:
            return "Trading is suspended. Send /resume to re-enable."
        elif not session.armed:
            return "Not armed yet. Send /start 500."
        return "System is active and scanning."

    # Market hours
    if any(w in t for w in ["market", "is it open", "trading today", "market open"]):
        if now.weekday() >= 5:
            return "Market is closed - it's the weekend."
        open_time = now.replace(hour=8, minute=30, second=0, microsecond=0)
        close_time = now.replace(hour=15, minute=0, second=0, microsecond=0)
        if now < open_time:
            mins = int((open_time - now).seconds / 60)
            return "Market opens in {} minutes (8:30am CT).".format(mins)
        elif now > close_time:
            return "Market closed for today."
        else:
            return "Market is open. Trading window is 8:30-10:00am CT."

    # Insights
    if any(w in t for w in ["insight", "learning", "what's working", "pattern", "improving"]):
        return _get_insights()

    # Best / worst trade
    if any(w in t for w in ["best trade", "biggest winner"]):
        trades = session.trades_today
        if not trades:
            return "No trades today yet."
        best = max(trades, key=lambda x: x["pnl"])
        return "Best trade: {} +${:.2f} ({}R)".format(best["ticker"], best["pnl"], best["r_multiple"])

    if any(w in t for w in ["worst trade", "biggest loss"]):
        trades = session.trades_today
        if not trades:
            return "No trades today yet."
        worst = min(trades, key=lambda x: x["pnl"])
        return "Worst trade: {} ${:.2f} ({}R)".format(worst["ticker"], worst["pnl"], worst["r_multiple"])

    # Help
    if any(w in t for w in ["help", "what can you", "commands", "what do you do"]):
        return (
            "Just talk to me naturally! Try:\n"
            "- 'How are we doing today?'\n"
            "- 'Any open positions?'\n"
            "- 'What day is it?'\n"
            "- 'Is the market open?'\n"
            "- 'What's working this week?'\n\n"
            "Or use: /status /scan /watchlist /suspend /resume /report"
        )

    # Positive / motivational
    if any(w in t for w in ["good day", "great day", "good first", "first day", "should be", "gonna be", "going to be", "big day", "excited", "nervous", "fired up", "pumped"]):
        return "Absolutely. Stick to the rules, trust the system, protect the capital. Let the setups come to you."

    # Ready / tomorrow
    if any(w in t for w in ["ready", "tomorrow", "lets go", "game plan"]):
        if session.watchlist:
            top = session.watchlist[0]
            return "Ready! Top pick so far is {} (+{}% gap, {}). Market opens 8:30am CT.".format(
                top["ticker"], top["gap_pct"], top["conviction"])
        return "Ready for tomorrow! Scan runs at 7am CT and I'll send the watchlist automatically."

    # Good night / bye
    if any(w in t for w in ["good night", "goodnight", "bye", "sleep", "night", "later", "see you"]):
        return "Good night Martin! I'll be here at 7am CT with the watchlist. Sleep well."

    # Thanks
    if any(w in t for w in ["thanks", "thank you", "cheers", "appreciate"]):
        return "Anytime! That's what I'm here for."

    # Improvement reminders
    if any(w in t for w in ["improve", "get better", "learn", "evolve", "upgrade", "continually"]):
        return "Always. After every session I analyse what worked and what didn't, and flag improvements. Check /suggestions after a few trading days to see what I've learned."

    # Fallback
    return "I'm still learning conversational stuff! Try: 'how are we doing', 'any positions', 'what day is it', or just say hey."


def _pnl_summary(session) -> str:
    trades = session.trades_today
    if not trades:
        if session.armed:
            return "No trades yet today. Loss limit remaining: ${:.2f}".format(session.daily_loss_remaining())
        return "Session not started. Send /start 500."
    total = sum(t["pnl"] for t in trades)
    winners = [t for t in trades if t["pnl"] > 0]
    losers = [t for t in trades if t["pnl"] <= 0]
    direction = "up" if total >= 0 else "down"
    return "We're {} ${:.2f} today. {} winners, {} losers out of {} trades. Limit used: ${:.2f}/${:.2f}".format(
        direction, abs(total), len(winners), len(losers), len(trades),
        session.daily_loss_used, session.daily_loss_limit
    )


def _account_summary(session) -> str:
    if not session.armed:
        return "Session not armed yet. Send /start 500."
    trades = session.trades_today
    total_pnl = sum(t["pnl"] for t in trades) if trades else 0
    current = session.account_value + total_pnl
    direction = "up" if total_pnl >= 0 else "down"
    return "Account: ${:.2f} ({} ${:.2f} today). Started at ${:.2f}".format(
        current, direction, abs(total_pnl), session.account_value
    )


def _trades_summary(session) -> str:
    trades = session.trades_today
    if not trades:
        return "No trades today yet."
    lines = ["{} trades today:".format(len(trades))]
    for t in trades:
        pnl = "+${:.2f}".format(t["pnl"]) if t["pnl"] >= 0 else "-${:.2f}".format(abs(t["pnl"]))
        lines.append("- {} {} ({}R) - {}".format(t["ticker"], pnl, t["r_multiple"], t["exit_reason"]))
    return "\n".join(lines)


def _get_insights() -> str:
    insights = _load_insights()
    return insights.get("latest_insight", "Need a few sessions of data to generate insights.")


# ── Self-improvement engine ────────────────────────────────

def analyze_and_improve(session):
    trades = session.trades_today
    if not trades:
        return None

    insights = _load_insights()
    today = datetime.now(ET).strftime("%Y-%m-%d")

    insights["history"][today] = {
        "trades": len(trades),
        "pnl": round(sum(t["pnl"] for t in trades), 2),
        "winners": len([t for t in trades if t["pnl"] > 0]),
        "by_setup": {},
        "by_conviction": {},
    }

    for t in trades:
        setup = t.get("setup_type", "unknown")
        conviction = t.get("conviction", "unknown")

        for key, val in [("by_setup", setup), ("by_conviction", conviction)]:
            if val not in insights["history"][today][key]:
                insights["history"][today][key][val] = {"trades": 0, "pnl": 0, "wins": 0}
            insights["history"][today][key][val]["trades"] += 1
            insights["history"][today][key][val]["pnl"] += t["pnl"]
            if t["pnl"] > 0:
                insights["history"][today][key][val]["wins"] += 1

    insight_lines = _generate_insight_text(insights)
    suggestions = generate_improvement_suggestions(insights)

    insights["latest_insight"] = insight_lines
    if suggestions:
        insights["latest_suggestions"] = suggestions
    insights["last_updated"] = today
    _save_insights(insights)

    if suggestions:
        return insight_lines + "\n\nSUGGESTIONS:\n" + "\n".join(suggestions)
    return insight_lines


def _generate_insight_text(insights: dict) -> str:
    history = insights.get("history", {})
    if len(history) < 2:
        return "Building data - need more sessions to generate insights."

    total_trades = sum(d["trades"] for d in history.values())
    total_pnl = sum(d["pnl"] for d in history.values())
    total_wins = sum(d["winners"] for d in history.values())
    wr = round(total_wins / total_trades * 100, 1) if total_trades > 0 else 0

    lines = ["SYSTEM INSIGHTS",
             "Overall: {} trades | Win rate: {}% | Total P&L: ${:.2f}".format(total_trades, wr, total_pnl)]

    setup_totals = {}
    for d in history.values():
        for setup, data in d.get("by_setup", {}).items():
            if setup not in setup_totals:
                setup_totals[setup] = {"trades": 0, "pnl": 0}
            setup_totals[setup]["trades"] += data["trades"]
            setup_totals[setup]["pnl"] += data["pnl"]

    if setup_totals:
        best = max(setup_totals, key=lambda s: setup_totals[s]["pnl"])
        lines.append("Best setup: {} (${:.2f} total)".format(best, setup_totals[best]["pnl"]))

    return "\n".join(lines)


def generate_improvement_suggestions(insights: dict) -> list:
    history = insights.get("history", {})
    if len(history) < 2:
        return []

    suggestions = []

    b_pnl = 0
    b_trades = 0
    for d in history.values():
        for conv, data in d.get("by_conviction", {}).items():
            if conv == "B":
                b_pnl += data["pnl"]
                b_trades += data["trades"]

    if b_trades >= 3 and b_pnl < 0:
        suggestions.append("B-grade setups losing ${:.2f} over {} trades. Consider A+ and A only.".format(abs(b_pnl), b_trades))

    total_trades = sum(d["trades"] for d in history.values())
    total_wins = sum(d["winners"] for d in history.values())
    if total_trades >= 5 and total_wins / total_trades < 0.45:
        suggestions.append("Win rate below 45%. Real-time news API (Benzinga) could improve catalyst quality.")

    recent_days = sorted(history.items())[-3:]
    if len(recent_days) == 3:
        if all(d["pnl"] > 0 for _, d in recent_days):
            suggestions.append("3 green days in a row - consider increasing account size.")
        elif all(d["pnl"] < 0 for _, d in recent_days):
            suggestions.append("3 red days in a row. Review watchlist quality and catalyst confirmation.")

    return suggestions


def _load_insights() -> dict:
    if os.path.exists(INSIGHTS_FILE):
        try:
            with open(INSIGHTS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"history": {}, "latest_insight": "", "last_updated": ""}


def _save_insights(insights: dict):
    with open(INSIGHTS_FILE, "w") as f:
        json.dump(insights, f, indent=2)

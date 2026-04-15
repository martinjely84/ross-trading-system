# ============================================================
# brain.py — Conversational AI + self-improvement engine
# ============================================================
import json
import os
import requests
from datetime import datetime, timedelta
import pytz

ET = pytz.timezone("America/New_York")
INSIGHTS_FILE = "insights.json"


# ── Conversational handler ─────────────────────────────────

def understand(text: str, session) -> str:
    """
    Understand plain English and return a response.
    Handles questions about performance, status, trades, etc.
    """
    t = text.lower().strip()

    # Greetings
    if any(w in t for w in ["hello", "hey", "hi", "morning", "good morning", "you ok", "how are you"]):
        now = datetime.now(ET)
        hour = now.hour
        greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
        armed = "Session is armed and ready." if session.armed else "Send /start 500 to arm the session."
        return f"{greeting} Martin. {armed}"

    # How are we doing / P&L questions
    if any(w in t for w in ["how are we", "how we doing", "how did we", "what did we make",
                              "profit", "p&l", "pnl", "make today", "lose today", "results"]):
        return _pnl_summary(session)

    # Account value
    if any(w in t for w in ["account", "balance", "how much", "total", "worth"]):
        return _account_summary(session)

    # Trades today
    if any(w in t for w in ["trades", "how many trades", "what trades", "trade today"]):
        return _trades_summary(session)

    # Watchlist
    if any(w in t for w in ["watchlist", "watching", "what stocks", "any stocks", "looking at"]):
        if session.watchlist:
            names = [f"{s['ticker']} ({s['conviction']}, +{s['gap_pct']}%)" for s in session.watchlist[:5]]
            return f"Watching {len(session.watchlist)} stocks today:\n" + "\n".join(names)
        return "No watchlist yet. Scan runs at 7am CT."

    # Open positions
    if any(w in t for w in ["position", "in anything", "holding", "open"]):
        pos = list(session.open_positions.keys())
        if pos:
            lines = []
            for ticker, p in session.open_positions.items():
                lines.append(f"{ticker}: {p['remaining_shares']} shares @ ${p['entry_price']:.2f}, stop ${p['current_stop']:.2f}")
            return "Open positions:\n" + "\n".join(lines)
        return "No open positions right now."

    # Best trade
    if any(w in t for w in ["best trade", "biggest winner", "best win"]):
        trades = session.trades_today
        if not trades:
            return "No trades today yet."
        best = max(trades, key=lambda t: t["pnl"])
        return f"Best trade today: {best['ticker']} +${best['pnl']:.2f} ({best['r_multiple']}R) via {best['exit_reason']}"

    # Worst trade
    if any(w in t for w in ["worst trade", "biggest loss", "worst loss"]):
        trades = session.trades_today
        if not trades:
            return "No trades today yet."
        worst = min(trades, key=lambda t: t["pnl"])
        return f"Worst trade: {worst['ticker']} ${worst['pnl']:.2f} ({worst['r_multiple']}R) — {worst['exit_reason']}"

    # Loss limit
    if any(w in t for w in ["limit", "how much left", "budget", "risk left"]):
        if not session.armed:
            return "Session not armed yet."
        return (f"Daily loss limit: ${session.daily_loss_limit:.2f}\n"
                f"Used: ${session.daily_loss_used:.2f}\n"
                f"Remaining: ${session.daily_loss_remaining():.2f}")

    # Suspended / status
    if any(w in t for w in ["suspended", "stopped", "running", "active", "status"]):
        if session.trading_suspended:
            return "⛔ Trading is currently suspended. Send /resume to re-enable."
        elif not session.armed:
            return "Session not armed. Send /start to arm."
        return "✅ System is active and scanning."

    # Insights / what's working
    if any(w in t for w in ["insight", "learning", "improving", "what's working", "pattern"]):
        return _get_insights()

    # Help
    if any(w in t for w in ["help", "what can you", "commands", "what do"]):
        return (
            "You can ask me anything naturally, like:\n"
            "• 'How are we doing today?'\n"
            "• 'What trades did we take?'\n"
            "• 'Any open positions?'\n"
            "• 'What's working this week?'\n\n"
            "Or use commands: /start, /status, /scan, /watchlist, /suspend, /resume, /report"
        )

    # Don't understand
    return None  # Fall through to command handler


def _pnl_summary(session) -> str:
    trades = session.trades_today
    if not trades:
        if session.armed:
            return f"No trades yet today. Loss limit remaining: ${session.daily_loss_remaining():.2f}"
        return "Session not started yet."
    total = sum(t["pnl"] for t in trades)
    winners = [t for t in trades if t["pnl"] > 0]
    losers = [t for t in trades if t["pnl"] <= 0]
    direction = "up" if total >= 0 else "down"
    return (
        f"We're {direction} ${abs(total):.2f} today.\n"
        f"{len(winners)} winners, {len(losers)} losers out of {len(trades)} trades.\n"
        f"Daily limit used: ${session.daily_loss_used:.2f} / ${session.daily_loss_limit:.2f}"
    )


def _account_summary(session) -> str:
    if not session.armed:
        return "Session not armed yet. Send /start to begin."
    trades = session.trades_today
    total_pnl = sum(t["pnl"] for t in trades) if trades else 0
    current = session.account_value + total_pnl
    change = "up" if total_pnl >= 0 else "down"
    return (
        f"Account: ${current:.2f} ({change} ${abs(total_pnl):.2f} today)\n"
        f"Started at: ${session.account_value:.2f}"
    )


def _trades_summary(session) -> str:
    trades = session.trades_today
    if not trades:
        return "No trades today yet."
    lines = [f"{len(trades)} trades today:"]
    for t in trades:
        pnl = f"+${t['pnl']:.2f}" if t["pnl"] >= 0 else f"-${abs(t['pnl']):.2f}"
        lines.append(f"• {t['ticker']} {pnl} ({t['r_multiple']}R) — {t['exit_reason']}")
    return "\n".join(lines)


# ── Self-improvement engine ────────────────────────────────

def analyze_and_improve(session):
    """
    After each session, analyze trades and update insights.
    Tunes conviction thresholds based on what's actually working.
    Returns insight summary string.
    """
    trades = session.trades_today
    if not trades:
        return

    insights = _load_insights()

    # Record today's trades
    today = datetime.now(ET).strftime("%Y-%m-%d")
    insights["history"][today] = {
        "trades": len(trades),
        "pnl": round(sum(t["pnl"] for t in trades), 2),
        "winners": len([t for t in trades if t["pnl"] > 0]),
        "by_setup": {},
        "by_catalyst": {},
        "by_float": {},
        "by_conviction": {},
    }

    for t in trades:
        setup = t.get("setup_type", "unknown")
        conviction = t.get("conviction", "unknown")

        # By setup type
        if setup not in insights["history"][today]["by_setup"]:
            insights["history"][today]["by_setup"][setup] = {"trades": 0, "pnl": 0, "wins": 0}
        insights["history"][today]["by_setup"][setup]["trades"] += 1
        insights["history"][today]["by_setup"][setup]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            insights["history"][today]["by_setup"][setup]["wins"] += 1

        # By conviction
        if conviction not in insights["history"][today]["by_conviction"]:
            insights["history"][today]["by_conviction"][conviction] = {"trades": 0, "pnl": 0, "wins": 0}
        insights["history"][today]["by_conviction"][conviction]["trades"] += 1
        insights["history"][today]["by_conviction"][conviction]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            insights["history"][today]["by_conviction"][conviction]["wins"] += 1

    # Build aggregate stats across all history
    all_trades = []
    for day_data in insights["history"].values():
        all_trades.append(day_data)

    # Generate insights text
    insight_lines = _generate_insight_text(insights)
    insights["latest_insight"] = insight_lines
    insights["last_updated"] = today

    # Generate improvement suggestions
    suggestions = generate_improvement_suggestions(insights)
    if suggestions:
        insights["latest_suggestions"] = suggestions

    _save_insights(insights)

    # Combine insights + suggestions
    if suggestions:
        full = insight_lines + "\n\n🔧 <b>IMPROVEMENT SUGGESTIONS</b>\n" + "\n".join(suggestions)
        return full
    return insight_lines


def generate_improvement_suggestions(insights: dict) -> list:
    """
    Analyze patterns and generate specific improvement suggestions.
    Only suggests things backed by actual data.
    """
    history = insights.get("history", {})
    if len(history) < 2:
        return []

    suggestions = []

    # Check if B setups are dragging performance
    b_pnl = 0
    b_trades = 0
    aplus_pnl = 0
    aplus_trades = 0
    for d in history.values():
        for conv, data in d.get("by_conviction", {}).items():
            if conv == "B":
                b_pnl += data["pnl"]
                b_trades += data["trades"]
            elif conv == "A+":
                aplus_pnl += data["pnl"]
                aplus_trades += data["trades"]

    if b_trades >= 3 and b_pnl < 0:
        suggestions.append(
            f"💡 B-grade setups losing ${abs(b_pnl):.2f} over {b_trades} trades. "
            f"Consider restricting to A+ and A only."
        )

    if aplus_trades >= 3 and b_trades >= 3:
        aplus_avg = aplus_pnl / aplus_trades
        b_avg = b_pnl / b_trades if b_trades > 0 else 0
        if aplus_avg > b_avg * 2:
            suggestions.append(
                f"💡 A+ averaging ${aplus_avg:.2f}/trade vs ${b_avg:.2f} for B-grade. "
                f"Strong case for A+ only after 10am."
            )

    # Win rate check
    total_trades = sum(d["trades"] for d in history.values())
    total_wins = sum(d["winners"] for d in history.values())
    if total_trades >= 5:
        win_rate = total_wins / total_trades
        if win_rate < 0.45:
            suggestions.append(
                "💡 Win rate below 45%. A real-time news API (Benzinga ~$99/mo) "
                "could improve catalyst quality and filter false gaps earlier."
            )

    # Setup type performance
    setup_totals = {}
    for d in history.values():
        for setup, data in d.get("by_setup", {}).items():
            if setup not in setup_totals:
                setup_totals[setup] = {"trades": 0, "pnl": 0}
            setup_totals[setup]["trades"] += data["trades"]
            setup_totals[setup]["pnl"] += data["pnl"]

    for setup, data in setup_totals.items():
        if data["trades"] >= 3 and data["pnl"] < -5:
            suggestions.append(
                f"💡 {setup} setup underwater (${data['pnl']:.2f} over {data['trades']} trades). "
                f"Consider reviewing entry criteria."
            )

    # Streak detection
    recent_days = sorted(history.items())[-3:]
    if len(recent_days) == 3:
        if all(d["pnl"] > 0 for _, d in recent_days):
            suggestions.append(
                "💡 3 green days in a row — system performing well. "
                "Consider increasing account size to capture more profit."
            )
        elif all(d["pnl"] < 0 for _, d in recent_days):
            suggestions.append(
                "⚠️ 3 red days in a row. Review watchlist quality "
                "and confirm catalysts before market open."
            )

    return suggestions


def _generate_insight_text(insights: dict) -> str:
    history = insights.get("history", {})
    if len(history) < 2:
        return "Building data — need more sessions to generate insights."

    lines = ["📊 SYSTEM INSIGHTS\n"]

    # Overall stats
    total_trades = sum(d["trades"] for d in history.values())
    total_pnl = sum(d["pnl"] for d in history.values())
    total_wins = sum(d["winners"] for d in history.values())
    wr = round(total_wins / total_trades * 100, 1) if total_trades > 0 else 0

    lines.append(f"Overall: {total_trades} trades | Win rate: {wr}% | Total P&L: ${total_pnl:.2f}")

    # Best setup
    setup_totals = {}
    for d in history.values():
        for setup, data in d.get("by_setup", {}).items():
            if setup not in setup_totals:
                setup_totals[setup] = {"trades": 0, "pnl": 0, "wins": 0}
            setup_totals[setup]["trades"] += data["trades"]
            setup_totals[setup]["pnl"] += data["pnl"]
            setup_totals[setup]["wins"] += data["wins"]

    if setup_totals:
        best_setup = max(setup_totals, key=lambda s: setup_totals[s]["pnl"])
        lines.append(f"Best setup: {best_setup} (${setup_totals[best_setup]['pnl']:.2f} total)")

    # Conviction performance
    conviction_totals = {}
    for d in history.values():
        for conv, data in d.get("by_conviction", {}).items():
            if conv not in conviction_totals:
                conviction_totals[conv] = {"trades": 0, "pnl": 0, "wins": 0}
            conviction_totals[conv]["trades"] += data["trades"]
            conviction_totals[conv]["pnl"] += data["pnl"]
            conviction_totals[conv]["wins"] += data["wins"]

    for conv, data in sorted(conviction_totals.items()):
        wr_c = round(data["wins"] / data["trades"] * 100, 1) if data["trades"] > 0 else 0
        lines.append(f"{conv} setups: {data['trades']} trades | WR {wr_c}% | ${data['pnl']:.2f}")

    # Trend (last 5 days)
    recent = sorted(history.items())[-5:]
    if len(recent) >= 3:
        recent_pnl = [d["pnl"] for _, d in recent]
        trend = "improving 📈" if recent_pnl[-1] > recent_pnl[0] else "declining 📉"
        lines.append(f"Recent trend: {trend}")

    return "\n".join(lines)


def _get_insights() -> str:
    insights = _load_insights()
    return insights.get("latest_insight", "No insights yet — need a few sessions of data.")


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

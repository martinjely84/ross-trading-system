# ============================================================
# brain.py -- Conversational AI + self-improvement engine
# ============================================================
import json
import os
from datetime import datetime, timedelta
import pytz
import anthropic
import credentials

ET = pytz.timezone("America/New_York")
INSIGHTS_FILE = "insights.json"


def understand(text: str, session) -> str:
    """Route all natural language through Claude."""
    try:
        client = anthropic.Anthropic(api_key=credentials.ANTHROPIC_KEY)
        now = datetime.now(ET)

        # Build session context
        trades = session.trades_today
        total_pnl = sum(t["pnl"] for t in trades) if trades else 0
        winners = len([t for t in trades if t["pnl"] > 0])
        positions = []
        for ticker, p in session.open_positions.items():
            positions.append(f"{ticker}: {p['remaining_shares']} shares @ ${p['entry_price']:.2f}, stop ${p['current_stop']:.2f}")
        watchlist = [f"{s['ticker']} ({s['conviction']}, +{s['gap_pct']}%)" for s in session.watchlist[:5]]

        context = f"""Current time: {now.strftime('%A %B %d, %I:%M %p')} CT
Session armed: {session.armed}
Account value: ${session.account_value:.2f}
Daily loss limit: ${session.daily_loss_limit:.2f}
Daily loss used: ${session.daily_loss_used:.2f}
Daily loss remaining: ${session.daily_loss_remaining():.2f}
Trading suspended: {session.trading_suspended}
Trades today: {len(trades)} ({winners} winners, P&L ${total_pnl:.2f})
Open positions: {', '.join(positions) if positions else 'None'}
Watchlist: {', '.join(watchlist) if watchlist else 'Empty'}
Market hours: 8:30am-3:00pm CT | Trading window: 8:30-10:00am CT"""

        system = """You are the trading assistant embedded in Martin Shearer's autonomous gap-and-go trading bot. You trade using the Ross Cameron momentum methodology on Alpaca paper trading with $500 paper account.

DAILY SCHEDULE (all times CT):
- 7:00am: Pre-market scan runs automatically via Finviz + yfinance
- 7:30am: Reminder sent to Martin to arm the session
- Martin sends /start 500 or /startauto to arm
- 8:30am-10:00am: Trading window — Gap and Go signals only (A+ and A conviction)
- 8:30am-9:30am: First Candle New High signals also valid
- 10:00am: A+ setups only reminder sent
- 10:45am: 15 minute cutoff warning sent
- 11:00am: Trading window closes automatically
- 2:30pm: Daily report + Claude AI trade review sent

TRADING RULES (Ross Cameron methodology):
- Only trade stocks with 10%+ pre-market gap
- Must have confirmed catalyst (earnings, FDA, contract, etc)
- Float under 100M shares (prefer under 10M)
- Relative volume 5x+ average
- Entry on breakout above pre-market high with 2x volume
- Stop at candle low or VWAP
- Target 1R and 2R
- Daily loss limit 2% of account ($10 on $500)
- Risk per trade 0.5% ($2.50 on $500)
- No trading after 10:00am CT unless A+ setup

SELF IMPROVEMENT:
You are constantly learning and improving. After every session you analyse what worked and what didn't. You track patterns across sessions — which setups are winning, which conviction grades are performing, whether stops are too tight or too loose, whether the trading window needs adjusting. You proactively flag when you notice a pattern. You suggest specific rule changes with data to back them up. You never suggest a change without a reason. When Martin asks how to improve, give him something concrete and actionable, not generic advice. Over time your goal is to make this system sharper, more selective, and more profitable.

You are embedded in Telegram. Keep responses concise and conversational. Plain text only — no markdown, no asterisks. Be direct and specific. Use the live session data provided to answer questions about positions, P&L, and watchlist accurately.

Martin is based in Dallas-Fort Worth TX, works at BAE Systems on the F-35 program, and is building this trading system alongside his day job."""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=system,
            messages=[
                {"role": "user", "content": f"Session context:\n{context}\n\nMartin says: {text}"}
            ]
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"[BRAIN] Claude error: {e}")
        return "Having trouble thinking right now — try again in a moment."


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

    # Claude AI review
    claude_review = _claude_trade_review(trades, insights)

    insights["latest_insight"] = insight_lines
    if suggestions:
        insights["latest_suggestions"] = suggestions
    if claude_review:
        insights["latest_claude_review"] = claude_review
    insights["last_updated"] = today
    _save_insights(insights)

    result = insight_lines
    if suggestions:
        result += "\n\nSUGGESTIONS:\n" + "\n".join(suggestions)
    if claude_review:
        result += "\n\nCLAUDE REVIEW:\n" + claude_review
    return result


def _claude_trade_review(trades, insights):
    try:
        client = anthropic.Anthropic(api_key=credentials.ANTHROPIC_KEY)

        history = insights.get("history", {})
        history_summary = ""
        if len(history) >= 2:
            recent = sorted(history.items())[-5:]
            for date, d in recent:
                history_summary += f"{date}: {d['trades']} trades, P&L ${d['pnl']:.2f}, {d['winners']} winners\n"

        trade_lines = ""
        for t in trades:
            trade_lines += (
                f"- {t['ticker']} [{t.get('conviction','?')}] {t.get('setup_type','?')}: "
                f"entry ${t['entry_price']:.2f} exit ${t['exit_price']:.2f} "
                f"P&L ${t['pnl']:.2f} ({t['r_multiple']}R) reason: {t['exit_reason']}\n"
            )

        prompt = f"""You are a trading coach reviewing a day of momentum trading using the Ross Cameron gap-and-go methodology.

Today's trades:
{trade_lines}

Recent session history:
{history_summary if history_summary else 'First session - no prior history.'}

In 3-5 bullet points, give specific, actionable feedback on:
- What worked and why
- What didn't work and why
- One concrete rule change to consider (be specific: e.g. "raise minimum gap threshold from 5% to 8%")
- Whether the trader should increase or decrease position size tomorrow

Be direct and specific. No padding. Reference the actual trades."""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"[CLAUDE REVIEW ERROR] {e}")
        return None


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

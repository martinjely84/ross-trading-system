# ============================================================
# opt_brain.py — Conversational AI + self-improvement for options bot
# ============================================================
import json
import os
from datetime import datetime
import pytz
import anthropic
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import credentials

ET = pytz.timezone("America/New_York")
INSIGHTS_FILE = "opt_insights.json"


def understand(text: str, session) -> str:
    """Route natural language through Claude with options context."""
    try:
        client = anthropic.Anthropic(api_key=credentials.ANTHROPIC_KEY)
        now = datetime.now(ET)

        trades = session.trades_today
        total_pnl = sum(t["pnl"] for t in trades) if trades else 0
        winners = len([t for t in trades if t["pnl"] > 0])

        positions = []
        for sym, p in session.open_positions.items():
            positions.append(
                f"{p['underlying']} {p['direction']} ${p['strike']} exp {p['expiry']} "
                f"x{p['contracts']} | paid ${p['premium_paid']:.2f}/share | "
                f"stop ${p['stop_premium']:.2f} | target ${p['target_premium']:.2f}"
            )

        watchlist = []
        for s in session.watchlist[:5]:
            c = s.get("contract", {})
            watchlist.append(
                f"{s['ticker']} ({s['conviction']}, +{s['gap_pct']}%) | "
                f"CALL ${c.get('strike','?')} exp {c.get('expiry','?')} ask ${c.get('ask','?')}"
            )

        context = f"""Time: {now.strftime('%A %B %d, %I:%M %p')} ET
Session armed: {session.armed}
Account: ${session.account_value:.2f}
Daily loss limit: ${session.daily_loss_limit:.2f}
Daily loss used: ${session.daily_loss_used:.2f}
Daily loss remaining: ${session.daily_loss_remaining():.2f}
Max premium per trade: ${session.max_premium:.2f}
Trades today: {len(trades)} ({winners} winners, P&L ${total_pnl:.2f})
Open positions: {', '.join(positions) if positions else 'None'}
Watchlist: {', '.join(watchlist) if watchlist else 'Empty'}"""

        system = """You are the trading assistant for Martin Shearer's options momentum bot. The system trades long calls on gap-up stocks using the Ross Cameron gap-and-go methodology adapted for options.

STRATEGY:
- Buy ATM or near-ATM calls on stocks gapping up 8%+ pre-market with catalyst
- Target 75% gain on premium, stop at 50% loss (defined risk)
- 1-7 DTE weekly options for gamma leverage on momentum
- Only trade 9:31am-11:00am ET window
- Max 3 trades per day, risk 1% of account per trade
- Stop trading if 2% daily loss limit hit

OPTIONS TERMINOLOGY you understand deeply:
- Premium: price paid for the option contract ($X per share × 100 shares/contract)
- DTE: days to expiration (1-7 for scalps, affects time decay)
- Delta: directional sensitivity (0.30-0.65 target for ATM-ish contracts)
- Theta: time decay — accelerates near expiration (why we exit quickly)
- IV (implied volatility): higher IV = more expensive premium
- ITM/OTM: in/out of the money relative to current stock price
- Contract symbol: OCC format e.g. AAPL250117C00150000 (ticker + date + C/P + strike)

SELF IMPROVEMENT:
Track which strikes, DTEs, and IV environments produce the best results. Flag if theta decay is killing positions, or if IV crush after catalyst news is a recurring problem. Suggest specific adjustments — e.g. "move to 14 DTE to reduce theta risk" or "focus on earnings day breakouts where IV hasn't peaked yet."

You are embedded in Telegram. Plain text only, no markdown/asterisks. Be concise and direct.
Martin is in Dallas-Fort Worth TX, works at BAE Systems on the F-35 program."""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=350,
            system=system,
            messages=[
                {"role": "user", "content": f"Session context:\n{context}\n\nMartin says: {text}"}
            ]
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"[OPT BRAIN] Claude error: {e}")
        return "Having trouble thinking right now — try again in a moment."


def analyze_and_improve(session):
    """End-of-day analysis and self-improvement for options."""
    trades = session.trades_today
    if not trades:
        return None

    insights = _load_insights()
    today = datetime.now(ET).strftime("%Y-%m-%d")

    day_data = {
        "trades":   len(trades),
        "pnl":      round(sum(t["pnl"] for t in trades), 2),
        "winners":  len([t for t in trades if t["pnl"] > 0]),
        "by_dte":   {},
        "by_conviction": {},
    }

    for t in trades:
        dte = str(t.get("dte", "unknown"))
        conv = t.get("conviction", "unknown")
        for key, val in [("by_dte", dte), ("by_conviction", conv)]:
            if val not in day_data[key]:
                day_data[key][val] = {"trades": 0, "pnl": 0, "wins": 0}
            day_data[key][val]["trades"] += 1
            day_data[key][val]["pnl"] += t["pnl"]
            if t["pnl"] > 0:
                day_data[key][val]["wins"] += 1

    insights["history"][today] = day_data

    insight_text = _generate_insight_text(insights)
    suggestions = _generate_suggestions(insights)
    claude_review = _claude_options_review(trades, insights)

    insights["latest_insight"] = insight_text
    if suggestions:
        insights["latest_suggestions"] = suggestions
    if claude_review:
        insights["latest_claude_review"] = claude_review
    insights["last_updated"] = today
    _save_insights(insights)

    result = insight_text
    if suggestions:
        result += "\n\nSUGGESTIONS:\n" + "\n".join(suggestions)
    if claude_review:
        result += "\n\nCLAUDE REVIEW:\n" + claude_review
    return result


def _claude_options_review(trades, insights):
    try:
        client = anthropic.Anthropic(api_key=credentials.ANTHROPIC_KEY)

        history = insights.get("history", {})
        history_summary = ""
        if len(history) >= 2:
            for d, data in sorted(history.items())[-5:]:
                history_summary += f"{d}: {data['trades']} trades P&L ${data['pnl']:.2f} ({data['winners']} wins)\n"

        trade_lines = ""
        for t in trades:
            pnl_sign = "+" if t["pnl"] >= 0 else ""
            trade_lines += (
                f"- {t['underlying']} {t['direction']} ${t['strike']} exp {t['expiry']} "
                f"({t.get('dte','?')} DTE) [{t['conviction']}] "
                f"entry ${t['premium_paid']:.2f} exit ${t['exit_premium']:.2f} "
                f"P&L {pnl_sign}${t['pnl']:.2f} ({t['pnl_pct']:+.1f}%) reason: {t['exit_reason']}\n"
            )

        prompt = f"""You are reviewing a day of options momentum trading (long calls on gap-up stocks).

Today's trades:
{trade_lines}
Recent history:
{history_summary if history_summary else 'First session.'}

In 3-4 bullet points give specific actionable feedback on:
- Did theta decay or IV crush hurt any positions? (if DTE was very short)
- Were the strikes appropriate (ITM vs ATM vs OTM)?
- Did stops trigger too early, or were they appropriate?
- One concrete adjustment to try tomorrow (e.g. "use 14 DTE instead of 2 DTE to reduce theta bleed")

Reference actual trades. No padding."""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"[OPT BRAIN] Review error: {e}")
        return None


def _generate_insight_text(insights: dict) -> str:
    history = insights.get("history", {})
    if len(history) < 2:
        return "Building data — need more sessions for insights."

    total_trades = sum(d["trades"] for d in history.values())
    total_pnl = sum(d["pnl"] for d in history.values())
    total_wins = sum(d["winners"] for d in history.values())
    wr = round(total_wins / total_trades * 100, 1) if total_trades > 0 else 0

    dte_totals = {}
    for d in history.values():
        for dte, data in d.get("by_dte", {}).items():
            if dte not in dte_totals:
                dte_totals[dte] = {"trades": 0, "pnl": 0}
            dte_totals[dte]["trades"] += data["trades"]
            dte_totals[dte]["pnl"] += data["pnl"]

    lines = [
        "OPTIONS SYSTEM INSIGHTS",
        f"Overall: {total_trades} trades | Win rate: {wr}% | Total P&L: ${total_pnl:.2f}",
    ]
    if dte_totals:
        best_dte = max(dte_totals, key=lambda d: dte_totals[d]["pnl"])
        lines.append(f"Best DTE: {best_dte} days (${dte_totals[best_dte]['pnl']:.2f} total)")

    return "\n".join(lines)


def _generate_suggestions(insights: dict) -> list:
    history = insights.get("history", {})
    if len(history) < 2:
        return []

    suggestions = []

    # If B-grade setups losing money
    b_pnl = 0
    b_trades = 0
    for d in history.values():
        for conv, data in d.get("by_conviction", {}).items():
            if conv == "B":
                b_pnl += data["pnl"]
                b_trades += data["trades"]
    if b_trades >= 3 and b_pnl < 0:
        suggestions.append(f"B-grade plays down ${abs(b_pnl):.2f} over {b_trades} trades. Consider A/A+ only.")

    # Win rate check
    total_t = sum(d["trades"] for d in history.values())
    total_w = sum(d["winners"] for d in history.values())
    if total_t >= 5 and total_w / total_t < 0.40:
        suggestions.append("Win rate below 40%. Consider moving to 7-14 DTE to reduce theta decay risk.")

    # Streak checks
    recent = sorted(history.items())[-3:]
    if len(recent) == 3:
        if all(d["pnl"] > 0 for _, d in recent):
            suggestions.append("3 green days — consider increasing premium budget slightly.")
        elif all(d["pnl"] < 0 for _, d in recent):
            suggestions.append("3 red days — review IV environment and catalyst quality.")

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

# Ross Cameron Momentum Day Trading System
### Powered by OpenClaw

---

## Setup (one time)

1. Double-click `install.bat` to install all dependencies
2. The Telegram bot token is already set in `config.py`
3. You need to **message your bot first** so it knows your chat ID:
   - Open Telegram, search for your bot by name
   - Send `/start 500` (or whatever your account value is)

---

## Daily Routine

1. **Double-click `run.bat`** — leave the window open all day
2. System starts scanning at **8:00am Eastern** automatically
3. Watchlist sent to Telegram by ~8:30am
4. When you're at your desk and ready: send `/start 500` to your Telegram bot
5. Signals fire automatically from 9:30am–11:00am
6. Daily report sent at 3:30pm
7. Close the window after market close

---

## Telegram Commands

| Command | What it does |
|---|---|
| `/start 500` | Arm the session with $500 account value |
| `/status` | Current P&L, positions, loss limit status |
| `/watchlist` | Today's pre-market watchlist |
| `/scan` | Run a manual scan right now |
| `/close AAPL` | Manually close a position |
| `/suspend` | Pause all signals (you take over) |
| `/resume` | Re-enable signals after suspend |
| `/report` | Generate today's report now |
| `/help` | All commands |

---

## Risk Rules (hardcoded, cannot be overridden)

- **Daily loss limit:** 2% of account ($10 on $500)
- **Per trade risk:** 0.5% of account ($2.50 on $500)
- **No trades after 11:00am Eastern**
- **No trades without confirmed catalyst**
- **No re-entry on stopped-out stocks without approval**
- **No averaging down**

---

## Files

| File | Purpose |
|---|---|
| `main.py` | Main loop — run this |
| `config.py` | Settings and thresholds |
| `scanner.py` | Pre-market gap scanner |
| `signals.py` | Entry signal logic |
| `monitor.py` | Exit and position monitoring |
| `reports.py` | Daily and weekly reports |
| `session.py` | Session state and trade tracking |
| `telegram_bot.py` | Telegram alerts |
| `trade_log.csv` | All trades (auto-created) |
| `session_state.json` | Today's session state (auto-created) |
| `weekly_log.json` | Weekly performance data (auto-created) |

---

## Important Notes

- The system uses **yfinance + Finviz** for free data — no API keys required
- Webull execution is not yet wired in — signals fire but you execute manually
- This is a **signal + alert system** first; auto-execution comes after you've validated the signals manually for a few weeks
- Start on **paper / very small size** until you trust the signals

---

## PDT Note

You're on a **cash account**. No PDT rule. Settlement takes 1-2 days so after a trade closes, those funds won't be available immediately. Keep this in mind — don't over-trade early in the week.

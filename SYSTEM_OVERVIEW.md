# Momentum Trading Bot — End-to-End System Overview

A plain-language description of what I've built, written to share and compare against another build. Account numbers and API keys are deliberately left out. I've tried to be as honest as his doc was — including where mine was hardened reactively rather than designed clean.

---

## What it is, in one paragraph

An automated paper-trading day-trading system running Ross Cameron's (Warrior Trading) gap-and-go momentum strategy on low-float small-cap stocks. It runs three strategies off one shared live watchlist — long gap-and-go (live and trading), short VWAP-fade (live but not yet triggered in production), and an options-calls module (scaffolded, currently disabled). It's deterministic and rule-based in the order path, with an AI layer (Claude) used only for catalyst quality scoring and post-session analysis — never for placing orders. It runs always-on on a DigitalOcean VPS under PM2, controlled and monitored through a Telegram bot and a web dashboard. It is paper-only. The current goal is to find out whether the encoded rules have a real edge on live market data — after a hard few weeks of fixing the plumbing that was hiding the answer.

---

## Strategy basis: Ross Cameron / Warrior Trading

The system encodes a published strategy. Core gap-and-go criteria currently enforced:

- Small-cap, low float, price roughly $0.50–$200 (band is wider than ideal — tightening planned)
- **Gap ≥ 8%** (recently raised from a loose 2% "training" setting that was letting in non-gaps)
- A **news catalyst, scored 1–10 by Claude Opus** — anything scoring ≤2 (no real news, or *danger* news like dilutive offerings / reverse splits) is hard-excluded
- Entry gates, all required: price **above VWAP**, last 1-min candle **green**, **volume surge** (last bar > 1.5× prior 5-bar average), **≥2M cumulative session volume**, and **not over-extended** (<5% above VWAP, i.e. don't chase)
- Stop at 2% below entry; **Target 1 at +1.5R (sell 50%, stop to breakeven), Target 2 at +3R (sell 25%), runner held**
- Morning session only — **hard flat at 11:00 AM ET**
- A daily max-loss limit (2%) that suspends new entries once hit

Short side (VWAP fade): only fades stocks that ran ≥4% above VWAP then broke down on a red candle with a lower high, 9:45–10:30 AM window, half-size, shortability pre-checked, T1 covers 65%.

---

## Architecture, end to end

1. **Scanner (built).** At 8:00 AM ET, pulls gappers from Finviz, with an **Alpaca market-movers API fallback** if the Finviz scrape returns empty (this fallback exists because the scrape silently failed on several mornings). Each candidate is enriched with volume/float data and a Claude-Opus catalyst score, then ranked A+/A/B and written to the watchlist.

2. **Watchlist (built).** Stored in the session state; re-scored and filtered. A separate gap-DOWN scan feeds short candidates.

3. **Strategy logic (built).** Long, short, and options signal evaluators. Honest note: these are **not** fully separated from the execution layer the way a clean engine/wrapper split would be — strategy and execution share modules. This bit me once when an external refactor silently deleted the short strategy and dashboard; both had to be restored. Separating pure engines from execution is on the roadmap.

4. **Live data feed (built, recently fixed).** Real-time prices and 1-min bars from **Alpaca's SIP feed** (full consolidated tape), with yfinance as a fallback. This was the single biggest fix of the project: it originally ran on Alpaca's **free IEX feed**, which carries ~2–3% of volume and returned 0–7 one-minute bars for small-caps — so the entry criteria couldn't even evaluate and the bot was effectively blind to its own universe. Switching to SIP made it able to see the stocks it trades.

5. **Execution layer (built, live).** Market orders on Alpaca. **Broker is the source of truth** — the bot waits for the actual fill, records the **real filled quantity** (not the intended size, so partial fills can't cause oversell), and recalculates stops/targets from the real fill price. Monitor-only stops: a 5-second loop watches price and places exits; there are **no broker-held bracket orders** (an earlier dual broker+monitor design caused double-fires and was removed).

6. **Duplicate / restart safety (built, the hard-won part).** Singleton enforcement kills any rogue second process on startup (a leftover manual process once ran for a week placing duplicate orders). Per-ticker file locks, an in-flight tracker, and a direct Alpaca position/order check guard every entry. On startup the bot **reconciles against the broker** — force-closing orphaned positions (on Alpaca, not in session) and removing phantoms (in session, not on Alpaca).

7. **Accounts & sizing.** Currently **one shared paper account** for all strategies, sized at 0.5% risk per trade, max 3 concurrent positions and 5 distinct tickers/day. Separating each strategy onto its own account (for clean isolation and apples-to-apples % comparison) is planned but not yet done.

8. **Storage.** Session state in JSON, trade log in CSV. Honest note: relative paths once caused a split-brain where state was written to two directories — fixed with absolute paths, but a proper database (SQLite) is the right answer and is on the roadmap.

9. **Control & observability.** A Telegram bot (auto-arms each morning, sends fills/exits/reports, accepts commands, authorized to one chat ID) and a Flask web dashboard (P&L, equity curve, positions, watchlist, trade history, per-conviction stats, strategy view).

---

## Risk controls

- **Per-trade:** 2% stop, 0.5% account risk sizing, position-size cap (5% of account)
- **Per-day:** 2% daily max-loss breaker that suspends new entries; daily ticker cap (5) and concurrent cap (3)
- **No-revenge rule:** a stock that stopped out or lost can't be re-entered the same day; re-entry only after a profitable exit + 20-minute cooldown
- **Hard time stop:** flat at 11:00 AM ET, force-closed against the broker (catches orphans too)
- **Manual control:** Telegram/dashboard Arm, Disarm, Suspend, Resume, Close-All
- **Buying-power pre-check** before every order (shared wallet can't over-deploy)

---

## AI usage

- **Claude Opus 4.8** scores every catalyst (gates which stocks make the watchlist) and writes the daily review, analysis, and config suggestions.
- **Claude Haiku 4.5** handles conversational Telegram replies (speed).
- **No AI in the order path** — entries, exits, sizing, and stops are deterministic code.

---

## Engineering principles (and an honest caveat)

- **Broker as source of truth** — reconcile against real fills/positions, never assume the intended fill happened. (Learned the hard way: an early version logged trades that never actually placed on Alpaca.)
- **Deterministic order path** — no AI/LLM near fills.
- **Regression tests from real failures** — every production bug became a permanent test case; 16 tests today, run before each session. (Honest: this is far fewer than a clean-room build would have, and the suite grew reactively after incidents.)
- **Singleton + reconcile by construction** — only one bot can run; state and broker are re-synced on every restart.
- **Paper-only today** — a `go_live` switch exists but is deliberately not flipped.

The honest framing: this system was built fast and **hardened reactively through live paper bugs** rather than designed clean up front. It works and is now stable, but a from-scratch rebuild would separate strategy engines from execution, use a database, use idempotency keys for orders, and isolate accounts per strategy. Those are known improvements, not surprises.

---

## Tech stack

Python; Alpaca (paper brokerage + execution + SIP market data); Finviz + Alpaca movers (scanning); yfinance (fallback data); Claude Opus/Haiku (catalyst scoring + analysis); JSON/CSV storage (SQLite planned); DigitalOcean VPS; PM2 process manager; Telegram + Flask dashboard. Built with Claude Code.

---

## Build status

| Component | Status |
|---|---|
| Scanner (Finviz + Alpaca fallback + Opus catalyst scoring) | ✅ Built |
| Real-time data feed (Alpaca SIP) | ✅ Built (just migrated off IEX) |
| Long gap-and-go execution | ✅ Live, placing paper trades |
| Short VWAP-fade | ✅ Live, not yet triggered in production |
| Monitor / exits / reconciliation / singleton | ✅ Built |
| Dashboard + Telegram control | ✅ Built |
| Options calls module | 🔨 Scaffolded, disabled (needs rework) |
| Paper validation (multi-week, clean data) | ⏳ Just started — first sessions on correct sizing + real data |
| Roadmap: SQLite, separate accounts per strategy, pure-engine refactor, idempotency keys | ⏳ Deferred |

---

## How I'll know if it works — and what it won't prove

**What the paper run proves:** whether these rules, at correct position size and on real-time data, produce positive expectancy — now that the bugs that were corrupting the result are fixed.

**The honest part:** edge is **unproven**. Stripped of a position-sizing bug that had been silently multiplying trade sizes 2–6× for weeks, the strategy's historical record nets to roughly **breakeven** — neither a disaster nor an edge. And paper P&L is a *ceiling*: it doesn't model slippage, latency, or partial fills, and low-float small-caps are the worst case — the clean breakout fill you get on paper often won't exist live because a real order moves a thin book. A sound architecture doesn't create an edge; it just lets you measure honestly. The real test, before any real money, is whether expectancy survives a realistic slippage haircut and a tiny live trial with the daily-loss breaker and kill switch active.

**Fair way to compare the two builds:** not architecture — **paper P&L over the same 30 days, same strategy, same window.** That's the only thing that answers whether the rules have an edge.

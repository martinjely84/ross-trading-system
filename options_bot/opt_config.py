# ============================================================
# opt_config.py — Options Trading Bot Configuration
# ============================================================
import os

# --- Telegram (create a new bot via BotFather for options) ---
# Set OPTIONS_TELEGRAM_TOKEN/OPTIONS_TELEGRAM_CHAT_ID, or fall back to
# TELEGRAM_TOKEN/TELEGRAM_CHAT_ID if reusing the stock bot.
TOKEN = os.getenv("OPTIONS_TELEGRAM_TOKEN") or os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("OPTIONS_TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")
CHAT_ID = int(CHAT_ID) if CHAT_ID else None

# --- Risk Management ---
DAILY_LOSS_PCT     = 0.02    # 2% of account daily loss limit
PER_TRADE_RISK_PCT = 0.01    # 1% per trade (options = defined risk, can go to zero)
MAX_PREMIUM_HARD   = 200     # hard cap: never spend more than $200 premium per position

# --- Options-Specific Filters ---
MIN_UNDERLYING_PRICE  = 5.0    # underlying stock must be $5+ to have liquid options
MIN_OPTION_VOLUME     = 10     # min daily volume on the contract (loose for paper trading)
MIN_OPEN_INTEREST     = 50     # min open interest on the contract
MAX_SPREAD_PCT        = 0.35   # max bid-ask spread as % of ask (35% max)

# --- DTE (Days to Expiration) ---
# Momentum scalps: 1-7 DTE (high gamma, fast moves)
# Swing plays: 7-21 DTE (more time cushion)
SCALP_MIN_DTE = 1
SCALP_MAX_DTE = 7
SWING_MAX_DTE = 21
DEFAULT_MAX_DTE = 7      # default to weekly scalps

# --- Delta Targets (ATM to slightly OTM) ---
TARGET_DELTA_MIN = 0.30   # minimum delta (too far OTM = lottery ticket)
TARGET_DELTA_MAX = 0.65   # maximum delta (too deep ITM = expensive, low leverage)

# --- Exit Rules ---
PROFIT_TARGET_PCT = 0.75  # exit at 75% gain on premium paid (e.g. paid $1.00, sell at $1.75)
STOP_LOSS_PCT     = 0.50  # exit at 50% loss on premium paid (e.g. paid $1.00, exit at $0.50)

# --- Gap Scanner Filters (stricter than stock bot — options need bigger moves) ---
MIN_GAP_PCT        = 8.0         # need at least 8% gap for options momentum
MIN_PREMARKET_VOL  = 50_000      # 50k shares pre-market
MIN_RELATIVE_VOL   = 2.0         # 2x average volume minimum
MAX_FLOAT_HARD     = 1_000_000_000   # 1B shares max float
MAX_FLOAT_PREFERRED = 50_000_000    # 50M preferred
MIN_PRICE          = 5.0          # options need underlying $5+
MAX_PRICE          = 500.0

# --- Session ---
SESSION_FILE   = "opt_session_state.json"
TRADE_LOG_FILE = "opt_trade_log.csv"

# --- Conviction Windows ---
# 9:31-10:00 ET: A+ and A setups
# 10:00-10:30 ET: A+ only
# 10:30-11:00 ET: A+ only, size warning
TRADING_START_HOUR   = 9
TRADING_START_MINUTE = 31
TRADING_END_HOUR     = 11
TRADING_END_MINUTE   = 0
MAX_TRADES_PER_DAY   = 3

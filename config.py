# ============================================================
# config.py — Ross Cameron Momentum System
# Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in the environment before running.
# ============================================================

# --- Telegram ---
import os

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
_chat_id_raw = os.getenv("TELEGRAM_CHAT_ID", "").strip()
try:
    TELEGRAM_CHAT_ID = int(_chat_id_raw) if _chat_id_raw else None
except ValueError:
    print(f"[CONFIG] TELEGRAM_CHAT_ID={_chat_id_raw!r} is not numeric; ignoring")
    TELEGRAM_CHAT_ID = None

# --- Trading Hours (Eastern) ---
PREMARKET_START = "08:00"
PREMARKET_END   = "09:29"
MARKET_OPEN     = "09:30"
TRADING_CUTOFF  = "11:00"
EOD_CLOSE       = "15:55"
WEEKLY_REPORT   = "16:00"  # Friday only

# --- Scanner Filters ---
# TRAINING MODE — ultra-loose filters so scanner always finds stocks
MIN_PRICE = 0.50             # very low floor
MAX_PRICE = 500.00           # effectively unlimited
MIN_GAP_PCT = 2.0            # catch even small gappers
MIN_PREMARKET_VOL = 10_000   # very low volume threshold
MIN_RELATIVE_VOL = 1.0       # any relative volume passes
MAX_FLOAT_HARD = 2_000_000_000  # 2B — only excludes mega-caps
MAX_FLOAT_PREFERRED = 10_000_000
MAX_FLOAT_ACCEPTABLE = 500_000_000
MIN_SQUEEZE_SHORT_INT = 5.0
HIGH_SQUEEZE_SHORT_INT = 20.0

# --- Risk Management ---
DAILY_LOSS_PCT = 0.02        # 2% of account
PER_TRADE_RISK_PCT = 0.005   # 0.5% of account
HALT_RESUME_RISK_PCT = 0.0025 # 0.25% for halt plays

# --- Entry Rules ---
BREAKOUT_VOL_MULTIPLIER = 2.0   # candle vol must be 2x avg of prior 5
VWAP_STOP_THRESHOLD_PCT = 0.03  # use VWAP as stop if within 3% below entry

# --- Conviction Windows ---
# 9:30-10:00 → A+ and A
# 10:00-10:30 → A+ only
# 10:30-11:00 → A+ only, reduced size warning

# --- Data ---
SCANNER_REFRESH_PREMARKET = 60   # seconds
SCANNER_REFRESH_MARKET    = 30   # seconds

# --- Session State File ---
SESSION_FILE = "session_state.json"
TRADE_LOG_FILE = "trade_log.csv"
WEEKLY_LOG_FILE = "weekly_log.json"

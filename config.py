# ============================================================
# config.py — Ross Cameron Momentum System
# Edit TELEGRAM_TOKEN and TELEGRAM_CHAT_ID before running
# ============================================================

# --- Telegram ---
TELEGRAM_TOKEN = "8370287942:AAGKQPIbybD3WByLiF29aqg9NxnWXLWrH-Q"
TELEGRAM_CHAT_ID = None  # Set automatically on first /start command

# --- Trading Hours (Eastern) ---
PREMARKET_START = "08:00"
PREMARKET_END   = "09:29"
MARKET_OPEN     = "09:30"
TRADING_CUTOFF  = "11:00"
EOD_CLOSE       = "15:55"
WEEKLY_REPORT   = "16:00"  # Friday only

# --- Scanner Filters ---
MIN_PRICE = 1.00
MAX_PRICE = 20.00
MIN_GAP_PCT = 10.0          # percent
MIN_PREMARKET_VOL = 100_000
MIN_RELATIVE_VOL = 5.0      # x average
MAX_FLOAT_HARD = 100_000_000
MAX_FLOAT_PREFERRED = 10_000_000
MAX_FLOAT_ACCEPTABLE = 20_000_000
MIN_SQUEEZE_SHORT_INT = 20.0   # percent
HIGH_SQUEEZE_SHORT_INT = 40.0  # percent

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

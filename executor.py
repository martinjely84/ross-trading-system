# ============================================================
# executor.py — Webull order execution
# ============================================================
from webull import webull
import credentials

_wb = None

def get_wb():
    global _wb
    if _wb is None:
        _wb = webull()
        _wb.login(credentials.WEBULL_EMAIL, credentials.WEBULL_PASSWORD, device_name="TradingBot")
        _wb.get_trade_token(credentials.WEBULL_TRADING_PIN)
        print("[WEBULL] Logged in.")
    return _wb


def buy_market(ticker: str, shares: int):
    try:
        wb = get_wb()
        order = wb.place_order(
            stock=ticker,
            action="BUY",
            orderType="MKT",
            enforce="DAY",
            quant=shares
        )
        print(f"[EXECUTOR] BUY {shares} {ticker}: {order}")
        return order
    except Exception as e:
        print(f"[EXECUTOR] BUY ERROR: {e}")
        return None


def sell_market(ticker: str, shares: int):
    try:
        wb = get_wb()
        order = wb.place_order(
            stock=ticker,
            action="SELL",
            orderType="MKT",
            enforce="DAY",
            quant=shares
        )
        print(f"[EXECUTOR] SELL {shares} {ticker}: {order}")
        return order
    except Exception as e:
        print(f"[EXECUTOR] SELL ERROR: {e}")
        return None


def get_positions():
    try:
        wb = get_wb()
        return wb.get_positions()
    except Exception as e:
        print(f"[EXECUTOR] POSITIONS ERROR: {e}")
        return []


def get_account():
    try:
        wb = get_wb()
        return wb.get_account()
    except Exception as e:
        print(f"[EXECUTOR] ACCOUNT ERROR: {e}")
        return {}

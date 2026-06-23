# ============================================================
# opt_executor.py — Alpaca paper trading for options
# Uses the same Alpaca account as the stock bot
# Options orders use OCC contract symbols (e.g. AAPL250117C00150000)
# ============================================================
import requests
import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import credentials

ALPACA_KEY    = credentials.ALPACA_KEY
ALPACA_SECRET = credentials.ALPACA_SECRET
BASE_URL      = "https://paper-api.alpaca.markets"

HEADERS = {
    "APCA-API-KEY-ID":     ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
    "Content-Type":        "application/json",
}


def buy_option(contract_symbol: str, qty: int):
    """
    Buy to open an options contract.
    contract_symbol: OCC format, e.g. "AAPL250117C00150000"
    qty: number of contracts (each = 100 shares)
    """
    try:
        payload = {
            "symbol":        contract_symbol,
            "qty":           str(qty),
            "side":          "buy",
            "type":          "market",
            "time_in_force": "day",
        }
        r = requests.post(f"{BASE_URL}/v2/orders", headers=HEADERS, json=payload, timeout=15)
        data = r.json()
        status = data.get("status", "unknown")
        print(f"[OPT EXEC] BUY {qty}x {contract_symbol}: {status}")
        if r.status_code not in (200, 201):
            print(f"[OPT EXEC] Error body: {data}")
            return None
        return data
    except Exception as e:
        print(f"[OPT EXEC] buy_option error: {e}")
        return None


def sell_option(contract_symbol: str, qty: int):
    """
    Sell to close an options contract.
    """
    try:
        payload = {
            "symbol":        contract_symbol,
            "qty":           str(qty),
            "side":          "sell",
            "type":          "market",
            "time_in_force": "day",
        }
        r = requests.post(f"{BASE_URL}/v2/orders", headers=HEADERS, json=payload, timeout=15)
        data = r.json()
        status = data.get("status", "unknown")
        print(f"[OPT EXEC] SELL {qty}x {contract_symbol}: {status}")
        if r.status_code not in (200, 201):
            print(f"[OPT EXEC] Error body: {data}")
            return None
        return data
    except Exception as e:
        print(f"[OPT EXEC] sell_option error: {e}")
        return None


def get_order(order_id: str):
    try:
        r = requests.get(f"{BASE_URL}/v2/orders/{order_id}", headers=HEADERS, timeout=10)
        return r.json()
    except Exception as e:
        print(f"[OPT EXEC] get_order error: {e}")
        return {}


def get_fill_price(order_id: str, max_wait_secs: int = 15):
    if not order_id:
        return None
    for _ in range(max_wait_secs * 2):
        order = get_order(order_id)
        status = order.get("status")
        if status == "filled":
            price = order.get("filled_avg_price")
            return round(float(price), 4) if price else None
        if status in ("canceled", "cancelled", "expired", "rejected"):
            print(f"[OPT EXEC] Order {order_id[:8]} ended with status={status}")
            return None
        time.sleep(0.5)
    print(f"[OPT EXEC] Fill timeout for order {order_id[:8]}")
    return None


def get_option_positions():
    """Get all open option positions from Alpaca."""
    try:
        r = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS, timeout=15)
        positions = r.json()
        if not isinstance(positions, list):
            return []
        # Filter to options only (contract symbols are longer than stock tickers)
        return [p for p in positions if len(p.get("symbol", "")) > 5]
    except Exception as e:
        print(f"[OPT EXEC] get_option_positions error: {e}")
        return []


def get_account():
    """Get account info from Alpaca."""
    try:
        r = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS, timeout=15)
        return r.json()
    except Exception as e:
        print(f"[OPT EXEC] get_account error: {e}")
        return {}


def get_options_chain(underlying: str, option_type: str = "call",
                      dte_min: int = 1, dte_max: int = 7):
    """
    Fetch options chain from Alpaca's options endpoint.
    This supplements yfinance data with Alpaca's own chain data.
    """
    from datetime import date, timedelta
    exp_gte = (date.today() + timedelta(days=dte_min)).isoformat()
    exp_lte = (date.today() + timedelta(days=dte_max)).isoformat()

    try:
        params = {
            "underlying_symbols":  underlying,
            "type":                option_type,
            "expiration_date_gte": exp_gte,
            "expiration_date_lte": exp_lte,
            "limit":               50,
        }
        r = requests.get(f"{BASE_URL}/v2/options/contracts",
                         headers=HEADERS, params=params, timeout=15)
        data = r.json()
        return data.get("option_contracts", [])
    except Exception as e:
        print(f"[OPT EXEC] get_options_chain error: {e}")
        return []

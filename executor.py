import time
import requests
import credentials

ALPACA_API_KEY = credentials.ALPACA_KEY
ALPACA_SECRET_KEY = credentials.ALPACA_SECRET
BASE_URL = "https://paper-api.alpaca.markets"

HEADERS = {
    "APCA-API-KEY-ID": ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
}


def _validate_order(data):
    if not isinstance(data, dict) or not data.get("id"):
        msg = data.get("message") if isinstance(data, dict) else data
        print(f"[ALPACA] Order rejected: {msg}")
        return False
    return True


def _place_order(ticker, qty, side, order_type="market", limit_price=None, stop_price=None):
    try:
        payload = {
            "symbol": ticker,
            "qty": str(int(qty)),
            "side": side,
            "type": order_type,
            "time_in_force": "day",
        }
        if limit_price is not None:
            payload["limit_price"] = str(round(float(limit_price), 4))
        if stop_price is not None:
            payload["stop_price"] = str(round(float(stop_price), 4))

        r = requests.post(f"{BASE_URL}/v2/orders", headers=HEADERS, json=payload, timeout=10)
        data = r.json()
        if _validate_order(data):
            print(f"[ALPACA] {side.upper()} {ticker} x{qty}: {data.get('status', data)}")
            return data
    except Exception as e:
        print(f"[ALPACA ERROR] {side} {ticker}: {e}")
    return None


def buy_market(ticker, qty):
    return _place_order(ticker, qty, "buy")


def sell_market(ticker, qty):
    return _place_order(ticker, qty, "sell")


def place_limit_order(ticker, qty, side, limit_price):
    return _place_order(ticker, qty, side, order_type="limit", limit_price=limit_price)


def place_stop_order(ticker, qty, side, stop_price):
    return _place_order(ticker, qty, side, order_type="stop", stop_price=stop_price)


def cancel_order(order_id):
    if not order_id:
        return False
    try:
        r = requests.delete(f"{BASE_URL}/v2/orders/{order_id}", headers=HEADERS, timeout=10)
        ok = r.status_code in (200, 204)
        print(f"[ALPACA] Cancel order {order_id[:8]}: HTTP {r.status_code}")
        return ok
    except Exception as e:
        print(f"[ALPACA ERROR] cancel_order: {e}")
        return False


def get_order(order_id):
    try:
        r = requests.get(f"{BASE_URL}/v2/orders/{order_id}", headers=HEADERS, timeout=5)
        return r.json()
    except Exception as e:
        print(f"[ALPACA ERROR] get_order: {e}")
        return {}


def get_fill_price(order_id, max_wait_secs=15):
    """Resolve an order to a terminal outcome.

    Returns a (status, fill_price) tuple where status is one of:
      "filled"  — order filled; fill_price is the avg fill (float)
      "failed"  — order reached a terminal non-fill state (canceled/rejected/
                  expired) and did NOT change the position; safe to retry
      "timeout" — outcome unknown; the order MAY have filled at the broker.
                  Callers must reconcile against the broker and must NOT blindly
                  resubmit, or they risk duplicate fills.
    """
    if not order_id:
        return "failed", None
    for _ in range(max_wait_secs * 2):
        order = get_order(order_id)
        status = order.get("status")
        if status == "filled":
            price = order.get("filled_avg_price")
            if price:
                return "filled", round(float(price), 4)
            # Filled but avg price not yet populated — keep polling briefly.
        elif status in ("canceled", "cancelled", "expired", "rejected"):
            print(f"[ALPACA] Order {order_id[:8]} ended with status={status}")
            return "failed", None
        time.sleep(0.5)
    print(f"[ALPACA] Fill timeout for order {order_id[:8]}")
    return "timeout", None


def get_positions():
    try:
        r = requests.get(f"{BASE_URL}/v2/positions", headers=HEADERS, timeout=10)
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[ALPACA ERROR] get_positions: {e}")
        return []


def get_position(ticker):
    """Return the broker's current position for a ticker, or None if flat.

    Used to reconcile session state after an unconfirmed fill.
    """
    try:
        r = requests.get(f"{BASE_URL}/v2/positions/{ticker}", headers=HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        print(f"[ALPACA ERROR] get_position: {e}")
        return None


def get_account():
    try:
        r = requests.get(f"{BASE_URL}/v2/account", headers=HEADERS, timeout=10)
        return r.json()
    except Exception as e:
        print(f"[ALPACA ERROR] get_account: {e}")
        return {}


def force_close_position(ticker):
    try:
        r = requests.delete(
            f"{BASE_URL}/v2/positions/{ticker}",
            headers=HEADERS,
            params={"cancel_orders": "true"},
            timeout=10,
        )
        ok = r.status_code in (200, 204)
        print(f"[ALPACA] Force-close {ticker}: HTTP {r.status_code}")
        return ok
    except Exception as e:
        print(f"[ALPACA ERROR] force_close_position: {e}")
        return False

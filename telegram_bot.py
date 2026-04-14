# ============================================================
# telegram_bot.py — Telegram alert + command handler
# ============================================================
import requests
import json
import os
import config

_chat_id_cache = None


def _get_chat_id():
    global _chat_id_cache
    if _chat_id_cache:
        return _chat_id_cache
    if config.TELEGRAM_CHAT_ID:
        _chat_id_cache = config.TELEGRAM_CHAT_ID
        return _chat_id_cache
    # Auto-detect from first message in getUpdates
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/getUpdates",
            timeout=10
        )
        data = resp.json()
        if data.get("result"):
            _chat_id_cache = data["result"][-1]["message"]["chat"]["id"]
            return _chat_id_cache
    except Exception:
        pass
    return None


def send(message: str, parse_mode="HTML"):
    chat_id = _get_chat_id()
    if not chat_id:
        print(f"[TELEGRAM] No chat_id — message dropped: {message[:80]}")
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": parse_mode
            },
            timeout=10
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")
        return False


def send_signal(signal_dict: dict):
    """Format and send a full trade signal block."""
    msg = (
        f"🚨 <b>SIGNAL: {signal_dict['signal_type']}</b>\n"
        f"<b>{signal_dict['ticker']}</b> | Conviction: {signal_dict['conviction']}\n"
        f"⏰ {signal_dict['time']} ET\n"
        f"📰 {signal_dict['catalyst']}\n"
        f"Float: {signal_dict['float']:,.0f} | SI: {signal_dict['short_interest']}%\n"
        f"\n"
        f"▶️ ENTRY: <b>${signal_dict['entry_price']:.2f}</b>\n"
        f"🛑 STOP: ${signal_dict['stop_loss']:.2f} ({signal_dict['stop_type']})\n"
        f"Risk/share: ${signal_dict['risk_per_share']:.2f}\n"
        f"\n"
        f"📦 SIZE: <b>{signal_dict['share_size']} shares</b>\n"
        f"💰 Total risk: ${signal_dict['total_risk']:.2f}\n"
        f"\n"
        f"🎯 T1 (sell 50%): ${signal_dict['target1']:.2f}\n"
        f"🎯 T2 (sell 25%): ${signal_dict['target2']:.2f}\n"
        f"🏃 Runner stop (BE): ${signal_dict['entry_price']:.2f}\n"
        f"\n"
        f"📊 Daily loss used: ${signal_dict['daily_loss_used']:.2f} / ${signal_dict['daily_loss_limit']:.2f}\n"
        f"📊 Remaining: ${signal_dict['daily_loss_remaining']:.2f}\n"
        f"\n"
        f"📝 {signal_dict.get('notes', '')}"
    )
    return send(msg)


def send_exit(exit_dict: dict):
    emoji = "✅" if exit_dict.get("pnl", 0) >= 0 else "❌"
    msg = (
        f"{emoji} <b>{exit_dict['trigger']}</b>\n"
        f"<b>{exit_dict['ticker']}</b> — {exit_dict['action']}\n"
        f"Exit price: ${exit_dict['exit_price']:.2f}\n"
        f"P&L: ${exit_dict['pnl']:.2f} ({exit_dict.get('r_multiple', '?')}R)\n"
        f"Daily loss used: ${exit_dict['daily_loss_used']:.2f} / ${exit_dict['daily_loss_limit']:.2f}"
    )
    return send(msg)


def get_updates(offset=None):
    params = {"timeout": 5}
    if offset:
        params["offset"] = offset
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/getUpdates",
            params=params,
            timeout=10
        )
        return resp.json().get("result", [])
    except Exception:
        return []

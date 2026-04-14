# ============================================================
# session.py — Session state management
# Tracks account value, daily loss, open positions, trades
# ============================================================
import json
import os
import csv
from datetime import datetime, date
import pytz
import config

ET = pytz.timezone("America/New_York")


def et_now():
    return datetime.now(ET)


def today_str():
    return et_now().strftime("%Y-%m-%d")


class Session:
    def __init__(self):
        self.armed = False
        self.chat_id = None
        self.account_value = 0.0
        self.daily_loss_limit = 0.0
        self.per_trade_risk = 0.0
        self.halt_risk = 0.0
        self.daily_loss_used = 0.0
        self.trading_suspended = False
        self.open_positions = {}   # ticker -> position dict
        self.trades_today = []
        self.signals_today = []
        self.watchlist = []
        self.session_date = today_str()
        self.update_id_offset = None
        self._load()

    def _load(self):
        if os.path.exists(config.SESSION_FILE):
            try:
                with open(config.SESSION_FILE) as f:
                    data = json.load(f)
                if data.get("session_date") == today_str():
                    self.__dict__.update(data)
            except Exception:
                pass

    def save(self):
        with open(config.SESSION_FILE, "w") as f:
            json.dump(self.__dict__, f, default=str, indent=2)

    def arm(self, account_value: float):
        self.armed = True
        self.account_value = account_value
        self.daily_loss_limit = round(account_value * config.DAILY_LOSS_PCT, 2)
        self.per_trade_risk = round(account_value * config.PER_TRADE_RISK_PCT, 2)
        self.halt_risk = round(account_value * config.HALT_RESUME_RISK_PCT, 2)
        self.daily_loss_used = 0.0
        self.trading_suspended = False
        self.open_positions = {}
        self.trades_today = []
        self.signals_today = []
        self.session_date = today_str()
        self.save()

    def daily_loss_remaining(self):
        return max(0.0, self.daily_loss_limit - self.daily_loss_used)

    def add_loss(self, amount: float):
        """Add realized or unrealized loss (positive number = loss)."""
        self.daily_loss_used = round(self.daily_loss_used + amount, 2)
        self.save()

    def check_limit_hit(self):
        return self.daily_loss_used >= self.daily_loss_limit

    def loss_pct(self):
        if self.daily_loss_limit == 0:
            return 0
        return self.daily_loss_used / self.daily_loss_limit

    def add_position(self, ticker, entry_price, stop_loss, share_size,
                     target1, target2, signal_type, conviction):
        self.open_positions[ticker] = {
            "ticker": ticker,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "current_stop": stop_loss,
            "share_size": share_size,
            "remaining_shares": share_size,
            "target1": target1,
            "target2": target2,
            "signal_type": signal_type,
            "conviction": conviction,
            "entry_time": et_now().strftime("%H:%M:%S"),
            "t1_hit": False,
            "t2_hit": False,
            "breakeven_set": False,
        }
        self.save()

    def close_position(self, ticker, exit_price, exit_reason, shares=None):
        pos = self.open_positions.get(ticker)
        if not pos:
            return None
        if shares is None:
            shares = pos["remaining_shares"]
        pnl = round((exit_price - pos["entry_price"]) * shares, 2)
        r_risk = pos["entry_price"] - pos["current_stop"]
        r_multiple = round(pnl / (r_risk * pos["share_size"]), 2) if r_risk > 0 else 0

        trade = {
            "date": today_str(),
            "ticker": ticker,
            "setup_type": pos["signal_type"],
            "conviction": pos["conviction"],
            "entry_time": pos["entry_time"],
            "entry_price": pos["entry_price"],
            "stop_loss": pos["stop_loss"],
            "share_size": pos["share_size"],
            "shares_closed": shares,
            "exit_time": et_now().strftime("%H:%M:%S"),
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "pnl": pnl,
            "r_multiple": r_multiple,
            "daily_loss_used": self.daily_loss_used,
        }
        self.trades_today.append(trade)
        self._log_trade(trade)

        if pnl < 0:
            self.add_loss(abs(pnl))

        if shares >= pos["remaining_shares"]:
            del self.open_positions[ticker]
        else:
            self.open_positions[ticker]["remaining_shares"] -= shares

        self.save()
        return trade

    def _log_trade(self, trade: dict):
        file_exists = os.path.exists(config.TRADE_LOG_FILE)
        with open(config.TRADE_LOG_FILE, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=trade.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(trade)

    def summary_stats(self):
        trades = self.trades_today
        if not trades:
            return {}
        winners = [t for t in trades if t["pnl"] > 0]
        losers = [t for t in trades if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in trades)
        avg_r = sum(t["r_multiple"] for t in trades) / len(trades)
        return {
            "total_trades": len(trades),
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": round(len(winners) / len(trades) * 100, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_r": round(avg_r, 2),
            "best_trade": max(trades, key=lambda t: t["pnl"]),
            "worst_trade": min(trades, key=lambda t: t["pnl"]),
        }

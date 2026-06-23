# ============================================================
# opt_session.py — Options session state
# Tracks account, daily loss, open option positions, trades
# ============================================================
import json
import os
import csv
from datetime import datetime
import pytz
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import options_bot.opt_config as cfg

ET = pytz.timezone("America/New_York")


def et_now():
    return datetime.now(ET)


def today_str():
    return et_now().strftime("%Y-%m-%d")


class OptionsSession:
    def __init__(self):
        self.armed = False
        self.account_value = 0.0
        self.daily_loss_limit = 0.0
        self.per_trade_risk = 0.0
        self.max_premium = 0.0
        self.daily_loss_used = 0.0
        self.trading_suspended = False
        self.open_positions = {}   # contract_symbol -> position dict
        self.trades_today = []
        self.signals_today = []
        self.watchlist = []
        self.session_date = today_str()
        self._load()

    def _load(self):
        if os.path.exists(cfg.SESSION_FILE):
            try:
                with open(cfg.SESSION_FILE) as f:
                    data = json.load(f)
                if data.get("session_date") == today_str():
                    self.__dict__.update(data)
            except Exception:
                pass

    def save(self):
        tmp_file = f"{cfg.SESSION_FILE}.tmp"
        with open(tmp_file, "w") as f:
            json.dump(self.__dict__, f, default=str, indent=2)
        os.replace(tmp_file, cfg.SESSION_FILE)

    def arm(self, account_value: float):
        self.armed = True
        self.account_value = account_value
        self.daily_loss_limit = round(account_value * cfg.DAILY_LOSS_PCT, 2)
        self.per_trade_risk = round(account_value * cfg.PER_TRADE_RISK_PCT, 2)
        # Max premium: 1% of account, capped at hard limit
        # Stop at 50% loss → risk = 50% of premium → premium = risk / 0.5
        self.max_premium = min(round(self.per_trade_risk / 0.50, 2), cfg.MAX_PREMIUM_HARD)
        self.daily_loss_used = 0.0
        self.trading_suspended = False
        self.open_positions = {}
        self.trades_today = []
        self.signals_today = []
        self.session_date = today_str()
        self.save()

    def daily_loss_remaining(self):
        return max(0.0, self.daily_loss_limit - self.daily_loss_used)

    def check_limit_hit(self):
        return self.daily_loss_used >= self.daily_loss_limit

    def add_position(self, contract_symbol, underlying, direction, strike,
                     expiry, contracts, premium_paid, stop_premium,
                     target_premium, signal_type, conviction):
        """Track an open options position."""
        self.open_positions[contract_symbol] = {
            "contract_symbol": contract_symbol,
            "underlying": underlying,
            "direction": direction,        # "CALL" or "PUT"
            "strike": strike,
            "expiry": expiry,
            "contracts": contracts,
            "premium_paid": premium_paid,  # per share (multiply by 100 for total)
            "total_cost": round(premium_paid * contracts * 100, 2),
            "stop_premium": stop_premium,
            "target_premium": target_premium,
            "signal_type": signal_type,
            "conviction": conviction,
            "entry_time": et_now().strftime("%H:%M:%S"),
        }
        self.save()

    def close_position(self, contract_symbol, exit_premium, exit_reason):
        """Close a position, record trade, update loss tracker."""
        pos = self.open_positions.get(contract_symbol)
        if not pos:
            return None

        contracts = pos["contracts"]
        premium_in = pos["premium_paid"]
        total_in = pos["total_cost"]
        total_out = round(exit_premium * contracts * 100, 2)
        pnl = round(total_out - total_in, 2)
        pnl_pct = round((exit_premium - premium_in) / premium_in * 100, 1) if premium_in > 0 else 0
        r_risk = premium_in - pos["stop_premium"]
        r_multiple = round((exit_premium - premium_in) / r_risk, 2) if r_risk > 0 else 0

        trade = {
            "date": today_str(),
            "contract_symbol": contract_symbol,
            "underlying": pos["underlying"],
            "direction": pos["direction"],
            "strike": pos["strike"],
            "expiry": pos["expiry"],
            "signal_type": pos["signal_type"],
            "conviction": pos["conviction"],
            "entry_time": pos["entry_time"],
            "exit_time": et_now().strftime("%H:%M:%S"),
            "premium_paid": premium_in,
            "exit_premium": exit_premium,
            "contracts": contracts,
            "total_cost": total_in,
            "total_received": total_out,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "r_multiple": r_multiple,
            "exit_reason": exit_reason,
        }
        self.trades_today.append(trade)
        self._log_trade(trade)

        if pnl < 0:
            self.daily_loss_used = round(self.daily_loss_used + abs(pnl), 2)

        del self.open_positions[contract_symbol]
        self.save()
        return trade

    def _log_trade(self, trade: dict):
        file_exists = os.path.exists(cfg.TRADE_LOG_FILE)
        with open(cfg.TRADE_LOG_FILE, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=trade.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(trade)

    def summary_stats(self):
        trades = self.trades_today
        if not trades:
            return {}
        winners = [t for t in trades if t["pnl"] > 0]
        total_pnl = sum(t["pnl"] for t in trades)
        return {
            "total_trades": len(trades),
            "winners": len(winners),
            "losers": len(trades) - len(winners),
            "win_rate": round(len(winners) / len(trades) * 100, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl_pct": round(sum(t["pnl_pct"] for t in trades) / len(trades), 1),
        }

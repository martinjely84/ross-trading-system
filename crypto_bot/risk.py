"""
risk.py — every risk limit, enforced BEFORE any entry.

The core decision logic (`evaluate_entry_risk`) is a pure function: it takes the
current state as explicit arguments and returns a RiskDecision. That makes each
limit unit-testable at its boundary and lets backtest.py reuse the exact same
rules. `RiskManager` is the thin live wrapper that reads those values from the
SQLite Store.

Rules (a rejected entry names the specific rule that blocked it):
  - kill switch        : KILL env OR /kill flag → no new entries
  - paused             : /pause flag → no new entries
  - daily halt         : daily loss limit already tripped today → no new entries
  - daily loss limit   : realized_pnl_today <= -(limit_pct x starting_equity)
  - max positions      : concurrent open positions cap
  - one per symbol     : no pyramiding
  - max daily trades   : entries per UTC day cap
  - re-entry cooldown  : symbol blocked for N hours after a close
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Set

from .config import Settings
from .state import Store, DailyState, BotFlags
from .util import from_iso, utcnow


@dataclass
class RiskDecision:
    allowed: bool
    reason: str


def daily_loss_limit_breached(daily_state: DailyState, limit_pct: float) -> bool:
    """True once realized P&L for the day is at or below the negative limit."""
    if daily_state.starting_equity_today <= 0:
        return False
    threshold = -(limit_pct * daily_state.starting_equity_today)
    return daily_state.realized_pnl_today <= threshold


def position_size_usd(available_cash: float, position_size_pct: float) -> float:
    """USD notional to deploy on the next entry. Precision/min-size live in exchange.py."""
    return max(0.0, available_cash) * position_size_pct


def evaluate_entry_risk(
    *,
    symbol: str,
    now_utc: datetime,
    kill_env: bool,
    flags: BotFlags,
    daily_state: DailyState,
    open_symbols: Set[str],
    cooldown_until_iso: Optional[str],
    max_positions: int,
    max_daily_trades: int,
    daily_loss_limit_pct: float,
) -> RiskDecision:
    """Pure: returns whether a NEW entry on `symbol` is permitted, and why not."""

    # 1. Kill switch (env-level or runtime flag) — most severe.
    if kill_env or flags.kill:
        src = "KILL env" if kill_env else "/kill flag"
        return RiskDecision(False, f"blocked: kill switch active ({src})")

    # 2. Paused.
    if flags.paused:
        return RiskDecision(False, "blocked: bot is paused (/pause)")

    # 3. Daily halt already recorded.
    if daily_state.halted_for_day:
        return RiskDecision(
            False, f"blocked: halted for the day ({daily_state.halt_reason or 'unspecified'})"
        )

    # 4. Daily loss limit (live recompute, in case halt flag not yet persisted).
    if daily_loss_limit_breached(daily_state, daily_loss_limit_pct):
        return RiskDecision(
            False,
            f"blocked: daily loss limit hit "
            f"(pnl {daily_state.realized_pnl_today:.2f} <= "
            f"-{daily_loss_limit_pct*100:.1f}% of {daily_state.starting_equity_today:.2f})",
        )

    # 5. One position per symbol (no pyramiding).
    if symbol in open_symbols:
        return RiskDecision(False, f"blocked: already holding {symbol} (no pyramiding)")

    # 6. Max concurrent positions.
    if len(open_symbols) >= max_positions:
        return RiskDecision(
            False, f"blocked: max positions reached ({len(open_symbols)}/{max_positions})"
        )

    # 7. Max daily trades.
    if daily_state.trades_taken_today >= max_daily_trades:
        return RiskDecision(
            False,
            f"blocked: max daily trades reached "
            f"({daily_state.trades_taken_today}/{max_daily_trades})",
        )

    # 8. Re-entry cooldown.
    if cooldown_until_iso:
        until = from_iso(cooldown_until_iso)
        if now_utc < until:
            return RiskDecision(
                False, f"blocked: {symbol} in re-entry cooldown until {until.isoformat()}"
            )

    return RiskDecision(True, "ok")


class RiskManager:
    """Live wrapper: pulls current state from the Store and applies the rules."""

    def __init__(self, store: Store, settings: Settings):
        self.store = store
        self.settings = settings

    def check_entry(self, symbol: str, now_utc: Optional[datetime] = None) -> RiskDecision:
        now_utc = now_utc or utcnow()
        flags = self.store.get_flags()
        daily_state = self.store.get_or_create_daily_state()
        open_symbols = {p.symbol for p in self.store.get_open_positions()}
        cooldown = self.store.get_cooldown(symbol)
        return evaluate_entry_risk(
            symbol=symbol,
            now_utc=now_utc,
            kill_env=self.settings.kill,
            flags=flags,
            daily_state=daily_state,
            open_symbols=open_symbols,
            cooldown_until_iso=cooldown,
            max_positions=self.settings.max_positions,
            max_daily_trades=self.settings.max_daily_trades,
            daily_loss_limit_pct=self.settings.daily_loss_limit_pct,
        )

    def position_size_usd(self, available_cash: float) -> float:
        return position_size_usd(available_cash, self.settings.position_size_pct)

    def register_daily_loss_halt_if_needed(self) -> bool:
        """If the loss limit is breached, persist the halt flag. Returns True if newly halted."""
        ds = self.store.get_or_create_daily_state()
        if ds.halted_for_day:
            return False
        if daily_loss_limit_breached(ds, self.settings.daily_loss_limit_pct):
            ds.halted_for_day = True
            ds.halt_reason = (
                f"daily loss limit {self.settings.daily_loss_limit_pct*100:.1f}% "
                f"(pnl {ds.realized_pnl_today:.2f})"
            )
            self.store.save_daily_state(ds)
            return True
        return False

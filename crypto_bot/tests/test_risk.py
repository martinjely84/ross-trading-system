"""
test_risk.py — every risk limit blocks correctly at its boundary.
Pure logic via evaluate_entry_risk; no network, no keys.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from crypto_bot.risk import (
    evaluate_entry_risk,
    daily_loss_limit_breached,
    position_size_usd,
)
from crypto_bot.state import DailyState, BotFlags
from crypto_bot.util import to_iso


NOW = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)


def fresh_daily(**kw) -> DailyState:
    base = dict(
        utc_date="2026-06-10",
        trades_taken_today=0,
        realized_pnl_today=0.0,
        starting_equity_today=10_000.0,
        halted_for_day=False,
        halt_reason="",
    )
    base.update(kw)
    return DailyState(**base)


def call(**overrides) -> "RiskDecision":
    """Defaults represent a clean, allowed entry; override to trip one rule."""
    kw = dict(
        symbol="BTC/USD",
        now_utc=NOW,
        kill_env=False,
        flags=BotFlags(paused=False, kill=False),
        daily_state=fresh_daily(),
        open_symbols=set(),
        cooldown_until_iso=None,
        max_positions=3,
        max_daily_trades=5,
        daily_loss_limit_pct=0.05,
    )
    kw.update(overrides)
    return evaluate_entry_risk(**kw)


def test_clean_entry_allowed():
    assert call().allowed is True


# --------------------------------------------------------------------------- #
# Kill / pause / halt
# --------------------------------------------------------------------------- #
def test_kill_env_blocks():
    d = call(kill_env=True)
    assert not d.allowed and "kill switch" in d.reason


def test_kill_flag_blocks():
    d = call(flags=BotFlags(kill=True))
    assert not d.allowed and "kill switch" in d.reason


def test_pause_blocks():
    d = call(flags=BotFlags(paused=True))
    assert not d.allowed and "paused" in d.reason


def test_daily_halt_flag_blocks():
    d = call(daily_state=fresh_daily(halted_for_day=True, halt_reason="manual"))
    assert not d.allowed and "halted for the day" in d.reason


# --------------------------------------------------------------------------- #
# Daily loss limit — boundary
# --------------------------------------------------------------------------- #
def test_daily_loss_limit_at_boundary_blocks():
    # -5% of 10,000 == -500 exactly → breached (<=).
    d = call(daily_state=fresh_daily(realized_pnl_today=-500.0))
    assert not d.allowed and "daily loss limit" in d.reason


def test_daily_loss_just_inside_limit_allowed():
    d = call(daily_state=fresh_daily(realized_pnl_today=-499.99))
    assert d.allowed is True


def test_daily_loss_breached_helper():
    assert daily_loss_limit_breached(fresh_daily(realized_pnl_today=-500.0), 0.05)
    assert not daily_loss_limit_breached(fresh_daily(realized_pnl_today=-499.0), 0.05)
    # No starting equity recorded → cannot breach.
    assert not daily_loss_limit_breached(
        fresh_daily(starting_equity_today=0.0, realized_pnl_today=-9999), 0.05
    )


# --------------------------------------------------------------------------- #
# Position caps
# --------------------------------------------------------------------------- #
def test_max_positions_at_boundary_blocks():
    d = call(open_symbols={"ETH/USD", "SOL/USD", "LINK/USD"}, max_positions=3)
    assert not d.allowed and "max positions" in d.reason


def test_below_max_positions_allowed():
    d = call(open_symbols={"ETH/USD", "SOL/USD"}, max_positions=3)
    assert d.allowed is True


def test_one_position_per_symbol_blocks():
    d = call(symbol="ETH/USD", open_symbols={"ETH/USD"})
    assert not d.allowed and "no pyramiding" in d.reason


# --------------------------------------------------------------------------- #
# Max daily trades — boundary
# --------------------------------------------------------------------------- #
def test_max_daily_trades_at_boundary_blocks():
    d = call(daily_state=fresh_daily(trades_taken_today=5), max_daily_trades=5)
    assert not d.allowed and "max daily trades" in d.reason


def test_below_max_daily_trades_allowed():
    d = call(daily_state=fresh_daily(trades_taken_today=4), max_daily_trades=5)
    assert d.allowed is True


# --------------------------------------------------------------------------- #
# Re-entry cooldown — boundary
# --------------------------------------------------------------------------- #
def test_cooldown_active_blocks():
    until = to_iso(NOW + timedelta(minutes=30))
    d = call(cooldown_until_iso=until)
    assert not d.allowed and "cooldown" in d.reason


def test_cooldown_expired_allowed():
    until = to_iso(NOW - timedelta(seconds=1))
    d = call(cooldown_until_iso=until)
    assert d.allowed is True


def test_cooldown_exactly_now_allowed():
    # now_utc < until is the block condition; equal is NOT blocked.
    d = call(cooldown_until_iso=to_iso(NOW))
    assert d.allowed is True


# --------------------------------------------------------------------------- #
# Precedence — kill beats everything
# --------------------------------------------------------------------------- #
def test_kill_takes_precedence_over_other_violations():
    d = call(
        kill_env=True,
        open_symbols={"ETH/USD", "SOL/USD", "LINK/USD"},
        daily_state=fresh_daily(trades_taken_today=99, realized_pnl_today=-9999),
    )
    assert not d.allowed and "kill switch" in d.reason


# --------------------------------------------------------------------------- #
# Sizing
# --------------------------------------------------------------------------- #
def test_position_size_usd():
    assert position_size_usd(10_000.0, 0.10) == pytest.approx(1000.0)
    assert position_size_usd(0.0, 0.10) == 0.0
    assert position_size_usd(-50.0, 0.10) == 0.0

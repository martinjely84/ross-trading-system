"""
test_state.py — SQLite persistence round-trips and UTC daily-reset rollover.
Runs fully offline; no network, no keys.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from crypto_bot.state import Store, Position, DailyState, ClosedTrade
from crypto_bot.util import utcnow, to_iso, utc_date_str


@pytest.fixture()
def store(tmp_path):
    s = Store(path=str(tmp_path / "test.db"))
    yield s
    s.close()


def _sample_position(symbol="BTC/USD") -> Position:
    now = utcnow()
    return Position(
        symbol=symbol,
        side="long",
        entry_price=50000.0,
        qty=0.01,
        entry_time_utc=to_iso(now),
        tp_price=52000.0,
        sl_price=49000.0,
        time_stop_utc=to_iso(now + timedelta(hours=4)),
        alpaca_order_id="abc-123",
    )


# --------------------------------------------------------------------------- #
# Positions
# --------------------------------------------------------------------------- #
def test_position_round_trip(store):
    pos = store.add_position(_sample_position())
    assert pos.id is not None

    fetched = store.get_open_position("BTC/USD")
    assert fetched is not None
    assert fetched.symbol == "BTC/USD"
    assert fetched.entry_price == 50000.0
    assert fetched.qty == 0.01
    assert fetched.alpaca_order_id == "abc-123"
    assert fetched.status == "open"
    assert fetched.adopted is False


def test_only_one_open_position_per_symbol(store):
    store.add_position(_sample_position())
    # The partial unique index must forbid a second OPEN row for the same symbol.
    with pytest.raises(Exception):
        store.add_position(_sample_position())


def test_close_position_records_trade_and_removes_from_open(store):
    pos = store.add_position(_sample_position())
    now = utcnow()
    trade = ClosedTrade(
        symbol=pos.symbol, side=pos.side, entry_price=pos.entry_price,
        exit_price=52000.0, qty=pos.qty, pnl_usd=20.0, pnl_pct=0.04,
        reason="TP", entry_time_utc=pos.entry_time_utc,
        exit_time_utc=to_iso(now), hold_seconds=3600.0,
    )
    store.close_position(pos, trade)

    assert store.get_open_position("BTC/USD") is None
    assert store.get_open_positions() == []
    trades = store.get_trades_for_date(utc_date_str(now))
    assert len(trades) == 1
    assert trades[0].reason == "TP"
    assert trades[0].pnl_usd == 20.0


def test_reopen_after_close_is_allowed(store):
    pos = store.add_position(_sample_position())
    trade = ClosedTrade(
        symbol=pos.symbol, side=pos.side, entry_price=pos.entry_price,
        exit_price=49000.0, qty=pos.qty, pnl_usd=-10.0, pnl_pct=-0.02,
        reason="SL", entry_time_utc=pos.entry_time_utc,
        exit_time_utc=to_iso(utcnow()), hold_seconds=1800.0,
    )
    store.close_position(pos, trade)
    # Symbol is free again now that the prior row is closed.
    reopened = store.add_position(_sample_position())
    assert reopened.id is not None


def test_mark_closed_no_trade_does_not_record_pnl(store):
    pos = store.add_position(_sample_position())
    store.mark_closed_no_trade(pos)
    assert store.get_open_position("BTC/USD") is None
    assert store.get_trades_for_date(utc_date_str()) == []


# --------------------------------------------------------------------------- #
# Daily state + rollover
# --------------------------------------------------------------------------- #
def test_daily_state_round_trip(store):
    ds = store.get_or_create_daily_state("2026-06-10", starting_equity=10000.0)
    ds.trades_taken_today = 3
    ds.realized_pnl_today = -125.5
    ds.halted_for_day = True
    ds.halt_reason = "daily loss limit"
    store.save_daily_state(ds)

    again = store.get_or_create_daily_state("2026-06-10")
    assert again.trades_taken_today == 3
    assert again.realized_pnl_today == -125.5
    assert again.halted_for_day is True
    assert again.halt_reason == "daily loss limit"
    assert again.starting_equity_today == 10000.0


def test_daily_reset_on_utc_date_change(store):
    # Yesterday hit the loss limit and halted.
    y = store.get_or_create_daily_state("2026-06-09", starting_equity=10000.0)
    y.trades_taken_today = 5
    y.realized_pnl_today = -600.0
    y.halted_for_day = True
    store.save_daily_state(y)

    # New UTC day must be a fresh row: zero counters, not halted.
    today = store.get_or_create_daily_state("2026-06-10", starting_equity=9400.0)
    assert today.trades_taken_today == 0
    assert today.realized_pnl_today == 0.0
    assert today.halted_for_day is False
    assert today.starting_equity_today == 9400.0
    # Yesterday's record is untouched.
    assert store.get_or_create_daily_state("2026-06-09").halted_for_day is True


# --------------------------------------------------------------------------- #
# Cooldowns
# --------------------------------------------------------------------------- #
def test_cooldown_round_trip_and_upsert(store):
    t1 = to_iso(utcnow())
    store.set_cooldown("ETH/USD", t1)
    assert store.get_cooldown("ETH/USD") == t1

    t2 = to_iso(utcnow() + timedelta(hours=2))
    store.set_cooldown("ETH/USD", t2)  # upsert, not duplicate
    assert store.get_cooldown("ETH/USD") == t2
    assert store.get_cooldown("NOPE/USD") is None


# --------------------------------------------------------------------------- #
# Flags
# --------------------------------------------------------------------------- #
def test_flags_default_and_toggle(store):
    flags = store.get_flags()
    assert flags.paused is False
    assert flags.kill is False

    store.set_flag("paused", True)
    store.set_flag("kill", True)
    flags = store.get_flags()
    assert flags.paused is True
    assert flags.kill is True

    with pytest.raises(ValueError):
        store.set_flag("bogus", True)


def test_flags_persist_across_reconnect(tmp_path):
    path = str(tmp_path / "persist.db")
    s1 = Store(path=path)
    s1.set_flag("paused", True)
    s1.close()

    s2 = Store(path=path)
    assert s2.get_flags().paused is True
    s2.close()


# --------------------------------------------------------------------------- #
# Alert dedup
# --------------------------------------------------------------------------- #
def test_alert_dedup(store):
    h = "entry:BTC/USD:50000"
    assert store.alert_already_sent(h) is False
    store.record_alert(h)
    assert store.alert_already_sent(h) is True
    # Idempotent re-record must not raise.
    store.record_alert(h)
    assert store.alert_already_sent(h) is True

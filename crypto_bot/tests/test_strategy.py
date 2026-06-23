"""
test_strategy.py — pure strategy logic. Each entry filter is exercised in
isolation (only that filter fails), plus a clean all-pass entry and the three
exit conditions. No network, no keys.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from crypto_bot.config import StrategyParams
from crypto_bot.strategy import (
    Bar,
    MarketSnapshot,
    evaluate_entry,
    evaluate_exit,
)


PARAMS = StrategyParams(
    momentum_pct=0.03,
    volume_mult=2.0,
    max_extension_pct=0.08,
    max_spread_pct=0.0015,
    min_hourly_volume_usd=500_000.0,
    tp_pct=0.04,
    sl_pct=0.02,
    max_hold_hours=4.0,
)

BASE_TS = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)


def make_snapshot(
    *,
    n_trailing: int = 24,
    trailing_close: float = 100.0,
    trailing_volume: float = 1000.0,
    last_close: float = 103.5,
    last_volume: float = 6000.0,
    current_price: float | None = None,
    day_open: float = 100.0,
    bid: float = 103.45,
    ask: float = 103.55,
) -> MarketSnapshot:
    """
    Builds a snapshot that PASSES every filter by default. Each test overrides
    exactly one knob to force a single filter to fail.

    Defaults: momentum +3.5%, vol 6x avg, extension +3.5%, spread ~0.10%,
    1h USD vol ~$621k — all comfortably inside the thresholds.
    """
    bars = [
        Bar(
            timestamp=BASE_TS - timedelta(hours=(n_trailing - i)),
            open=trailing_close, high=trailing_close, low=trailing_close,
            close=trailing_close, volume=trailing_volume,
        )
        for i in range(n_trailing)
    ]
    bars.append(
        Bar(timestamp=BASE_TS, open=trailing_close, high=last_close,
            low=trailing_close, close=last_close, volume=last_volume)
    )
    return MarketSnapshot(
        symbol="BTC/USD", bars=bars, bid=bid, ask=ask,
        day_open_price=day_open,
        current_price=current_price if current_price is not None else last_close,
    )


# --------------------------------------------------------------------------- #
# All-pass entry
# --------------------------------------------------------------------------- #
def test_entry_fires_when_all_filters_pass():
    d = evaluate_entry(make_snapshot(), PARAMS)
    assert d.should_enter is True
    assert d.reason.startswith("ENTER")
    assert d.metrics["momentum_pct"] == pytest.approx(0.035, abs=1e-9)


# --------------------------------------------------------------------------- #
# Each filter isolated
# --------------------------------------------------------------------------- #
def test_momentum_filter_blocks():
    # Only +1% over the hour.
    d = evaluate_entry(make_snapshot(last_close=101.0, current_price=101.0), PARAMS)
    assert d.should_enter is False
    assert "momentum" in d.reason


def test_volume_filter_blocks():
    # Last-hour volume only 1.5x the trailing average.
    d = evaluate_entry(make_snapshot(last_volume=1500.0), PARAMS)
    assert d.should_enter is False
    assert "vol" in d.reason and "avg" in d.reason


def test_extension_filter_blocks():
    # Price already +10% on the day (day_open=100, price=110).
    d = evaluate_entry(
        make_snapshot(last_close=110.0, current_price=110.0, last_volume=6000.0),
        PARAMS,
    )
    assert d.should_enter is False
    assert "extension" in d.reason


def test_spread_filter_blocks():
    # ~0.97% spread, far above the 0.15% cap.
    d = evaluate_entry(make_snapshot(bid=103.0, ask=104.0), PARAMS)
    assert d.should_enter is False
    assert "spread" in d.reason


def test_liquidity_filter_blocks():
    # vol still 3x avg, but 1h USD volume only ~$310k (< $500k).
    d = evaluate_entry(make_snapshot(last_volume=3000.0), PARAMS)
    assert d.should_enter is False
    assert "USD vol" in d.reason


def test_insufficient_history_blocks():
    d = evaluate_entry(make_snapshot(n_trailing=1), PARAMS)
    assert d.should_enter is False
    assert "insufficient history" in d.reason


# --------------------------------------------------------------------------- #
# Boundary behavior — thresholds are strict (>) for momentum/volume/liquidity
# --------------------------------------------------------------------------- #
def test_momentum_exactly_at_threshold_does_not_enter():
    # Exactly +3.0% should NOT pass a strict '>' momentum check.
    d = evaluate_entry(make_snapshot(last_close=103.0, current_price=103.0), PARAMS)
    assert d.should_enter is False
    assert "momentum" in d.reason


# --------------------------------------------------------------------------- #
# Exits
# --------------------------------------------------------------------------- #
def _now():
    return datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc)


def _future():
    return datetime(2026, 6, 10, 16, 0, tzinfo=timezone.utc)


def _past():
    return datetime(2026, 6, 10, 13, 0, tzinfo=timezone.utc)


def test_exit_take_profit():
    d = evaluate_exit(100.0, 104.0, _now(), _future(), PARAMS)
    assert d.should_exit and d.reason == "TP"


def test_exit_stop_loss():
    d = evaluate_exit(100.0, 98.0, _now(), _future(), PARAMS)
    assert d.should_exit and d.reason == "SL"


def test_exit_time_stop():
    # Price is fine, but the time stop has passed.
    d = evaluate_exit(100.0, 101.0, _now(), _past(), PARAMS)
    assert d.should_exit and d.reason == "TIME"


def test_exit_hold():
    d = evaluate_exit(100.0, 101.0, _now(), _future(), PARAMS)
    assert d.should_exit is False and d.reason is None


def test_exit_tp_takes_priority_over_time():
    # Both TP hit AND time stop passed in the same check → TP wins.
    d = evaluate_exit(100.0, 105.0, _now(), _past(), PARAMS)
    assert d.reason == "TP"

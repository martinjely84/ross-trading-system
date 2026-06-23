"""
strategy.py — PURE crypto momentum-breakout signal logic.

>>> THIS MODULE PERFORMS ZERO I/O. <<<
No network, no SQLite, no env, no clock reads other than values passed in.
It takes market data (dataclasses) and returns typed decisions. That purity is
what lets bot.py (live) and backtest.py (replay) share the *identical* logic and
what makes every filter unit-testable offline.

ENTRY (long only) requires ALL of:
  1. Momentum   : price up > momentum_pct over the last 1h
  2. Volume     : last-1h volume > volume_mult x avg hourly volume (trailing 24h)
  3. Extension  : NOT already up > max_extension_pct on the day (no chasing tops)
  4. Spread     : (ask - bid) / mid < max_spread_pct
  5. Liquidity  : last-1h USD volume > min_hourly_volume_usd

EXIT (whichever hits first):
  - Take profit : price >= entry * (1 + tp_pct)
  - Stop loss   : price <= entry * (1 - sl_pct)
  - Time stop   : now_utc >= time_stop_utc
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from .config import StrategyParams


# Need the current (in-progress) hour plus a trailing window to average against.
# We cap the averaging window at 24 prior bars but accept fewer if that's all the
# history we have — below MIN_BARS we decline rather than guess.
MIN_BARS = 3
TRAILING_AVG_BARS = 24


# --------------------------------------------------------------------------- #
# Inputs (constructed by bot.py from Alpaca data, or by backtest.py from CSV)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Bar:
    """A single OHLCV bar. `volume` is in COIN units (not USD)."""
    timestamp: datetime  # aware UTC
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class MarketSnapshot:
    """
    Everything strategy.py needs to judge one symbol at one instant.

    Contract:
      - `bars` are chronological 1h bars; bars[-1] is the most recent hour.
      - `current_price` is the live price now (defaults to bars[-1].close).
      - `bid`/`ask` are the live top-of-book quote.
      - `day_open_price` is the price at 00:00 UTC today (for the extension check).
    """
    symbol: str
    bars: List[Bar]
    bid: float
    ask: float
    day_open_price: float
    current_price: Optional[float] = None

    def price(self) -> float:
        if self.current_price is not None:
            return self.current_price
        if self.bars:
            return self.bars[-1].close
        raise ValueError("snapshot has neither current_price nor bars")


@dataclass
class SignalDecision:
    should_enter: bool
    symbol: str
    reason: str
    metrics: dict = field(default_factory=dict)


@dataclass
class ExitDecision:
    should_exit: bool
    reason: Optional[str]  # "TP" | "SL" | "TIME" | None
    detail: str


# --------------------------------------------------------------------------- #
# Individual filters — each returns (passed, human_detail, metric_value)
# --------------------------------------------------------------------------- #
def _momentum_1h(snapshot: MarketSnapshot) -> float:
    """Fractional change over the last hour: current vs the prior hour's close."""
    ref = snapshot.bars[-2].close
    if ref <= 0:
        return 0.0
    return (snapshot.price() - ref) / ref


def _avg_hourly_volume(snapshot: MarketSnapshot) -> float:
    """Mean COIN volume over up to TRAILING_AVG_BARS bars before the last one."""
    trailing = snapshot.bars[-(TRAILING_AVG_BARS + 1):-1]
    if not trailing:
        return 0.0
    return sum(b.volume for b in trailing) / len(trailing)


def _spread_pct(snapshot: MarketSnapshot) -> float:
    mid = (snapshot.ask + snapshot.bid) / 2.0
    if mid <= 0:
        return float("inf")
    return (snapshot.ask - snapshot.bid) / mid


def _extension_pct(snapshot: MarketSnapshot) -> float:
    if snapshot.day_open_price <= 0:
        return 0.0
    return (snapshot.price() - snapshot.day_open_price) / snapshot.day_open_price


# --------------------------------------------------------------------------- #
# Entry
# --------------------------------------------------------------------------- #
def evaluate_entry(snapshot: MarketSnapshot, params: StrategyParams) -> SignalDecision:
    """
    Evaluate all entry filters. Returns a SignalDecision whose `reason` always
    explains the outcome (logged every cycle, entering or not).
    """
    sym = snapshot.symbol

    if len(snapshot.bars) < MIN_BARS:
        return SignalDecision(
            should_enter=False, symbol=sym,
            reason=f"insufficient history ({len(snapshot.bars)} bars < {MIN_BARS})",
        )

    last_bar = snapshot.bars[-1]
    momentum = _momentum_1h(snapshot)
    avg_vol = _avg_hourly_volume(snapshot)
    vol_mult = (last_bar.volume / avg_vol) if avg_vol > 0 else 0.0
    extension = _extension_pct(snapshot)
    spread = _spread_pct(snapshot)
    hourly_usd_vol = last_bar.close * last_bar.volume

    metrics = {
        "momentum_pct": momentum,
        "vol_mult": vol_mult,
        "extension_pct": extension,
        "spread_pct": spread,
        "hourly_usd_vol": hourly_usd_vol,
        "price": snapshot.price(),
    }

    # Evaluate filters in order; first failure short-circuits the reason.
    checks = []

    passed_mom = momentum > params.momentum_pct
    checks.append((passed_mom,
                   f"momentum {momentum*100:.2f}% "
                   f"{'>' if passed_mom else '<='} {params.momentum_pct*100:.2f}%"))

    passed_vol = avg_vol > 0 and vol_mult > params.volume_mult
    checks.append((passed_vol,
                   f"vol {vol_mult:.2f}x "
                   f"{'>' if passed_vol else '<='} {params.volume_mult:.2f}x avg"))

    passed_ext = extension <= params.max_extension_pct
    checks.append((passed_ext,
                   f"extension {extension*100:.2f}% "
                   f"{'<=' if passed_ext else '>'} {params.max_extension_pct*100:.2f}% cap"))

    passed_spread = spread < params.max_spread_pct
    checks.append((passed_spread,
                   f"spread {spread*100:.3f}% "
                   f"{'<' if passed_spread else '>='} {params.max_spread_pct*100:.3f}%"))

    passed_liq = hourly_usd_vol > params.min_hourly_volume_usd
    checks.append((passed_liq,
                   f"1h USD vol ${hourly_usd_vol:,.0f} "
                   f"{'>' if passed_liq else '<='} ${params.min_hourly_volume_usd:,.0f}"))

    all_pass = all(p for p, _ in checks)
    if all_pass:
        reason = "ENTER: " + "; ".join(d for _, d in checks)
        return SignalDecision(True, sym, reason, metrics)

    # Name the first failing filter clearly, then list the rest for the log.
    first_fail = next(d for p, d in checks if not p)
    reason = "no entry — " + first_fail + " | " + "; ".join(d for _, d in checks)
    return SignalDecision(False, sym, reason, metrics)


# --------------------------------------------------------------------------- #
# Exit
# --------------------------------------------------------------------------- #
def take_profit_price(entry_price: float, params: StrategyParams) -> float:
    return entry_price * (1 + params.tp_pct)


def stop_loss_price(entry_price: float, params: StrategyParams) -> float:
    return entry_price * (1 - params.sl_pct)


def evaluate_exit(
    entry_price: float,
    current_price: float,
    now_utc: datetime,
    time_stop_utc: datetime,
    params: StrategyParams,
) -> ExitDecision:
    """
    Source-of-truth exit check, shared by the live bot and the backtester.
    TP and SL are derived from entry_price and params so there is exactly one
    definition. Priority: TP, then SL, then TIME (a price target beats the clock).
    """
    tp = take_profit_price(entry_price, params)
    sl = stop_loss_price(entry_price, params)

    if current_price >= tp:
        return ExitDecision(True, "TP", f"price {current_price:.6g} >= TP {tp:.6g}")
    if current_price <= sl:
        return ExitDecision(True, "SL", f"price {current_price:.6g} <= SL {sl:.6g}")
    if now_utc >= time_stop_utc:
        return ExitDecision(True, "TIME", f"now >= time stop {time_stop_utc.isoformat()}")

    return ExitDecision(
        False, None,
        f"hold — price {current_price:.6g} within ({sl:.6g}, {tp:.6g}), "
        f"time stop {time_stop_utc.isoformat()}",
    )

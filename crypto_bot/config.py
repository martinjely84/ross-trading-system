"""
config.py — typed, validated configuration for the crypto momentum bot.

All thresholds and secrets load from environment variables (see .env.example).
Validation runs on load: if anything is missing or insane (negative size,
stop/take-profit out of range, empty watchlist) we raise immediately so the
bot can refuse to boot rather than trade on garbage.

`Settings` holds everything. `StrategyParams` is a frozen, I/O-free subset that
gets passed into strategy.py — this keeps strategy.py pure and importable by the
backtester without ever touching the environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import List

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_WATCHLIST = [
    "BTC/USD",
    "ETH/USD",
    "SOL/USD",
    "AVAX/USD",
    "LINK/USD",
    "DOGE/USD",
    "XRP/USD",
    "ADA/USD",
]


class Settings(BaseSettings):
    """All runtime configuration, validated on construction."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Alpaca ---
    alpaca_api_key: str = Field(default="", alias="ALPACA_API_KEY")
    alpaca_secret_key: str = Field(default="", alias="ALPACA_SECRET_KEY")
    alpaca_paper: bool = Field(default=True, alias="ALPACA_PAPER")

    # --- Telegram ---
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")

    # --- Kill switch (env-level) ---
    kill: bool = Field(default=False, alias="KILL")

    # --- Strategy ---
    momentum_pct: float = Field(default=0.03, alias="MOMENTUM_PCT")
    volume_mult: float = Field(default=2.0, alias="VOLUME_MULT")
    max_extension_pct: float = Field(default=0.08, alias="MAX_EXTENSION_PCT")
    max_spread_pct: float = Field(default=0.0015, alias="MAX_SPREAD_PCT")
    min_hourly_volume_usd: float = Field(default=500_000.0, alias="MIN_HOURLY_VOLUME_USD")
    tp_pct: float = Field(default=0.04, alias="TP_PCT")
    sl_pct: float = Field(default=0.02, alias="SL_PCT")
    max_hold_hours: float = Field(default=4.0, alias="MAX_HOLD_HOURS")

    # --- Risk ---
    position_size_pct: float = Field(default=0.10, alias="POSITION_SIZE_PCT")
    max_positions: int = Field(default=3, alias="MAX_POSITIONS")
    max_daily_trades: int = Field(default=5, alias="MAX_DAILY_TRADES")
    reentry_cooldown_hours: float = Field(default=2.0, alias="REENTRY_COOLDOWN_HOURS")
    daily_loss_limit_pct: float = Field(default=0.05, alias="DAILY_LOSS_LIMIT_PCT")

    # --- Backtest ---
    fee_pct: float = Field(default=0.0025, alias="FEE_PCT")
    slippage_pct: float = Field(default=0.001, alias="SLIPPAGE_PCT")

    # --- Operational ---
    watchlist: List[str] = Field(default_factory=lambda: list(DEFAULT_WATCHLIST))
    loop_interval_seconds: int = Field(default=60, alias="LOOP_INTERVAL_SECONDS")
    scan_interval_seconds: int = Field(default=300, alias="SCAN_INTERVAL_SECONDS")
    db_path: str = Field(default="crypto_bot_state.db", alias="DB_PATH")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # ------------------------------------------------------------------ #
    # Validators — reject insane config at load time.
    # ------------------------------------------------------------------ #
    @field_validator(
        "momentum_pct",
        "max_extension_pct",
        "max_spread_pct",
        "tp_pct",
        "sl_pct",
        "daily_loss_limit_pct",
    )
    @classmethod
    def _pct_must_be_fraction(cls, v: float, info) -> float:
        # These are fractions (0.03 == 3%). Catch the classic "entered 3 not 0.03".
        if not (0.0 < v < 1.0):
            raise ValueError(
                f"{info.field_name}={v} must be a fraction in (0, 1) "
                f"(e.g. 0.03 for 3%, not 3)"
            )
        return v

    @field_validator("position_size_pct")
    @classmethod
    def _position_size_in_range(cls, v: float) -> float:
        if not (0.0 < v <= 1.0):
            raise ValueError(f"position_size_pct={v} must be in (0, 1]")
        return v

    @field_validator("volume_mult", "min_hourly_volume_usd", "max_hold_hours", "reentry_cooldown_hours")
    @classmethod
    def _must_be_positive(cls, v: float, info) -> float:
        if v <= 0:
            raise ValueError(f"{info.field_name}={v} must be > 0")
        return v

    @field_validator("max_positions", "max_daily_trades")
    @classmethod
    def _int_must_be_positive(cls, v: int, info) -> int:
        if v < 1:
            raise ValueError(f"{info.field_name}={v} must be >= 1")
        return v

    @field_validator("fee_pct", "slippage_pct")
    @classmethod
    def _cost_in_range(cls, v: float, info) -> float:
        if not (0.0 <= v < 1.0):
            raise ValueError(f"{info.field_name}={v} must be in [0, 1)")
        return v

    @field_validator("watchlist", mode="before")
    @classmethod
    def _parse_watchlist(cls, v):
        # Allow a comma-separated env override: WATCHLIST="BTC/USD,ETH/USD"
        if isinstance(v, str):
            v = [s.strip() for s in v.split(",") if s.strip()]
        return v

    @model_validator(mode="after")
    def _cross_field_sanity(self) -> "Settings":
        if not self.watchlist:
            raise ValueError("watchlist is empty — nothing to trade")
        for sym in self.watchlist:
            if "/" not in sym:
                raise ValueError(
                    f"watchlist symbol {sym!r} is not in 'BASE/QUOTE' crypto format"
                )
        # A take-profit smaller than the stop means we risk more than we target —
        # not inherently illegal, but it's almost always a config typo, so block it.
        if self.tp_pct <= self.sl_pct:
            raise ValueError(
                f"tp_pct ({self.tp_pct}) must be greater than sl_pct ({self.sl_pct})"
            )
        if self.scan_interval_seconds < self.loop_interval_seconds:
            raise ValueError(
                "scan_interval_seconds must be >= loop_interval_seconds"
            )
        return self

    # Convenience -------------------------------------------------------
    @property
    def telegram_chat_id_int(self) -> int | None:
        raw = (self.telegram_chat_id or "").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def strategy_params(self) -> "StrategyParams":
        return StrategyParams(
            momentum_pct=self.momentum_pct,
            volume_mult=self.volume_mult,
            max_extension_pct=self.max_extension_pct,
            max_spread_pct=self.max_spread_pct,
            min_hourly_volume_usd=self.min_hourly_volume_usd,
            tp_pct=self.tp_pct,
            sl_pct=self.sl_pct,
            max_hold_hours=self.max_hold_hours,
        )


@dataclass(frozen=True)
class StrategyParams:
    """
    Immutable, I/O-free strategy thresholds.

    This is the ONLY config that strategy.py sees. Passing a frozen dataclass
    (rather than letting strategy.py import Settings) is what keeps the strategy
    pure and lets the backtester reuse it with overridden values.
    """

    momentum_pct: float
    volume_mult: float
    max_extension_pct: float
    max_spread_pct: float
    min_hourly_volume_usd: float
    tp_pct: float
    sl_pct: float
    max_hold_hours: float


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache settings. Raises pydantic ValidationError on bad config."""
    return Settings()

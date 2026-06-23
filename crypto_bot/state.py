"""
state.py — SQLite persistence layer. Built FIRST so the bot loses nothing on a
Railway restart (deploys, crashes, platform events).

Everything the bot needs to fully rebuild itself lives here:
  - open_positions : live positions with their TP/SL/time-stop
  - closed_trades  : history for /pnl and the daily summary
  - daily_state    : per-UTC-day counters, realized P&L, halt flag
  - cooldowns      : per-symbol re-entry blocks
  - bot_flags      : runtime paused/kill toggles (Telegram-controllable)
  - sent_alerts    : outbound alert dedup hashes (survives restarts)

All timestamps are stored as ISO-8601 UTC strings (see util.py). All writes
commit immediately — we favor durability over throughput; this bot does a
handful of writes per hour, not per second.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict, field
from typing import Optional

from .util import utcnow_iso, utc_date_str


# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #
@dataclass
class Position:
    symbol: str
    side: str  # "long" only for now
    entry_price: float
    qty: float
    entry_time_utc: str  # ISO
    tp_price: float
    sl_price: float
    time_stop_utc: str  # ISO
    alpaca_order_id: Optional[str] = None
    status: str = "open"  # "open" | "closed"
    # adopted == reconciled off Alpaca without our original basis (flagged)
    adopted: bool = False
    id: Optional[int] = None


@dataclass
class DailyState:
    utc_date: str
    trades_taken_today: int = 0
    realized_pnl_today: float = 0.0
    starting_equity_today: float = 0.0
    halted_for_day: bool = False
    halt_reason: str = ""


@dataclass
class ClosedTrade:
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    qty: float
    pnl_usd: float
    pnl_pct: float
    reason: str  # TP | SL | TIME | RECONCILE | MANUAL
    entry_time_utc: str
    exit_time_utc: str
    hold_seconds: float
    id: Optional[int] = None


@dataclass
class BotFlags:
    paused: bool = False
    kill: bool = False


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
_SCHEMA = """
CREATE TABLE IF NOT EXISTS open_positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    qty             REAL NOT NULL,
    entry_time_utc  TEXT NOT NULL,
    tp_price        REAL NOT NULL,
    sl_price        REAL NOT NULL,
    time_stop_utc   TEXT NOT NULL,
    alpaca_order_id TEXT,
    status          TEXT NOT NULL DEFAULT 'open',
    adopted         INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_open_pos_symbol
    ON open_positions(symbol) WHERE status = 'open';

CREATE TABLE IF NOT EXISTS closed_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    exit_price      REAL NOT NULL,
    qty             REAL NOT NULL,
    pnl_usd         REAL NOT NULL,
    pnl_pct         REAL NOT NULL,
    reason          TEXT NOT NULL,
    entry_time_utc  TEXT NOT NULL,
    exit_time_utc   TEXT NOT NULL,
    hold_seconds    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_state (
    utc_date              TEXT PRIMARY KEY,
    trades_taken_today    INTEGER NOT NULL DEFAULT 0,
    realized_pnl_today    REAL NOT NULL DEFAULT 0,
    starting_equity_today REAL NOT NULL DEFAULT 0,
    halted_for_day        INTEGER NOT NULL DEFAULT 0,
    halt_reason           TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS cooldowns (
    symbol             TEXT PRIMARY KEY,
    cooldown_until_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_flags (
    id     INTEGER PRIMARY KEY CHECK (id = 1),
    paused INTEGER NOT NULL DEFAULT 0,
    kill   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sent_alerts (
    alert_hash   TEXT PRIMARY KEY,
    sent_at_utc  TEXT NOT NULL
);
"""


class Store:
    """Thin, explicit SQLite wrapper. One Store == one connection."""

    def __init__(self, path: str = "crypto_bot_state.db"):
        # check_same_thread=False: the bot's main loop and the Telegram poller
        # may live on different threads; we serialize writes ourselves and
        # sqlite handles its own locking.
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA foreign_keys=ON;")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(_SCHEMA)
        # Ensure the singleton flags row exists.
        self.conn.execute(
            "INSERT OR IGNORE INTO bot_flags (id, paused, kill) VALUES (1, 0, 0)"
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ #
    # Positions
    # ------------------------------------------------------------------ #
    def add_position(self, pos: Position) -> Position:
        cur = self.conn.execute(
            """INSERT INTO open_positions
               (symbol, side, entry_price, qty, entry_time_utc, tp_price,
                sl_price, time_stop_utc, alpaca_order_id, status, adopted)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pos.symbol, pos.side, pos.entry_price, pos.qty, pos.entry_time_utc,
                pos.tp_price, pos.sl_price, pos.time_stop_utc, pos.alpaca_order_id,
                pos.status, int(pos.adopted),
            ),
        )
        self.conn.commit()
        pos.id = cur.lastrowid
        return pos

    def get_open_positions(self) -> list[Position]:
        rows = self.conn.execute(
            "SELECT * FROM open_positions WHERE status = 'open' ORDER BY id"
        ).fetchall()
        return [self._row_to_position(r) for r in rows]

    def get_open_position(self, symbol: str) -> Optional[Position]:
        row = self.conn.execute(
            "SELECT * FROM open_positions WHERE symbol = ? AND status = 'open'",
            (symbol,),
        ).fetchone()
        return self._row_to_position(row) if row else None

    def update_position(self, pos: Position) -> None:
        if pos.id is None:
            raise ValueError("cannot update a position without an id")
        self.conn.execute(
            """UPDATE open_positions SET
                 symbol=?, side=?, entry_price=?, qty=?, entry_time_utc=?,
                 tp_price=?, sl_price=?, time_stop_utc=?, alpaca_order_id=?,
                 status=?, adopted=?
               WHERE id=?""",
            (
                pos.symbol, pos.side, pos.entry_price, pos.qty, pos.entry_time_utc,
                pos.tp_price, pos.sl_price, pos.time_stop_utc, pos.alpaca_order_id,
                pos.status, int(pos.adopted), pos.id,
            ),
        )
        self.conn.commit()

    def close_position(self, pos: Position, trade: ClosedTrade) -> None:
        """Atomically mark a position closed and record the closed trade."""
        if pos.id is None:
            raise ValueError("cannot close a position without an id")
        with self.conn:  # transaction
            self.conn.execute(
                "UPDATE open_positions SET status='closed' WHERE id=?", (pos.id,)
            )
            self.conn.execute(
                """INSERT INTO closed_trades
                   (symbol, side, entry_price, exit_price, qty, pnl_usd, pnl_pct,
                    reason, entry_time_utc, exit_time_utc, hold_seconds)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    trade.symbol, trade.side, trade.entry_price, trade.exit_price,
                    trade.qty, trade.pnl_usd, trade.pnl_pct, trade.reason,
                    trade.entry_time_utc, trade.exit_time_utc, trade.hold_seconds,
                ),
            )

    def mark_closed_no_trade(self, pos: Position) -> None:
        """Mark a position closed WITHOUT recording P&L.

        Used during reconciliation when a position vanished from Alpaca while we
        were down — we don't know the fill, so we must NOT double-count P&L.
        """
        if pos.id is None:
            raise ValueError("cannot close a position without an id")
        self.conn.execute(
            "UPDATE open_positions SET status='closed' WHERE id=?", (pos.id,)
        )
        self.conn.commit()

    @staticmethod
    def _row_to_position(row: sqlite3.Row) -> Position:
        return Position(
            id=row["id"],
            symbol=row["symbol"],
            side=row["side"],
            entry_price=row["entry_price"],
            qty=row["qty"],
            entry_time_utc=row["entry_time_utc"],
            tp_price=row["tp_price"],
            sl_price=row["sl_price"],
            time_stop_utc=row["time_stop_utc"],
            alpaca_order_id=row["alpaca_order_id"],
            status=row["status"],
            adopted=bool(row["adopted"]),
        )

    # ------------------------------------------------------------------ #
    # Closed trades
    # ------------------------------------------------------------------ #
    def get_trades_for_date(self, utc_date: str) -> list[ClosedTrade]:
        rows = self.conn.execute(
            "SELECT * FROM closed_trades WHERE substr(exit_time_utc,1,10)=? "
            "ORDER BY id",
            (utc_date,),
        ).fetchall()
        return [self._row_to_trade(r) for r in rows]

    @staticmethod
    def _row_to_trade(row: sqlite3.Row) -> ClosedTrade:
        return ClosedTrade(
            id=row["id"], symbol=row["symbol"], side=row["side"],
            entry_price=row["entry_price"], exit_price=row["exit_price"],
            qty=row["qty"], pnl_usd=row["pnl_usd"], pnl_pct=row["pnl_pct"],
            reason=row["reason"], entry_time_utc=row["entry_time_utc"],
            exit_time_utc=row["exit_time_utc"], hold_seconds=row["hold_seconds"],
        )

    # ------------------------------------------------------------------ #
    # Daily state
    # ------------------------------------------------------------------ #
    def get_or_create_daily_state(
        self, utc_date: Optional[str] = None, starting_equity: float = 0.0
    ) -> DailyState:
        utc_date = utc_date or utc_date_str()
        row = self.conn.execute(
            "SELECT * FROM daily_state WHERE utc_date=?", (utc_date,)
        ).fetchone()
        if row:
            return self._row_to_daily(row)
        ds = DailyState(utc_date=utc_date, starting_equity_today=starting_equity)
        self.save_daily_state(ds)
        return ds

    def save_daily_state(self, ds: DailyState) -> None:
        self.conn.execute(
            """INSERT INTO daily_state
                 (utc_date, trades_taken_today, realized_pnl_today,
                  starting_equity_today, halted_for_day, halt_reason)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(utc_date) DO UPDATE SET
                 trades_taken_today=excluded.trades_taken_today,
                 realized_pnl_today=excluded.realized_pnl_today,
                 starting_equity_today=excluded.starting_equity_today,
                 halted_for_day=excluded.halted_for_day,
                 halt_reason=excluded.halt_reason""",
            (
                ds.utc_date, ds.trades_taken_today, ds.realized_pnl_today,
                ds.starting_equity_today, int(ds.halted_for_day), ds.halt_reason,
            ),
        )
        self.conn.commit()

    @staticmethod
    def _row_to_daily(row: sqlite3.Row) -> DailyState:
        return DailyState(
            utc_date=row["utc_date"],
            trades_taken_today=row["trades_taken_today"],
            realized_pnl_today=row["realized_pnl_today"],
            starting_equity_today=row["starting_equity_today"],
            halted_for_day=bool(row["halted_for_day"]),
            halt_reason=row["halt_reason"],
        )

    # ------------------------------------------------------------------ #
    # Cooldowns
    # ------------------------------------------------------------------ #
    def set_cooldown(self, symbol: str, until_utc: str) -> None:
        self.conn.execute(
            """INSERT INTO cooldowns (symbol, cooldown_until_utc) VALUES (?, ?)
               ON CONFLICT(symbol) DO UPDATE SET cooldown_until_utc=excluded.cooldown_until_utc""",
            (symbol, until_utc),
        )
        self.conn.commit()

    def get_cooldown(self, symbol: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT cooldown_until_utc FROM cooldowns WHERE symbol=?", (symbol,)
        ).fetchone()
        return row["cooldown_until_utc"] if row else None

    def all_cooldowns(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT symbol, cooldown_until_utc FROM cooldowns").fetchall()
        return {r["symbol"]: r["cooldown_until_utc"] for r in rows}

    # ------------------------------------------------------------------ #
    # Bot flags
    # ------------------------------------------------------------------ #
    def get_flags(self) -> BotFlags:
        row = self.conn.execute(
            "SELECT paused, kill FROM bot_flags WHERE id=1"
        ).fetchone()
        return BotFlags(paused=bool(row["paused"]), kill=bool(row["kill"]))

    def set_flag(self, name: str, value: bool) -> None:
        if name not in ("paused", "kill"):
            raise ValueError(f"unknown flag {name!r}")
        self.conn.execute(
            f"UPDATE bot_flags SET {name}=? WHERE id=1", (int(value),)
        )
        self.conn.commit()

    # ------------------------------------------------------------------ #
    # Alert dedup
    # ------------------------------------------------------------------ #
    def alert_already_sent(self, alert_hash: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sent_alerts WHERE alert_hash=?", (alert_hash,)
        ).fetchone()
        return row is not None

    def record_alert(self, alert_hash: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO sent_alerts (alert_hash, sent_at_utc) VALUES (?, ?)",
            (alert_hash, utcnow_iso()),
        )
        self.conn.commit()

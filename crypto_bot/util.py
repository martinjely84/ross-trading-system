"""
util.py — small shared helpers, chiefly UTC time handling.

Rule for the whole codebase: timestamps are timezone-aware UTC in memory and
stored as ISO-8601 strings with a 'Z'/offset. Never a naive datetime.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    """Serialize an aware datetime to ISO-8601 UTC. Rejects naive datetimes."""
    if dt.tzinfo is None:
        raise ValueError("refusing to serialize a naive datetime; use aware UTC")
    return dt.astimezone(timezone.utc).isoformat()


def from_iso(s: str) -> datetime:
    """Parse an ISO-8601 string back to an aware UTC datetime."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        # Assume stored value was UTC if it somehow lost its tz.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utcnow_iso() -> str:
    return to_iso(utcnow())


def utc_date_str(dt: datetime | None = None) -> str:
    """The UTC calendar date (YYYY-MM-DD) for daily-state keying."""
    dt = dt or utcnow()
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")

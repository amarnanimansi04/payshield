"""Shared ISO-8601 formatting - millisecond precision with a trailing
Z, matching JS's Date.prototype.toISOString() format exactly. Used
wherever a stable, string-comparable timestamp is needed (canonical
window boundaries, idempotency keys, audit timestamps)."""

from datetime import datetime, timezone


def to_iso(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def now_iso() -> str:
    return to_iso(datetime.now(timezone.utc))


def from_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

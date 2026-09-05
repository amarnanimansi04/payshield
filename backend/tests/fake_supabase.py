"""
PAYSHIELD — in-memory fake Supabase client, for integration testing ONLY.

Implements just the chainable subset of the supabase-py async
query-builder API this codebase actually calls: select (with
eq/neq/gte/lte/lt/gt/order/limit/maybe_single), insert, upsert (with
on_conflict + ignore_duplicates), update, delete (with count="exact").
Also emulates Postgres unique-constraint violations (SQLSTATE 23505)
for the `events` table's two real indexes, since the webhook route's
idempotency logic specifically branches on `error.code == "23505"`.

This is NOT a general-purpose Postgres emulator - it's scoped exactly
to what this codebase's queries need, so it stays honest about what it
proves: that the REAL route handlers and services, unmodified, behave
correctly against a stand-in database enforcing the same constraints
the real schema declares.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from postgrest.exceptions import APIError


@dataclass
class UniqueIndex:
    columns: list[str]
    partial: bool = False  # only enforced when every indexed column is non-null


TABLE_SCHEMAS: dict[str, dict[str, Any]] = {
    "events": {
        "primary_key": ["event_id"],
        "unique_indexes": [
            UniqueIndex(["razorpay_event_id"], partial=True),
            UniqueIndex(["payment_id", "outcome"]),
        ],
    },
    "segment_windows": {"primary_key": ["id"], "unique_indexes": [UniqueIndex(["segment", "window_end"])]},
    "agent_state": {"primary_key": ["segment"], "unique_indexes": []},
    "agent_state_history": {"primary_key": ["id"], "unique_indexes": []},
    "recovery_policy": {"primary_key": ["segment"], "unique_indexes": []},
    "alerts": {"primary_key": ["id"], "unique_indexes": []},
    "injected_anomalies": {"primary_key": ["id"], "unique_indexes": []},
    "evaluation_runs": {"primary_key": ["id"], "unique_indexes": []},
}


class FakeDb:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {t: [] for t in TABLE_SCHEMAS}

    def table(self, name: str) -> "FakeQueryBuilder":
        if name not in TABLE_SCHEMAS:
            raise ValueError(f"FakeDb: unknown table '{name}' — add it to TABLE_SCHEMAS")
        return FakeQueryBuilder(self, name)

    def find_unique_violation(self, table: str, candidate: dict) -> bool:
        schema = TABLE_SCHEMAS[table]
        rows = self.tables[table]
        for idx in schema["unique_indexes"]:
            if idx.partial and any(candidate.get(c) is None for c in idx.columns):
                continue
            if any(all(r.get(c) == candidate.get(c) for c in idx.columns) for r in rows):
                return True
        return False


def _cmp_matches(row: dict, col: str, op: str, val: Any) -> bool:
    v = row.get(col)
    if op == "eq":
        return v == val
    if op == "neq":
        return v != val
    if op == "gte":
        return v is not None and v >= val
    if op == "lte":
        return v is not None and v <= val
    if op == "lt":
        return v is not None and v < val
    if op == "gt":
        return v is not None and v > val
    raise ValueError(f"unsupported op {op}")


@dataclass
class _Response:
    data: Any
    count: int | None = None


class FakeQueryBuilder:
    def __init__(self, db: FakeDb, table: str) -> None:
        self.db = db
        self.table_name = table
        self.mode = "select"
        self.payload: Any = None
        self.opts: dict = {}
        self.filters: list[tuple[str, str, Any]] = []
        self.order_col: str | None = None
        self.order_desc = False
        self.limit_n: int | None = None
        self.range_start: int | None = None
        self.range_end: int | None = None
        self.want_single = False
        self.want_count = False

    # --- mode setters ---
    def select(self, _cols: str = "*") -> "FakeQueryBuilder":
        self.mode = "select"
        return self

    def insert(self, payload: Any) -> "FakeQueryBuilder":
        self.mode = "insert"
        self.payload = payload
        return self

    def upsert(self, payload: Any, on_conflict: str = "", ignore_duplicates: bool = False, **_: Any) -> "FakeQueryBuilder":
        self.mode = "upsert"
        self.payload = payload
        self.opts = {"on_conflict": on_conflict, "ignore_duplicates": ignore_duplicates}
        return self

    def update(self, payload: dict) -> "FakeQueryBuilder":
        self.mode = "update"
        self.payload = payload
        return self

    def delete(self, count: str | None = None) -> "FakeQueryBuilder":
        self.mode = "delete"
        self.want_count = count == "exact"
        return self

    # --- filters ---
    def eq(self, col: str, val: Any) -> "FakeQueryBuilder":
        self.filters.append((col, "eq", val))
        return self

    def neq(self, col: str, val: Any) -> "FakeQueryBuilder":
        self.filters.append((col, "neq", val))
        return self

    def gte(self, col: str, val: Any) -> "FakeQueryBuilder":
        self.filters.append((col, "gte", val))
        return self

    def lte(self, col: str, val: Any) -> "FakeQueryBuilder":
        self.filters.append((col, "lte", val))
        return self

    def lt(self, col: str, val: Any) -> "FakeQueryBuilder":
        self.filters.append((col, "lt", val))
        return self

    def gt(self, col: str, val: Any) -> "FakeQueryBuilder":
        self.filters.append((col, "gt", val))
        return self

    def order(self, col: str, desc: bool = False) -> "FakeQueryBuilder":
        self.order_col = col
        self.order_desc = desc
        return self

    def limit(self, n: int) -> "FakeQueryBuilder":
        self.limit_n = n
        return self

    def range(self, start: int, end: int) -> "FakeQueryBuilder":
        # Mirrors postgrest-py's .range(start, end) — inclusive on both
        # ends, applied after ordering, exactly like the real Supabase
        # pagination this fake stands in for.
        self.range_start = start
        self.range_end = end
        return self

    def maybe_single(self) -> "FakeQueryBuilder":
        self.want_single = True
        return self

    def _matches(self, row: dict) -> bool:
        return all(_cmp_matches(row, col, op, val) for col, op, val in self.filters)

    async def execute(self) -> _Response | None:
        rows = self.db.tables[self.table_name]
        schema = TABLE_SCHEMAS[self.table_name]

        if self.mode == "select":
            result = [r for r in rows if self._matches(r)]
            if self.order_col:
                # None sorts last, and never gets compared against
                # another None's actual value (which would raise -
                # None has no ordering) - the sentinel second element
                # is only ever compared against other Nones' identical
                # sentinel, never against a real value.
                def _sort_key(r: dict) -> tuple:
                    v = r.get(self.order_col)
                    return (1, "") if v is None else (0, v)

                result = sorted(result, key=_sort_key, reverse=self.order_desc)
            if self.range_start is not None and self.range_end is not None:
                result = result[self.range_start : self.range_end + 1]
            elif self.limit_n is not None:
                result = result[: self.limit_n]
            if self.want_single:
                if len(result) == 0:
                    return None
                return _Response(data=dict(result[0]))
            return _Response(data=[dict(r) for r in result])

        if self.mode == "insert":
            arr = self.payload if isinstance(self.payload, list) else [self.payload]
            for row in arr:
                if self.db.find_unique_violation(self.table_name, row):
                    raise APIError(
                        {"message": f'duplicate key value violates unique constraint on "{self.table_name}"', "code": "23505"}
                    )
            for row in arr:
                rows.append(_with_defaults(row))
            return _Response(data=arr)

        if self.mode == "upsert":
            arr = self.payload if isinstance(self.payload, list) else [self.payload]
            conflict_cols = self.opts["on_conflict"].split(",") if self.opts.get("on_conflict") else schema["primary_key"]
            ignore_duplicates = self.opts.get("ignore_duplicates", False)
            for row in arr:
                idx = next((i for i, r in enumerate(rows) if all(r.get(c) == row.get(c) for c in conflict_cols)), None)
                if idx is not None:
                    if not ignore_duplicates:
                        rows[idx] = {**rows[idx], **row}
                else:
                    rows.append(_with_defaults(row))
            return _Response(data=arr)

        if self.mode == "update":
            count = 0
            for r in rows:
                if self._matches(r):
                    r.update(self.payload)
                    count += 1
            return _Response(data=None, count=count)

        if self.mode == "delete":
            before = len(rows)
            remaining = [r for r in rows if not self._matches(r)]
            deleted = before - len(remaining)
            # Mutate the SAME list object in place (rather than
            # rebinding self.db.tables[key] to a new list) - anything
            # holding an earlier reference to this list (as a test
            # might, for convenience) must keep seeing the true
            # current state, exactly as a real table reference would.
            rows[:] = remaining
            return _Response(data=None, count=deleted if self.want_count else None)

        raise ValueError(f"unsupported mode {self.mode}")


def _with_defaults(row: dict) -> dict:
    # Mirrors the real schema's `default now()` / `default
    # uuid_generate_v4()` columns - several tables (agent_state_history,
    # alerts, ...) never set created_at explicitly in application code,
    # relying on Postgres to fill it in. Sorting by a column that's
    # simply absent here (unlike real Postgres, which has no such gap)
    # would compare None to None and raise - so simulate the default.
    defaults = {
        "id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "merchant_id": "merchant_demo_001",
    }
    return {**defaults, **row}


class FakeSupabaseClient:
    """Matches the surface AsyncClient exposes that this codebase
    actually calls: just `.table(name)`."""

    def __init__(self) -> None:
        self.db = FakeDb()

    def table(self, name: str) -> FakeQueryBuilder:
        return self.db.table(name)

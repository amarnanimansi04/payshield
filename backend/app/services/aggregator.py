"""
PAYSHIELD — aggregator (port of the former src/lib/aggregate.ts)

Reads real event rows from Supabase, buckets them into CANONICAL,
epoch-aligned fixed windows per segment (e.g. 12:00-12:05, 12:05-12:10,
never "whenever now happens to be minus 5 minutes"), and produces the
WindowedSegmentSeries shape the (Razorpay-independent, already-tested)
detector expects.

This is the ONLY module that bridges "real ingested data" and "the
pure statistical detector" - keeping that seam explicit and narrow is
what makes the detector's correctness trustworthy for the live
pipeline too.

Empty-window handling: a canonical window with zero attempts is NOT a
0% failure rate - it's NO DATA. Such windows are excluded entirely from
the rates/sample_sizes arrays fed to the detector, never included as a
synthetic zero. This module still PERSISTS a row for every canonical
window it computes (including empty ones, with current_rate=None)
because "no data arrived in this window" is itself a fact worth
keeping in the historical record.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.core.supabase_client import get_supabase
from app.core.time_utils import to_iso
from app.models.schemas import NormalizedEvent
from app.services.detector import WindowedSegmentSeries, build_error_source_distribution

WINDOW_MINUTES = 5  # must match the GitHub Actions cron cadence
WINDOWS_OF_HISTORY = 8  # how many prior canonical windows feed the EWMA baseline


def canonical_window_end(now: datetime, window_minutes: int = WINDOW_MINUTES) -> datetime:
    """Floors `now` to the most recent canonical, epoch-aligned window
    boundary - e.g. with a 5-minute window, 12:07:32 floors to
    12:05:00. Based on UTC epoch time, not wall-clock "now minus N
    minutes" - timezone conversion happens ONLY at the presentation
    layer. Also doubles as the stable per-window identifier used for
    idempotent re-evaluation: calling this twice within the same
    5-minute epoch slot always returns the identical timestamp."""
    window_seconds = window_minutes * 60
    epoch = now.astimezone(timezone.utc).timestamp()
    floored = (epoch // window_seconds) * window_seconds
    return datetime.fromtimestamp(floored, tz=timezone.utc)


@dataclass
class CanonicalBucket:
    window_start: datetime
    window_end: datetime
    total_attempts: int = 0  # captured + failed - the real denominator
    failure_count: int = 0
    rate: float | None = None  # None means NO DATA (zero attempts)
    failed_amount_sum: int = 0  # paise, failed only
    total_amount_sum: int = 0  # paise, ALL attempts
    data_source: str | None = None  # None if empty
    events: list[NormalizedEvent] = field(default_factory=list)


def bucket_into_canonical_windows(
    events: list[NormalizedEvent],
    history_start: datetime,
    canonical_end: datetime,
    window_minutes: int = WINDOW_MINUTES,
) -> list[CanonicalBucket]:
    """Pure, I/O-free bucketing function - the testable core of the
    aggregator (tests/test_aggregator.py exercises this directly,
    without touching Supabase, exactly like the detector and decision
    engine are tested)."""
    window_seconds = window_minutes * 60
    num_windows = round((canonical_end - history_start).total_seconds() / window_seconds)
    buckets = [
        CanonicalBucket(
            window_start=history_start + timedelta(seconds=i * window_seconds),
            window_end=history_start + timedelta(seconds=(i + 1) * window_seconds),
        )
        for i in range(num_windows)
    ]

    for e in events:
        t = datetime.fromisoformat(e.created_at.replace("Z", "+00:00"))
        if t < history_start or t >= canonical_end:
            continue  # outside range
        idx = min(num_windows - 1, max(0, int((t - history_start).total_seconds() / window_seconds)))
        b = buckets[idx]
        b.total_attempts += 1  # both captured and failed count as an "attempt"
        b.total_amount_sum += e.amount
        b.events.append(e)
        if e.outcome == "failed":
            b.failure_count += 1
            b.failed_amount_sum += e.amount

    for b in buckets:
        if b.total_attempts > 0:
            b.rate = b.failure_count / b.total_attempts
            sources = {ev.source for ev in b.events}
            b.data_source = "mixed" if len(sources) > 1 else next(iter(sources))

    return buckets


@dataclass
class AggregationBundle:
    series: list[WindowedSegmentSeries]
    # every canonical bucket computed this cycle, for every segment
    # observed - including empty ones - so the caller can persist the
    # full historical record (see persist_segment_windows).
    buckets_by_segment: list[tuple[str, CanonicalBucket]]


_FETCH_PAGE_SIZE = 1000  # PostgREST's own default max-rows cap - a
# single unpaginated .execute() silently truncates at this many rows,
# with no guaranteed ordering for which rows survive the truncation.
# Confirmed via direct reproduction: with only ~1800-5400 events
# injected into a single 40-minute rolling window (routine for this
# project's own demo scenarios), an unpaginated query silently dropped
# the MOST RECENT events - exactly the ones the detector needs to see -
# while still returning 200 status with no error. Paginating with
# .range() + .order("created_at") is what actually fetches every row.


_FETCH_CONCURRENCY = 4  # pages requested per round-trip round, see below


async def _fetch_page(supabase, history_start_iso: str, canonical_end_iso: str, merchant_id: str, offset: int):
    resp = await (
        supabase.table("events")
        .select("*")
        .eq("merchant_id", merchant_id)
        .gte("created_at", history_start_iso)
        .lt("created_at", canonical_end_iso)
        .order("created_at")
        .range(offset, offset + _FETCH_PAGE_SIZE - 1)
        .execute()
    )
    return resp.data or []


async def _fetch_all_events_in_range(
    supabase, history_start: datetime, canonical_end: datetime, merchant_id: str
) -> list[dict]:
    # Same query, same filters, same order, same final row set as a
    # plain sequential loop - the only difference is that each ROUND
    # requests _FETCH_CONCURRENCY pages at once via asyncio.gather
    # instead of one page per network round-trip. gather() preserves
    # input order in its results, so pages are still concatenated in
    # the exact same offset order a sequential loop would produce.
    # Still fully correct with no known upper bound on row count: a
    # round only stops once ANY page in it comes back short, exactly
    # like the single-page version stopping on a short page - it just
    # does that check _FETCH_CONCURRENCY pages at a time instead of 1.
    history_start_iso = to_iso(history_start)
    canonical_end_iso = to_iso(canonical_end)
    all_rows: list[dict] = []
    offset = 0
    while True:
        round_offsets = [offset + i * _FETCH_PAGE_SIZE for i in range(_FETCH_CONCURRENCY)]
        pages = await asyncio.gather(
            *(
                _fetch_page(supabase, history_start_iso, canonical_end_iso, merchant_id, o)
                for o in round_offsets
            )
        )
        for page in pages:
            all_rows.extend(page)
        if any(len(page) < _FETCH_PAGE_SIZE for page in pages):
            break
        offset += _FETCH_CONCURRENCY * _FETCH_PAGE_SIZE
    return all_rows


async def build_segment_series_for_all_segments(
    merchant_id: str, now: datetime | None = None
) -> AggregationBundle:
    now = now or datetime.now(timezone.utc)
    supabase = await get_supabase()
    canonical_end = canonical_window_end(now, WINDOW_MINUTES)
    history_start = canonical_end - timedelta(minutes=WINDOWS_OF_HISTORY * WINDOW_MINUTES)

    rows = await _fetch_all_events_in_range(supabase, history_start, canonical_end, merchant_id)
    events = [NormalizedEvent(**row) for row in rows]
    by_segment: dict[str, list[NormalizedEvent]] = {}
    for e in events:
        by_segment.setdefault(e.segment_primary, []).append(e)

    series: list[WindowedSegmentSeries] = []
    buckets_by_segment: list[tuple[str, CanonicalBucket]] = []

    for segment, seg_events in by_segment.items():
        buckets = bucket_into_canonical_windows(seg_events, history_start, canonical_end, WINDOW_MINUTES)
        for bucket in buckets:
            buckets_by_segment.append((segment, bucket))

        # Empty windows (rate is None) are EXCLUDED entirely from the
        # series - never folded in as a synthetic 0%. Need at least 2
        # real (non-empty) windows to form an EWMA baseline plus one
        # test point.
        non_empty = [b for b in buckets if b.rate is not None]
        if len(non_empty) < 2:
            continue

        final_bucket = non_empty[-1]
        final_window_failures = [e for e in final_bucket.events if e.outcome == "failed"]

        series.append(
            WindowedSegmentSeries(
                segment=segment,
                window_start=to_iso(final_bucket.window_start),
                window_end=to_iso(final_bucket.window_end),
                rates=[b.rate for b in non_empty],  # type: ignore[misc]
                sample_sizes=[b.total_attempts for b in non_empty],
                final_window_failed_amount=final_bucket.failed_amount_sum,
                final_window_total_amount=final_bucket.total_amount_sum,
                final_window_failure_count=final_bucket.failure_count,
                error_source_distribution=build_error_source_distribution(final_window_failures),
                data_source=final_bucket.data_source or "live",
            )
        )

    return AggregationBundle(series=series, buckets_by_segment=buckets_by_segment)


async def persist_segment_windows(
    buckets_by_segment: list[tuple[str, CanonicalBucket]],
    merchant_id: str,
) -> str | None:
    """Persists every canonical window computed this cycle - including
    empty ones (current_rate stored as None, never 0). Upserts on
    (segment, window_end), so re-running the cycle on unchanged data is
    idempotent.

    Deliberately does NOT include baseline_rate/sigma_deviation/
    is_significant here - those are written separately, only for the
    specific window that was actually tested this cycle (see
    services/agent.py), so this bulk upsert never clobbers enrichment
    written for an older window in an earlier cycle."""
    if not buckets_by_segment:
        return None
    supabase = await get_supabase()
    rows = [
        {
            "segment": segment,
            "merchant_id": merchant_id,
            "window_start": to_iso(bucket.window_start),
            "window_end": to_iso(bucket.window_end),
            "event_count": bucket.total_attempts,
            "failure_count": bucket.failure_count,
            "current_rate": bucket.rate,
            "total_transaction_amount": bucket.total_amount_sum,
            "failed_transaction_amount": bucket.failed_amount_sum,
            "data_source": bucket.data_source or "live",
        }
        for segment, bucket in buckets_by_segment
    ]
    try:
        await supabase.table("segment_windows").upsert(rows, on_conflict="segment,window_end").execute()
        return None
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any
        # Postgrest error here should degrade to "reported", not crash
        # the whole detection cycle.
        return str(exc)

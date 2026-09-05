"""
PAYSHIELD — regression test for a real bug found via live testing.

`_fetch_all_events_in_range` (services/aggregator.py) exists because an
earlier, unpaginated `.execute()` call silently truncated results at
PostgREST's default 1000-row response cap, with no guaranteed
ordering for which rows survived the truncation. Reproduced directly
against a live Supabase project: a routine demo scenario injects
1800-5400 events into a single 40-minute rolling window (comfortably
over 1000), and the detector silently lost visibility into the most
recent - and specifically the anomalous - windows as a result, with no
error raised anywhere. This test seeds more than one page's worth of
rows and confirms every single one is retrieved, not just the first
page.
"""

import asyncio
from datetime import datetime, timedelta, timezone

from app.services.aggregator import _fetch_all_events_in_range, _FETCH_PAGE_SIZE
from app.core.time_utils import to_iso
from tests.fake_supabase import FakeSupabaseClient


def _make_event(i: int, created_at: str) -> dict:
    return {
        "event_id": f"evt_{i}",
        "razorpay_event_id": None,
        "payment_id": f"pay_{i}",
        "outcome": "captured",
        "created_at": created_at,
        "method": "upi",
        "bank": None,
        "vpa": "user@okhdfcbank",
        "vpa_derived_bank_label": None,
        "card_issuer": None,
        "card_network": None,
        "error_code": None,
        "error_source": None,
        "error_reason": None,
        "error_step": None,
        "amount": 10000,
        "segment_primary": "upi",
        "segment_bank": None,
        "ingested_at": created_at,
        "source": "constructed",
        "merchant_id": "merchant_demo_001",
    }


def test_fetch_all_events_in_range_retrieves_more_than_one_page():
    # Deliberately does NOT touch the shared global Supabase-client
    # singleton (no set_supabase_client_for_testing call) - this fake
    # is passed directly as the `supabase` argument instead, since
    # _fetch_all_events_in_range takes it as an explicit parameter.
    # Mutating the global singleton here would leak across test files
    # that run in the same session (a real isolation bug caught while
    # writing this test: test_integration.py's own fake client got
    # silently replaced by an earlier version of this test).
    fake = FakeSupabaseClient()

    history_start = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    canonical_end = datetime(2026, 1, 1, 12, 40, tzinfo=timezone.utc)

    # Comfortably more than one _FETCH_PAGE_SIZE (1000) worth of rows,
    # spread evenly across the query's time range.
    total_events = int(_FETCH_PAGE_SIZE * 2.5)
    assert total_events > _FETCH_PAGE_SIZE  # sanity-check the test itself isn't vacuous

    span_seconds = (canonical_end - history_start).total_seconds()
    rows = [
        _make_event(i, to_iso(history_start + timedelta(seconds=(i / total_events) * span_seconds)))
        for i in range(total_events)
    ]
    fake.db.tables["events"].extend(rows)

    fetched = asyncio.run(
        _fetch_all_events_in_range(fake, history_start, canonical_end, "merchant_demo_001")
    )

    assert len(fetched) == total_events, (
        f"expected all {total_events} rows to be retrieved via pagination, got {len(fetched)} — "
        f"a regression here means the 1000-row PostgREST cap is silently truncating results again"
    )
    # The most recent row specifically must be present - this is the
    # exact failure mode that broke detection: the newest (most
    # relevant) events being the ones silently dropped.
    assert any(r["event_id"] == f"evt_{total_events - 1}" for r in fetched)

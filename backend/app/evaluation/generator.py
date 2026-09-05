"""
PAYSHIELD — synthetic event generator (port of the former src/lib/generator.ts)

Supports two generation modes, matching the original TypeScript
design's disclosed methodology:

  "live"        - events triggered through real Razorpay test-mode
                   transactions, with whatever error_code Razorpay
                   actually assigns.
  "constructed" - rows built directly using Razorpay's REAL,
                   DOCUMENTED error_code/error_source/error_reason
                   taxonomy (not invented values), with synthetic
                   timing/volume/amount.

Either way: transaction VOLUME and any injected ANOMALY are always
synthetic, by design - that's true regardless of which mode produced
the underlying failure-reason taxonomy.

Uses numpy's default_rng (PCG64) for randomness - a proper, modern
generator with no float-precision failure modes. (The project's
TypeScript version originally used a hand-rolled LCG that silently
overflowed JS's safe-integer range and produced ~48% duplicate values
- a real bug found via integration testing there. numpy's default_rng
cannot repeat that failure mode: it never routes random state through
a float multiplication that could lose precision.)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

from app.models.schemas import ErrorSource, PaymentMethod, RazorpayPaymentEvent


@dataclass
class ErrorTaxonomyEntry:
    error_code: str
    error_description: str
    error_source: ErrorSource
    error_reason: str
    error_step: str
    applicable_methods: list[PaymentMethod]


# Razorpay's own documented error taxonomy - not exhaustive, just the
# subset relevant to PayShield's segmentation (method x error_source),
# each one a real, named Razorpay decline category.
RAZORPAY_ERROR_TAXONOMY: list[ErrorTaxonomyEntry] = [
    ErrorTaxonomyEntry(
        "BAD_REQUEST_ERROR",
        "Payment failed due to insufficient funds in the customer's account.",
        "customer", "insufficient_funds", "payment_authorization",
        ["upi", "netbanking", "card"],
    ),
    ErrorTaxonomyEntry(
        "GATEWAY_ERROR", "The issuing bank declined the transaction.",
        "bank", "payment_declined", "payment_authorization",
        ["upi", "netbanking", "card"],
    ),
    ErrorTaxonomyEntry(
        "GATEWAY_ERROR", "The bank's servers timed out while processing the request.",
        "bank", "gateway_timeout", "payment_authorization",
        ["upi", "netbanking"],
    ),
    ErrorTaxonomyEntry(
        "BAD_REQUEST_ERROR", "Card has expired.",
        "customer", "card_expired", "payment_authorization",
        ["card"],
    ),
    ErrorTaxonomyEntry(
        "SERVER_ERROR", "Internal processing error on Razorpay's side.",
        "internal", "internal_error", "payment_initiation",
        ["upi", "netbanking", "card", "wallet"],
    ),
    ErrorTaxonomyEntry(
        "BAD_REQUEST_ERROR", "Customer declined / cancelled the authentication.",
        "customer", "payment_cancelled", "payment_authentication",
        ["upi", "card", "netbanking"],
    ),
]

SYNTHETIC_BANK_CODES = ["UTIB", "ICIC", "HDFC", "SBIN", "PUNB"]
SYNTHETIC_VPA_HANDLES = ["okhdfcbank", "okicici", "oksbi", "okaxis", "ybl"]

_ID_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"


@dataclass
class GenerateBatchOptions:
    method: PaymentMethod
    total_attempts: int  # captured + failed - the real denominator
    failure_rate: float  # 0..1
    failure_source_mix: dict[ErrorSource, float]
    rng: np.random.Generator
    window_start: datetime
    window_end: datetime
    mode: str  # "live" | "constructed"
    bank: str | None = None


def generate_synthetic_batch(opts: GenerateBatchOptions) -> list[RazorpayPaymentEvent]:
    """Generates a realistic batch of BOTH captured and failed events
    for one segment/window, so the resulting data has a real,
    computable denominator."""
    failure_count = round(opts.total_attempts * opts.failure_rate)
    captured_count = opts.total_attempts - failure_count
    span_seconds = (opts.window_end - opts.window_start).total_seconds()

    mix_items = list(opts.failure_source_mix.items())
    total_mix_weight = sum(w for _, w in mix_items) or 1.0

    def pick_error_source() -> ErrorSource:
        r = opts.rng.random() * total_mix_weight
        for source, weight in mix_items:
            if r < weight:
                return source
            r -= weight
        return mix_items[-1][0] if mix_items else "bank"

    def taxonomy_for(source: ErrorSource) -> ErrorTaxonomyEntry:
        candidates = [
            e for e in RAZORPAY_ERROR_TAXONOMY if e.error_source == source and opts.method in e.applicable_methods
        ]
        if not candidates:
            raise ValueError(
                f"No taxonomy entry for method={opts.method} error_source={source} - add one to "
                f"RAZORPAY_ERROR_TAXONOMY rather than inventing values inline."
            )
        return candidates[opts.rng.integers(0, len(candidates))]

    def base_fields() -> dict:
        ts = opts.window_start + timedelta(seconds=opts.rng.random() * span_seconds)
        random_id = "".join(_ID_CHARS[i] for i in opts.rng.integers(0, len(_ID_CHARS), size=14))
        return {
            "payment_id": f"pay_synthetic_{random_id}",
            "order_id": None,
            "method": opts.method,
            "amount": 5000 + int(opts.rng.integers(0, 495000)),  # paise: Rs50-Rs5000
            "currency": "INR",
            "created_at": int(ts.timestamp()),
            "bank": (opts.bank or SYNTHETIC_BANK_CODES[opts.rng.integers(0, len(SYNTHETIC_BANK_CODES))])
            if opts.method == "netbanking"
            else None,
            "vpa": (
                f"user{int(opts.rng.integers(0, 10000))}@"
                f"{SYNTHETIC_VPA_HANDLES[opts.rng.integers(0, len(SYNTHETIC_VPA_HANDLES))]}"
            )
            if opts.method == "upi"
            else None,
            "card_issuer": SYNTHETIC_BANK_CODES[opts.rng.integers(0, len(SYNTHETIC_BANK_CODES))]
            if opts.method == "card"
            else None,
            "card_network": ["Visa", "Mastercard", "RuPay"][opts.rng.integers(0, 3)] if opts.method == "card" else None,
        }

    events: list[RazorpayPaymentEvent] = []
    for _ in range(captured_count):
        events.append(
            RazorpayPaymentEvent(
                **base_fields(), outcome="captured", error_code=None, error_description=None,
                error_source=None, error_step=None, error_reason=None,
            )
        )
    for _ in range(failure_count):
        source = pick_error_source()
        entry = taxonomy_for(source)
        events.append(
            RazorpayPaymentEvent(
                **base_fields(), outcome="failed", error_code=entry.error_code,
                error_description=entry.error_description, error_source=entry.error_source,
                error_step=entry.error_step, error_reason=entry.error_reason,
            )
        )
    return events


def seeded_rng(seed: int) -> np.random.Generator:
    """Deterministic seeded RNG (numpy PCG64 via default_rng) - used
    everywhere in generation so every batch is reproducible, which
    matters for an evaluation that needs to be re-run and compared,
    not just eyeballed once."""
    return np.random.default_rng(seed)

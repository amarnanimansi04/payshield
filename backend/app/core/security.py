"""
PAYSHIELD — webhook signature verification + cron auth

Port of the former src/lib/webhookSignature.ts. Razorpay signs webhook
payloads with HMAC-SHA256 using the webhook secret configured in the
Dashboard (a DIFFERENT secret from the API key/secret pair), sent in
the `X-Razorpay-Signature` header.

Pure function - no I/O - deliberately, so it's unit-testable without a
live Razorpay account (see tests/test_security.py).
"""

import hmac
import hashlib


def verify_razorpay_signature(
    raw_body: bytes, signature_header: str | None, webhook_secret: str
) -> bool:
    if not signature_header:
        return False
    expected = hmac.new(
        webhook_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    # Constant-time comparison - a naive == on a security signature is
    # a timing-attack surface; hmac.compare_digest avoids it.
    return hmac.compare_digest(expected, signature_header)


def verify_cron_bearer(auth_header: str | None, expected_secret: str) -> bool:
    if not expected_secret:
        return False
    return auth_header == f"Bearer {expected_secret}"

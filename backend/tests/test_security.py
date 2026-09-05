"""
PAYSHIELD — webhook signature tests (port of scripts/verify-webhook-signature.ts)
"""

import hmac
import hashlib

from app.core.security import verify_razorpay_signature


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature_is_accepted():
    body = b'{"event":"payment.failed"}'
    secret = "whsec_test"
    assert verify_razorpay_signature(body, _sign(body, secret), secret) is True


def test_tampered_body_is_rejected():
    body = b'{"event":"payment.failed"}'
    secret = "whsec_test"
    sig = _sign(body, secret)
    tampered = b'{"event":"payment.captured"}'
    assert verify_razorpay_signature(tampered, sig, secret) is False


def test_wrong_secret_is_rejected():
    body = b'{"event":"payment.failed"}'
    sig = _sign(body, "whsec_correct")
    assert verify_razorpay_signature(body, sig, "whsec_wrong") is False


def test_missing_signature_header_is_rejected():
    body = b'{"event":"payment.failed"}'
    assert verify_razorpay_signature(body, None, "whsec_test") is False


def test_garbage_signature_is_rejected():
    body = b'{"event":"payment.failed"}'
    assert verify_razorpay_signature(body, "not-a-real-signature", "whsec_test") is False

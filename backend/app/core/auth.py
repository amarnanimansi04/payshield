"""
PAYSHIELD — merchant resolution / authentication

FastAPI dependency that resolves "which merchant is this request for".

Two modes, controlled by the `REQUIRE_AUTH` setting (default: False):

  REQUIRE_AUTH=false (today's default — the existing demo, unchanged):
    Every request resolves to the single demo merchant
    ("merchant_demo_001"). No Authorization header is required. This
    is the exact behavior the app already had before this pass; it
    stays the default specifically so nothing about the existing,
    working demo changes unless this flag is deliberately flipped on.

  REQUIRE_AUTH=true (production posture):
    Requires a valid Supabase Auth JWT in the `Authorization: Bearer
    <token>` header. The token is verified against Supabase itself
    (not decoded locally), then the corresponding merchant is looked
    up via the `merchant_users` table. Requests with a missing,
    invalid, or unmapped token get a 401 - never silently fall back to
    the demo merchant once this flag is on.

This is a clearly-separated demo/production toggle (frozen spec /
hardening pass requirement), not a permanent bypass: flipping
REQUIRE_AUTH on is the whole point of building this module, and
requires zero code changes elsewhere - every route that depends on
`get_current_merchant` immediately starts enforcing real auth.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from app.core.config import get_settings
from app.core.supabase_client import get_supabase

DEMO_MERCHANT_ID = "merchant_demo_001"


async def get_current_merchant(request: Request) -> str:
    settings = get_settings()

    if not settings.require_auth:
        return DEMO_MERCHANT_ID

    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing or malformed Authorization header")
    token = auth_header[len("Bearer "):].strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")

    supabase = await get_supabase()

    try:
        user_response = await supabase.auth.get_user(token)
    except Exception as exc:  # noqa: BLE001 - any verification failure is an auth failure
        raise HTTPException(status_code=401, detail="invalid or expired session") from exc

    if not user_response or not user_response.user:
        raise HTTPException(status_code=401, detail="invalid or expired session")

    user_id = user_response.user.id

    mapping = (
        await supabase.table("merchant_users")
        .select("merchant_id")
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    if not mapping or not mapping.data:
        raise HTTPException(status_code=403, detail="this account is not linked to a merchant")

    return mapping.data["merchant_id"]

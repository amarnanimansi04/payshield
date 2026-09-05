"""
PAYSHIELD — Supabase client

Reads exclusively from environment variables (via core/config.py) -
never hardcode a key here. This route layer uses the service-role key
(full access, required to write agent_state/alerts) - nothing in this
project uses a client-side Supabase call, so the anon key is never
needed at all; the Next.js dashboard never talks to Supabase directly,
only to this API.
"""

from __future__ import annotations

import asyncio
from typing import Any

from supabase import AsyncClient, create_async_client

from app.core.config import get_settings

_client: AsyncClient | None = None
_lock = asyncio.Lock()


async def get_supabase() -> AsyncClient:
    global _client
    if _client is not None:
        return _client
    async with _lock:
        if _client is not None:
            return _client
        settings = get_settings()
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set. See .env.example."
            )
        _client = await create_async_client(
            settings.supabase_url, settings.supabase_service_role_key
        )
        return _client


def set_supabase_client_for_testing(fake: Any) -> None:
    """TEST-ONLY seam. Lets an integration test inject an in-memory
    fake client implementing the same chainable subset of the
    supabase-py API this codebase actually uses, so the REAL route
    handlers and services can be exercised end-to-end without a live
    Supabase project or credentials. Never called from application
    code - application code only ever calls get_supabase()."""
    global _client
    _client = fake

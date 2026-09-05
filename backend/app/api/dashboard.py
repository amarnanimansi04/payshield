"""
PAYSHIELD — dashboard + scheduled-detection endpoints (port of the
former src/app/api/dashboard/state, dashboard/run-cycle, and
cron/detect route.ts files)
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.core.auth import DEMO_MERCHANT_ID, get_current_merchant
from app.core.config import get_settings
from app.core.security import verify_cron_bearer
from app.core.supabase_client import get_supabase
from app.services.agent import run_agent_loop_cycle

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/dashboard/state")
async def dashboard_state(merchant_id: str = Depends(get_current_merchant)) -> JSONResponse:
    """The browser never talks to Supabase directly (no anon key is
    used anywhere in this project). This route is the one read-only
    surface the dashboard polls."""
    try:
        supabase = await get_supabase()

        states, recent_history, recent_alerts, recent_windows, recovery_policies, latest_evaluation = (
            await asyncio.gather(
                supabase.table("agent_state").select("*").eq("merchant_id", merchant_id).order("updated_at", desc=True).execute(),
                supabase.table("agent_state_history").select("*").eq("merchant_id", merchant_id).order("created_at", desc=True).limit(30).execute(),
                supabase.table("alerts").select("*").eq("merchant_id", merchant_id).order("created_at", desc=True).limit(5).execute(),
                supabase.table("segment_windows").select("*").eq("merchant_id", merchant_id).order("window_end", desc=True).limit(200).execute(),
                supabase.table("recovery_policy").select("*").eq("merchant_id", merchant_id).execute(),
                supabase.table("evaluation_runs").select("*").order("created_at", desc=True).limit(1).maybe_single().execute(),
            )
        )

        return JSONResponse(
            {
                "states": states.data or [],
                "recentHistory": recent_history.data or [],
                "recentAlerts": recent_alerts.data or [],
                "recentWindows": recent_windows.data or [],
                "recoveryPolicies": recovery_policies.data or [],
                "latestEvaluation": (latest_evaluation.data if latest_evaluation else None),
            }
        )
    except Exception:  # noqa: BLE001 - never leak internals (stack
        # traces, connection strings) to the client - log server-side,
        # return a generic message.
        logger.exception("Failed to load dashboard state")
        return JSONResponse({"error": "failed to load dashboard state"}, status_code=500)


@router.post("/api/dashboard/run-cycle")
async def dashboard_run_cycle(merchant_id: str = Depends(get_current_merchant)) -> JSONResponse:
    """Same-origin convenience so the dashboard's "Run detection cycle
    now" button never needs a client-exposed secret. This route is
    intentionally NOT the same as /api/cron/detect: that one stays
    secret-protected because it's invoked by an external caller
    (GitHub Actions) over the public internet. This one is a
    same-origin convenience for demo pacing; the worst-case abuse (a
    stranger repeatedly triggering your own public demo's detection
    cycle) costs a few Supabase reads/writes and, at most, one
    templated/LLM alert per genuine NEW state transition - never a
    financial action, and idempotent against replays of unchanged data."""
    try:
        result = await run_agent_loop_cycle(merchant_id)
        return JSONResponse(result.to_dict())
    except Exception as exc:  # noqa: BLE001
        logger.exception("Manual detection cycle trigger failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/cron/detect")
async def cron_detect(request: Request) -> JSONResponse:
    """Called by GitHub Actions on a 5-minute cadence. Protected by a
    shared secret so this can't be triggered by an arbitrary request -
    cheap but necessary given it writes agent state and can trigger an
    LLM call.

    Not merchant-scoped by a request-time identity (there's no logged-in
    user on a server-to-server cron call) - runs for the one demo
    merchant, same as every other still-single-tenant-in-practice path
    (the webhook included). Documented in README.md as the next thing to
    generalize once there's more than one onboarded merchant."""
    settings = get_settings()
    auth = request.headers.get("authorization")
    if not settings.cron_secret or not verify_cron_bearer(auth, settings.cron_secret):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        result = await run_agent_loop_cycle(DEMO_MERCHANT_ID)
        return JSONResponse(result.to_dict())
    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent loop cycle failed")
        return JSONResponse({"error": str(exc)}, status_code=500)

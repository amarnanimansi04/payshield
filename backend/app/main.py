"""
PAYSHIELD — FastAPI backend entrypoint

Architecture (unchanged from the frozen spec, just re-hosted):

  Razorpay -> webhook ingestion (this app) -> Supabase
  GitHub Actions (5-min cron) -> /api/cron/detect (this app) ->
      aggregator -> detector -> decision engine -> agent (recovery
      policy + audit trail + alert) -> Supabase
  Next.js dashboard -> /api/dashboard/state, /api/dashboard/run-cycle,
      /api/demo/scenario, /api/demo/reset (this app)

The LLM (Groq) is called in exactly one place (services/alert_text.py),
only to generate merchant-facing alert TEXT, only after a decision has
already been made deterministically. It never has authority over what
action fires, whether an action fires, or any financial API.
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import dashboard, demo, razorpay_webhook
from app.core.config import get_settings
from app.core.rate_limit import RateLimitMiddleware

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="PayShield API", version="1.0.0")

settings = get_settings()

# Middleware order: added first = innermost. Rate limiting is added
# before CORS so CORS ends up as the outer layer (standard pattern -
# browser preflight/OPTIONS requests are handled by CORS without being
# subject to the rate limiter).
app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.rate_limit_per_minute)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(razorpay_webhook.router)
app.include_router(dashboard.router)
app.include_router(demo.router)


@app.get("/health")
async def health() -> dict:
    """Cheap liveness check - also useful for free-tier hosts that
    ping an endpoint to keep the service warm."""
    return {"ok": True, "service": "payshield-api"}

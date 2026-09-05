"""
PAYSHIELD — lightweight rate limiting

A simple in-memory sliding-window limiter per client IP. No new
dependency, no Redis, no external service — appropriate for a
single-process hackathon deployment. Generous default limits: this
exists to block obvious abuse (e.g. someone hammering the demo-inject
endpoint), not to shape legitimate traffic, and is deliberately loose
enough that it will never interfere with the actual demo flow (a human
clicking buttons, or GitHub Actions' 5-minute cron).

Documented limitation (see README's Scalability section): this is
per-process, in-memory state — it resets on every restart and doesn't
share state across multiple backend instances. That's fine for a
single Render/Fly instance today; a real production deployment behind
a load balancer would move this to a shared store (Redis, or the
edge/CDN's own rate limiting) instead. This module is intentionally
structured as a single, swappable dependency so that swap is
localized to one file when it's actually needed.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, requests_per_minute: int = 120):
        super().__init__(app)
        self.limit = requests_per_minute
        self.window_seconds = 60.0
        self._hits: dict[str, deque] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):
        # /health is exempt — free-tier hosts and uptime checks ping it
        # frequently, and it does no work worth throttling.
        if request.url.path == "/health":
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        hits = self._hits[client_ip]

        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()

        if len(hits) >= self.limit:
            return JSONResponse(
                {"error": "rate limit exceeded, try again shortly"},
                status_code=429,
            )

        hits.append(now)
        return await call_next(request)

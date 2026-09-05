"""
PAYSHIELD — settings

Reads exclusively from environment variables - never hardcode a
secret here. Every value here is SERVER-ONLY; the frontend never sees
any of these (it only calls this API's endpoints).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = ""
    supabase_service_role_key: str = ""

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    groq_api_key: str = ""

    cron_secret: str = ""

    # CORS: the Next.js dashboard's origin(s), comma-separated. Set
    # this to your deployed Vercel URL(s) plus http://localhost:3000
    # for local dev.
    allowed_origins: str = "http://localhost:3000"

    # Auth toggle (see core/auth.py). Defaults to False so every
    # existing route behaves EXACTLY as it did before this setting
    # existed - flipping it on is what actually turns on Supabase-Auth
    # enforcement, with zero other code changes required.
    require_auth: bool = False

    # Rate limiting (see core/rate_limit.py) - requests per minute per
    # client IP. Generous by default; deliberately loose enough to
    # never interfere with normal demo usage or the 5-minute cron.
    rate_limit_per_minute: int = 120

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

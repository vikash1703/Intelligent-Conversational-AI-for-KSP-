import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # ... purane variables ...
    ZOHO_ORG_ID: str = os.getenv("ZOHO_ORG_ID")
    ZOHO_PROJECT_ID: str = os.getenv("ZOHO_PROJECT_ID")
    ZOHO_RAG_ENDPOINT: str = os.getenv("ZOHO_RAG_ENDPOINT")
    ZOHO_DOCUMENT_IDS: list = os.getenv("ZOHO_DOCUMENT_IDS", "").split(",")
    ZIA_TRANSLATE_ENDPOINT: str = os.getenv("ZIA_TRANSLATE_ENDPOINT")

    # Catalyst defaults to the Production/Live environment if this header is omitted —
    # our real 30-table schema lives in Development, so this must be sent on every call.
    # env var NAME renamed off "CATALYST_ENVIRONMENT" (2nd reserved-keyword
    # 400 fix attempt, 2026-08-30 — the ONE var Catalyst is confirmed to
    # reserve, X_ZOHO_CATALYST_LISTEN_PORT, shares this exact "CATALYST_"
    # prefix, making this the next strongest suspect after the OAuth triad
    # rename didn't clear the error alone). settings.CATALYST_ENVIRONMENT
    # attribute name unchanged.
    CATALYST_ENVIRONMENT: str = os.getenv("APP_CATALYST_ENV", "Development")

    # Naye variables (jo missing hain) — env var NAMES renamed off the
    # ZOHO_CLIENT_ID/ZOHO_CLIENT_SECRET/ZOHO_REFRESH_TOKEN Catalyst reserves
    # (Catalyst AppSail deploy 400s with "environment_variables must not
    # contain reserved keywords" — these three exact names collide with
    # Zoho's own standard self-client OAuth env var convention, which
    # Catalyst's platform blocks a customer from setting directly). The
    # Python attribute names below are UNCHANGED (settings.ZOHO_CLIENT_ID
    # etc. still works everywhere) — only the .env/app-config.json key each
    # one reads from is renamed.
    ZOHO_CLIENT_ID: str = os.getenv("ZIA_OAUTH_CLIENT_ID")
    ZOHO_CLIENT_SECRET: str = os.getenv("ZIA_OAUTH_CLIENT_SECRET")
    ZOHO_REFRESH_TOKEN: str = os.getenv("ZIA_OAUTH_REFRESH_TOKEN")

    # Auth / JWT
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))

    # Catalyst Data Store table names (governance tables, separate from the FIR schema)
    APP_USER_TABLE: str = os.getenv("APP_USER_TABLE", "AppUser")
    AUDIT_LOG_TABLE: str = os.getenv("AUDIT_LOG_TABLE", "AuditLog")
    CHAT_HISTORY_TABLE: str = os.getenv("CHAT_HISTORY_TABLE", "ChatHistory")

    # Voice I/O (peripheral, non-Catalyst layer — see services/voice_service.py)
    ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY")

    # Automatic-failover fallback LLM provider (chat/llm_provider.py) — Zia
    # stays primary; this is only ever used when Zia's circuit breaker trips
    # or a specific task prefers whichever provider is currently faster (see
    # chat/circuit_breaker.py). Read from the environment only — if unset,
    # GroqProvider.available() is False and the app behaves exactly as
    # Zia-only (this is the expected state until a real key is supplied; see
    # .env.example).
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY")
    # llama-3.3-70b-versatile was decommissioned by Groq (live-verified 2026-08-22:
    # every call 404'd "model_not_found" — see chat/llm_provider.py's module
    # docstring). openai/gpt-oss-20b confirmed live against Groq's own
    # /openai/v1/models listing and a real completions call before switching.
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

    # Second failover provider (chat/llm_provider.py) — Google AI Studio's free
    # tier for Gemini Flash. Unlike Groq, Gemini is trusted for user-facing
    # answer composition in any language (including Hindi/Kannada) and is the
    # ONLY fallback in that chain — Groq never composes answers, only handles
    # classification/entity-extraction. Read from the environment only; if
    # unset, GeminiProvider.available() is False and composition falls back to
    # Zia-only (same "unset key = feature inert" convention as GROQ_API_KEY).
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY")
    # Google partitions free-tier quota PER MODEL NAME, not per key/project —
    # live-verified 2026-07-23: "gemini-2.0-flash" had a ZERO quota (a
    # 429 with "limit: 0"), "gemini-flash-latest" (resolves to
    # gemini-3.6-flash) worked but only allows ~20 free requests/day and was
    # exhausted during this same session's testing, while
    # "gemini-flash-lite-latest" — untouched, a different model bucket —
    # still had headroom. If this default ever starts 429ing with
    # "RESOURCE_EXHAUSTED", try a different `models/*-latest` alias from
    # GET https://generativelanguage.googleapis.com/v1beta/models before
    # assuming the integration itself is broken — each is a genuinely
    # separate quota pool on the free tier.
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")

settings = Settings()
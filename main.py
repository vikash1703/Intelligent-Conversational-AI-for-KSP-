import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routers.auth import router as auth_router
from api.routers.cases import router as cases_router
from api.routers.chat import router as chat_router
from api.routers.translate import router as translate_router
from api.routers.report import router as report_router
from api.routers.network import router as network_router
from api.routers.analytics import router as analytics_router
from api.routers.scoring import router as scoring_router
from api.routers.insights import router as insights_router
from api.routers.financial import router as financial_router
from api.routers.voice import router as voice_router
from api.routers.social import router as social_router
from api.routers.legal import router as legal_router
from core.middleware import AuditLogMiddleware
from core.exceptions import AppException, app_exception_handler
from chat.answer_cache import prewarm as prewarm_answer_cache
from services.legal_kb_service import prewarm_ksp_stats

app = FastAPI(title="KSP Chatbot API")

# 1. Global Exception Handler
app.add_exception_handler(AppException, app_exception_handler)

# 2. Middleware Registration
# Vite's dev server default port — the frontend lives in a separate origin, so the
# browser blocks every request here without this. Local dev origins always stay
# allowed; ALLOWED_ORIGINS (added 2026-07-24, comma-separated) adds the real
# deployed frontend URL(s) on top for production — e.g.
# ALLOWED_ORIGINS=https://ksp-sahay.example.com. Never set to "*" alongside
# allow_credentials=True; browsers reject that combination outright, and even
# if they didn't, it would defeat the point of an origin allowlist entirely.
_default_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
_extra_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
# CORSMiddleware must be added LAST so it ends up outermost in the stack —
# Starlette applies middleware in reverse registration order, and a
# BaseHTTPMiddleware subclass (AuditLogMiddleware) wrapping CORSMiddleware
# instead of the other way around causes duplicate Access-Control-* response
# headers (a known Starlette interaction). Browsers reject responses with
# duplicate CORS headers outright — curl doesn't care and shows them as
# fine, which made this invisible to curl-based testing.
app.add_middleware(AuditLogMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_default_origins + _extra_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. Router Registration
app.include_router(auth_router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(cases_router, prefix="/api/v1/cases", tags=["Cases"])
app.include_router(chat_router, prefix="/api/v1/chat", tags=["Chat"])
app.include_router(translate_router, prefix="/api/v1/translate", tags=["Translation"])
app.include_router(report_router, prefix="/api/v1/report", tags=["Report"])
app.include_router(network_router, prefix="/api/v1/network", tags=["Network"])
app.include_router(analytics_router, prefix="/api/v1/analytics", tags=["Analytics"])
app.include_router(scoring_router, prefix="/api/v1/scoring", tags=["Scoring"])
app.include_router(insights_router, prefix="/api/v1/insights", tags=["Insights"])
app.include_router(financial_router, prefix="/api/v1/financial", tags=["Financial"])
app.include_router(voice_router, prefix="/api/v1/voice", tags=["Voice"])
app.include_router(social_router, prefix="/api/v1/social", tags=["Social Insights"])
app.include_router(legal_router, prefix="/api/v1/legal", tags=["Legal Reference"])

@app.on_event("startup")
def _prewarm_caches():
    # Every IPC/BNS section + procedure question, pre-computed against the
    # deterministic legal KB (no Zia calls involved) so those answers are
    # instant from the first real request on, not just after someone happens
    # to ask them once. See chat/answer_cache.py's module docstring.
    prewarm_answer_cache()
    # Real per-crime-type Karnataka stats (count/top-district/example case)
    # injected into legal-KB section answers (added 2026-07-23) — computed
    # once here so the FIRST real request never pays this query cost either.
    # Also happens as a side effect of prewarm_answer_cache() above (which
    # already asks every IPC section question, including the 4 mapped ones)
    # — called explicitly too so this stays warm even if that prewarm list
    # ever changes.
    prewarm_ksp_stats()


@app.get("/")
def home():
    return {"message": "KSP Chatbot API is running successfully!"}
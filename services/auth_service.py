import logging
import time

import requests
from core.config import settings

logger = logging.getLogger("TokenManager")

# Refresh this many seconds before actual expiry, to absorb request latency
_EXPIRY_SAFETY_MARGIN_SECONDS = 60


class TokenManager:
    _access_token = None
    _expires_at = 0  # epoch seconds

    @classmethod
    def get_token(cls):
        # Reuse the cached access token until it's close to expiring — Zoho access
        # tokens are valid ~1 hour, and refreshing on every call trips their rate limiter.
        if cls._access_token and time.time() < cls._expires_at:
            return cls._access_token

        url = "https://accounts.zoho.in/oauth/v2/token"
        payload = {
            "refresh_token": settings.ZOHO_REFRESH_TOKEN,
            "client_id": settings.ZOHO_CLIENT_ID,
            "client_secret": settings.ZOHO_CLIENT_SECRET,
            "grant_type": "refresh_token"
        }

        response = requests.post(url, data=payload, timeout=15)
        data = response.json()

        if "access_token" not in data:
            logger.error(f"Failed to refresh Zoho token: {data}")
            raise Exception(f"Failed to refresh token: {data}")

        cls._access_token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        cls._expires_at = time.time() + expires_in - _EXPIRY_SAFETY_MARGIN_SECONDS

        return cls._access_token
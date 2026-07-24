import time
import logging
import jwt
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("AuditLogger")


def _extract_username(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return "anonymous"
    token = auth_header.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload.get("sub", "anonymous")
    except jwt.PyJWTError:
        return "anonymous"


class AuditLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()

        # Request details nikalna
        client_ip = request.client.host if request.client else "Unknown"
        method = request.method
        url = request.url.path
        username = _extract_username(request)

        # 1. Request aane par log karo
        logger.info(f"Incoming Request: {method} {url} | IP: {client_ip} | User: {username}")

        try:
            # 2. Request ko aage process hone do (Routers ke paas)
            response = await call_next(request)

            # 3. Response aane ke baad time aur status log karo
            process_time = time.time() - start_time
            logger.info(f"Completed Request: {method} {url} | Status: {response.status_code} | Time: {process_time:.4f}s")

            # Custom header add kar rahe hain (Industry standard practice)
            response.headers["X-Process-Time"] = str(process_time)

            return response

        except Exception as e:
            # Agar API crash hoti hai toh error log karo
            process_time = time.time() - start_time
            logger.error(f"Failed Request: {method} {url} | Error: {str(e)} | Time: {process_time:.4f}s")
            raise e
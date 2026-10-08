import time
import random
import asyncio
from datetime import datetime, timezone
from collections import defaultdict
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.database import SessionLocal
from app.models import ApiLog, SystemSettings, OAuthToken
from app.services.api_serializer import api_error

# In-memory token request tracker for 100 req/min rate limiting
# token_str -> list of timestamps
RATE_LIMIT_STORE = defaultdict(list)

class ExternalApiMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Only apply outage/slow/flaky/rate-limit middleware to external /v2/ data endpoints
        if path.startswith("/v2/"):
            db = SessionLocal()
            try:
                # Check system flags
                outage_setting = db.query(SystemSettings).filter(SystemSettings.key == "simulate_outage").first()
                if outage_setting and outage_setting.value.lower() == "true":
                    return JSONResponse(
                        status_code=503,
                        content=api_error("SERVICE_UNAVAILABLE", "Simulated system outage in progress")
                    )

                slow_setting = db.query(SystemSettings).filter(SystemSettings.key == "slow_mode").first()
                if slow_setting and slow_setting.value.lower() == "true":
                    await asyncio.sleep(5)

                flaky_setting = db.query(SystemSettings).filter(SystemSettings.key == "flaky_mode").first()
                if flaky_setting and flaky_setting.value.lower() == "true":
                    if random.random() < 0.20:
                        return JSONResponse(
                            status_code=500,
                            content=api_error("INTERNAL_SERVER_ERROR", "Simulated flaky API error")
                        )

                # Token Rate-Limiting (100 requests per minute)
                auth_token = request.headers.get("X-Auth-Token")
                if auth_token:
                    now_ts = time.time()
                    timestamps = RATE_LIMIT_STORE[auth_token]
                    # Filter timestamps within last 60 seconds
                    timestamps = [t for t in timestamps if now_ts - t < 60]
                    RATE_LIMIT_STORE[auth_token] = timestamps

                    if len(timestamps) >= 100:
                        return JSONResponse(
                            status_code=429,
                            headers={"Retry-After": "60"},
                            content=api_error("RATE_LIMIT_EXCEEDED", "Rate limit exceeded. Maximum 100 requests per minute allowed.")
                        )
                    RATE_LIMIT_STORE[auth_token].append(now_ts)
            finally:
                db.close()

        start_time = time.time()
        response = await call_next(request)

        # Log API call if /v2/
        if path.startswith("/v2/"):
            db = SessionLocal()
            try:
                auth_token = request.headers.get("X-Auth-Token")
                app_id = None
                user_id = None

                if auth_token:
                    t_obj = db.query(OAuthToken).filter(OAuthToken.access_token == auth_token).first()
                    if t_obj:
                        app_id = t_obj.app_id
                        user_id = t_obj.user_id

                log_entry = ApiLog(
                    timestamp=datetime.now(timezone.utc),
                    endpoint=path,
                    status_code=response.status_code,
                    app_id=app_id,
                    user_id=user_id
                )
                db.add(log_entry)
                db.commit()
            except Exception:
                pass
            finally:
                db.close()

        return response


class SessionSlidingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        session_token = request.cookies.get("session")
        if session_token:
            from app.services.auth_service import decode_web_session_token, create_web_session_token
            user_id = decode_web_session_token(session_token)
            if user_id:
                new_token = create_web_session_token(user_id, expiry_minutes=60)
                response.set_cookie(
                    key="session",
                    value=new_token,
                    max_age=3600,
                    path="/",
                    httponly=True,
                    samesite="lax"
                )
        return response

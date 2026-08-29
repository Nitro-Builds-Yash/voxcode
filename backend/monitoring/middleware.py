from __future__ import annotations
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class MonitoringMiddleware(BaseHTTPMiddleware):
    """
    FastAPI / Starlette middleware to record request latencies and response codes.
    """

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        try:
            response: Response = await call_next(request)
            process_time = (time.time() - start_time) * 1000
            response.headers["X-Process-Time-Ms"] = f"{process_time:.2f}"
            return response
        except Exception:
            raise

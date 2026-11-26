"""FastAPI application entry point."""

from __future__ import annotations

import json
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from voxcode.api import services
from voxcode.api.routes.agent import router as agent_router
from voxcode.api.routes.config import router as config_router
from voxcode.api.routes.health import router as health_router
from voxcode.api.routes.index import router as index_router
from voxcode.api.routes.voice import router as voice_router
from voxcode.api.security import LocalSecurityMiddleware
from voxcode.config.paths import ensure_storage_dirs
from voxcode.config.settings import settings
from voxcode.monitoring import tracing

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("voxcode")

UI_DIR = Path(__file__).parent / "ui"


def _warm_up() -> None:
    """Load heavy models in the background so the first request is fast."""
    services.registry.get_embedder()
    services.registry.get_reranker()
    services.registry.get_stt()
    services.memory()


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_storage_dirs()
    if settings.PRELOAD_MODELS:
        threading.Thread(target=_warm_up, name="model-warmup", daemon=True).start()
    yield
    services.watcher.stop_all()
    services.registry.unload()


app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)

if settings.allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-VoxCode-Token"],
    )
app.add_middleware(LocalSecurityMiddleware)
tracing.setup(app)

app.include_router(health_router)
app.include_router(agent_router)
app.include_router(voice_router)
app.include_router(index_router)
app.include_router(config_router)

try:
    from prometheus_client import make_asgi_app

    app.mount("/metrics", make_asgi_app())
except ImportError:  # pragma: no cover
    logger.info("prometheus-client not installed; /metrics disabled")

if UI_DIR.exists():
    app.mount("/static", StaticFiles(directory=UI_DIR), name="static")


@app.get("/", include_in_schema=False)
def root():
    index_file = UI_DIR / "index.html"
    if not index_file.exists():
        return JSONResponse({"name": settings.APP_NAME, "version": settings.APP_VERSION, "docs_url": "/docs"})
    html = index_file.read_text(encoding="utf-8")
    # Same-origin page: other sites cannot read it, so it can carry the token.
    token_json = json.dumps(settings.API_TOKEN).replace("<", "\\u003c")
    token_script = f"<script>window.VOXCODE_TOKEN = {token_json};</script>"
    return HTMLResponse(html.replace("</head>", f"{token_script}\n</head>", 1))


def main() -> None:
    import uvicorn

    uvicorn.run("voxcode.main:app", host=settings.HOST, port=settings.PORT, reload=settings.RELOAD)


if __name__ == "__main__":
    main()

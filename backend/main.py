import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.api.routes.health import (
    router as health_router,
)
from backend.api.routes.agent import (
    router as agent_router,
)
from backend.api.routes.voice import (
    router as voice_router,
)
from backend.api.routes.index import (
    router as index_router,
)
from backend.api.routes.config import (
    router as config_router,
)
from backend.config.settings import (
    settings,
)

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API Routers
app.include_router(health_router)
app.include_router(agent_router)
app.include_router(voice_router)
app.include_router(index_router)
app.include_router(config_router)

# Serve Web UI
ui_dir = os.path.join(os.path.dirname(__file__), "ui")
if os.path.exists(ui_dir):
    app.mount("/static", StaticFiles(directory=ui_dir), name="static")


@app.get("/")
def root():
    index_file = os.path.join(ui_dir, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs_url": "/docs",
    }


def main():
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
    )


if __name__ == "__main__":
    main()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
from backend.config.settings import (
    settings,
)

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
)

# Allow VS Code extension and local frontends to communicate
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(agent_router)
app.include_router(voice_router)
app.include_router(index_router)


@app.get("/")
def root():
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

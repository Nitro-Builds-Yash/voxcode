from fastapi import APIRouter

from voxcode.config.settings import settings

router = APIRouter()


@router.get("/health")
def health_check() -> dict:
    return {"status": "healthy", "service": "voxcode", "version": settings.APP_VERSION}

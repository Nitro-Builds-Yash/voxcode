from fastapi import APIRouter

from voxcode.config.settings import settings

router = APIRouter()


@router.get("/health")
def health_check() -> dict:
    from voxcode.native import status

    return {"status": "healthy", "service": "voxcode", "version": settings.APP_VERSION, "native": status()}

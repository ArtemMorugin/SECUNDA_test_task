from fastapi import APIRouter, Depends
from sqlalchemy import text

from src.config.database import engine
from src.presentation.api.security import require_api_key

router = APIRouter(tags=["Health"], dependencies=[Depends(require_api_key)])


@router.get("/health")
async def health_check() -> dict[str, str]:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return {"status": "ok"}

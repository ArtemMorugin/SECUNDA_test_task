import logging
import secrets

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from src.config.settings import get_settings

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
)


async def require_api_key(
    api_key: str | None = Security(api_key_header),
) -> None:
    expected_key = get_settings().api_key

    if api_key is None or not secrets.compare_digest(
        api_key.encode(), expected_key.encode()
    ):
        logger.warning(
            "api_auth_rejected reason=%s",
            "missing" if api_key is None else "invalid",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )

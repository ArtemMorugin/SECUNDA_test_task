import asyncio
import logging
from typing import Any
from uuid import UUID

import httpx

from src.config.logging import log_context

logger = logging.getLogger(__name__)


class HttpxWebhookClient:
    def __init__(
        self, client: httpx.AsyncClient, timeout_seconds: float = 5
    ) -> None:
        self.client = client
        self.timeout_seconds = timeout_seconds

    async def send(
        self, delivery_id: UUID, url: str, payload: dict[str, Any]
    ) -> None:
        with log_context(
            delivery_id=delivery_id,
            payment_id=payload.get("payment_id"),
        ):
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    response = await self.client.post(
                        url,
                        json=payload,
                        headers={"Idempotency-Key": str(delivery_id)},
                    )
                    response.raise_for_status()
            except (httpx.HTTPError, TimeoutError) as exc:
                logger.debug(
                    "webhook_http_failed error_type=%s", type(exc).__name__
                )
                raise
            logger.debug(
                "webhook_http_succeeded status=%s", response.status_code
            )

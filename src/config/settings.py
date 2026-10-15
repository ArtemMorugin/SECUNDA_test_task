from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=False,
    )
    app_name: str = "Payment Service"
    app_version: str = "0.1.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = (
        "INFO"
    )
    debug: bool = Field(default=False, validation_alias="APP_DEBUG")
    api_key: str = Field(min_length=1)
    database_url: str
    rabbitmq_url: str
    payment_processing_min_delay: float = Field(default=2.0, ge=0)
    payment_processing_max_delay: float = Field(default=5.0, ge=0)
    webhook_timeout_seconds: float = Field(default=5.0, gt=0)
    webhook_max_attempts: int = Field(default=3, ge=1, le=3)
    broker_timeout_seconds: float = Field(default=5.0, gt=0)
    delivery_lease_seconds: float = Field(default=30.0, gt=0)
    worker_poll_seconds: float = Field(default=1.0, gt=0)
    retry_base_seconds: float = Field(default=1.0, gt=0)
    rabbitmq_prefix: str = "payments"
    consume_legacy_queue: bool = True

    @model_validator(mode="after")
    def validate_timing(self) -> Self:
        if (
            self.payment_processing_min_delay
            > self.payment_processing_max_delay
        ):
            raise ValueError(
                "Processing minimum delay must not exceed maximum"
            )
        if (
            self.delivery_lease_seconds
            <= max(self.webhook_timeout_seconds, self.broker_timeout_seconds)
            + 5
        ):
            raise ValueError(
                "Delivery lease must exceed I/O timeout "
                "by more than five seconds"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

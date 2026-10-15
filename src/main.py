from fastapi import FastAPI

from src.config.lifespan import lifespan
from src.config.settings import get_settings
from src.presentation.api.errors import register_exception_handlers
from src.presentation.api.health import router as health_router
from src.presentation.api.middleware import RequestLoggingMiddleware
from src.presentation.api.v1.routers.payments import router as payments_router

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect",
)
app.include_router(payments_router)
app.include_router(health_router)
app.add_middleware(RequestLoggingMiddleware)
register_exception_handlers(app)

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from basketiq.api.errors import install_error_handlers
from basketiq.api.ratelimit import limiter
from basketiq.api.routers import admin, analytics, auth, me, receipts
from basketiq.api.routers import config as config_router
from basketiq.app_config import get_app_config
from basketiq.config import get_settings
from basketiq.db.session import get_engine
from basketiq.observability import metrics
from basketiq.observability.logging import configure_logging, get_logger, request_id_var

log = get_logger("basketiq.api")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    get_app_config()  # fail fast if config/app-config.yaml is missing or invalid
    app = FastAPI(
        title="BasketIQ API",
        version="0.1.0",
        description="Importação de notas de supermercado, revisão e análises de compras.",
        docs_url="/docs" if settings.is_local else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.is_local else None,
    )
    app.state.limiter = limiter
    install_error_handlers(app)
    app.add_exception_handler(RateLimitExceeded, app.exception_handlers[RateLimitExceeded])

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        rid = (
            incoming if 8 <= len(incoming) <= 64 and incoming.replace("-", "").isalnum() else uuid.uuid4().hex
        )
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response
        finally:
            elapsed = time.perf_counter() - start
            route = request.scope.get("route")
            path = getattr(route, "path", "unmatched")
            metrics.HTTP_LATENCY.labels(request.method, path, str(status)).observe(elapsed)
            # Path template only: concrete ids and query strings are not logged.
            log.info("request", method=request.method, route=path, status=status, ms=round(elapsed * 1000))
            request_id_var.reset(token)

    for module in (config_router, auth, me, receipts, analytics, admin):
        app.include_router(module.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> dict[str, str]:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready"}

    @app.get("/metrics", include_in_schema=False)
    def prometheus() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

GOOGLE_ISSUER = "https://accounts.google.com"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BASKETIQ_", env_file=".env", extra="ignore")

    env: Literal["local", "test", "staging", "production"] = "local"
    log_level: str = "INFO"
    timezone: str = "Europe/Lisbon"

    database_url: str = "postgresql+psycopg://basketiq:basketiq@localhost:5432/basketiq"
    redis_url: str = "redis://localhost:6379/0"

    # Object storage (S3 compatible). The public endpoint is the one the browser reaches;
    # presigned URLs must be signed against it because the host is part of the signature.
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_public_endpoint_url: str | None = None
    s3_region: str = "eu-west-1"
    s3_access_key: str = "basketiq"
    s3_secret_key: str = "basketiq-secret"  # noqa: S105 - local default only
    s3_bucket: str = "basketiq-originals"
    storage_backend: Literal["s3", "memory"] = "s3"

    # OIDC. Google in staging/production; a mock OIDC server locally.
    oidc_issuer: str = GOOGLE_ISSUER
    oidc_discovery_url: str | None = None
    # Local only: the browser cannot resolve the docker-internal host of the mock server.
    oidc_browser_base_url: str | None = None
    oidc_internal_base_url: str | None = None
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = "http://localhost:5173/v1/auth/callback"
    frontend_base_url: str = "http://localhost:5173"

    jwt_secret: str = "change-me-local-only-secret-with-enough-length"  # noqa: S105
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_days: int = 30
    cookie_secure: bool = False
    admin_emails: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Upload rules
    upload_max_bytes: int = 15 * 1024 * 1024
    upload_url_ttl_seconds: int = 600
    original_url_ttl_seconds: int = 60
    pdf_max_pages: int = 5
    upload_max_photos: int = 6  # photos of one long receipt
    abandoned_upload_hours: int = 24

    # Extraction / decision rules
    confidence_threshold: float = 0.80
    reconciliation_tolerance_cents: int = 2
    auto_confirm_enabled: bool = True
    retention_days: int = 30

    # Processing components
    queue_backend: Literal["dramatiq", "inline"] = "dramatiq"
    ocr_engine: Literal["paddle", "none"] = "paddle"
    ocr_det_model: str = "PP-OCRv5_mobile_det"
    ocr_rec_model: str = "latin_PP-OCRv5_mobile_rec"
    # Whether the LLM agent runs is LLM_ENABLED in config/app-config.yaml, not an env var.
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:3b-instruct"
    llm_timeout_seconds: float = 30.0
    external_fallback: Literal["disabled", "mock"] = "disabled"
    external_monthly_budget_cents: int = 0
    external_timeout_seconds: float = 30.0
    job_max_attempts: int = 3
    job_stuck_after_seconds: int = 600

    # Rate limits (slowapi syntax)
    rate_limit_login: str = "10/minute"
    rate_limit_upload: str = "30/hour"
    rate_limit_process: str = "60/hour"
    rate_limit_enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def _split_admin_emails(cls, data: dict) -> dict:
        raw = data.get("admin_emails") if isinstance(data, dict) else None
        if isinstance(raw, str):
            data["admin_emails"] = [e.strip().lower() for e in raw.split(",") if e.strip()]
        return data

    @model_validator(mode="after")
    def _check_production_rules(self) -> Settings:
        if self.env in ("staging", "production"):
            # RF-01: Google is the only authentication method outside local/test.
            if self.oidc_issuer != GOOGLE_ISSUER:
                raise ValueError("Only Google OIDC is allowed in staging/production")
            if self.jwt_secret.startswith("change-me") or len(self.jwt_secret) < 32:
                raise ValueError("A strong BASKETIQ_JWT_SECRET is required")
            if not self.cookie_secure:
                raise ValueError("Secure cookies are required outside local/test")
            if self.oidc_browser_base_url or self.oidc_internal_base_url:
                raise ValueError("OIDC URL rewriting is a local-only feature")
        return self

    @property
    def is_local(self) -> bool:
        return self.env in ("local", "test")


@lru_cache
def get_settings() -> Settings:
    return Settings()

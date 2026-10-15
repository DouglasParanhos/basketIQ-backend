from __future__ import annotations

from sqlalchemy.orm import Session

from basketiq.config import Settings
from basketiq.db.models import MerchantConfig
from basketiq.domain.confidence import Thresholds


def thresholds_for(db: Session, settings: Settings, merchant: str | None) -> Thresholds:
    cfg = db.get(MerchantConfig, merchant) if merchant else None
    return Thresholds(
        default=(
            cfg.default_threshold
            if cfg and cfg.default_threshold is not None
            else settings.confidence_threshold
        ),
        per_field=dict(cfg.field_thresholds) if cfg else {},
        tolerance_cents=(
            cfg.reconciliation_tolerance_cents
            if cfg and cfg.reconciliation_tolerance_cents is not None
            else settings.reconciliation_tolerance_cents
        ),
    )


def merchant_enabled(db: Session, merchant: str) -> bool:
    cfg = db.get(MerchantConfig, merchant)
    return cfg.enabled if cfg else True

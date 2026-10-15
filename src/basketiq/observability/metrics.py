"""Prometheus metrics (spec técnica §10). Labels never contain user or receipt identifiers."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

STAGE_SECONDS = Histogram(
    "basketiq_pipeline_stage_seconds",
    "Duration of each pipeline stage",
    ["stage"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120),
)
JOBS = Counter("basketiq_jobs_total", "Processing jobs by outcome", ["outcome", "route", "merchant"])
JOB_RETRIES = Counter("basketiq_job_retries_total", "Retried processing jobs")
RECEIPT_CONFIDENCE = Histogram(
    "basketiq_receipt_confidence",
    "Overall extraction confidence",
    buckets=(0.2, 0.4, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0),
)
TOTAL_DIVERGENCE = Counter(
    "basketiq_total_divergence_total", "Receipts whose totals did not reconcile", ["merchant"]
)
EXTERNAL_CALLS = Counter("basketiq_external_calls_total", "External fallback calls", ["provider", "reason"])
EXTERNAL_COST_CENTS = Counter("basketiq_external_cost_cents_total", "Estimated external cost", ["provider"])
LLM_CALLS = Counter("basketiq_llm_calls_total", "Local LLM ambiguity resolutions")
PARSER_ERRORS = Counter(
    "basketiq_parser_unresolved_lines_total", "Lines the parser could not resolve", ["parser"]
)
ANALYTICS_REQUESTS = Counter(
    "basketiq_analytics_requests_total",
    "Dashboard usage and filters",
    ["endpoint", "merchant_filter", "product_filter"],
)
HTTP_LATENCY = Histogram(
    "basketiq_http_request_seconds",
    "API latency",
    ["method", "route", "status"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)

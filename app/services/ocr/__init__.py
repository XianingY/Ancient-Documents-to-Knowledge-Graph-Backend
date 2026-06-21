"""Conservative OCR pipeline components."""

from .paddle_backend import OcrBackendUnavailable, run_paddle_consensus
from .metrics import (
    aggregate_char_metrics,
    char_level_metrics,
    char_metric_modes,
    normalize_metric_text,
)
from .types import OcrPipelineResult

__all__ = [
    "OcrBackendUnavailable",
    "OcrPipelineResult",
    "aggregate_char_metrics",
    "char_level_metrics",
    "char_metric_modes",
    "normalize_metric_text",
    "run_paddle_consensus",
]

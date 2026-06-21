"""Conservative OCR pipeline components."""

from .paddle_backend import OcrBackendUnavailable, run_paddle_consensus
from .metrics import char_level_metrics
from .types import OcrPipelineResult

__all__ = [
    "OcrBackendUnavailable",
    "OcrPipelineResult",
    "char_level_metrics",
    "run_paddle_consensus",
]

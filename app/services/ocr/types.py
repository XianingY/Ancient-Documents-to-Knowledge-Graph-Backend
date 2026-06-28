from dataclasses import dataclass, field
from typing import Any


@dataclass
class OcrPipelineResult:
    text: str
    confidence: float
    coverage: float
    engine: str
    model_versions: str
    segments: list[dict[str, Any]] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    crop_bbox: list[int] | None = None
    image_size: list[int] | None = None


import os
from typing import Set, Tuple, List, Optional
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # JWT
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 24 * 60  # 24h
    DEFAULT_TOKEN_EXPIRE_MINUTES: int = 15

    # File Storage
    UPLOAD_DIR: str = "pic"
    ALLOWED_EXTENSIONS: Set[str] = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}
    MAX_FILE_SIZE: int = 10 * 1024 * 1024  # 10MB

    # Thumbnails
    THUMBNAIL_SIZE: Tuple[int, int] = (320, 320)
    THUMBNAIL_QUALITY: int = 85

    # Pagination
    DEFAULT_PAGE_SIZE: int = 10
    MAX_PAGE_SIZE: int = 100

    # Server
    SERVER_PORT: int = 3000

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:8081,http://127.0.0.1:3000,http://127.0.0.1:8081"

    # AI Services
    DASHSCOPE_API_KEY: Optional[str] = None

    # Database (Optional, fallback to SQLite if not provided)
    DATABASE_URL: Optional[str] = None

    # Redis & Celery
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    # OCR Optimization Settings
    REAL_ESRGAN_MODEL_PATH: str = "weights/realesr-general-x4v3.pth"
    ENSEMBLE_PASSES: int = 1
    ENSEMBLE_DOWNSCALE: float = 0.85
    ENSEMBLE_NOISE_SIGMA: float = 3.0
    FUSION_ENABLED: bool = True
    HALLUCINATION_FILTER_ENABLED: bool = True
    OCR_LLM_POST_CORRECTION_ENABLED: bool = False
    EXPECTED_TEXT_MIN: int = 60
    EXPECTED_TEXT_MAX: int = 420
    OCR_ENGINE: str = "paddle_v6_consensus"
    OCR_FALLBACK_ENGINE: str = "qwen_conservative"
    OCR_TARGET_LONG_SIDE: int = 2400
    OCR_DETECTION_THRESHOLD: float = 0.2
    OCR_BOX_THRESHOLD: float = 0.3
    OCR_UNCLIP_RATIO: float = 1.2
    OCR_RECOGNITION_BATCH_SIZE: int = 16
    OCR_CONSENSUS_MIN_SCORE: float = 0.45
    OCR_CONSENSUS_MIN_SIMILARITY: float = 0.40

    @property
    def REDIS_URL(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"


    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"

    @property
    def THUMBNAIL_DIR(self) -> str:
        return os.path.join(self.UPLOAD_DIR, "thumbnails")

settings = Settings()

# Ensure directories exist
if not os.path.exists(settings.UPLOAD_DIR):
    os.makedirs(settings.UPLOAD_DIR)
if not os.path.exists(settings.THUMBNAIL_DIR):
    os.makedirs(settings.THUMBNAIL_DIR)

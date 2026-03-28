
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

    # AI Services
    DASHSCOPE_API_KEY: Optional[str] = None

    # Database (Optional, fallback to SQLite if not provided)
    DATABASE_URL: Optional[str] = None

    # Redis & Celery
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    
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

import os
from celery import Celery
from dotenv import load_dotenv

# Celery worker 进程不经过 FastAPI 启动流程，需手动加载 .env
load_dotenv(override=True)

from app.core.config import settings

celery_app = Celery(
    "worker",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.worker.tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
)

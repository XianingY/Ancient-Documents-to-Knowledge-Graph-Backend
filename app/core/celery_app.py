import os
from celery import Celery
from dotenv import load_dotenv

# Celery worker 进程不经过 FastAPI 启动流程，需手动加载 .env。
# Docker/CI 注入的环境变量优先级必须高于本地 .env，否则容器内 Redis 端口会被宿主配置覆盖。
load_dotenv(override=False)

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

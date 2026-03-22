"""
FastAPI 主入口
仅负责应用初始化、中间件配置和路由注册，业务逻辑全部在 app/routers/ 下
"""
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.logger import setup_logging, get_logger
from database import init_db

# 初始化日志
setup_logging()
logger = get_logger(__name__)

# 路由模块
from app.routers.auth import router as auth_router
from app.routers.users import router as users_router
from app.routers.images import router as images_router
from app.routers.ocr import router as ocr_router
from app.routers.structured import router as structured_router
from app.routers.graphs import relation_graph_router, multi_relation_graph_router
from app.routers.chat import router as chat_router
from app.routers.statistics import router as statistics_router
from app.routers.multi_tasks import router as multi_task_router

# 速率限制
from app.core.rate_limit import limiter, SLOWAPI_AVAILABLE as _SLOWAPI_AVAILABLE
try:
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware
except ImportError:
    pass

# 初始化数据库
init_db()

app = FastAPI(title="古代地契文书知识图谱 API", version="2.0.0")

# 允许的来源：本地开发 + 线上服务器地址
# 如需新增来源，在此列表追加即可
_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:8081",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8081",
    "http://8.162.9.49:3000",
    "exp://8.162.9.49:8081",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

if _SLOWAPI_AVAILABLE and limiter is not None:
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(images_router)
app.include_router(ocr_router)
app.include_router(structured_router)
app.include_router(relation_graph_router)
app.include_router(multi_relation_graph_router)
app.include_router(multi_task_router)
app.include_router(chat_router)
app.include_router(statistics_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", extra={
        "path": str(request.url),
        "method": request.method,
        "error": str(exc),
    })
    return JSONResponse(
        status_code=500,
        content={"success": False, "detail": "服务器内部错误，请稍后重试"},
    )


@app.get("/api")
async def health_check():
    return {"status": "ok", "version": "2.0.0"}


if __name__ == "__main__":
    import uvicorn
    from app.core.config import settings
    uvicorn.run(app, host="0.0.0.0", port=settings.SERVER_PORT)

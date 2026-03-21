"""
结构化日志配置模块
统一使用 Python 标准 logging，JSON 格式输出便于日志收集
"""
import logging
import json
import sys
from datetime import datetime, timezone, timedelta


class JSONFormatter(logging.Formatter):
    """将日志记录格式化为 JSON 字符串"""

    def format(self, record: logging.LogRecord) -> str:
        tz_beijing = timezone(timedelta(hours=8))
        log_obj = {
            "time": datetime.now(tz_beijing).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # 附加 extra 字段
        for key, val in record.__dict__.items():
            if key not in (
                "name", "msg", "args", "levelname", "levelno",
                "pathname", "filename", "module", "exc_info", "exc_text",
                "stack_info", "lineno", "funcName", "created", "msecs",
                "relativeCreated", "thread", "threadName", "processName",
                "process", "message",
            ):
                try:
                    json.dumps(val)
                    log_obj[key] = val
                except (TypeError, ValueError):
                    log_obj[key] = str(val)

        if record.exc_info:
            log_obj["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(log_obj, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """初始化全局日志配置，调用一次即可"""
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 降低第三方库的噪音
    for noisy in ("uvicorn.access", "uvicorn.error", "sqlalchemy.engine", "passlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

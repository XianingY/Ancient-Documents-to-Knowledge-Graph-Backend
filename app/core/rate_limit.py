"""共享速率限制器实例，供各路由模块按需导入"""
from functools import wraps

try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=["200/minute"],
        application_limits=["1000/hour"],
    )
    SLOWAPI_AVAILABLE = True
except ImportError:
    limiter = None
    SLOWAPI_AVAILABLE = False


def rate_limit(limit_string: str):
    """per-route 限流装饰器，slowapi 不可用时退化为无操作"""
    if SLOWAPI_AVAILABLE and limiter is not None:
        return limiter.limit(limit_string)
    return lambda fn: fn


from datetime import datetime, timedelta, timezone
from typing import Optional
import time
from collections import OrderedDict
import uuid
import jwt
from passlib.context import CryptContext
from fastapi.security import HTTPBearer
from fastapi import HTTPException
from database import get_beijing_time
from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

# ── Token 黑名单（基于 Redis）────────────────────────────────
# 登出时将 jti 写入 Redis，TTL 与 Token 过期时间一致
# 若 Redis 不可用则降级为内存集合（重启后失效，但不影响基本功能）

class TTLSet:
    """Bounded set with TTL-based expiration. Max 10000 entries."""
    def __init__(self, max_size: int = 10000):
        self._data: OrderedDict[str, float] = OrderedDict()
        self._max_size = max_size
    
    def add(self, item: str, ttl_seconds: int) -> None:
        self._data[item] = time.monotonic() + ttl_seconds
        self._cleanup()
    
    def contains(self, item: str) -> bool:
        if item not in self._data:
            return False
        if time.monotonic() > self._data[item]:
            del self._data[item]
            return False
        return True
    
    def _cleanup(self) -> None:
        now = time.monotonic()
        while self._data and now > next(iter(self._data.values())):
            self._data.popitem(last=False)
        while len(self._data) > self._max_size:
            self._data.popitem(last=False)

_memory_blacklist = TTLSet()

def _get_redis():
    try:
        import redis as redis_lib
        r = redis_lib.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            socket_connect_timeout=1,
        )
        r.ping()
        return r
    except Exception:
        return None


def blacklist_token(jti: str, ttl_seconds: int) -> None:
    """将 Token 的 jti 加入黑名单，ttl 与 Token 有效期一致"""
    r = _get_redis()
    if r:
        r.setex(f"bl:{jti}", ttl_seconds, "1")
    else:
        _memory_blacklist.add(jti, ttl_seconds)


def is_token_blacklisted(jti: str) -> bool:
    """检查 Token 是否已被加入黑名单"""
    r = _get_redis()
    if r:
        return bool(r.exists(f"bl:{jti}"))
    return _memory_blacklist.contains(jti)


# ── 密码工具 ─────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return pwd_context.hash(password[:72])

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password[:72], hashed_password)


# ── Token 工具 ───────────────────────────────────────────────

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = get_beijing_time() + expires_delta
    else:
        expire = get_beijing_time() + timedelta(minutes=settings.DEFAULT_TOKEN_EXPIRE_MINUTES)

    to_encode.update({
        "exp": expire,
        "jti": uuid.uuid4().hex,  # 唯一 Token ID，用于黑名单校验
    })
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

def verify_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        jti = payload.get("jti")
        if jti and is_token_blacklisted(jti):
            raise HTTPException(status_code=401, detail="Token 已失效，请重新登录")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

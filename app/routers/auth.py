"""认证路由：注册 / 登录 / 登出 / Token 刷新"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import User, get_beijing_time, get_db
from app.core.config import settings
from app.core.logger import get_logger
from app.core.security import (
    blacklist_token,
    create_access_token,
    hash_password,
    security,
    verify_password,
    verify_token,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/auth", tags=["认证"])


class RegisterRequest(BaseModel):
    username: str
    password: str
    email: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/register", summary="用户注册", description="创建新用户账号，用户名唯一，邮箱可选")
async def register(request: RegisterRequest, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.username == request.username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="用户名已存在")

    hashed_password = hash_password(request.password)
    db_user = User(
        username=request.username,
        email=request.email,
        password_hash=hashed_password,
        created_at=get_beijing_time(),
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)

    logger.info("user_registered", extra={"username": db_user.username, "user_id": db_user.id})

    return {
        "success": True,
        "message": "注册成功",
        "userId": db_user.id,
        "username": db_user.username,
        "email": db_user.email,
    }


@router.post("/login", summary="用户登录", description="验证用户名和密码，成功后返回 JWT Bearer Token，有效期24小时")
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.username == request.username).first()
    if not db_user or not verify_password(request.password, db_user.password_hash):
        logger.warning("login_failed", extra={"username": request.username})
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": db_user.username, "user_id": db_user.id},
        expires_delta=access_token_expires,
    )

    logger.info("user_logged_in", extra={"username": db_user.username, "user_id": db_user.id})

    return {
        "success": True,
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": db_user.id,
        "username": db_user.username,
    }


@router.post("/logout", summary="用户登出", description="登出后 Token 立即失效，无法继续使用")
async def logout(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    payload = verify_token(token)
    jti = payload.get("jti")
    if jti:
        # 计算 Token 剩余有效秒数，将其加入黑名单
        from datetime import timezone as tz
        exp = payload.get("exp", 0)
        remaining = max(int(exp - datetime.now(tz.utc).timestamp()), 0)
        blacklist_token(jti, remaining + 60)
    logger.info("user_logged_out", extra={"user_id": payload.get("user_id")})
    return {"success": True, "message": "已成功登出"}


@router.post("/refresh", summary="刷新 Token", description="使用当前有效 Token 换取新 Token，延长登录有效期")
async def refresh_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    token = credentials.credentials
    payload = verify_token(token)
    username = payload.get("sub")

    db_user = db.query(User).filter(User.username == username).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="用户不存在")

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    new_access_token = create_access_token(
        data={"sub": db_user.username, "user_id": db_user.id},
        expires_delta=access_token_expires,
    )

    return {"success": True, "access_token": new_access_token, "token_type": "bearer"}

"""用户路由：获取 / 更新个人信息，获取图片列表 / 跨任务列表"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import Image, MultiTask, User, get_db
from app.core.logger import get_logger
from app.core.security import security, verify_token, hash_password

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/users", tags=["用户管理"])


class UpdateUserRequest(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    email: Optional[str] = None


@router.get("/me")
async def get_user_info(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    payload = verify_token(credentials.credentials)
    username = payload.get("sub")

    db_user = db.query(User).filter(User.username == username).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="用户不存在")

    return {
        "success": True,
        "user": {
            "id": db_user.id,
            "username": db_user.username,
            "email": db_user.email,
            "created_at": db_user.created_at.isoformat(),
        },
    }


@router.put("/me")
async def update_user_info(
    request: UpdateUserRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    payload = verify_token(credentials.credentials)
    username = payload.get("sub")

    db_user = db.query(User).filter(User.username == username).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if request.username:
        existing = db.query(User).filter(
            User.username == request.username, User.id != db_user.id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="用户名已存在")
        db_user.username = request.username

    if request.password:
        db_user.password_hash = hash_password(request.password)

    if request.email:
        db_user.email = request.email

    db.commit()
    db.refresh(db_user)

    logger.info("user_updated", extra={"user_id": db_user.id})

    return {
        "success": True,
        "message": "更新成功",
        "user": {
            "id": db_user.id,
            "username": db_user.username,
            "email": db_user.email,
            "created_at": db_user.created_at.isoformat(),
        },
    }


@router.get("/images")
async def get_user_images(
    skip: int = 0,
    limit: int = 10,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    payload = verify_token(credentials.credentials)
    user_id = payload.get("user_id")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    images = (
        db.query(Image.id)
        .filter(Image.user_id == user_id)
        .order_by(Image.upload_time.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    total = db.query(Image).filter(Image.user_id == user_id).count()

    return {
        "success": True,
        "data": {
            "total": total,
            "skip": skip,
            "limit": limit,
            "ids": [image[0] for image in images],
        },
    }


@router.get("/multi-tasks")
async def get_user_multi_tasks(
    skip: int = 0,
    limit: int = 10,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    payload = verify_token(credentials.credentials)
    user_id = payload.get("user_id")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    multi_tasks = (
        db.query(MultiTask.id)
        .filter(MultiTask.user_id == user_id)
        .order_by(MultiTask.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    total = db.query(MultiTask).filter(MultiTask.user_id == user_id).count()

    return {
        "success": True,
        "data": {
            "total": total,
            "skip": skip,
            "limit": limit,
            "ids": [task[0] for task in multi_tasks],
        },
    }

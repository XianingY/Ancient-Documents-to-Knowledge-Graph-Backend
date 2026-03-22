"""用户路由：获取 / 更新个人信息，获取图片列表 / 跨任务列表"""
import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import Image, MultiTask, MultiTaskStructuredResult, User, get_db
from app.core.deps import get_current_user, get_current_user_id
from app.core.logger import get_logger
from app.core.security import hash_password

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/users", tags=["用户管理"])


class UpdateUserRequest(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    email: Optional[str] = None


@router.get("/me")
async def get_user_info(
    db_user: User = Depends(get_current_user),
):
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
    db_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):

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


def _friendly_title(image_id: int, filename: str, upload_time) -> str:
    base = os.path.splitext(filename)[0]
    clean = re.sub(r'_[0-9a-f]{8}$', '', base).strip()
    generic_patterns = re.compile(
        r'^(img|image|photo|dsc|pic|screenshot|scan|capture|frame|'
        r'file|\d+|img_\d+|dsc_\d+|photo_\d+)$',
        re.IGNORECASE,
    )
    t = upload_time
    if not clean or generic_patterns.fullmatch(clean):
        return f"#{image_id} 地契文书 · {t.month}月{t.day}日 {t.strftime('%H:%M')}"
    return f"#{image_id} {clean} · {t.month}月{t.day}日"


@router.get("/images", summary="获取当前用户图片列表", description="返回完整的图片信息列表（含标题、文件名、上传时间），同时兼容旧的 ids 字段")
async def get_user_images(
    skip: int = 0,
    limit: int = 10,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):

    images = (
        db.query(Image)
        .filter(Image.user_id == user_id)
        .order_by(Image.upload_time.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    total = db.query(Image).filter(Image.user_id == user_id).count()

    items = [
        {
            "id": img.id,
            "filename": img.filename,
            "upload_time": img.upload_time.isoformat(),
            "title": _friendly_title(img.id, img.filename, img.upload_time),
        }
        for img in images
    ]

    return {
        "success": True,
        "data": {
            "total": total,
            "skip": skip,
            "limit": limit,
            "ids": [img.id for img in images],
            "items": items,
        },
    }


@router.get("/multi-tasks", summary="获取当前用户跨文档任务列表", description="返回跨文档任务摘要信息（含文书数量、创建时间），同时兼容旧的 ids 字段")
async def get_user_multi_tasks(
    skip: int = 0,
    limit: int = 10,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):

    multi_tasks = (
        db.query(MultiTask)
        .filter(MultiTask.user_id == user_id)
        .order_by(MultiTask.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    total = db.query(MultiTask).filter(MultiTask.user_id == user_id).count()

    items = []
    for task in multi_tasks:
        doc_count = (
            db.query(MultiTaskStructuredResult)
            .filter(MultiTaskStructuredResult.multi_task_id == task.id)
            .count()
        )
        items.append({
            "id": task.id,
            "status": task.status.value,
            "doc_count": doc_count,
            "created_at": task.created_at.isoformat(),
        })

    return {
        "success": True,
        "data": {
            "total": total,
            "skip": skip,
            "limit": limit,
            "ids": [task.id for task in multi_tasks],
            "items": items,
        },
    }

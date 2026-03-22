"""图片路由：上传 / 获取 / 缩略图 / 删除 / 信息 / 触发OCR"""
import os
import re
import uuid
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image as PILImage, UnidentifiedImageError
from sqlalchemy.orm import Session

from database import (
    Image,
    MultiTaskStructuredResult,
    OcrResult,
    RelationGraph,
    StructuredResult,
    User,
    get_beijing_time,
    get_db,
)
from app.core.config import settings
from app.core.deps import get_current_user_id
from app.core.logger import get_logger
from app.core.rate_limit import rate_limit
from app.worker.tasks import task_ocr_image

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/images", tags=["图片管理"])


def _friendly_title(image_id: int, filename: str, upload_time) -> str:
    """
    从存储文件名（含随机后缀）生成展示友好的标题，始终包含图片 ID。
    例：'contract_scan_a1b2c3d4.jpg' → '#3 contract_scan · 03月19日'
         'photo_a1b2c3d4.jpg'         → '#3 地契文书 · 03月19日 14:25'
    """
    base = os.path.splitext(filename)[0]
    # 去掉末尾 _xxxxxxxx 随机哈希
    clean = re.sub(r'_[0-9a-f]{8}$', '', base).strip()
    # 过滤掉手机相机生成的无意义名称（纯数字/IMG/DSC/photo 等）
    generic_patterns = re.compile(
        r'^(img|image|photo|dsc|pic|screenshot|scan|capture|frame|'
        r'file|\d+|img_\d+|dsc_\d+|photo_\d+)$',
        re.IGNORECASE,
    )
    t = upload_time
    if not clean or generic_patterns.fullmatch(clean):
        return f"#{image_id} 地契文书 · {t.month}月{t.day}日 {t.strftime('%H:%M')}"
    return f"#{image_id} {clean} · {t.month}月{t.day}日"


def _build_thumbnail_path(filename: str) -> str:
    stem, _ = os.path.splitext(filename)
    return os.path.join(settings.THUMBNAIL_DIR, f"{stem}_thumb.jpg")


def _ensure_thumbnail(image_path: str, thumbnail_path: str) -> None:
    if os.path.exists(thumbnail_path):
        return
    try:
        with PILImage.open(image_path) as img:
            rgb_img = img.convert("RGB")
            rgb_img.thumbnail(settings.THUMBNAIL_SIZE)
            rgb_img.save(thumbnail_path, format="JPEG", quality=settings.THUMBNAIL_QUALITY, optimize=True)
    except UnidentifiedImageError:
        raise HTTPException(status_code=400, detail="无法识别的图片格式")
    except Exception as e:
        raise HTTPException(status_code=500, detail="生成缩略图失败")


@router.post("/upload", summary="上传地契图片", description="支持 JPG/PNG/WEBP/GIF/BMP/TIFF 格式，最大 10MB。上传后自动触发 OCR → 结构化分析 → 知识图谱生成流水线")
@rate_limit("30/minute")
async def upload_image(
    _req: Request,
    image: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):

    ext = os.path.splitext(image.filename or "")[1].lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型 '{ext}'。允许的类型: {', '.join(settings.ALLOWED_EXTENSIONS)}",
        )

    try:
        file_data = await image.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail="读取文件失败")

    file_size = len(file_data)
    if file_size > settings.MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大。最大允许 10MB，当前 {file_size / 1024 / 1024:.2f}MB",
        )
    if file_size == 0:
        raise HTTPException(status_code=400, detail="文件为空")

    original_name = os.path.splitext(image.filename or "upload")[0]
    unique_filename = f"{original_name}_{uuid.uuid4().hex[:8]}{ext}"
    file_path = os.path.join(settings.UPLOAD_DIR, unique_filename)

    try:
        with open(file_path, "wb") as buf:
            buf.write(file_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail="保存文件失败")

    try:
        db_image = Image(
            user_id=user_id,
            filename=unique_filename,
            path=file_path,
            upload_time=get_beijing_time(),
        )
        db.add(db_image)
        db.commit()
        db.refresh(db_image)

        logger.info("image_uploaded", extra={"image_id": db_image.id, "user_id": user_id, "size": file_size})

        # 上传成功后自动排队 OCR（与前端「自动识别」一致）；队列失败不阻断上传
        try:
            task_ocr_image.delay(db_image.id)
        except Exception as queue_err:
            logger.warning(
                "ocr_queue_failed_after_upload",
                extra={"image_id": db_image.id, "error": str(queue_err)},
            )

        return {
            "success": True,
            "imageId": db_image.id,
            "filename": db_image.filename,
            "originalName": image.filename,
            "fileSize": file_size,
            "pipeline_started": True,
        }
    except Exception as e:
        db.rollback()
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass
        raise HTTPException(status_code=500, detail="保存到数据库失败")


@router.get("/{image_id}", summary="获取原始图片", description="返回指定图片的原始文件流（FileResponse）")
async def get_image(
    image_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    db_image = db.query(Image).filter(Image.id == image_id, Image.user_id == user_id).first()
    if not db_image:
        raise HTTPException(status_code=404, detail="image not found")
    if not os.path.exists(str(db_image.path)):
        raise HTTPException(status_code=404, detail="image file not found")
    return FileResponse(str(db_image.path))


@router.get("/{image_id}/thumbnail", summary="获取缩略图", description="返回 320×320 JPEG 缩略图，首次访问时自动生成并缓存")
async def get_thumbnail(
    image_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    db_image = db.query(Image).filter(Image.id == image_id, Image.user_id == user_id).first()
    if not db_image:
        raise HTTPException(status_code=404, detail="image not found")
    if not os.path.exists(str(db_image.path)):
        raise HTTPException(status_code=404, detail="image file not found")

    thumbnail_path = _build_thumbnail_path(db_image.filename)
    _ensure_thumbnail(str(db_image.path), thumbnail_path)
    return FileResponse(thumbnail_path, media_type="image/jpeg")


@router.get("/{image_id}/info", summary="获取图片元信息", description="返回图片的基本元数据：ID、文件名、上传时间")
async def get_image_info(
    image_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    db_image = db.query(Image).filter(Image.id == image_id, Image.user_id == user_id).first()
    if not db_image:
        raise HTTPException(status_code=404, detail="image not found")

    return {
        "success": True,
        "data": {
            "id": db_image.id,
            "filename": db_image.filename,
            "upload_time": db_image.upload_time.isoformat(),
            "title": _friendly_title(db_image.id, db_image.filename, db_image.upload_time),
        },
    }


@router.delete("/{image_id}", summary="删除图片及全部关联数据", description="级联删除：图片文件 + OCR结果 + 结构化结果 + 关系图 + 跨文档任务关联，操作不可逆")
async def delete_image(
    image_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    db_image = db.query(Image).filter(Image.id == image_id, Image.user_id == user_id).first()
    if not db_image:
        raise HTTPException(status_code=404, detail="image not found")

    original_path = str(db_image.path)
    thumbnail_path = _build_thumbnail_path(db_image.filename)

    # 提前收集 OCR result ID，用于删除 ChromaDB 向量索引
    ocr_ids_to_delete = [
        r.id for r in db.query(OcrResult).filter(OcrResult.image_id == image_id).all()
    ]

    structured_ids_query = (
        db.query(StructuredResult.id)
        .join(OcrResult, StructuredResult.ocr_result_id == OcrResult.id)
        .filter(OcrResult.image_id == image_id)
    )

    deleted_ocr_count = db.query(OcrResult).filter(OcrResult.image_id == image_id).count()
    deleted_struct_count = (
        db.query(StructuredResult)
        .join(OcrResult, StructuredResult.ocr_result_id == OcrResult.id)
        .filter(OcrResult.image_id == image_id)
        .count()
    )
    deleted_graph_count = (
        db.query(RelationGraph)
        .filter(RelationGraph.structured_result_id.in_(structured_ids_query))
        .count()
    )
    deleted_assoc_count = (
        db.query(MultiTaskStructuredResult)
        .filter(MultiTaskStructuredResult.structured_result_id.in_(structured_ids_query))
        .count()
    )

    try:
        db.query(MultiTaskStructuredResult).filter(
            MultiTaskStructuredResult.structured_result_id.in_(structured_ids_query)
        ).delete(synchronize_session=False)
        db.query(RelationGraph).filter(
            RelationGraph.structured_result_id.in_(structured_ids_query)
        ).delete(synchronize_session=False)
        db.query(StructuredResult).filter(
            StructuredResult.id.in_(structured_ids_query)
        ).delete(synchronize_session=False)
        db.query(OcrResult).filter(OcrResult.image_id == image_id).delete(synchronize_session=False)
        db.delete(db_image)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="删除图片失败")

    removed_files: List[str] = []
    for path in (original_path, thumbnail_path):
        try:
            if os.path.exists(path):
                os.remove(path)
                removed_files.append(path)
        except OSError:
            pass

    # 同步清理 ChromaDB 向量索引（非阻塞，失败不影响主流程）
    try:
        from app.services.vector_store.chroma import delete_documents
        chroma_doc_ids = [f"ocr_{oid}" for oid in ocr_ids_to_delete]
        delete_documents(chroma_doc_ids)
    except Exception as chroma_err:
        logger.warning("chroma_delete_failed", extra={"image_id": image_id, "error": str(chroma_err)})

    logger.info("image_deleted", extra={"image_id": image_id, "user_id": user_id})

    return {
        "success": True,
        "message": "图片及关联分析结果已删除",
        "deleted": {
            "image_id": image_id,
            "ocr_results": deleted_ocr_count,
            "structured_results": deleted_struct_count,
            "relation_graphs": deleted_graph_count,
            "multi_task_associations": deleted_assoc_count,
            "removed_files": removed_files,
        },
    }


@router.post("/{image_id}/ocr", summary="手动触发 OCR", description="将图片加入 OCR 任务队列（Celery 异步执行），可用于重新识别")
async def trigger_ocr(
    image_id: int,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    image = db.query(Image).filter(Image.id == image_id, Image.user_id == user_id).first()
    if not image:
        raise HTTPException(status_code=404, detail="图片不存在")

    task_ocr_image.delay(image_id)
    logger.info("ocr_triggered", extra={"image_id": image_id})

    return {"success": True, "message": f"图片 {image_id} 的OCR任务已提交到队列"}


@router.get("/{image_id}/ocr-results")
async def get_image_ocr_results(
    image_id: int,
    skip: int = 0,
    limit: int = 10,
    user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    image = db.query(Image).filter(Image.id == image_id, Image.user_id == user_id).first()
    if not image:
        raise HTTPException(status_code=404, detail="图片不存在")

    ocr_results = (
        db.query(OcrResult.id)
        .filter(OcrResult.image_id == image_id)
        .offset(skip)
        .limit(limit)
        .all()
    )
    total = db.query(OcrResult).filter(OcrResult.image_id == image_id).count()

    return {
        "success": True,
        "data": {
            "total": total,
            "skip": skip,
            "limit": limit,
            "ids": [r[0] for r in ocr_results],
        },
    }
